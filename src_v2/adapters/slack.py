# src_v2/adapters/slack.py
import os
from typing import Any, Dict, List, Optional
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
import logging

logger = logging.getLogger(__name__)

from .base import BaseChatAdapter

class SlackAdapter(BaseChatAdapter):
    """
    Slack-specific implementation of the BaseChatAdapter.
    """
    
    def __init__(self, bot_token: str = None):
        token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise ValueError("SLACK_BOT_TOKEN must be provided or set in environment variables.")
        self.client = AsyncWebClient(token=token)

    # --- FUNCTION 1: SEND MESSAGE ---
    async def send_message(self, channel_id: str, text: str, image_urls: Optional[List[str]] = None, thread_ts: Optional[str] = None) -> Dict[str, Any]:
        print(f"--- DEBUG: SlackAdapter sending to channel={channel_id}, thread_ts={thread_ts} ---")
        try:
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
            
            if image_urls:
                for url in image_urls:
                    blocks.append({
                        "type": "image",
                        "image_url": url,
                        "alt_text": "Reference Image"
                    })

            # Send the message and DISABLE Slack's bulky link previews
            response = await self.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=text, # Fallback text
                blocks=blocks,
                unfurl_links=False,  # <-- THIS FIXES THE SPAMMY PREVIEWS
                unfurl_media=False   # <-- THIS STOPS MEDIA CARDS
            )
            return response.data
        except SlackApiError as e:
            print(f"Error sending message to Slack: {e}")
            return {}

    # --- FUNCTION 2: UPDATE MESSAGE ---
    async def update_message(self, channel_id: str, message_id: str, text: str, thread_ts=None) -> Dict[str, Any]:
        """
        Updates an existing Slack message using its timestamp (message_id).
        """
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            }
        ]

        try:
            response = await self.client.chat_update(
                channel=channel_id,
                thread_ts=thread_ts,
                ts=message_id, # Slack uses 'ts' as the unique message identifier
                text=text,     # Fallback text
                blocks=blocks,
                unfurl_links=False,
                unfurl_media=False
            )
            return response.data
        except SlackApiError as e:
            logger.error(f"Error updating message in Slack: {e.response['error']}")
            raise    

    # --- FUNCTION 3 & 4: OTHER ADAPTER METHODS ---
    async def send_typing_indicator(self, channel_id: str) -> None:
        pass

    async def ask_for_human_approval(self, channel_id: str, repo_options: list, thread_ts: Optional[str] = None, text: str = "Please, make a selection:") -> Dict[str, Any]:
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
                    } for option in repo_options
                ]
            }
        ]
        
        try:
            response = await self.client.chat_postMessage(
                channel=channel_id,
                text="Please select an option.", # Fallback text
                blocks=blocks,
                thread_ts=thread_ts,
            )
            return response.data
        except SlackApiError as e:
            logger.error(f"Error sending approval blocks to Slack: {e.response['error']}")
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
