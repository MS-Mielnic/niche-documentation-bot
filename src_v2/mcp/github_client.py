# src_v2/mcp/github_client.py
import base64
import mimetypes
import os
from typing import Dict, List, Optional

import httpx


class GitHubClient:
    """
    Lightweight async wrapper for the GitHub REST API.

    Responsibilities:
    - Search repositories.
    - Discover the repository default branch.
    - Fetch latest commit hash for JIT sync.
    - List repository files.
    - Read text/binary assets.
    - Convert image assets to base64 data URIs for multimodal RAG.
    """

    def __init__(self):
        # Keep compatibility with both names, because your Kubernetes secret/env
        # has used GITHUB_PAT while older code used GITHUB_TOKEN.
        self.token = os.getenv("GITHUB_PAT") or os.getenv("GITHUB_TOKEN")

        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "nichedocbot",
        }

        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

        self.base_url = "https://api.github.com"

        # Small in-memory cache so repeated calls during one ingestion do not
        # refetch repo metadata every time.
        self._default_branch_cache: Dict[str, str] = {}

    async def search_repositories(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """
        Searches GitHub for repositories matching the user's query.
        Returns dictionaries with repo full_name, description, and url.
        """
        print(f"--- MCP: SEARCHING GITHUB FOR '{query}' ---")

        url = f"{self.base_url}/search/repositories"
        params = {"q": query, "per_page": limit}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()

        repos = []
        for item in data.get("items", []):
            repos.append(
                {
                    "full_name": item["full_name"],
                    "description": item.get("description"),
                    "url": item["html_url"],
                    "default_branch": item.get("default_branch"),
                }
            )

        return repos

    async def get_default_branch(self, repo_id: str) -> str:
        """
        Returns the repository's actual default branch.

        This avoids hardcoding 'main' or 'master', which breaks ingestion for
        repositories using a different default branch.
        """
        if repo_id in self._default_branch_cache:
            return self._default_branch_cache[repo_id]

        print(f"--- MCP: FETCHING DEFAULT BRANCH FOR '{repo_id}' ---")

        url = f"{self.base_url}/repos/{repo_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers)

        if response.status_code != 200:
            print(
                f"--- WARNING: Could not fetch repo metadata for {repo_id}. "
                f"Status: {response.status_code}. Falling back to 'main'. ---"
            )
            self._default_branch_cache[repo_id] = "main"
            return "main"

        data = response.json()
        default_branch = data.get("default_branch") or "main"

        self._default_branch_cache[repo_id] = default_branch
        return default_branch

    async def get_latest_commit_hash(self, full_name: str, branch: Optional[str] = None) -> Optional[str]:
        """
        Fetches the latest commit SHA for the repository's default branch.

        Used by the JIT sync check before deciding whether ingestion is needed.
        """
        target_branch = branch or await self.get_default_branch(full_name)

        print(f"--- MCP: FETCHING LATEST HASH FOR '{full_name}' ON BRANCH '{target_branch}' ---")

        url = f"{self.base_url}/repos/{full_name}/commits/{target_branch}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers)

        if response.status_code == 200:
            data = response.json()
            return data.get("sha")

        print(
            f"--- WARNING: Could not fetch commit hash for {full_name} "
            f"on branch {target_branch}. Status: {response.status_code} ---"
        )
        return None

    async def list_directory_contents(self, repo_id: str) -> List[Dict]:
        """
        Fetches the complete file tree of a repository using its default branch.
        Used by Node 5 to identify Markdown/text files for ingestion.
        """
        branch = await self.get_default_branch(repo_id)

        print(f"--- MCP: LISTING ALL FILES FOR '{repo_id}' ON BRANCH '{branch}' ---")

        url = f"{self.base_url}/repos/{repo_id}/git/trees/{branch}"
        params = {"recursive": "1"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=self.headers, params=params)

        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Failed to list repository tree for {repo_id} on branch {branch}. "
                f"Status: {response.status_code}",
                request=response.request,
                response=response,
            )

        tree = response.json().get("tree", [])
        return [item for item in tree if item.get("type") == "blob"]

    async def read_single_file(self, repo_id: str, file_path: str):
        """
        Reads a single file from the target repository using the default branch.
        Returns bytes for binary files and text for text files.
        """
        branch = await self.get_default_branch(repo_id)

        is_binary = file_path.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf")
        )

        url = f"https://raw.githubusercontent.com/{repo_id}/{branch}/{file_path}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=self.headers)

        if response.status_code == 200:
            if is_binary:
                return response.content
            return response.text

        raise httpx.HTTPStatusError(
            f"Failed to fetch file {file_path} from {repo_id} on branch {branch}. "
            f"Status: {response.status_code}",
            request=response.request,
            response=response,
        )

    async def download_image_as_base64(self, repo_id: str, file_path: str) -> str | None:
        """
        Downloads an image asset from the repository and converts it into a
        base64 data URI for multimodal RAG.
        """
        branch = await self.get_default_branch(repo_id)

        print(
            f"--- FETCHING IMAGE FOR MULTIMODAL CONVERSION: "
            f"{file_path} FROM {repo_id}@{branch} ---"
        )

        url = f"{self.base_url}/repos/{repo_id}/contents/{file_path}"
        headers = self.headers.copy()
        headers["Accept"] = "application/vnd.github.v3.raw"

        params = {"ref": branch}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, headers=headers, params=params)

            if response.status_code != 200:
                print(
                    f"❌ Failed to download asset {file_path} from {repo_id}@{branch}. "
                    f"Status: {response.status_code}"
                )
                return None

            image_bytes = response.content
            base64_encoded = base64.b64encode(image_bytes).decode("utf-8")

            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "image/png"

            data_uri = f"data:{mime_type};base64,{base64_encoded}"
            print(f"✅ Successfully downloaded and encoded asset: {file_path}")
            return data_uri

        except Exception as e:
            print(f"❌ Error during image base64 processing for {file_path}: {e}")
            return None