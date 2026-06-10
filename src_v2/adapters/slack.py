# src_v2/adapters/slack.py
import os
from typing import Any, Dict, List, Optional
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
import logging
from opentelemetry import trace
from src_v2.observability.telemetry import tracer
from .base import BaseChatAdapter


logger = logging.getLogger(__name__)

def _set_if_present(span, key: str, value):
    """
    Set a span attribute only when a value is available.
    Keeps outbound Slack telemetry clean and avoids None attributes.
    """
    if value is not None:
        span.set_attribute(key, value)

class SlackAdapter(BaseChatAdapter):
    """
    Slack-specific implementation of the BaseChatAdapter.
    """
    
    def __init__(self, bot_token: str = None):
        token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise ValueError("SLACK_BOT_TOKEN must be provided or set in environment variables.")
            
        # ENTERPRISE UPDATE: Allow overriding the Slack API URL for local simulation/testing
        custom_base_url = os.getenv("SLACK_API_URL", "https://slack.com/api/")
        
        self.client = AsyncWebClient(token=token, base_url=custom_base_url)

    # --- FUNCTION 1: SEND MESSAGE ---
    async def send_message(
        self,
        channel_id: str,
        text: str,
        image_urls: Optional[List[str]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        print(f"--- DEBUG: SlackAdapter sending to channel={channel_id}, thread_ts={thread_ts} ---")

        with tracer.start_as_current_span(
            "slack.send_message",
            kind=trace.SpanKind.CLIENT,
        ) as span:
            _set_if_present(span, "slack.channel_id", channel_id)
            _set_if_present(span, "slack.thread_ts", thread_ts)
            span.set_attribute("slack.response_length", len(text or ""))
            span.set_attribute("slack.has_images", bool(image_urls))
            span.set_attribute("slack.image_count", len(image_urls or []))

            try:
                blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

                if image_urls:
                    for url in image_urls:
                        blocks.append({
                            "type": "image",
                            "image_url": url,
                            "alt_text": "Reference Image",
                        })

                span.set_attribute("slack.has_blocks", bool(blocks))
                span.set_attribute("slack.block_count", len(blocks))

                response = await self.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    blocks=blocks,
                    unfurl_links=False,
                    unfurl_media=False,
                )

                response_data = response.data or {}
                span.set_attribute("slack.api_ok", bool(response_data.get("ok")))
                span.set_attribute("slack.api_status", "ok" if response_data.get("ok") else "not_ok")
                _set_if_present(span, "slack.message_ts", response_data.get("ts"))

                return response_data

            except SlackApiError as e:
                error_code = e.response.get("error") if e.response else str(e)
                span.set_attribute("slack.api_ok", False)
                span.set_attribute("slack.api_status", error_code)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error_code))
                print(f"Error sending message to Slack: {e}")
                return {}

    # --- FUNCTION 2: UPDATE MESSAGE ---
    async def update_message(
        self,
        channel_id: str,
        message_id: str,
        text: str,
        thread_ts=None,
    ) -> Dict[str, Any]:
        """
        Updates an existing Slack message using its timestamp (message_id).
        """
        with tracer.start_as_current_span(
            "slack.update_message",
            kind=trace.SpanKind.CLIENT,
        ) as span:
            _set_if_present(span, "slack.channel_id", channel_id)
            _set_if_present(span, "slack.thread_ts", thread_ts)
            _set_if_present(span, "slack.message_ts", message_id)
            span.set_attribute("slack.response_length", len(text or ""))
            span.set_attribute("slack.has_images", False)

            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                }
            ]

            span.set_attribute("slack.has_blocks", bool(blocks))
            span.set_attribute("slack.block_count", len(blocks))

            try:
                response = await self.client.chat_update(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    ts=message_id,
                    text=text,
                    blocks=blocks,
                    unfurl_links=False,
                    unfurl_media=False,
                )

                response_data = response.data or {}
                span.set_attribute("slack.api_ok", bool(response_data.get("ok")))
                span.set_attribute("slack.api_status", "ok" if response_data.get("ok") else "not_ok")

                return response_data

            except SlackApiError as e:
                error_code = e.response.get("error") if e.response else str(e)
                span.set_attribute("slack.api_ok", False)
                span.set_attribute("slack.api_status", error_code)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error_code))
                logger.error(f"Error updating message in Slack: {error_code}")
                raise

    # --- FUNCTION 3 & 4: OTHER ADAPTER METHODS ---
    async def send_typing_indicator(self, channel_id: str) -> None:
        pass

    async def ask_for_human_approval(
        self,
        channel_id: str,
        repo_options: list,
        thread_ts: Optional[str] = None,
        text: str = "Please, make a selection:",
    ) -> Dict[str, Any]:

        with tracer.start_as_current_span(
            "slack.ask_for_human_approval",
            kind=trace.SpanKind.CLIENT,
        ) as span:
            _set_if_present(span, "slack.channel_id", channel_id)
            _set_if_present(span, "slack.thread_ts", thread_ts)
            span.set_attribute("slack.response_length", len(text or ""))
            span.set_attribute("slack.button_options_count", len(repo_options or []))
            span.set_attribute("slack.has_images", False)

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

            span.set_attribute("slack.has_blocks", bool(blocks))
            span.set_attribute("slack.block_count", len(blocks))

            try:
                response = await self.client.chat_postMessage(
                    channel=channel_id,
                    text="Please select an option.",
                    blocks=blocks,
                    thread_ts=thread_ts,
                )

                response_data = response.data or {}
                span.set_attribute("slack.api_ok", bool(response_data.get("ok")))
                span.set_attribute("slack.api_status", "ok" if response_data.get("ok") else "not_ok")
                _set_if_present(span, "slack.message_ts", response_data.get("ts"))

                return response_data

            except SlackApiError as e:
                error_code = e.response.get("error") if e.response else str(e)
                span.set_attribute("slack.api_ok", False)
                span.set_attribute("slack.api_status", error_code)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error_code))
                logger.error(f"Error sending approval blocks to Slack: {error_code}")
                raise
    
    def get_formatting_constraints(self) -> str:
        return """
        CRITICAL UI FORMATTING RULES:
        1. You are outputting to Slack, which DOES NOT support Markdown tables. 
        2. If the context contains table data, YOU MUST smoothly integrate it into a clean, readable list of bullet points (e.g., • **[Name]**: [Data]).
        3. NEVER output raw markdown tables using `|` characters.
        4. NEVER announce that you are reformatting a table or converting a list. Just provide the answer natively and naturally.
        5. Hide all internal RAG plumbing (e.g., '--- RECONSTRUCTED TABLE ---') from the user.
        """
