# src_v2/graph/nodes.py
# Add this near your other imports at the top
from opentelemetry import trace
from src_v2.observability.telemetry import (
    tracer,
    log_rag_retrieval,
    add_retrieved_document_events,
)
from typing import Dict, Any
from src_v2.rag.vector_store import get_local_hash, save_local_hash, get_vector_store, check_if_repo_exists
from src_v2.rag.ingestion import ingest_repository
from src_v2.rag.parent_store import get_parent # NEW: The Heavy Parent Store
from src_v2.mcp.github_client import GitHubClient

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
# NEW: Required for Multimodal Prompting
from langchain_core.messages import HumanMessage, SystemMessage 
from langgraph.types import interrupt
from src_v2.graph.state import AgentState
import re
import traceback
import os
import time


# --- GLOBAL OLLAMA MODEL ALIASES ---
# Aligned exactly with your local machine's 'ollama list' profiles
TEXT_MODEL = "llama3.1:latest"
VISION_MODEL = "llama3.2-vision:latest"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

def _approx_message_chars(messages) -> int:
    """
    Estimate prompt size in characters without storing prompt content in traces.

    Supports both text-only messages and multimodal HumanMessage content lists.
    """
    total = 0

    for message in messages:
        content = getattr(message, "content", "")

        if isinstance(content, str):
            total += len(content)

        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        total += len(item.get("text", ""))
                    elif item.get("type") == "image_url":
                        # Do not count or store base64 image content.
                        # Count only the presence of an image as a small fixed marker.
                        total += len("[image_url]")
                else:
                    total += len(str(item))

        else:
            total += len(str(content))

    return total

# --- NODE 0: INTENT CLASSIFICATION ---
@tracer.start_as_current_span("node.classify_intent")
def classify_intent(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 0: Analyzes the user's message and classifies their intent.
    This prevents expensive or unnecessary vector DB searches for simple greetings.
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("llm.model_used", VISION_MODEL)

    user_request = state["user_request"]

    print(f"--- NODE 0: CLASSIFYING INTENT FOR: '{user_request}' ---")

    llm = ChatOpenAI(
        temperature=0, 
        # This will now use the environment variable, or fall back to localhost if it's missing
        base_url=OLLAMA_BASE_URL, 
        api_key="local-llm",
        model=VISION_MODEL
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a highly efficient routing AI. 
        Analyze the user's message and classify it into EXACTLY ONE of the following three categories:
        
        1. GREETING: The user is saying hello, expressing thanks, or making casual conversation.
        2. KNOWLEDGE_QUERY: The user is asking a question about code, documentation, or technical systems.
        3. NEW_REPO_REQUEST: The user is explicitly asking you to read, ingest, or look at a new GitHub repository.
        
        Respond with ONLY the category name. Do not include any other text."""),
        ("user", "{user_request}")
    ])

    chain = prompt | llm
    response = chain.invoke({"user_request": user_request})
    intent = response.content.strip().upper()

    # Fallback safety
    valid_intents = ["GREETING", "KNOWLEDGE_QUERY", "NEW_REPO_REQUEST"]
    if intent not in valid_intents:
        print(f"--- WARNING: LLM returned invalid intent '{intent}'. Defaulting to KNOWLEDGE_QUERY ---")
        intent = "KNOWLEDGE_QUERY"

    print(f"--- INTENT CLASSIFIED AS: {intent} ---")
    return {"user_intent": intent}


# --- NODE 1: DATABASE CHECK ---
@tracer.start_as_current_span("node.check_chroma_db")
async def check_chroma_db(state: AgentState) -> Dict[str, Any]:
    """
    Node 1: Checks if the requested repository documentation is already stored locally.
    """
    current_span = trace.get_current_span()
    print("--- NODE 1: CHECKING LOCAL CHROMA DB ---")
    
    repo_to_check = state.get("selected_repo") or state.get("user_request")
    
    if not repo_to_check:
        current_span.set_attribute("db.check_skipped", True)
        return {"db_has_data": False}

    current_span.set_attribute("db.repo_checked", repo_to_check)
    exists = check_if_repo_exists(repo_id=repo_to_check)
    current_span.set_attribute("db.found", exists)
    
    if exists:
        print(f"--- DATABASE FOUND FOR: {repo_to_check} ---")
        return {"db_has_data": True, "selected_repo": repo_to_check}
    else:
        print(f"--- NO LOCAL DATA FOR: {repo_to_check} ---")
        return {"db_has_data": False}


# --- CONDITIONAL EDGES (ROUTING LOGIC) ---
def route_after_classification(state: AgentState) -> str:
    intent = state.get("user_intent")
    
    if intent == "KNOWLEDGE_QUERY":
        return "check_db"
    elif intent == "NEW_REPO_REQUEST":
        return "search_github"
    else:
        return "reply_to_greeting"

def route_after_db_check(state: AgentState) -> str:
    if state.get("db_has_data"):
        return "prompt_for_query"
    else:
        return "search_github"


# --- NEW NODE: REPLY TO GREETING ---
@tracer.start_as_current_span("node.reply_to_greeting")
async def reply_to_greeting(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    # We grab the current span to allow for potential future attribute tagging
    # (e.g., if we want to track which channel_id was greeted)
    current_span = trace.get_current_span()

    print("--- NODE: REPLY TO GREETING ---")
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = state.get("channel_id")
    if channel_id:
        current_span.set_attribute("slack.channel_id", channel_id)
    thread_ts = state.get("thread_ts")

    if chat_adapter and channel_id:
        await chat_adapter.send_message(
            channel_id=channel_id,
            thread_ts =thread_ts, 
            text="Hello! I am ready to help you search or ingest GitHub repositories. What do you need?"
        )
    return {}

# --- NODE 2 ---

@tracer.start_as_current_span("node.prompt_for_query")
async def prompt_for_query(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 2 (V3): The Hybrid Interceptor.
    Leverages Token Hot-Swapping, Proximity Cascading, and Sniper Catching to 
    guarantee 100% structural fidelity of heavy Markdown elements at query time.
    """
    print("--- NODE 2: PERFORMING HYBRID MULTIMODAL VECTOR SEARCH ---")
    # Grab the current span so we can attach RAG data to it
    current_span = trace.get_current_span()

    user_request = state["user_request"]
    repo_to_check = state.get("selected_repo") or user_request
    
    vector_db = get_vector_store(repo_to_check)
    docs_with_scores = vector_db.similarity_search_with_score(user_request, k=6)
    
    add_retrieved_document_events(
        span=current_span,
        docs_with_scores=docs_with_scores,
    )
    
    # --- TEMPORARY RAW DATA DUMP ---
    print("\n--- RAW CHROMA RETRIEVAL DATA ---")
    for i, (doc, score) in enumerate(docs_with_scores):
        print(f"\n[Chunk {i+1} | Score: {score:.4f}]")
        print(f"Metadata: {doc.metadata}")
        print(f"Content snippet: {doc.page_content[:200]}...")
    print("---------------------------------\n")



    # 1. INITIALIZE VARIABLES & BUCKETS (Top of prompt_for_query)
    context_text = []
    
    # These are the final arrays the routing logic will use
    vision_images = []
    original_image_paths = [] 
    
    # --- NEW: BUCKETS FOR HIERARCHY PRUNING ---
    specific_vision_images = []
    specific_image_paths = []
    root_vision_images = []
    root_image_paths = []
    
    retrieved_structural_blocks = set() 

    # 2. THE MAIN CHROMA LOOP
    for doc, score in docs_with_scores:
        meta = doc.metadata
        element_type = meta.get("element_type", "text")
        raw_content = doc.page_content
        source = meta.get("source", "Unknown")

        print(f"====== INTERCEPTOR HOOK: {element_type.upper()} ======")


        # 🎯 MECH 2 & 3: TEXT CHUNKS (Token Hot-Swap & Proximity Catch)
        if element_type == "text":
            processed_content = raw_content

            # A. The Token Hot-Swap (Spatial Integrity)
            anchors = re.findall(r'\[\[RAG_(TABLE|TREE|IMAGE)_ANCHOR:\s*(.*?)\]\]', processed_content)
            
            for anchor_type, parent_id in anchors:
                if parent_id not in retrieved_structural_blocks:
                    parent_data = get_parent(repo_to_check, parent_id)
                    if parent_data:
                        retrieved_structural_blocks.add(parent_id)
                        
                        if anchor_type == "IMAGE":
                            # 🎯 INLINE IMAGES: Sort into Buckets!
                            is_root = "root document" in meta.get("section", "root document").lower()
                            
                            img_b64 = parent_data.get("content", "")
                            if not img_b64.startswith("data:image/"):
                                img_b64 = f"data:image/png;base64,{img_b64}"
                            
                            target_path = parent_data.get("image_path") or meta.get("image_path", "unknown_asset.png")
                            
                            # Place in appropriate bucket based on hierarchy
                            if is_root:
                                if target_path not in root_image_paths:
                                    root_image_paths.append(target_path)
                                    root_vision_images.append(img_b64)
                            else:
                                if target_path not in specific_image_paths:
                                    specific_image_paths.append(target_path)
                                    specific_vision_images.append(img_b64)
                                    print(f"✅ Interceptor loaded specific inline companion image: {target_path}")
                            
                            processed_content = processed_content.replace(
                                f"[[RAG_IMAGE_ANCHOR: {parent_id}]]", 
                                f"[System Note: Companion diagram '{parent_data.get('alt_text', 'Diagram')}' provided to vision model.]"
                            )
                        else:
                            # Swap the text token with the actual raw Table/Tree data
                            replacement = f"\n--- RECONSTRUCTED {anchor_type} ---\n{parent_data['content']}\n-------------------\n"
                            processed_content = processed_content.replace(f"[[RAG_{anchor_type}_ANCHOR: {parent_id}]]", replacement)
                else:
                    processed_content = processed_content.replace(f"[[RAG_{anchor_type}_ANCHOR: {parent_id}]]", "")

            # Append the beautifully reconstructed narrative chunk
            context_text.append(f"Source: {source}\n{processed_content}")

            # B. The Proximity Catch (Cascaded Section Metadata)
            cascade_table_ids = meta.get("section_table_ids", "").split(",")
            cascade_tree_id = meta.get("tree_parent_id")
            cascade_image_id = meta.get("active_image_id")

            proximity_ids = [tid for tid in cascade_table_ids if tid]
            if cascade_tree_id: proximity_ids.append(cascade_tree_id)
            if cascade_image_id: proximity_ids.append(cascade_image_id)

            # Loop through proximity IDs
            for p_id in proximity_ids:
                if p_id and p_id not in retrieved_structural_blocks:
                    parent_data = get_parent(repo_to_check, p_id)
                    if parent_data:
                        retrieved_structural_blocks.add(p_id)
                        p_type = parent_data.get("element_type", "").upper()
                        
                        if p_type == "IMAGE":
                            # 🎯 SECTION IMAGES: Sort into Buckets!
                            is_root = "root document" in meta.get("section", "root document").lower()
                            
                            img_b64 = parent_data.get("content", "")
                            if not img_b64.startswith("data:image/"):
                                img_b64 = f"data:image/png;base64,{img_b64}"
                                
                            target_path = parent_data.get("image_path") or meta.get("image_path", "unknown_context_image.png")
                            
                            # Place in appropriate bucket based on hierarchy
                            if is_root:
                                if target_path not in root_image_paths:
                                    root_image_paths.append(target_path)
                                    root_vision_images.append(img_b64)
                            else:
                                if target_path not in specific_image_paths:
                                    specific_image_paths.append(target_path)
                                    specific_vision_images.append(img_b64)
                                    print(f"✅ Interceptor loaded specific section companion image: {target_path}")
                                    
                            context_text.append(f"[System Note: Contextual section diagram '{target_path}' provided.]")
                        else:
                            context_text.append(f"--- CONTEXTUAL SECTION {p_type} ---\n{parent_data['content']}\n-------------------")

    # =========================================================================
    # 🎯 THE PRUNING GATE (Executes AFTER the entire vector search loop is done)
    # =========================================================================
    if specific_image_paths:
        vision_images = specific_vision_images
        original_image_paths = specific_image_paths
        print(f"🎯 UI Focus: Specific diagrams found {original_image_paths}. Pruning root images.")
    else:
        vision_images = root_vision_images
        original_image_paths = root_image_paths
        if original_image_paths:
            print(f"🌐 UI Focus: No specific diagrams found. Defaulting to root images {original_image_paths}.")

    full_context = "\n\n".join(context_text)
    
    #tracing span and rags metrics ################
    # Attach retrieval metrics to the span
    current_span.set_attribute("rag.query", user_request)
    current_span.set_attribute("rag.repo_target", repo_to_check)
    current_span.set_attribute("rag.retrieved_chunks_count", len(docs_with_scores))
    
    # If chunks were found, record the best score to monitor retrieval drift over time
    if docs_with_scores:
        best_score = float(docs_with_scores[0][1])
        current_span.set_attribute("rag.best_distance_score", best_score)
    ###################    
    
    # 3. DYNAMIC MODEL ROUTING
    chat_adapter = config["configurable"].get("chat_adapter")
    
    # Dynamically fetch UI constraints from the injected adapter
    ui_constraints = chat_adapter.get_formatting_constraints() if chat_adapter else ""
    
    base_system_prompt = f"""You are an expert technical analyst. Use the provided context to answer the user's question precisely.
    
    {ui_constraints}
    """

    if vision_images:
        print(f"🧠 ROUTING: Image detected. Invoking {VISION_MODEL}...")
        current_span.set_attribute("llm.model_used", VISION_MODEL)
        current_span.set_attribute("llm.is_multimodal", True)
        llm = ChatOpenAI(
                temperature=0, 
                # This will now use the environment variable, or fall back to localhost if it's missing
                base_url=OLLAMA_BASE_URL, 
                api_key="local-llm",
                model=VISION_MODEL
            )
        
        content_list = [{"type": "text", "text": f"Context:\n{full_context}\n\nQuestion: {user_request}"}]
        for b64_uri in vision_images:
            content_list.append({"type": "image_url", "image_url": {"url": b64_uri}})
            
        messages = [
            SystemMessage(content=base_system_prompt),
            HumanMessage(content=content_list)
        ]
    else:
        print("⚡ ROUTING: Text only. Invoking Llama 3.1...")
        current_span.set_attribute("llm.model_used", TEXT_MODEL)
        current_span.set_attribute("llm.is_multimodal", False)
        llm = ChatOpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="local-llm", 
            model=TEXT_MODEL, 
            temperature=0
            )
        prompt = f"Context:\n{full_context}\n\nQuestion: {user_request}"
        messages = [
            SystemMessage(content=base_system_prompt),
            HumanMessage(content=prompt)
        ]

    # 4. GENERATE ANSWER & DISPATCH
    approx_prompt_chars = _approx_message_chars(messages)
    current_span.set_attribute("llm.approx_prompt_chars", approx_prompt_chars)

    llm_start = time.perf_counter()
    response = await llm.ainvoke(messages)
    llm_latency_ms = (time.perf_counter() - llm_start) * 1000

    response_content = response.content or ""

    current_span.set_attribute("llm.latency_ms", llm_latency_ms)
    current_span.set_attribute("llm.approx_response_chars", len(response_content))

    # Keep the existing field for backward compatibility with your current traces.
    current_span.set_attribute("llm.response_length", len(response_content))
    log_rag_retrieval(query=user_request, docs_with_scores=docs_with_scores, llm_response=response.content, repo_id=repo_to_check)
    
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = state.get("channel_id")
    thread_ts = state.get("thread_ts")
    
    if chat_adapter and channel_id:
        image_urls = []
        for path in original_image_paths:
            clean_path = path.lstrip('./').lstrip('/')
            image_urls.append(f"https://raw.githubusercontent.com/{repo_to_check}/main/{clean_path}")

        await chat_adapter.send_message(
            channel_id=channel_id, 
            thread_ts = thread_ts,
            text=response.content,
            image_urls=image_urls 
        )    

    return {"db_has_data": True}

# --- NODE 3: GITHUB SEARCH ---
@tracer.start_as_current_span("node.search_github")
async def search_github(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    print("--- NODE 3: SEARCHING GITHUB ---")

    current_span = trace.get_current_span()
    current_span.set_attribute("llm.model_used", TEXT_MODEL)

    user_request = state["user_request"]
    
    llm = ChatOpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key="local-llm", 
        model=TEXT_MODEL, 
        temperature=0
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", "Extract only the core technology or repository name from the user's request. Ignore Slack handles (like <@U123>), greetings, or conversational words. Return ONLY the strict search term (e.g., 'langgraph' or 'react'). Do not include any other text or punctuation."),
        ("user", "{user_request}")
    ])

    chain = prompt | llm
    response = await chain.ainvoke({"user_request": user_request})
    
    search_query = response.content.strip()
    print(f"--- EXTRACTED KEYWORD: '{search_query}' ---")
    
    client = GitHubClient()
    repos = await client.search_repositories(query=search_query, limit=3)
    
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = state.get("channel_id")
    thread_ts = state.get("thread_ts")


    # Handle No Repos Found Cleanly
    if not repos:
        print("--- NO REPOS FOUND ---")
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id, 
                thread_ts=thread_ts,
                text=f"I couldn't find any repositories matching '{search_query}'."
            )
        return {"repo_options": []} 

    # Format options and SEND THE SLACK MESSAGE HERE (Before Node 4)
    options = [repo['full_name'] for repo in repos]
    print(f"--- FORMATTED OPTIONS: {options} ---")

    # 🎯 DEBUG: Let's see exactly what's failing the check
    print(f"--- DEBUG: chat_adapter={chat_adapter}, channel_id={channel_id} ---")
    
    if chat_adapter and channel_id:
        print(f"--- SENDING SLACK BUTTONS TO CHANNEL: {channel_id} ---")
        await chat_adapter.ask_for_human_approval(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text="I found these repositories. Which one should I ingest?",
            repo_options=options
        )
    else:
        print("--- CRITICAL: CANNOT SEND BUTTONS, ADAPTER OR CHANNEL MISSING ---")

    return {"repo_options": options}


# --- NODE 4: HUMAN IN THE LOOP ---
@tracer.start_as_current_span("node.wait_for_human")
async def wait_for_human(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 4: This node is now pure. It only pauses the graph. 
    When resumed, it will re-execute, but the interrupt will instantly return the user's selection 
    without causing any duplicate Slack messages.
    """
    current_span = trace.get_current_span()
    print("--- NODE 4: WAITING FOR HUMAN APPROVAL ---")
    
    repo_options = state.get("repo_options", [])

    # Capture how many options were presented as an attribute
    current_span.set_attribute("hitl.options_count", len(repo_options))
    
    # If Node 3 found nothing, skip the pause entirely
    if not repo_options:
        current_span.set_attribute("hitl.skipped", True)
        print("--- SKIPPING HITL: NO VALID REPOS ---")
        return {"selected_repo": None}
    
    # The graph goes to sleep here. On resume, this line grabs the clicked button value.
    user_selection = interrupt("Waiting for user to select a repository...")
    # Upon resume, capture the selection
    current_span.set_attribute("hitl.user_selection", str(user_selection))
    
    print(f"--- WAKING UP! HUMAN SELECTED: {user_selection} ---")
    return {"selected_repo": user_selection}

# --- UPDATED NODE 5: TRUE JIT SYNC ENGINE ---
@tracer.start_as_current_span("node.throttled_ingestion")
async def throttled_ingestion(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:

    # 1. Grab the current span at the very beginning
    current_span = trace.get_current_span()

    selected_repo = state.get("selected_repo")
    if not selected_repo:
        current_span.set_attribute("ingestion.skipped", True)
        return {"db_has_data": False}
        
    print(f"--- NODE 5: STARTING JIT SYNC FOR '{selected_repo}' ---")

    # 2. Attach the target repository to the span
    current_span.set_attribute("ingestion.repo_target", selected_repo)
    
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = state.get("channel_id")
    thread_ts = state.get("thread_ts")


    # 🚨 DEBUG LOG: Prove if LangGraph lost the adapter during the "sleep" phase
    print(f"--- DEBUG: Is chat_adapter present after waking up? {chat_adapter is not None} ---")
    
    # 1. THE JIT HASH CHECK
    client = GitHubClient()
    latest_hash = await client.get_latest_commit_hash(selected_repo)
    local_hash = get_local_hash(selected_repo)

    if latest_hash:
        current_span.set_attribute("ingestion.latest_hash", latest_hash)
    if local_hash:
        current_span.set_attribute("ingestion.local_hash", local_hash)


    if latest_hash and latest_hash == local_hash:
        print(f"--- JIT MATCH: '{selected_repo}' is already up to date (Hash: {latest_hash}). Skipping ingestion. ---")
        current_span.set_attribute("ingestion.sync_required", False)
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id,
                thread_ts=thread_ts, 
                text=f"✅ `{selected_repo}` is already completely up to date with the latest GitHub commit.\n *You can now ask me technical questions about this repository*"
            )
        return {"db_has_data": True}
    
    # Mark that a full sync is initiating
    current_span.set_attribute("ingestion.sync_required", True)
    
    # 2. THE INGESTION ENGINE (Executes if new repo OR if hashes differ)
    message_id = None # <-- NEW: Variable to hold our target message pointer
    
    if chat_adapter and channel_id:
        # Better UX: Distinguish between an update and a first-time sync
        if local_hash:
            msg_text = f"🔄 I see new updates in `{selected_repo}`. Starting background sync..."
        else:
            msg_text = f"🚀 First time seeing `{selected_repo}`. Starting full ingestion..."
            
        # Capture the response dict from Slack
        response_data = await chat_adapter.send_message(
            channel_id=channel_id, 
            thread_ts=thread_ts,
            text=msg_text
        )
        
        # Extract the Slack message timestamp ('ts') to use as our update pointer
        if response_data:
            message_id = response_data.get("ts")

    try:
        # <-- NEW: Pass the message_id into the ingestion engine
        success = await ingest_repository(
            repo_id=selected_repo, 
            chat_adapter=chat_adapter, 
            channel_id=channel_id,
            message_id=message_id,
            thread_ts=thread_ts 
        )
        #  Attach the ultimate success/fail status of the pipeline   
        current_span.set_attribute("ingestion.success", success)
        if success:
            # 3. SAVE THE NEW HASH ON SUCCESS
            if latest_hash:
                save_local_hash(selected_repo, latest_hash)
            # 🎯 INSTRUMENTED DEBUG BLOCK
            print(f"--- DEBUG: Attempting to send success message ---")
            print(f"--- DEBUG DATA: chat_adapter={chat_adapter}, channel_id={channel_id}, thread_ts={thread_ts} ---")
                
            if chat_adapter and channel_id:
                # We can send this as a new message, or theoretically update the progress 
                # message to "Done". Sending a new message provides a nice final notification.
                try:
                    await chat_adapter.send_message(
                        channel_id=channel_id, 
                        thread_ts=thread_ts,
                        text=f"✅ Done! I've fully synced `{selected_repo}`. You can now ask me technical questions about it."
                    )
                    print("--- DEBUG: Success message sent successfully! ---")
                except Exception as e:
                    print(f"--- CRITICAL ERROR: send_message failed: {str(e)} ---")
                    traceback.print_exc()
        else:
            print(f"--- CRITICAL: Could not send message. Adapter={chat_adapter}, Channel={channel_id} ---")
        return {"db_has_data": True}
        
    except Exception as e:
        print(f"--- INGESTION FAILED: {e} ---")
        # Record the failure in your trace!
        current_span.set_attribute("ingestion.success", False)
        current_span.set_attribute("ingestion.error_message", str(e))
        
        # ADD THIS: Mark span as error in OTel
        current_span.set_status(trace.Status(trace.StatusCode.ERROR)) 
        
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id, 
                thread_ts=thread_ts,
                text=f"❌ Sorry, something went wrong during ingestion: {e}"
            )
        return {"db_has_data": False}
    




  