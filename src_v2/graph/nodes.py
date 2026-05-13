# src/graph/nodes.py
from src.observability.telemetry import log_rag_retrieval
from typing import Dict, Any
from src.rag.vector_store import get_local_hash, save_local_hash
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt

from src.graph.state import AgentState
from src.mcp.github_client import GitHubClient
from src.rag.vector_store import get_vector_store, check_if_repo_exists
from src.rag.ingestion import ingest_repository

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
        model="llama3.1" 
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

async def prompt_for_query(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Node 2: The real RAG retrieval. Retrieves context from ChromaDB and answers the user.
    """
    print("--- NODE 2: PERFORMING VECTOR SEARCH & ANSWERING ---")
    
    # Prioritize selected_repo, fallback to parsing user request
    repo_to_check = state.get("selected_repo") or state.get("user_request")
    vector_db = get_vector_store(repo_to_check)
    
    # UPGRADE: Use with_score to get the mathematical distance for our telemetry
    docs_with_scores = vector_db.similarity_search_with_score(state["user_request"], k=4)
    
    # Rebuild the context string using the doc from the tuple
    context = "\n\n".join([f"Source: {doc.metadata.get('source', 'Unknown')}\n{doc.page_content}" for doc, score in docs_with_scores])
    
    llm = ChatOpenAI(temperature=0, base_url="http://localhost:11434/v1", api_key="local-llm", model="llama3.1")
    
    prompt = f"Use this context to answer: {context}\n\nQuestion: {state['user_request']}"
    response = await llm.ainvoke(prompt)

    # --- THE TELEMETRY HOOK ---
    # Log the exact data behind the scenes asynchronously to keep the UI fast
    log_rag_retrieval(
        query=state["user_request"],
        docs_with_scores=docs_with_scores,
        llm_response=response.content,
        repo_id=repo_to_check
    )
    # --------------------------
    
    chat_adapter = config["configurable"].get("chat_adapter")
    channel_id = config["configurable"].get("thread_id")
    if chat_adapter and channel_id:
        await chat_adapter.send_message(channel_id=channel_id, text=response.content)

    return {"db_has_data": True}


# --- NODE 3: GITHUB SEARCH ---
async def search_github(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    print("--- NODE 3: SEARCHING GITHUB ---")
    user_request = state["user_request"]
    
    llm = ChatOpenAI(
        temperature=0, 
        base_url="http://localhost:11434/v1", 
        api_key="local-llm",
        model="llama3.1" 
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