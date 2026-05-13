# test_github.py
import asyncio
from dotenv import load_dotenv
from src.mcp.github_client import GitHubClient

# 1. Load the .env file so the client can find your GitHub token
load_dotenv()

async def run_test():
    client = GitHubClient()
    
    print("\n--- 1. TESTING REPO SEARCH ---")
    repos = await client.search_repositories(query="langgraph", limit=2)
    for repo in repos:
        print(f"Found: {repo['full_name']} -> {repo['url']}")
        
    print("\n--- 2. TESTING COMMIT HASH (JIT SYNC) ---")
    # Let's test the hash function on one of the repos we just found
    if repos:
        test_repo = repos[0]['full_name']
        commit_hash = await client.get_latest_commit_hash(full_name=test_repo)
        print(f"Latest commit for {test_repo}: {commit_hash}\n")

if __name__ == "__main__":
    asyncio.run(run_test())