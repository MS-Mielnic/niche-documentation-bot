# src/main.py
import os
import json
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Form
import uvicorn
from dotenv import load_dotenv

# Import Command to resume LangGraph from an interrupt
from langgraph.types import Command 
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

load_dotenv()

from src.adapters.slack import SlackAdapter
from src.graph.builder import build_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
slack_adapter = SlackAdapter()

async def process_slack_message(event: dict):
    """Handles standard text messages"""
    if "user" not in event or "text" not in event:
        return
        
    user_id = event["user"]
    text = event["text"]
    channel = event["channel"]
    
    if "bot_id" in event:
        return

    logger.info(f"Processing message from {user_id} in {channel}: '{text}'")

    initial_state = {
        "user_request": text,
        "thread_id": channel
    }

    config = {
        "configurable": {
            "thread_id": channel, 
            "chat_adapter": slack_adapter
        }
    }

    os.makedirs("data", exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite") as memory:
        agent_app = build_graph(memory)
        logger.info("--- WAKING UP LANGGRAPH (NEW MESSAGE) ---")
        try:
            await agent_app.ainvoke(initial_state, config=config)
            logger.info("--- GRAPH EXECUTION PAUSED OR COMPLETED ---")
        except Exception as e:
            logger.error(f"Error executing graph: {e}")

async def process_button_click(payload_json: str):
    """Handles interactive button clicks to resume the graph"""
    # Slack sends the payload as a JSON string, so we must parse it
    data = json.loads(payload_json)
    
    channel = data["channel"]["id"]
    user_id = data["user"]["id"]
    
    # Extract the specific repo the user clicked
    actions = data.get("actions", [])
    if not actions:
        return
        
    selected_repo = actions[0].get("value")
    logger.info(f"--- HUMAN SELECTED REPO: {selected_repo} ---")

    config = {
        "configurable": {
            "thread_id": channel, 
            "chat_adapter": slack_adapter
        }
    }

    # Open the database and resume the exact thread
    async with AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite") as memory:
        agent_app = build_graph(memory)
        
        logger.info(f"--- RESUMING LANGGRAPH FOR THREAD {channel} ---")
        try:
            # Command(resume=value) officially answers the interrupt() in Node 4
            await agent_app.ainvoke(Command(resume=selected_repo), config=config)
            logger.info("--- GRAPH EXECUTION COMPLETED (NODE 5 FINISHED) ---")
        except Exception as e:
            logger.error(f"Error resuming graph: {e}")

@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Webhook for text messages"""
    payload = await request.json()
    
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
        
    if payload.get("event", {}).get("type") == "message":
        event = payload["event"]
        background_tasks.add_task(process_slack_message, event)
        return {"status": "ok"}
        
    return {"status": "unhandled_event_type"}

@app.post("/slack/interactions")
async def slack_interactions(background_tasks: BackgroundTasks, payload: str = Form(...)):
    """Webhook for interactive Block Kit button clicks"""
    # Slack sends interaction payloads as Form data, not pure JSON
    background_tasks.add_task(process_button_click, payload)
    
    # Instantly return 200 OK so the Slack UI knows the click was received
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)