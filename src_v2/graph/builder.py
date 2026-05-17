# src/graph/builder.py
from typing import Dict, Any
from langgraph.graph import StateGraph, END

from src_v2.graph.state import AgentState
from src_v2.graph.nodes import (
    classify_intent,
    check_chroma_db,
    search_github,
    wait_for_human,
    throttled_ingestion,
    prompt_for_query,
    reply_to_greeting
)

def build_graph(memory):
    """
    Constructs the StateGraph and wires the nodes.
    Accepts an async memory checkpointer from the orchestrator.
    """
    print("--- BUILDING AGENT GRAPH ---")
    workflow = StateGraph(AgentState)

    # Add all of our Nodes
    workflow.add_node("classify_intent", classify_intent)
    workflow.add_node("check_chroma_db", check_chroma_db)
    workflow.add_node("prompt_for_query", prompt_for_query)
    workflow.add_node("search_github", search_github)
    workflow.add_node("wait_for_human", wait_for_human)
    workflow.add_node("throttled_ingestion", throttled_ingestion)
    workflow.add_node("reply_to_greeting", reply_to_greeting)

    workflow.set_entry_point("classify_intent")

    # Conditional Edges (Traffic Cops)
    workflow.add_conditional_edges(
        "classify_intent",
        lambda state: state.get("user_intent"),
        {
            "KNOWLEDGE_QUERY": "check_chroma_db",
            "NEW_REPO_REQUEST": "search_github",
            "GREETING": "reply_to_greeting"
        }
    )

    workflow.add_conditional_edges(
        "check_chroma_db",
        lambda state: "prompt_for_query" if state.get("db_has_data") else "search_github"
    )

    # Standard Edges
    workflow.add_edge("search_github", "wait_for_human")
    workflow.add_edge("wait_for_human", "throttled_ingestion")
    
    # End points
    workflow.add_edge("reply_to_greeting", END)
    workflow.add_edge("prompt_for_query", END)
    workflow.add_edge("throttled_ingestion", END)

    app = workflow.compile(checkpointer=memory)
    return app