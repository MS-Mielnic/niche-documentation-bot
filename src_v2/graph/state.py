# src/graph/state.py
from typing import List, Optional, TypedDict

class AgentState(TypedDict):
    """
    The state object that LangGraph will pass between nodes.
    CRITICAL: Everything in this TypedDict MUST be 100% JSON serializable.
    """
    thread_id: str             
    user_request: str          
    user_intent: str           # Stores the LLM classification (e.g., GREETING, KNOWLEDGE_QUERY, NEW_REPO_REQUEST)
    db_has_data: bool          
    repo_options: List[str]    
    selected_repo: Optional[str]
    channel_id: str
    thread_ts: str