# src_v2/rag/parent_store.py
import os
import json

DATA_DIR = os.getenv("DATA_DIR", "data")
VECTOR_DB_DIR = os.getenv("VECTOR_DB_DIR", os.path.join(DATA_DIR, "vector_db_v2"))


def _get_parent_dir(repo_id: str) -> str:
    safe_repo_id = repo_id.replace("/", "_")
    parent_dir = os.path.join(VECTOR_DB_DIR, safe_repo_id, "parents")
    os.makedirs(parent_dir, exist_ok=True)
    return parent_dir

def save_parent(repo_id: str, parent_id: str, element_type: str, content: str, alt_text: str = None) -> bool:
    """
    Saves a heavy element (table, tree, or base64 image) to the local disk.
    """
    parent_dir = _get_parent_dir(repo_id)
    file_path = os.path.join(parent_dir, f"{parent_id}.json")
    
    data = {
        "parent_id": parent_id,
        "element_type": element_type,
        "content": content,
        "alt_text": alt_text
    }
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"❌ Failed to save parent {parent_id}: {e}")
        return False

def get_parent(repo_id: str, parent_id: str) -> dict | None:
    """
    Retrieves a heavy element from the local disk using its ID.
    """
    parent_dir = _get_parent_dir(repo_id)
    file_path = os.path.join(parent_dir, f"{parent_id}.json")
    
    if not os.path.exists(file_path):
        return None
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load parent {parent_id}: {e}")
        return None