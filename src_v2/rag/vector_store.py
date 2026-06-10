# src/rag/vector_store.py
import os
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings # Updated from community to dedicated package

DATA_DIR = os.getenv("DATA_DIR", "data")
VECTOR_DB_DIR = os.getenv("VECTOR_DB_DIR", os.path.join(DATA_DIR, "vector_db_v2"))
OLLAMA_EMBEDDINGS_BASE_URL = os.getenv("OLLAMA_EMBEDDINGS_BASE_URL", "http://localhost:11434")

def _repo_dir(repo_id: str) -> str:
    return os.path.join(VECTOR_DB_DIR, repo_id.replace("/", "_"))

def get_vector_store(repo_id: str):
    persist_directory = _repo_dir(repo_id)
    os.makedirs(persist_directory, exist_ok=True)

    embeddings = OllamaEmbeddings(
        base_url=OLLAMA_EMBEDDINGS_BASE_URL,
        model=os.getenv("OLLAMA_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    )

    vector_db = Chroma(
        collection_name="repo_docs",
        embedding_function=embeddings,
        persist_directory=persist_directory
    )

    return vector_db

def check_if_repo_exists(repo_id: str) -> bool:
    persist_directory = _repo_dir(repo_id)
    return os.path.exists(persist_directory) and len(os.listdir(persist_directory)) > 0

def get_local_hash(repo_id: str) -> str | None:
    hash_path = os.path.join(_repo_dir(repo_id), "commit_hash.txt")
    if os.path.exists(hash_path):
        with open(hash_path, "r") as f:
            return f.read().strip()
    return None

def save_local_hash(repo_id: str, hash_val: str):
    persist_directory = _repo_dir(repo_id)
    os.makedirs(persist_directory, exist_ok=True)
    with open(os.path.join(persist_directory, "commit_hash.txt"), "w") as f:
        f.write(hash_val)