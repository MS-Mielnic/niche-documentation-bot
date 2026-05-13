# src/rag/vector_store.py
import os
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings # Updated from community to dedicated package

def get_vector_store(repo_id: str):
    persist_directory = f"data/vector_db/{repo_id.replace('/', '_')}"
    os.makedirs(persist_directory, exist_ok=True)

    embeddings = OllamaEmbeddings(
        base_url="http://localhost:11434",
        model="nomic-embed-text:latest" 
    )

    vector_db = Chroma(
        collection_name="repo_docs",
        embedding_function=embeddings,
        persist_directory=persist_directory
    )
    
    return vector_db

def check_if_repo_exists(repo_id: str) -> bool:
    persist_directory = f"data/vector_db/{repo_id.replace('/', '_')}"
    return os.path.exists(persist_directory) and len(os.listdir(persist_directory)) > 0

def get_local_hash(repo_id: str) -> str | None:
    """Reads the last ingested commit hash for the JIT Sync."""
    hash_path = f"data/vector_db/{repo_id.replace('/', '_')}/commit_hash.txt"
    if os.path.exists(hash_path):
        with open(hash_path, "r") as f:
            return f.read().strip()
    return None

def save_local_hash(repo_id: str, hash_val: str):
    """Saves the latest commit hash after a successful ingestion."""
    persist_directory = f"data/vector_db/{repo_id.replace('/', '_')}"
    os.makedirs(persist_directory, exist_ok=True)
    with open(f"{persist_directory}/commit_hash.txt", "w") as f:
        f.write(hash_val)