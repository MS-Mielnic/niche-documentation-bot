#src_v2/observability/telemetry.py
import json
import os
from datetime import datetime
from typing import List, Tuple, Optional
from langchain_core.documents import Document
from opentelemetry import trace



#This tracer will be imported by your LangGraph nodes to create manual spans.
tracer = trace.get_tracer("nichedocbot.tracer")

def add_retrieved_document_events(
    span,
    docs_with_scores: List[Tuple[Document, float]],
) -> None:
    """
    Add one OpenTelemetry span event per retrieved document.

    This provides per-document retrieval visibility without storing full
    document content in traces.
    """
    if not span or not docs_with_scores:
        return

    for index, (doc, score) in enumerate(docs_with_scores):
        metadata = doc.metadata or {}
        content = doc.page_content or ""

        span.add_event(
            "retrieved_document",
            {
                "rag.document_index": index,
                "rag.source": metadata.get("source", "unknown"),
                "rag.element_type": metadata.get("element_type", "unknown"),
                "rag.distance_score": float(score),
                "rag.content_preview_length": len(content[:250]),
            },
        )

def log_rag_retrieval(query: str, docs_with_scores: List[Tuple[Document, float]], llm_response: str, repo_id: str):
    """
    Logs RAG retrieval metrics to a local JSONL file for analysis.
    Every line is a valid JSON object, making it easy to parse in Pandas or Jupyter.
    """
    data_dir = os.getenv("DATA_DIR", "data")
    log_dir = os.path.join(data_dir, "telemetry")
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