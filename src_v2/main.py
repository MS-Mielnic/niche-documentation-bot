# src_v2/main.py
import os
import traceback
import json
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Form
import uvicorn
from dotenv import load_dotenv

# Import Command to resume LangGraph from an interrupt
from langgraph.types import Command 
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

load_dotenv()

from src_v2.adapters.slack import SlackAdapter
from src_v2.graph.builder import build_graph

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
    channel = event["channel"]  # This is the raw Slack Channel ID
    
    if "bot_id" in event:
        return

    # Extract timestamps
    message_ts = event.get("ts")
    thread_ts = event.get("thread_ts", message_ts)
    langgraph_thread_id = f"{channel}_{thread_ts}"

    logger.info(f"Processing message from {user_id} in {channel} (Thread: {langgraph_thread_id}): '{text}'")

    # 🎯 THIS INITIAL STATE NOW MATCHES THE NEW AGENTSTATE SCHEMA
    initial_state = {
        "user_request": text,
        "thread_id": langgraph_thread_id,
        "channel_id": channel,   # Raw ID for Slack API
        "thread_ts": thread_ts    # Used for threading replies
    }

    config = {
        "configurable": {
            "thread_id": langgraph_thread_id, 
            "chat_adapter": slack_adapter
        }
    }

    os.makedirs("data", exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite") as memory:
        agent_app = build_graph(memory)
        print(f"--- WAKING UP LANGGRAPH (THREAD: {langgraph_thread_id}) ---")
        try:
            # When you invoke, LangGraph will validate this dictionary against your TypedDict
            await agent_app.ainvoke(initial_state, config=config)
            logger.info("--- GRAPH EXECUTION COMPLETED ---")
        except Exception as e:
            logger.error(f"Error executing graph: {e}")
            traceback.print_exc()


async def process_button_click(payload_str: str):
    """Handles interactive Block Kit button clicks"""
    try:
        payload_dict = json.loads(payload_str)
        
        actions = payload_dict.get("actions", [])
        if not actions:
            return
            
        selected_repo = actions[0].get("value")
        
        # 🎯 NEW THREAD LOGIC: Reconstruct the LangGraph thread ID from the button's context
        container = payload_dict.get("container", {})
        channel = container.get("channel_id")
        message_ts = container.get("message_ts")
        thread_ts = container.get("thread_ts", message_ts)
        langgraph_thread_id = f"{channel}_{thread_ts}"

        # Configure the checkpointer to resume this specific thread
        config = {
            "configurable": {
                "thread_id": langgraph_thread_id,
                "chat_adapter": slack_adapter
            }
        }

        os.makedirs("data", exist_ok=True)

        async with AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite") as memory:
            agent_app = build_graph(memory)
            
            logger.info(f"--- RESUMING LANGGRAPH FOR THREAD {langgraph_thread_id} ---")
            try:
                # Command(resume=value) officially answers the interrupt() in Node 4
                await agent_app.ainvoke(Command(resume=selected_repo), config=config)
                logger.info("--- GRAPH EXECUTION COMPLETED (NODE 5 FINISHED) ---")
            except Exception as e:
                logger.error(f"Error resuming graph: {e}")
                
    except Exception as e:
        logger.error(f"Error processing button payload: {e}")

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