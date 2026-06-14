#niche-bot/src_v2/observability/simulator.py
import asyncio
import logging
import time
import os
from typing import Dict, Any
import httpx
import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks
from opentelemetry import trace
import json

# ==========================================
# ENTERPRISE LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Simulator] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("nichedocbot.simulator")

def _set_if_present(span, key: str, value):
    if value is not None:
        span.set_attribute(key, value)

def _debug_trace(method: str, url: str, payload: Dict):
    logger.info(f"DEBUG_TRACE: Sending {method} to {url}")
    logger.info(f"DEBUG_TRACE: Payload: {payload}")

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BOT_EVENTS_URL = os.getenv(
    "BOT_EVENTS_URL",
    "http://nichedocbot:8000/slack/events",
)

BOT_INTERACTIONS_URL = os.getenv(
    "BOT_INTERACTIONS_URL",
    "http://nichedocbot:8000/slack/interactions",
)
# Terminal phrases provided by your spec
TERMINAL_PHRASES = [
    "Done! I've fully synced",
    "is already completely up to date",
    "You can now ask me technical questions"
]

# 10 test scenarios to generate rich telemetry (Splunk Dashboard data)


TEST_SCENARIOS = [
    "pandas",
    "kubernetes",
    "opentelemetry",
    "fastapi",
    "langgraph",
    "vector databases",
    "splunk",
    "docker",
    "ci-cd pipelines",
    "llama",
]



TECHNICAL_QUESTIONS = [
    "What is the core architecture of this repository?",
    "What are the guidelines for contributing?",
    "List the main dependencies used in this project."
]

# ==========================================
# STATE MANAGEMENT
# ==========================================
class SimulationState:
    """Thread-safe state manager for the asyncio lifecycle."""
    def __init__(self):
        # Maps a thread_ts to an asyncio Event that fires when ingestion completes
        self.completion_events: Dict[str, asyncio.Event] = {}
        # Maps a thread_ts to an asyncio Event that fires when a question is answered
        self.answer_events: Dict[str, asyncio.Event] = {}
        self.client = httpx.AsyncClient(timeout=30.0)
        self.awaiting_answer: Dict[str, bool] = {}

    def register_thread(self, thread_ts: str):
        self.completion_events[thread_ts] = asyncio.Event()
        self.answer_events[thread_ts] = asyncio.Event()
        self.awaiting_answer[thread_ts] = False

    async def trigger_button_click(self, thread_ts: str, action_id: str, value: str):
        with tracer.start_as_current_span("simulator.trigger_button_click") as span:
            span.set_attribute("simulator.thread_ts", thread_ts)
            span.set_attribute("simulator.action_id", action_id)
            span.set_attribute("simulator.selected_value", value)
            span.set_attribute("simulator.target_url", BOT_INTERACTIONS_URL)

            payload = {
                "type": "block_actions",
                "container": {
                    "type": "message",
                    "channel_id": "C12345678",
                    "message_ts": thread_ts,
                    "thread_ts": thread_ts,
                },
                "channel": {
                    "id": "C12345678",
                },
                "user": {
                    "id": "U1234SIMULATOR",
                },
                "message": {
                    "ts": thread_ts,
                    "thread_ts": thread_ts,
                },
                "actions": [
                    {
                        "action_id": action_id,
                        "value": value,
                    }
                ],
            }

            _debug_trace("POST", BOT_INTERACTIONS_URL, payload)

            try:
                response = await self.client.post(BOT_INTERACTIONS_URL, data={"payload": json.dumps(payload)})
                span.set_attribute("http.status_code", response.status_code)
                span.set_attribute("simulator.interaction_post_success", response.status_code < 400)
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to send interaction: {e}")
            
state = SimulationState()
app = FastAPI(title="NicheDocBot Local Simulator Adapter")

# ==========================================
# WEBHOOK RECEIVER (THE "EAR")
# ==========================================
@app.post("/webhook")
async def receive_adapter_webhook(request: Request, background_tasks: BackgroundTasks):
    """Intercepts outgoing messages from the Bot's K8s Adapter."""
    with tracer.start_as_current_span("simulator.receive_webhook") as span:
        payload = await request.json()
        thread_ts = payload.get("thread_ts") or payload.get("ts")
        text = payload.get("text", "")
        blocks = payload.get("blocks", [])

        logger.info(
            f"[{thread_ts}] Webhook received: text={text[:300]!r}, blocks={len(blocks or [])}"
        )
        normalized_text = text.lower()

        is_terminal_message = (
            "done! i've fully synced" in normalized_text
            or "already completely up to date" in normalized_text
            or "you can now ask me technical questions" in normalized_text
        )

        if is_terminal_message:
            logger.info(f"[{thread_ts}] Terminal state reached: {text}")

            if thread_ts in state.completion_events:
                state.completion_events[thread_ts].set()
            else:
                logger.warning(
                    f"[{thread_ts}] Terminal message received, but no completion event was registered."
                )

            return {"status": "terminal_detected"}


        _set_if_present(span, "simulator.thread_ts", thread_ts)
        span.set_attribute("simulator.webhook_text_length", len(text or ""))
        span.set_attribute("simulator.webhook_block_count", len(blocks or []))
        span.set_attribute("simulator.has_blocks", bool(blocks))

        if not thread_ts:
            return {"status": "ignored"}

        logger.debug(f"[{thread_ts}] Received Webhook Payload: {text[:50]}...")

        if "Which one should I ingest?" in text or any(b.get("type") == "actions" for b in blocks):
            for block in blocks:
                if block.get("type") == "actions":
                    # Always click the first repository option to proceed
                    first_button = block["elements"][0]
                    action_id = first_button["action_id"]
                    value = first_button.get("value", first_button.get("text", {}).get("text", "repo"))
                    
                    logger.info(f"[{thread_ts}] Simulator selected repo: {value}")

                    background_tasks.add_task(state.trigger_button_click, thread_ts, action_id, value)
                    return {"status": "interaction_triggered"}
        # If this is a regular bot response in a registered thread,
        # treat it as the answer to the current simulator question.
        if text and thread_ts in state.answer_events and state.awaiting_answer.get(thread_ts):
            logger.info(f"[{thread_ts}] Answer detected. Marking current question as answered.")
            state.awaiting_answer[thread_ts] = False
            state.answer_events[thread_ts].set()
            return {"status": "answer_detected"}
        
        if text and thread_ts in state.answer_events:
            logger.info(f"[{thread_ts}] Bot message received while not awaiting answer. Ignoring for answer tracking.")
            return {"status": "bot_message_ignored_not_awaiting_answer"}


        return {"status": "ignored"}

# ==========================================
# SCENARIO RUNNER (THE "BRAIN")
# ==========================================
async def send_slack_message(thread_ts: str, text: str):
    with tracer.start_as_current_span("simulator.send_slack_message") as span:
        span.set_attribute("simulator.thread_ts", thread_ts)
        span.set_attribute("simulator.message_length", len(text or ""))
        span.set_attribute("simulator.target_url", BOT_EVENTS_URL)

        payload = {
            "event": {
                "type": "message",
                "text": text,
                "ts": thread_ts,
                "thread_ts": thread_ts,
                "channel": "C12345678",
                "user": "U1234SIMULATOR"
            }
        }

        _debug_trace("POST", BOT_EVENTS_URL, payload)

        response = await state.client.post(BOT_EVENTS_URL, json=payload)
        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("simulator.post_success", response.status_code < 400)
        response.raise_for_status()

async def run_load_test():
    """Cycles through the test scenarios to generate OpenTelemetry traces."""
    with tracer.start_as_current_span("simulator.run_load_test") as root_span:
        root_span.set_attribute("simulator.scenario_count", len(TEST_SCENARIOS))
        root_span.set_attribute("simulator.questions_per_scenario", len(TECHNICAL_QUESTIONS))
        root_span.set_attribute("simulator.bot_events_url", BOT_EVENTS_URL)
        root_span.set_attribute("simulator.bot_interactions_url", BOT_INTERACTIONS_URL)

        logger.info("Starting Enterprise Load Cycle in 3 seconds...")
        await asyncio.sleep(3)

        for i, topic in enumerate(TEST_SCENARIOS, 1):
            thread_ts = str(time.time())

            with tracer.start_as_current_span("simulator.scenario") as scenario_span:
                scenario_span.set_attribute("simulator.scenario_index", i)
                scenario_span.set_attribute("simulator.scenario_total", len(TEST_SCENARIOS))
                scenario_span.set_attribute("simulator.topic", topic)
                scenario_span.set_attribute("simulator.thread_ts", thread_ts)

                state.register_thread(thread_ts)

                logger.info(f"--- [Cycle {i}/10] Starting flow for topic: '{topic}' ---")

                try:
                    # Phase 1: Trigger Ingestion
                    scenario_span.add_event("phase.ingestion_request_started")
                    await send_slack_message(
                        thread_ts,
                        f"@NicheDocBot I would like to search for {topic}",
                    )

                    # Phase 2: Wait for backend vector DB ingestion / update check
                    logger.info(f"[{thread_ts}] Waiting for background ingestion to complete...")
                    scenario_span.add_event("phase.wait_for_ingestion_started")

                    await state.completion_events[thread_ts].wait()

                    scenario_span.set_attribute("simulator.ingestion_completed", True)
                    scenario_span.add_event("phase.ingestion_completed")
                    logger.info(f"[{thread_ts}] Waiting for background ingestion to complete...")

                    # Phase 3: The Q&A Barrage
                    logger.info(f"[{thread_ts}] Ingestion complete. Starting Q&A barrage...")
                    scenario_span.add_event("phase.qa_started")

                    answered_count = 0

                    for q_num, question in enumerate(TECHNICAL_QUESTIONS, 1):
                        state.answer_events[thread_ts].clear()
                        logger.info(f"[{thread_ts}] Asking Q{q_num}: {question}")

                        with tracer.start_as_current_span("simulator.question") as question_span:
                            question_span.set_attribute("simulator.thread_ts", thread_ts)
                            question_span.set_attribute("simulator.question_index", q_num)
                            question_span.set_attribute("simulator.question_length", len(question))
                            question_span.set_attribute("simulator.topic", topic)

                            state.awaiting_answer[thread_ts] = True
                            await send_slack_message(thread_ts, question)

                            try:
                                await asyncio.wait_for(
                                    state.answer_events[thread_ts].wait(),
                                    timeout=60.0,
                                )
                                answered_count += 1
                                question_span.set_attribute("simulator.answer_received", True)

                            except asyncio.TimeoutError:
                                question_span.set_attribute("simulator.answer_received", False)
                                question_span.set_attribute("simulator.timeout_phase", "answer")
                                question_span.add_event("phase.answer_timeout")
                                logger.warning(f"[{thread_ts}] TIMEOUT waiting for answer to Q{q_num}.")

                    scenario_span.set_attribute("simulator.questions_answered", answered_count)
                    scenario_span.set_attribute("simulator.scenario_completed", True)
                    logger.info(f"--- Cycle {i} Complete ---\n")

                except Exception as e:
                    scenario_span.record_exception(e)
                    scenario_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    scenario_span.set_attribute("simulator.scenario_completed", False)
                    logger.error(f"[{thread_ts}] Scenario failed: {e}")

        root_span.add_event("load_test.completed")
        logger.info("All Load Test Scenarios Completed. Check traces!")
        await state.client.aclose()

# ==========================================
# BOOTSTRAP
# ==========================================
@app.on_event("startup")
async def startup_event():
    # Start the load generator in the background as soon as the server boots
    asyncio.create_task(run_load_test())


if __name__ == "__main__":
    logger.info("Booting Simulator Webhook Server on port 8080...")
    # Run the embedded API
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")