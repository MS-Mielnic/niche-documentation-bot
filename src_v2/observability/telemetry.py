import json
import os
from datetime import datetime
from typing import List, Tuple
from langchain_core.documents import Document

def log_rag_retrieval(query: str, docs_with_scores: List[Tuple[Document, float]], llm_response: str, repo_id: str):
    """
    Logs RAG retrieval metrics to a local JSONL file for analysis.
    Every line is a valid JSON object, making it easy to parse in Pandas or Jupyter.
    """
    log_dir = "data/telemetry"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "rag_logs.jsonl")

    # Format the retrieved chunks for the log
    retrieved_data = []
    for doc, score in docs_with_scores:
        retrieved_data.append({
            "source": doc.metadata.get("source", "unknown"),
            "element_type": doc.metadata.get("element_type", "unknown"),
            "distance_score": float(score), # Chroma uses distance (lower is closer/better)
            "content_preview": doc.page_content[:250] + "..." # Truncated to keep logs manageable
        })

    # Build the final telemetry payload
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "repo_id": repo_id,
        "user_query": query,
        "retrieved_chunks": retrieved_data,
        "llm_response": llm_response
    }

    # Append to the JSONL file (creates the file if it doesn't exist)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")