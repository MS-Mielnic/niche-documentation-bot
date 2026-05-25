# src/adapters/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseChatAdapter(ABC):
    """
    Abstract Base Class defining the contract for all chat platform integrations.
    Keeps the core LangGraph logic completely UI-agnostic.
    """

    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> Dict[str, Any]:
        """
        Send a standard text message to the user/channel.
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


    @abstractmethod
    def get_formatting_constraints(self) -> str:
        """
        Returns platform-specific instructions for the LLM's system prompt.
        (e.g., 'Do not use markdown tables', 'Use bolding for headers', etc.)
        """
        pass

