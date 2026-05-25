# src_v2/rag/ingestion.py
import os
import re
import uuid
import asyncio
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src_v2.mcp.github_client import GitHubClient
from src_v2.rag.vector_store import get_vector_store
from src_v2.rag.parent_store import save_parent


async def ingest_repository(repo_id: str, chat_adapter=None, channel_id=None, thread_ts=None, message_id=None):
    print(f"--- DEBUG: Ingesting into channel={channel_id}, thread={thread_ts} ---")
    client = GitHubClient()
    vector_db = get_vector_store(repo_id)
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, 
        chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""]
    )
    
    files = await client.list_directory_contents(repo_id) 

    # Pre-filter valid files to get an accurate total count for our progress updates
    valid_files = [f for f in files if f["path"].endswith(('.md', '.txt'))]
    total_files = len(valid_files)
    processed_files = 0
    
    # Calculate throttling interval
    if total_files > 0:
        update_interval = max(1, total_files // 5)
    else:
        update_interval = 1

    for file_info in valid_files:
        file_path = file_info["path"]
        processed_files += 1

        # --- PROGRESS UPDATE LOGIC ---
        if chat_adapter and channel_id and message_id:
            if processed_files % update_interval == 0 or processed_files == total_files:
                percentage = int((processed_files / total_files) * 100)
                progress_text = f"⏳ Syncing `{repo_id}`: *{percentage}%* ({processed_files}/{total_files} files)\n_Currently reading: `{file_path}`_"
                
                try:
                    await chat_adapter.update_message(channel_id, thread_ts,message_id, progress_text)
                except Exception as e:
                    print(f"⚠️ Non-fatal: Failed to update Slack progress message: {e}")

        try:
            content = await client.read_single_file(repo_id, file_path)
            docs = []

            # --- PHASE 1: PROCESS HEAVY GLOBAL OBJECTS (TABLES) ---
            table_pattern = r'((?:\|[^\n]*\|(?:\n|$))+)'
            tables = re.findall(table_pattern, content)
            active_table_ids = []

            for raw_table in tables:
                if not raw_table.strip(): continue
                table_id = f"tbl_{uuid.uuid4().hex[:8]}"
                
                # 1. Save pristine table to KV Store
                save_parent(repo_id, table_id, "table", raw_table)
                active_table_ids.append(table_id)
                
                # 2. Create the Dense "Sniper Document" for Chroma
                docs.append(Document(
                    page_content=f"Data Table Context:\n{raw_table.strip()}",
                    metadata={
                        "source": file_path, 
                        "element_type": "table_sniper", 
                        "parent_id": table_id, 
                        "repo": repo_id
                    }
                ))
        
                # 3. INLINE TOKEN REPLACEMENT
                anchor_token = f"\n[[RAG_TABLE_ANCHOR: {table_id}]]\n"
                content = content.replace(raw_table, anchor_token)
                
            # --- PHASE 2: STATEFUL LINE-BY-LINE PARSING (TREES, IMAGES, TEXT) ---
            lines = content.split('\n')
            
            current_section_title = "Root Document"
            current_section_accumulator = []
            
            active_image_id = None
            active_image_path = None
            active_tree_id = None
            
            in_tree_block = False
            tree_accumulator = []

            current_section_table_ids = []

            # Abstracted Helper Function for DRY Section Flushing
            def _flush_section_to_docs():
                if current_section_accumulator:
                    section_text = "\n".join(current_section_accumulator)
                    chunks = text_splitter.split_text(section_text)
                    
                    for chunk in chunks:
                        meta = {
                            "source": file_path, 
                            "element_type": "text", 
                            "repo": repo_id, 
                            "section": current_section_title
                        }
                        
                        if active_image_id:
                            meta["active_image_id"] = active_image_id
                            meta["image_path"] = active_image_path
                        if active_tree_id:
                            meta["tree_parent_id"] = active_tree_id
                        if current_section_table_ids:
                            meta["section_table_ids"] = ",".join(current_section_table_ids)
                            
                        docs.append(Document(page_content=chunk, metadata=meta))

            for line in lines:
                
                # 0. DYNAMIC TABLE CATCHER
                table_match = re.search(r'\[\[RAG_TABLE_ANCHOR: (tbl_[a-f0-9]+)\]\]', line)
                if table_match:
                    current_section_table_ids.append(table_match.group(1))

                # 1. Catch Header Block Changes -> Reset Stateful Anchors
                if line.startswith('## ') or line.startswith('### '):
                    # Call DRY flush helper
                    _flush_section_to_docs()
                    
                    # Reset section trackers
                    current_section_title = line.strip()
                    current_section_accumulator = [line]
                    active_image_id = None
                    active_image_path = None
                    active_tree_id = None
                    current_section_table_ids = [] 
                    continue

                # 2. Stateful ASCII Tree Block Parser
                is_tree_line = any(char in line for char in ["├──", "└──", "│"]) and not line.strip().startswith('-')
                if is_tree_line:
                    in_tree_block = True
                    tree_accumulator.append(line)
                    
                    clean_line = line.replace('├──', '').replace('└──', '').replace('│', '').strip()
                    if clean_line:
                        docs.append(Document(
                            page_content=f"Directory layout path index: {clean_line} under {current_section_title}",
                            metadata={"source": file_path, "element_type": "tree", "repo": repo_id, "parent_id": active_tree_id}
                        ))
                    continue
                elif in_tree_block and not is_tree_line:
                    if tree_accumulator:
                        raw_tree_block = "\n".join(tree_accumulator)
                        active_tree_id = f"tre_{uuid.uuid4().hex[:8]}"
                        
                        save_parent(repo_id, active_tree_id, "tree", raw_tree_block)
                        print(f"🌲 Ingested inline section file tree structural object: {active_tree_id}")
                        
                        docs.append(Document(
                            page_content=f"Directory Tree Context for section {current_section_title}:\n{raw_tree_block}",
                            metadata={
                                "source": file_path, 
                                "element_type": "tree_sniper", 
                                "parent_id": active_tree_id, 
                                "repo": repo_id
                            }
                        ))
                        
                        anchor_token = f"\n[[RAG_TREE_ANCHOR: {active_tree_id}]]\n"
                        current_section_accumulator.append(anchor_token)
                        
                    tree_accumulator = []
                    in_tree_block = False

                # 3. Stateful Image Asset Extractor (Markdown & HTML)
                md_img_match = re.search(r'!\[(.*?)\]\((.*?)\)', line)
                html_img_match = re.search(r'<img\s+[^>]*src=["\'](.*?)["\'][^>]*>', line)
                
                detected_img_path = None
                alt_text = "Diagram"
                
                if md_img_match:
                    alt_text = md_img_match.group(1).strip()
                    detected_img_path = md_img_match.group(2).strip()
                elif html_img_match:
                    detected_img_path = html_img_match.group(1).strip()

                if detected_img_path:
                    if detected_img_path.startswith(('http://', 'https://', 'data:image')):
                        continue 

                    try:
                        base_dir = os.path.dirname(file_path)
                        target_image_path = os.path.normpath(os.path.join(base_dir, detected_img_path)).lstrip('./').lstrip('/')
                        
                        img_b64_uri = await client.download_image_as_base64(repo_id, target_image_path)
                        
                        if img_b64_uri:
                            active_image_id = f"img_{uuid.uuid4().hex[:8]}"
                            active_image_path = target_image_path
                            
                            save_parent(repo_id, active_image_id, "image", img_b64_uri, alt_text)
                            print(f"🖼️ Ingested inline image: {active_image_path} linked to {current_section_title}")
                            
                            docs.append(Document(
                                page_content=f"Image Context: {active_image_path} showing '{alt_text}' under section {current_section_title}",
                                metadata={
                                    "source": file_path, 
                                    "element_type": "image_sniper", 
                                    "parent_id": active_image_id, 
                                    "image_path": active_image_path,
                                    "repo": repo_id
                                }
                            ))
                            
                            anchor_token = f"\n[[RAG_IMAGE_ANCHOR: {active_image_id}]]\n"
                            current_section_accumulator.append(anchor_token)
                            
                    except Exception as img_err:
                        print(f"⚠️ Image parsing bypassed: {img_err}")
                    continue

                # 4. Standard Line Accumulator
                if line.strip():
                    current_section_accumulator.append(line)

            # Final Cleanup Flush for the last section of the file
            _flush_section_to_docs()

            # --- PHASE 3: BATCH VECTOR COMMITS ---
            if docs:
                for i in range(0, len(docs), 10):
                    vector_db.add_documents(docs[i:i + 10])
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"❌ Failed processing file {file_path}: {e}")
            continue

    return True