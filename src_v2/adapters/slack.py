# src_v2/adapters/slack.py
import os
from typing import Any, Dict, List, Optional
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
import logging

# Ensure you configure your logger appropriately in src/core/logger.py later
logger = logging.getLogger(__name__)

from .base import BaseChatAdapter

class SlackAdapter(BaseChatAdapter):
    """
    Slack-specific implementation of the BaseChatAdapter.
    """
    
    def __init__(self, bot_token: str = None):
        # Fallback to environment variable if not passed explicitly
        token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise ValueError("SLACK_BOT_TOKEN must be provided or set in environment variables.")
        self.client = AsyncWebClient(token=token)

    async def send_message(self, channel_id: str, text: str, image_urls: Optional[List[str]] = None) -> Dict[str, Any]:
        # 1. Base Block: The LLM's text response
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            }
        ]

        # 2. Dynamic Image Blocks: Append if vision triggered
        if image_urls:
            for i, url in enumerate(image_urls):
                blocks.append({
                    "type": "image",
                    "image_url": url,
                    "alt_text": f"Repository Visual Artifact {i+1}"
                })

        try:
            # 3. Execute the payload
            response = await self.client.chat_postMessage(
                channel=channel_id,
                text=text,     # Required fallback text for push notifications/mobile previews
                blocks=blocks  # The rich UI layout
            )
            return response.data
        except SlackApiError as e:
            logger.error(f"Error sending message to Slack: {e.response['error']}")
            raise

    async def send_typing_indicator(self, channel_id: str) -> None:
        # Note: Slack's async typing indicator requires the Events API/Socket Mode,
        # but you can simulate it or rely on their built-in event mechanisms depending on your setup.
        pass

    async def ask_for_human_approval(self, channel_id: str, text: str, options: List[str]) -> Dict[str, Any]:
        """
        Constructs a Slack Block Kit message with buttons for the user to select an option.
        """
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": option,
                            "emoji": True
                        },
                        "value": option,
                        "action_id": f"select_{option.lower().replace(' ', '_')}"
                    } for option in options
                ]
            }
        ]
        
        try:
            response = await self.client.chat_postMessage(
                channel=channel_id,
                text="Please select an option.", # Fallback text
                blocks=blocks
            )
            return response.data
        except SlackApiError as e:
            logger.error(f"Error sending approval blocks to Slack: {e.response['error']}")
            raise