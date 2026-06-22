# src_v2/rag/ingestion.py
import os
import re
import uuid
import asyncio
from html import unescape
from urllib.parse import unquote, urlparse
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src_v2.mcp.github_client import GitHubClient
from src_v2.rag.vector_store import get_vector_store
from src_v2.rag.parent_store import save_parent

SUPPORTED_REPO_IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".ico",
    ".tif",
    ".tiff",
)

UNSUPPORTED_REPO_MEDIA_EXTENSIONS = (
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".pdf",
)


def _clean_image_reference(raw_path: str) -> str:
    """
    Clean Markdown/HTML image references without changing repo-relative meaning.
    Removes URL query/fragment decorations and HTML escaping.
    """
    cleaned = unescape(raw_path or "").strip()

    # Markdown also permits image paths like <docs/diagram.png>
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()

    cleaned = cleaned.split("#", 1)[0].split("?", 1)[0].strip()
    return cleaned


def _has_unresolved_template_tokens(path: str) -> bool:
    """
    Detect unresolved site-template paths that are not real repo assets, for example:
    {{ base_url }}static/img/logo.svg
    {{ person.avatar_url }}
    {% static 'img/logo.png' %}
    """
    return any(token in path for token in ("{{", "}}", "{%", "%}", "{#", "#}", "${"))


def _has_supported_image_extension(path: str) -> bool:
    return path.lower().endswith(SUPPORTED_REPO_IMAGE_EXTENSIONS)


def _has_unsupported_media_extension(path: str) -> bool:
    return path.lower().endswith(UNSUPPORTED_REPO_MEDIA_EXTENSIONS)


def _same_repo_github_image_path(url: str, repo_id: str) -> str | None:
    """
    Convert same-repository GitHub image URLs into repo-relative paths.

    Supported examples:
    - https://raw.githubusercontent.com/owner/repo/main/docs/flow.png
    - https://github.com/owner/repo/blob/main/docs/flow.png
    - https://github.com/owner/repo/raw/main/docs/flow.png

    External GitHub/CDN/user-content URLs return None.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [unquote(part) for part in parsed.path.split("/") if part]

    try:
        owner, repo = repo_id.split("/", 1)
    except ValueError:
        return None

    if host == "raw.githubusercontent.com":
        if len(parts) >= 4 and parts[0] == owner and parts[1] == repo:
            return "/".join(parts[3:])
        return None

    if host in {"github.com", "www.github.com"}:
        if (
            len(parts) >= 5
            and parts[0] == owner
            and parts[1] == repo
            and parts[2] in {"blob", "raw"}
        ):
            return "/".join(parts[4:])
        return None

    return None


def _resolve_repo_image_path(raw_path: str, current_file_path: str, repo_id: str) -> tuple[str | None, str]:
    """
    Resolve an image reference from Markdown/HTML to a repo-relative path.

    This preserves the original multimodal RAG design:
    valid repo-owned images are still downloaded, saved as parent image objects,
    linked with image_sniper documents, and attached to section metadata.

    This only filters out noisy/non-repo image references before download:
    external ads/CDNs/badges, videos/media, and unresolved template paths.
    """
    cleaned = _clean_image_reference(raw_path)

    if not cleaned:
        return None, "empty image reference"

    if _has_unresolved_template_tokens(cleaned):
        return None, "unresolved template path"

    if _has_unsupported_media_extension(cleaned):
        return None, "unsupported media file"

    parsed = urlparse(cleaned)
    already_repo_relative = False

    # External URL: only ingest if it is a same-repo GitHub raw/blob image.
    # The converted same_repo_path is already relative to the repository root.
    if parsed.scheme in {"http", "https"}:
        same_repo_path = _same_repo_github_image_path(cleaned, repo_id)
        if not same_repo_path:
            return None, "external image URL"
        cleaned = same_repo_path
        already_repo_relative = True

    # Other schemes like data:, mailto:, javascript:, etc. are not repo files.
    elif parsed.scheme:
        return None, f"unsupported URL scheme: {parsed.scheme}"

    if _has_unresolved_template_tokens(cleaned):
        return None, "unresolved template path"

    if not _has_supported_image_extension(cleaned):
        return None, "not a supported repo image extension"

    # Same-repo GitHub URLs and root-relative image paths are repo-root-relative.
    # Normal relative paths are resolved relative to the Markdown file.
    if already_repo_relative:
        resolved = os.path.normpath(cleaned).lstrip("./").lstrip("/")
    elif cleaned.startswith("/"):
        resolved = os.path.normpath(cleaned).lstrip("/")
    else:
        base_dir = os.path.dirname(current_file_path)
        resolved = os.path.normpath(os.path.join(base_dir, cleaned)).lstrip("./").lstrip("/")

    # Prevent paths that normalize outside the repository.
    if not resolved or resolved == "." or resolved.startswith("../") or "/../" in resolved:
        return None, "image path resolves outside repository"

    if not _has_supported_image_extension(resolved):
        return None, "not a supported repo image extension"

    return resolved, "repo image"



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
                    await chat_adapter.update_message(
                        channel_id=channel_id,
                        message_id=message_id,
                        text=progress_text,
                        thread_ts=thread_ts,
                    )
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
                    target_image_path, skip_reason = _resolve_repo_image_path(
                        raw_path=detected_img_path,
                        current_file_path=file_path,
                        repo_id=repo_id,
                    )

                    if not target_image_path:
                        print(
                            f"↪️ Skipping image reference in {file_path}: "
                            f"{detected_img_path} ({skip_reason})"
                        )
                        continue

                    try:
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