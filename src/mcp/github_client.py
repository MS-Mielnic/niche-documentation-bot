# src/mcp/github_client.py
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
        Downloads the raw content of a specific file from GitHub.
        """
        url = f"https://raw.githubusercontent.com/{repo_id}/main/{file_path}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            if response.status_code != 200:
                # Fallback for 'master' branches
                url = url.replace("/main/", "/master/")
                response = await client.get(url)
            return response.text