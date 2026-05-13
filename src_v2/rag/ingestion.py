# src/rag/ingestion.py
import io
import asyncio
from unstructured.partition.md import partition_md
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.mcp.github_client import GitHubClient
from src.rag.vector_store import get_vector_store

async def ingest_repository(repo_id: str, chat_adapter=None, channel_id=None):
    client = GitHubClient()
    vector_db = get_vector_store(repo_id)
    
    # Safety Splitter: Strict chunking to protect Ollama's context window
    fallback_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
    
    files = await client.list_directory_contents(repo_id) 
    
    for file_info in files:
        file_path = file_info["path"]
        if not file_path.endswith(('.md', '.txt')): continue

        try:
            content = await client.read_single_file(repo_id, file_path)
            file_like_object = io.BytesIO(content.encode('utf-8'))
            elements = partition_md(file=file_like_object)
            
            docs = []
            for element in elements:
                text_content = str(element).strip()
                if not text_content: continue
                
                # Flat, primitive metadata to prevent serialization crashes
                metadata = {
                    "source": str(file_path), 
                    "element_type": str(element.category), 
                    "repo": str(repo_id)
                }
                
                # Overflow check applied explicitly
                if len(text_content) > 2000:
                    for chunk in fallback_splitter.split_text(text_content):
                        docs.append(Document(page_content=chunk, metadata=metadata))
                else:
                    docs.append(Document(page_content=text_content, metadata=metadata))
            
            if docs:
                # Batching to protect local memory
                for i in range(0, len(docs), 10):
                    vector_db.add_documents(docs[i:i + 10])
            
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"❌ Skipping {file_path} due to error: {e}")
            continue

    return True