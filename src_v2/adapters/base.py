# src_v2/adapters/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseChatAdapter(ABC):
    """
    Abstract Base Class defining the contract for all chat platform integrations.
    Keeps the core LangGraph logic completely UI-agnostic.
    """

    @abstractmethod
    async def send_message(self, channel_id: str, text: str, image_urls: Optional[List[str]] = None, thread_ts: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a standard text message to the user/channel/thread
        Optionally supports rendering a list of image URLs inline.
        """
        pass
    
    @abstractmethod
    async def update_message(self, channel_id: str, message_id: str, text: str) -> Dict[str, Any]:
        """
        Update an existing message in the chat interface.
        Used for real-time progress indicators without spamming the channel.
        """
        pass

    @abstractmethod
    async def send_typing_indicator(self, channel_id: str) -> None:
        """
        Send a visual cue that the bot is processing/typing.
        """
        pass

    @abstractmethod
    async def ask_for_human_approval(self, channel_id: str, repo_options: list, thread_ts: Optional[str] = None, text: str = "Please make a selection") -> Dict[str, Any]:      
        """
        Send a message with interactive elements (e.g., buttons or dropdowns)
        to pause the graph and request human input (HITL).
        """
        pass

    @abstractmethod
    def get_formatting_constraints(self) -> str:
        """
        Returns platform-specific instructions for the LLM's system prompt.
        (e.g., 'Do not use markdown tables', 'Use bolding for headers', etc.)
        """
        pass

    # ... [Keep your existing abstract methods like send_message, etc.] ...