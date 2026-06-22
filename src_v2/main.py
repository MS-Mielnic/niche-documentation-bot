# src_v2/main.py
import os
import re
import traceback
import json
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Form
import uvicorn
from dotenv import load_dotenv

# Import Command to resume LangGraph from an interrupt
from langgraph.types import Command 
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src_v2.observability.telemetry import tracer
from opentelemetry import trace


from src_v2.adapters.slack import SlackAdapter
from src_v2.graph.builder import build_graph
from src_v2.graph.repo_workflow_store import (
    record_selection_received,
    claim_resume,
    mark_resume_completed,
    mark_resume_failed,
)

from src_v2.adapters.simulator import SimulatorAdapter

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


def build_chat_adapter():
    adapter_name = os.getenv("CHAT_ADAPTER", "slack").strip().lower()

    if adapter_name == "simulator":
        logger.info("Using SimulatorAdapter for outbound chat messages")
        return SimulatorAdapter()

    if adapter_name == "slack":
        logger.info("Using SlackAdapter for outbound chat messages")
        return SlackAdapter()

    raise ValueError(
        f"Unsupported CHAT_ADAPTER={adapter_name!r}. "
        "Expected 'slack' or 'simulator'."
    )


chat_adapter = build_chat_adapter()

def _safe_preview(text: str, max_len: int = 120) -> str:
    """
    Return a short, single-line, sanitized preview for telemetry.

    This avoids sending full Slack messages into traces while still making
    traces useful for debugging request flow.
    """
    if not text:
        return ""

    preview = " ".join(text.split())

    # Basic Slack mention redaction. Keeps telemetry readable without storing
    # full user/channel references from the message body.
    preview = re.sub(r"<@[A-Z0-9]+>", "<@user>", preview)
    preview = re.sub(r"<#[A-Z0-9]+\|[^>]+>", "<#channel>", preview)

    if len(preview) > max_len:
        return preview[: max_len - 3] + "..."

    return preview


def _set_if_present(span, key: str, value):
    """
    Set a span attribute only when the value exists.

    This keeps traces clean and avoids attributes with None values.
    """
    if value is not None:
        span.set_attribute(key, value)

async def process_slack_message(event: dict):
    """Handles standard text messages."""
    if "user" not in event or "text" not in event:
        return

    user_id = event["user"]
    text = event["text"]
    channel = event["channel"]

    if "bot_id" in event:
        return

    message_ts = event.get("ts")
    thread_ts = event.get("thread_ts", message_ts)
    event_type = event.get("type")
    langgraph_thread_id = f"{channel}_{thread_ts}"

    with tracer.start_as_current_span("slack.process_message") as span:
        _set_if_present(span, "slack.channel_id", channel)
        _set_if_present(span, "slack.thread_ts", thread_ts)
        _set_if_present(span, "slack.user_id", user_id)
        _set_if_present(span, "slack.event_type", event_type)
        span.set_attribute("slack.message_length", len(text))
        span.set_attribute("agent.thread_id", langgraph_thread_id)
        span.set_attribute("agent.user_request_preview", _safe_preview(text))

        logger.info(
            "Processing message from %s in %s (Thread: %s)",
            user_id,
            channel,
            langgraph_thread_id,
        )

        initial_state = {
            "user_request": text,
            "thread_id": langgraph_thread_id,
            "channel_id": channel,
            "thread_ts": thread_ts,

            # Telemetry/context enrichment for downstream spans.
            "slack_user_id": user_id,
            "slack_event_type": event_type,
            "message_ts": message_ts,
            "user_request_preview": _safe_preview(text),
        }

        config = {
            "configurable": {
                "thread_id": langgraph_thread_id,
                "chat_adapter": chat_adapter,
            }
        }

        os.makedirs("/data", exist_ok=True)

        async with AsyncSqliteSaver.from_conn_string("/data/checkpoints.sqlite") as memory:
            agent_app = build_graph(memory)
            print(f"--- WAKING UP LANGGRAPH (THREAD: {langgraph_thread_id}) ---")
            try:
                await agent_app.ainvoke(initial_state, config=config)
                logger.info("--- GRAPH EXECUTION COMPLETED ---")
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Error executing graph: {e}")
                traceback.print_exc()


async def process_button_click(payload_str: str):
    """
    Handles interactive Block Kit button clicks.

    Durable coordination design:
    - Always record the selected repo first.
    - Resume LangGraph only if the graph is already paused at wait_for_human.
    - If the click arrives early, leave the selection durably stored.
      wait_for_human will consume it when the original graph reaches that node.
    - Duplicate Slack/simulator retries are idempotent.
    """
    try:
        payload_dict = json.loads(payload_str)

        actions = payload_dict.get("actions", [])
        if not actions:
            return

        selected_repo = actions[0].get("value")

        container = payload_dict.get("container", {})
        channel = container.get("channel_id")
        message_ts = container.get("message_ts")
        thread_ts = container.get("thread_ts", message_ts)
        langgraph_thread_id = f"{channel}_{thread_ts}"

        logger.info("--- BUTTON SELECTED REPO: %s ---", selected_repo)
        logger.info("--- RECORDING SELECTION FOR THREAD %s ---", langgraph_thread_id)

        selection_record = await record_selection_received(
            thread_id=langgraph_thread_id,
            selected_repo=selected_repo,
            source="slack_interaction",
        )

        if not selection_record.get("ok"):
            logger.warning(
                "--- SELECTION REJECTED FOR THREAD %s: %s ---",
                langgraph_thread_id,
                selection_record,
            )
            return

        config = {
            "configurable": {
                "thread_id": langgraph_thread_id,
                "chat_adapter": chat_adapter,
            }
        }

        os.makedirs("/data", exist_ok=True)

        async with AsyncSqliteSaver.from_conn_string("/data/checkpoints.sqlite") as memory:
            agent_app = build_graph(memory)
            checkpoint_state = await agent_app.aget_state(config)

            logger.info(
                "--- THREAD %s CHECKPOINT NEXT=%s AFTER SELECTION ---",
                langgraph_thread_id,
                checkpoint_state.next,
            )

            if not checkpoint_state.next or "wait_for_human" not in checkpoint_state.next:
                logger.info(
                    "--- THREAD %s IS NOT INTERRUPT-READY; SELECTION STORED FOR GRAPH TO CONSUME ---",
                    langgraph_thread_id,
                )
                return

            if not await claim_resume(langgraph_thread_id):
                logger.info(
                    "--- THREAD %s RESUME ALREADY CLAIMED OR COMPLETED; IGNORING DUPLICATE CLICK ---",
                    langgraph_thread_id,
                )
                return

            logger.info("--- RESUMING LANGGRAPH FOR THREAD %s ---", langgraph_thread_id)

            try:
                await agent_app.ainvoke(Command(resume=selected_repo), config=config)
                await mark_resume_completed(langgraph_thread_id)
                logger.info("--- GRAPH RESUME COMPLETED ---")
            except Exception as e:
                await mark_resume_failed(langgraph_thread_id, str(e))
                logger.error("Error resuming graph: %s", e, exc_info=True)

    except Exception as e:
        logger.error("Error processing button payload: %s", e, exc_info=True)


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