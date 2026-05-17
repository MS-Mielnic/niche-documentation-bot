# src_v2/adapters/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseChatAdapter(ABC):
    """
    Abstract Base Class defining the contract for all chat platform integrations.
    Keeps the core LangGraph logic completely UI-agnostic.
    """

    @abstractmethod
    async def send_message(self, channel_id: str, text: str, image_urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Send a standard text message to the user/channel. 
        Optionally supports rendering a list of image URLs inline.
        """
        pass

    @abstractmethod
    async def send_typing_indicator(self, channel_id: str) -> None:
        """
        Send a visual cue that the bot is processing/typing.
        """
        pass

    @abstractmethod
    async def ask_for_human_approval(self, channel_id: str, text: str, options: List[str]) -> Dict[str, Any]:
        """
        Send a message with interactive elements (e.g., buttons or dropdowns)
        to pause the graph and request human input (HITL).
        """
        pass