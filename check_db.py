# check_db.py
# check_db.py
import asyncio
from src_v2.mcp.github_client import GitHubClient

async def check_github_binary_stream():
    client = GitHubClient()
    repo = "google-gemini/gemini-fullstack-langgraph-quickstart"
    path = "agent.png"
    
    print("📡 TEST DOWNLOADING AGENT.PNG VIA GITHUB CLIENT:")
    try:
        raw_data = await client.read_single_file(repo, path)
        print(f"Returned Data Type: {type(raw_data)}")
        
        if isinstance(raw_data, str):
            print(f"Data is a String representation. Length: {len(raw_data)}")
            # Print the first 10 characters as an array of numerical byte integers
            try:
                print(f"First characters bytes representation: {[ord(c) for c in raw_data[:15]]}")
            except:
                pass
        elif isinstance(raw_data, bytes):
            print(f"Data is true Raw Bytes stream. Length: {len(raw_data)}")
            print(f"First 15 bytes: {list(raw_data[:15])}")
    except Exception as err:
        print(f"❌ Client download failed: {err}")

asyncio.run(check_github_binary_stream())