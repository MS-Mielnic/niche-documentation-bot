# src_v2/rag/ingestion.py
import os
import re
import uuid
import asyncio
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src_v2.mcp.github_client import GitHubClient
from src_v2.rag.vector_store import get_vector_store
from src_v2.rag.parent_store import save_parent  # Adjusted to match your structural path name

async def ingest_repository(repo_id: str, chat_adapter=None, channel_id=None):
    client = GitHubClient()
    vector_db = get_vector_store(repo_id)
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, 
        chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""]
    )
    
    files = await client.list_directory_contents(repo_id) 
    
    for file_info in files:
        file_path = file_info["path"]
        if not file_path.endswith(('.md', '.txt')): continue

        try:
            content = await client.read_single_file(repo_id, file_path)
            docs = []

            # --- PHASE 1: PROCESS HEAVY GLOBAL OBJECTS (TABLES) ---
            # Tables span multiple lines explicitly and are best segmented first
            table_pattern = r'((?:\|[^\n]*\|(?:\n|$))+)'
            tables = re.findall(table_pattern, content)
            for raw_table in tables:
                if not raw_table.strip(): continue
                table_id = f"tbl_{uuid.uuid4().hex[:8]}"
                save_parent(repo_id, table_id, "table", raw_table)
                
                rows = raw_table.strip().split('\n')
                for row in rows:
                    if set(row.strip()) == {'|', '-'}: continue
                    docs.append(Document(
                        page_content=f"Table Data Row context: {row.strip()}",
                        metadata={"source": file_path, "element_type": "table", "parent_id": table_id, "repo": repo_id}
                    ))
            content = re.sub(table_pattern, '', content)

            # --- PHASE 2: STATEFUL LINE-BY-LINE PARSING (TREES, IMAGES, TEXT) ---
            lines = content.split('\n')
            
            current_section_title = "Root Document"
            current_section_accumulator = []
            
            # Context state blocks for the active topic segment
            active_image_id = None
            active_image_path = None
            active_tree_id = None
            
            # Temporal variables to extract multiline directory structures
            in_tree_block = False
            tree_accumulator = []

            for line in lines:
                # 1. Catch Header Block Changes -> Reset Stateful Anchors
                if line.startswith('## ') or line.startswith('### '):
                    # Flush the text compiled in the previous section before transitioning
                    if current_section_accumulator:
                        section_text = "\n".join(current_section_accumulator)
                        chunks = text_splitter.split_text(section_text)
                        for chunk in chunks:
                            meta = {"source": file_path, "element_type": "text", "repo": repo_id, "section": current_section_title}
                            if active_image_id:
                                meta["parent_id"] = active_image_id
                                meta["image_path"] = active_image_path
                            if active_tree_id:
                                meta["tree_parent_id"] = active_tree_id
                            docs.append(Document(page_content=chunk, metadata=meta))
                    
                    # Reset section trackers
                    current_section_title = line.strip()
                    current_section_accumulator = [line]
                    active_image_id = None
                    active_image_path = None
                    active_tree_id = None
                    continue

                # 2. Stateful ASCII Tree Block Parser
                is_tree_line = any(char in line for char in ["├──", "└──", "│"]) and not line.strip().startswith('-')
                if is_tree_line:
                    in_tree_block = True
                    tree_accumulator.append(line)
                    
                    # Generate line searchable reference items instantly
                    clean_line = line.replace('├──', '').replace('└──', '').replace('│', '').strip()
                    if clean_line:
                        docs.append(Document(
                            page_content=f"Directory layout path index: {clean_line} under {current_section_title}",
                            metadata={"source": file_path, "element_type": "tree", "repo": repo_id}
                        ))
                    continue
                elif in_tree_block and not is_tree_line:
                    # Tree finished segment loop -> process accumulated tree block
                    if tree_accumulator:
                        raw_tree_block = "\n".join(tree_accumulator)
                        active_tree_id = f"tre_{uuid.uuid4().hex[:8]}"
                        save_parent(repo_id, active_tree_id, "tree", raw_tree_block)
                        print(f"🌲 Ingested inline section file tree structural object: {active_tree_id}")
                    tree_accumulator = []
                    in_tree_block = False

                # 3. Stateful Image Asset Extractor (Markdown & HTML)
                md_img_match = re.search(r'!\[(.*?)\]\((.*?)\)', line)
                html_img_match = re.search(r'<img\s+[^>]*src=["\'](.*?)["\'][^>]*>', line)
                
                detected_img_path = None
                if md_img_match:
                    detected_img_path = md_img_match.group(2).strip()
                elif html_img_match:
                    detected_img_path = html_img_match.group(1).strip()

                if detected_img_path:
                    try:
                        base_dir = os.path.dirname(file_path)
                        target_image_path = os.path.normpath(os.path.join(base_dir, detected_img_path)).lstrip('./').lstrip('/')
                        
                        # 🎯 FIX: Call your dedicated GitHub base64 downloader directly!
                        # This skips read_single_file text stream formatting and returns a clean base64 data URI string.
                        img_b64_uri = await client.download_image_as_base64(repo_id, target_image_path)
                        
                        if img_b64_uri:
                            active_image_id = f"img_{uuid.uuid4().hex[:8]}"
                            active_image_path = target_image_path
                            
                            # Save the clean base64 string to the parent store
                            save_parent(repo_id, active_image_id, "image", img_b64_uri)
                            print(f"🖼️ Ingested inline image: {active_image_path} linked to {current_section_title}")
                    except Exception as img_err:
                        print(f"⚠️ Image parsing bypassed: {img_err}")
                    continue # Skip adding raw image text line to text pool

                # 4. Standard Line Accumulator
                if line.strip():
                    current_section_accumulator.append(line)

            # Final Cleanup Flush for the last section block of the file
            if current_section_accumulator:
                section_text = "\n".join(current_section_accumulator)
                chunks = text_splitter.split_text(section_text)
                for chunk in chunks:
                    meta = {"source": file_path, "element_type": "text", "repo": repo_id, "section": current_section_title}
                    if active_image_id:
                        meta["parent_id"] = active_image_id
                        meta["image_path"] = active_image_path
                    if active_tree_id:
                        meta["tree_parent_id"] = active_tree_id
                    docs.append(Document(page_content=chunk, metadata=meta))

            # --- PHASE 3: BATCH VECTOR COMMITS ---
            if docs:
                for i in range(0, len(docs), 10):
                    vector_db.add_documents(docs[i:i + 10])
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"❌ Failed processing file {file_path}: {e}")
            continue

    return True