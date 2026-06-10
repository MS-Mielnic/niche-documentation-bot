# src_v2/adapters/simulator.py
import os
import logging
from typing import Any, Dict, List, Optional

import httpx
from opentelemetry import trace

from src_v2.observability.telemetry import tracer
from .base import BaseChatAdapter

logger = logging.getLogger(__name__)


def _set_if_present(span, key: str, value):
    if value is not None:
        span.set_attribute(key, value)


class SimulatorAdapter(BaseChatAdapter):
    """
    Chat adapter used when the real app is driven by nichedocbot-simulator.

    Instead of sending messages to Slack, this adapter posts normalized outbound
    UI messages to the simulator webhook. The simulator can then observe bot
    output, auto-click approval buttons, and continue the test flow without
    adding a polling/status API to the app.
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv(
            "SIMULATOR_WEBHOOK_URL",
            "http://nichedocbot-simulator:8080/webhook",
        )
        self.client = httpx.AsyncClient(timeout=30.0)

    async def _post_webhook(
        self,
        *,
        operation: str,
        channel_id: str,
        text: str,
        thread_ts: Optional[str] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        image_urls: Optional[List[str]] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with tracer.start_as_current_span(
            f"simulator_adapter.{operation}",
            kind=trace.SpanKind.CLIENT,
        ) as span:
            _set_if_present(span, "simulator.webhook_url", self.webhook_url)
            _set_if_present(span, "slack.channel_id", channel_id)
            _set_if_present(span, "slack.thread_ts", thread_ts)
            _set_if_present(span, "slack.message_ts", message_id)

            span.set_attribute("simulator_adapter.operation", operation)
            span.set_attribute("simulator_adapter.text_length", len(text or ""))
            span.set_attribute("simulator_adapter.has_blocks", bool(blocks))
            span.set_attribute("simulator_adapter.block_count", len(blocks or []))
            span.set_attribute("simulator_adapter.has_images", bool(image_urls))
            span.set_attribute("simulator_adapter.image_count", len(image_urls or []))

            # Use thread_ts as the simulator correlation key. If unavailable,
            # fall back to message_id so progress updates can still be correlated.
            ts = thread_ts or message_id

            payload = {
                "operation": operation,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "ts": ts,
                "message_id": message_id,
                "text": text,
                "blocks": blocks or [],
                "image_urls": image_urls or [],
            }

            try:
                response = await self.client.post(self.webhook_url, json=payload)
                span.set_attribute("http.status_code", response.status_code)
                span.set_attribute("simulator_adapter.post_success", response.status_code < 400)
                response.raise_for_status()

                response_data = response.json() if response.content else {}
                return {
                    "ok": True,
                    "ts": ts,
                    "thread_ts": thread_ts,
                    "simulator_response": response_data,
                }

            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error("SimulatorAdapter failed to post webhook: %s", e)
                return {
                    "ok": False,
                    "error": str(e),
                    "ts": ts,
                    "thread_ts": thread_ts,
                }

    async def send_message(
        self,
        channel_id: str,
        text: str,
        image_urls: Optional[List[str]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

        if image_urls:
            for url in image_urls:
                blocks.append({
                    "type": "image",
                    "image_url": url,
                    "alt_text": "Reference Image",
                })

        return await self._post_webhook(
            operation="send_message",
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            blocks=blocks,
            image_urls=image_urls,
        )

    async def update_message(
        self,
        channel_id: str,
        message_id: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

        return await self._post_webhook(
            operation="update_message",
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_id=message_id,
            text=text,
            blocks=blocks,
        )

    async def send_typing_indicator(self, channel_id: str) -> None:
        return None

    async def ask_for_human_approval(
        self,
        channel_id: str,
        repo_options: list,
        thread_ts: Optional[str] = None,
        text: str = "Please make a selection",
    ) -> Dict[str, Any]:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": option,
                            "emoji": True,
                        },
                        "value": option,
                        "action_id": f"select_{option.lower().replace(' ', '_')}",
                    }
                    for option in repo_options
                ],
            },
        ]

        return await self._post_webhook(
            operation="ask_for_human_approval",
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            blocks=blocks,
        )

    def get_formatting_constraints(self) -> str:
        return """
        CRITICAL UI FORMATTING RULES:
        1. You are outputting to a simulator adapter that mimics Slack behavior.
        2. Keep answers concise and readable as Slack-style text.
        3. Avoid raw markdown tables using `|` characters.
        4. Hide all internal RAG plumbing from the user.
        """