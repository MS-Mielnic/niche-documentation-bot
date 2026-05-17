# src_v2/graph/nodes.py
from src_v2.observability.telemetry import log_rag_retrieval
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


# --- GLOBAL OLLAMA MODEL ALIASES ---
# Aligned exactly with your local machine's 'ollama list' profiles
TEXT_MODEL = "llama3.1:latest"
VISION_MODEL = "llama3.2-vision:latest"

# --- NODE 0: INTENT CLASSIFICATION ---
def classify_intent(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 0: Analyzes the user's message and classifies their intent.
    This prevents expensive or unnecessary vector DB searches for simple greetings.
    """
    user_request = state["user_request"]

    print(f"--- NODE 0: CLASSIFYING INTENT FOR: '{user_request}' ---")

    llm = ChatOpenAI(
        temperature=0, 
        base_url="http://localhost:11434/v1", 
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
async def check_chroma_db(state: AgentState) -> Dict[str, Any]:
    """
    Node 1: Real Database Check.
    Checks if the requested repository documentation is already stored locally.
    """
    print("--- NODE 1: CHECKING LOCAL CHROMA DB ---")
    
    repo_to_check = state.get("selected_repo") or state.get("user_request")
    
    if not repo_to_check:
        return {"db_has_data": False}

    exists = check_if_repo_exists(repo_id=repo_to_check)
    
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
async def reply_to_greeting(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    print("--- NODE: REPLY TO GREETING ---")
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = config["configurable"].get("thread_id")
    if chat_adapter and channel_id:
        await chat_adapter.send_message(
            channel_id=channel_id, 
            text="Hello! I am ready to help you search or ingest GitHub repositories. What do you need?"
        )
    return {}

# --- NODE 2: THE VISION INTERCEPTOR ---
async def prompt_for_query(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 2 (V2): The Vision Interceptor.
    Retrieves narrative text chunks, intercepts section-bounded structural anchors 
    (images & directory trees) via metadata keys, restores them from the Heavy Store,
    and dynamically configures payloads for Llama 3.1 or 3.2-Vision.
    """
    print("--- NODE 2: PERFORMING MULTIMODAL VECTOR SEARCH & ANSWERING ---")
    
    user_request = state["user_request"]
    repo_to_check = state.get("selected_repo") or user_request
    
    vector_db = get_vector_store(repo_to_check)
    
    docs_with_scores = vector_db.similarity_search_with_score(user_request, k=6)
    
    context_text = []
    vision_images = []
    original_image_paths = [] 
    retrieved_tree_blocks = []
    
    # 1. THE INTERCEPTOR LOOP
    for doc, score in docs_with_scores:
        metadata = doc.metadata
        source = metadata.get("source", "Unknown")

        # 🚨 TEMPORARY DATA DIAGNOSTIC LOGS (PRESERVED) 🚨
        print("====== CHROMA METADATA DUMP ======")
        print(f"Content Snippet: {doc.page_content[:50]}...")
        print(f"Full Metadata Dict: {metadata}")
        print(f"Raw 'image_path' from DB: {metadata.get('image_path')}")
        print(f"Raw 'source' from DB: {metadata.get('source')}")
        print("==================================")

        # Standard Operation: Always append the direct page content retrieved
        context_text.append(f"Source: {source}\n{doc.page_content}")

        # 🎯 FIX: ANCHOR TYPE A - DETECT ADJACENT INLINE SECTION IMAGES
        # Stamped right onto narrative text chunks by your stateful ingestion loop
        image_path = metadata.get("image_path")
        image_parent_id = metadata.get("parent_id")
        element_type = metadata.get("element_type", "text")
        
        # Branch 1: Extracted via text chunk metadata pointer (Contextual Sync)
        # --- Inside Node 2 Loop ---
        # --- Inside src_v2/graph/nodes.py -> Node 2 Interceptor Loop ---
        if image_path and image_parent_id:
            if image_path not in original_image_paths:
                
                # 🎯 NEW: SEMANTIC CONTEXT GATE
                # Extract the section header name (e.g., "## How the Backend Agent Works")
                section_title = metadata.get("section", "").lower()
                user_query = user_request.lower()
                
                # Clean up filenames for string matching (e.g., "agent.png" -> "agent")
                image_keyword = image_path.split('.')[0].replace('_', ' ').replace('-', ' ')
                
                # CRITICAL VERIFICATION: Only load the image if the user is asking about 
                # words in the section header, OR if the query mentions the image filename context directly.
                # This perfectly blocks global 'app.png' from sneaking into a specific backend agent query!
                is_relevant_section = any(word in user_query for word in section_title.split() if len(word) > 3)
                is_explicit_image_request = any(kw in user_query for kw in image_keyword.split())
                
                if is_relevant_section or is_explicit_image_request or "root document" not in section_title:
                    parent_data = get_parent(repo_to_check, image_parent_id)
                    if parent_data:
                        print(f"🎯 Interceptor Approved Relevant Image: {image_path} (Section: {metadata.get('section')})")
                        
                        # Extract and sanitize your base64 string completely
                        raw_content = parent_data if isinstance(parent_data, str) else parent_data.get("content", "")
                        clean_b64 = str(raw_content).strip().replace("\n", "").replace("\r", "")
                        
                        if not clean_b64.startswith("data:image/"):
                            clean_b64 = f"data:image/png;base64,{clean_b64}"
                            
                        vision_images.append(clean_b64)
                        original_image_paths.append(image_path)
                        
                        alt_text = parent_data.get("alt_text", "Diagram") if isinstance(parent_data, dict) else "Diagram"
                        context_text.append(f"[System Note: Relevant engineering diagram '{alt_text}' has been provided for this topic segment.]")
                else:
                    print(f"🚫 Interceptor Filtered Out Unrelated Image: {image_path} (Belongs to section '{metadata.get('section')}', not contextually relevant to query.)")

        # Branch 2: Legacy fallback if Chroma returns the raw standalone image token document itself
        elif element_type == "image" and image_parent_id:
            parent_data = get_parent(repo_to_check, image_parent_id)
            if parent_data:
                print(f"👁️ Interceptor: Loading Raw Standalone Image Document ({image_parent_id})")
                vision_images.append(parent_data["content"])
                fallback_path = metadata.get("image_path", "unknown_asset.png")
                if fallback_path not in original_image_paths:
                    original_image_paths.append(fallback_path)

        # 🎯 FIX: ANCHOR TYPE B - DETECT ADJACENT INLINE SECTION DIRECTORY TREES
        tree_parent_id = metadata.get("tree_parent_id")
        
        # Branch 1: Extracted via text chunk metadata pointer (Contextual Sync)
        if tree_parent_id:
            if tree_parent_id not in retrieved_tree_blocks:
                parent_data = get_parent(repo_to_check, tree_parent_id)
                if parent_data:
                    print(f"🌲 Interceptor: Restoring Contextual Directory Tree ({tree_parent_id}) via text chunk pointer.")
                    retrieved_tree_blocks.append(tree_parent_id)
                    context_text.append(f"--- RELEVANT SECTION REPOSITORY LAYOUT ---\n{parent_data['content']}\n-------------------")

        # Branch 2: Legacy fallback if Chroma returns the raw standalone table/tree document rows
        elif element_type in ["table", "tree"] and image_parent_id: # uses parent_id for standalone rows
            parent_data = get_parent(repo_to_check, image_parent_id)
            if parent_data and image_parent_id not in retrieved_tree_blocks:
                print(f"🧩 Interceptor: Restoring Standalone Row {element_type} ({image_parent_id})")
                retrieved_tree_blocks.append(image_parent_id)
                context_text.append(f"--- FULL {element_type.upper()} ---")
                context_text.append(parent_data["content"])
                context_text.append("-------------------")
            
    full_context = "\n\n".join(context_text)

    # 3. DYNAMIC MODEL ROUTING
    if vision_images:
        print(f"🧠 ROUTING: Image detected. Invoking {VISION_MODEL}...")
        llm = ChatOpenAI(
            base_url="http://localhost:11434/v1",
            api_key="local-llm",
            model=VISION_MODEL,
            temperature=0
        )
        
        # Structure payload cleanly for LangChain + Ollama OpenAI Endpoint Specs
        content_list = [{"type": "text", "text": f"Context:\n{full_context}\n\nQuestion: {user_request}"}]
        
        for b64_uri in vision_images:
            content_list.append({
                "type": "image_url", 
                "image_url": {
                    "url": b64_uri  # Contains the validated 'data:image/png;base64,...' string
                }
            })
            
        messages = [
            SystemMessage(content="""You are an expert technical analyst with vision capabilities.
            Use the provided text context and images to answer the user's question precisely.
            
            CRITICAL FORMATTING RULES:
            1. Never mention or write out filenames like 'agent.png' or markdown image text.
            2. Never output HTML tags like <img> or markdown images like ![](...) under any circumstances.
            3. Do not tell the user an image has been provided or is shown below. Simply provide your raw technical explanation text."""),
            HumanMessage(content=content_list)
        ]
        
    else:
        print("⚡ ROUTING: Text only. Invoking Llama 3.1 (8B)...")
        llm = ChatOpenAI(
            base_url="http://localhost:11434/v1",
            api_key="local-llm",
            model=TEXT_MODEL,
            temperature=0
        )
        
        prompt = f"Use this context to answer:\n{full_context}\n\nQuestion: {user_request}"
        messages = [
            SystemMessage(content="You are an expert technical analyst. Use the provided context to answer the user's question precisely."),
            HumanMessage(content=prompt)
        ]

    # 4. GENERATE ANSWER 
    response = await llm.ainvoke(messages)
    
    # --- THE TELEMETRY HOOK ---
    log_rag_retrieval(
        query=user_request,
        docs_with_scores=docs_with_scores,
        llm_response=response.content,
        repo_id=repo_to_check
    )
    
    # --- CONSOLIDATED SLACK CHAT ADAPTER HOOK ---
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = config["configurable"].get("thread_id")
    
    if chat_adapter and channel_id:
        image_urls = []
        # Build the public GitHub URLs for Slack
        for path in original_image_paths:
            # Safely strip leading dots and slashes (e.g., ./app.png -> app.png)
            clean_path = path.lstrip('./').lstrip('/')
            image_urls.append(f"https://raw.githubusercontent.com/{repo_to_check}/main/{clean_path}")

        # Execute a SINGLE send_message call
        await chat_adapter.send_message(
            channel_id=channel_id, 
            text=response.content,
            image_urls=image_urls # Passes empty list if no images, preventing Slack errors
        )    

    return {"db_has_data": True}

# --- NODE 3: GITHUB SEARCH ---
async def search_github(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    print("--- NODE 3: SEARCHING GITHUB ---")
    user_request = state["user_request"]
    
    llm = ChatOpenAI(
        temperature=0, 
        base_url="http://localhost:11434/v1", 
        api_key="local-llm",
        model=TEXT_MODEL 
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
    channel_id = config["configurable"].get("thread_id")

    # Handle No Repos Found Cleanly
    if not repos:
        print("--- NO REPOS FOUND ---")
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id, 
                text=f"I couldn't find any repositories matching '{search_query}'."
            )
        return {"repo_options": []} 

    # Format options and SEND THE SLACK MESSAGE HERE (Before Node 4)
    options = [repo['full_name'] for repo in repos]
    print(f"--- FORMATTED OPTIONS: {options} ---")
    
    if chat_adapter and channel_id:
        print(f"--- SENDING SLACK BUTTONS TO CHANNEL: {channel_id} ---")
        await chat_adapter.ask_for_human_approval(
            channel_id=channel_id,
            text="I found these repositories. Which one should I ingest?",
            options=options
        )

    return {"repo_options": options}


# --- NODE 4: HUMAN IN THE LOOP ---
async def wait_for_human(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 4: This node is now pure. It only pauses the graph. 
    When resumed, it will re-execute, but the interrupt will instantly return the user's selection 
    without causing any duplicate Slack messages.
    """
    print("--- NODE 4: WAITING FOR HUMAN APPROVAL ---")
    
    repo_options = state.get("repo_options", [])
    
    # If Node 3 found nothing, skip the pause entirely
    if not repo_options:
        print("--- SKIPPING HITL: NO VALID REPOS ---")
        return {"selected_repo": None}
    
    # The graph goes to sleep here. On resume, this line grabs the clicked button value.
    user_selection = interrupt("Waiting for user to select a repository...")
    
    print(f"--- WAKING UP! HUMAN SELECTED: {user_selection} ---")
    return {"selected_repo": user_selection}


# --- UPDATED NODE 5: TRUE JIT SYNC ENGINE ---
async def throttled_ingestion(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    selected_repo = state.get("selected_repo")
    if not selected_repo:
        return {"db_has_data": False}
        
    print(f"--- NODE 5: STARTING JIT SYNC FOR '{selected_repo}' ---")
    
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = config["configurable"].get("thread_id")
    
    # 1. THE JIT HASH CHECK
    client = GitHubClient()
    latest_hash = await client.get_latest_commit_hash(selected_repo)
    local_hash = get_local_hash(selected_repo)

    if latest_hash and latest_hash == local_hash:
        print(f"--- JIT MATCH: '{selected_repo}' is already up to date (Hash: {latest_hash}). Skipping ingestion. ---")
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id, 
                text=f"✅ `{selected_repo}` is already completely up to date with the latest GitHub commit."
            )
        return {"db_has_data": True}
    
    # 2. THE INGESTION ENGINE (Executes if new repo OR if hashes differ)
    if chat_adapter and channel_id and local_hash:
        await chat_adapter.send_message(
            channel_id=channel_id, 
            text=f"🔄 I see new updates in `{selected_repo}`. Starting background sync..."
        )

    try:
        success = await ingest_repository(repo_id=selected_repo, chat_adapter=chat_adapter, channel_id=channel_id)
        
        if success:
            # 3. SAVE THE NEW HASH ON SUCCESS
            if latest_hash:
                save_local_hash(selected_repo, latest_hash)
                
            if chat_adapter and channel_id:
                await chat_adapter.send_message(
                    channel_id=channel_id, 
                    text=f"✅ Done! I've fully synced `{selected_repo}`. You can now ask me technical questions about it."
                )
        return {"db_has_data": True}
        
    except Exception as e:
        print(f"--- INGESTION FAILED: {e} ---")
        if chat_adapter and channel_id:
            await chat_adapter.send_message(
                channel_id=channel_id, 
                text=f"❌ Sorry, something went wrong during ingestion: {e}"
            )
        return {"db_has_data": False}