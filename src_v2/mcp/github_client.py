# src/mcp/github_client.py
import base64
import mimetypes
import os
import httpx
from typing import List, Dict, Optional

class GitHubClient:
    """
    A lightweight, async wrapper for the GitHub REST API.
    Handles searching for repositories and fetching commit hashes for the JIT Sync.
    """
    def __init__(self):
        # We will load the token from your .env file
        self.token = os.getenv("GITHUB_TOKEN")
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        
        # If a token exists, add it to the headers to bypass strict rate limits
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
            
        self.base_url = "https://api.github.com"

    async def search_repositories(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """
        Searches GitHub for repositories matching the user's query.
        Returns a list of dictionaries with repo 'full_name' and 'url'.
        """
        print(f"--- MCP: SEARCHING GITHUB FOR '{query}' ---")
        url = f"{self.base_url}/search/repositories"
        params = {"q": query, "per_page": limit}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params)
            
            # Catch errors (like rate limits)
            response.raise_for_status()
            data = response.json()
            
            repos = []
            for item in data.get("items", []):
                repos.append({
                    "full_name": item["full_name"], # e.g., "langchain-ai/langgraph"
                    "description": item["description"],
                    "url": item["html_url"]
                })
            return repos

    async def get_latest_commit_hash(self, full_name: str, branch: str = "main") -> Optional[str]:
        """
        Fetches the latest commit SHA for a given repository and branch.
        Critical for the ~100ms 'Lazy Load' Hash Check feature.
        """
        print(f"--- MCP: FETCHING LATEST HASH FOR '{full_name}' ---")
        url = f"{self.base_url}/repos/{full_name}/commits/{branch}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("sha")
            else:
                print(f"--- WARNING: Could not fetch commit hash for {full_name}. Status: {response.status_code} ---")
                return None
            
    async def list_directory_contents(self, repo_id: str):
        """
        Fetches the complete file tree of a repository.
        Used by Node 5 to identify all Markdown and text files for ingestion.
        """
        print(f"--- MCP: LISTING ALL FILES FOR '{repo_id}' ---")
        # We use the 'recursive=1' parameter to flatten the entire tree in one call
        url = f"https://api.github.com/repos/{repo_id}/git/trees/main?recursive=1"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            if response.status_code == 200:
                tree = response.json().get("tree", [])
                # Return only files (blobs), ignoring directory entries
                return [item for item in tree if item["type"] == "blob"]
            else:
                # Handle cases where the default branch might be 'master' instead of 'main'
                url = url.replace("/main", "/master")
                response = await client.get(url, headers=self.headers)
                tree = response.json().get("tree", [])
                return [item for item in tree if item["type"] == "blob"]
            
    async def read_single_file(self, repo_id: str, file_path: str):
        """
        Reads a single file from the target repository via raw GitHub content URLs.
        Bypasses string encoders for binary files to keep data streams pristine.
        """
        is_binary = file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf'))
        
        # Construct the raw download endpoint URL
        url = f"https://raw.githubusercontent.com/{repo_id}/main/{file_path}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            
            # Fallback check if the main branch is configured as 'master'
            if response.status_code == 404:
                url = url.replace("/main/", "/master/")
                response = await client.get(url, headers=self.headers)
                
            if response.status_code == 200:
                if is_binary:
                    # 🎯 Return raw bytes directly to protect multimedia hex values
                    return response.content  
                else:
                    # Return clear text strings for text assets
                    return response.text
            else:
                raise httpx.HTTPStatusError(
                    f"Failed to fetch file {file_path}. Status: {response.status_code}",
                    request=response.request,
                    response=response
                )
        
    async def download_image_as_base64(self, repo_id: str, file_path: str) -> str | None:
        """
        Downloads an image asset file from the GitHub repository 
        and transforms it into a clean Base64 Data URI string.
        """
        print(f"--- FETCHING IMAGE FOR MULTIMODAL CONVERSION: {file_path} ---")
        
        url = f"https://api.github.com/repos/{repo_id}/contents/{file_path}"
        headers = self.headers.copy() 
        headers["Accept"] = "application/vnd.github.v3.raw"

        try:
            # FIX: Swap out the undefined 'self.session' for your class standard 'httpx' client pattern
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                
                if response.status_code == 200:
                    image_bytes = response.content
                    
                    # Transform raw visual binary arrays into clean Base64 strings
                    base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
                    
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if not mime_type:
                        mime_type = "image/png" 
                        
                    data_uri = f"data:{mime_type};base64,{base64_encoded}"
                    print(f"✅ Successfully downloaded and encoded asset: {file_path}")
                    return data_uri
                else:
                    print(f"❌ Failed to download asset {file_path}. Status: {response.status_code}")
                    return None
                    
        except Exception as e:
            print(f"❌ Error during image base64 processing for {file_path}: {e}")
            return None