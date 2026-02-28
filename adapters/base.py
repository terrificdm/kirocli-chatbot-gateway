"""Abstract base class for chat platform adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable
from enum import Enum


class ChatType(Enum):
    """Type of chat context."""
    PRIVATE = "private"  # 1:1 direct message
    GROUP = "group"      # Group chat


@dataclass
class IncomingMessage:
    """Normalized incoming message from any platform."""
    chat_id: str                              # Unique identifier for the chat/conversation
    chat_type: ChatType                       # Private or group
    user_id: str                              # User who sent the message
    text: str                                 # Message text content
    images: list[tuple[str, str]] | None      # List of (base64_data, mime_type) tuples
    raw: dict                                 # Raw platform-specific message data


@dataclass 
class CardHandle:
    """Handle to an updatable card/message."""
    message_id: str                           # Platform-specific message ID
    chat_id: str                              # Chat where the card was sent


# Type alias for message callback
MessageCallback = Callable[[IncomingMessage], None]


class ChatAdapter(ABC):
    """Abstract base class for chat platform adapters.
    
    Each platform (Feishu, Discord, Slack, etc.) implements this interface
    to provide a unified way for the Gateway to interact with different platforms.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform name (e.g., 'feishu', 'discord')."""
        pass

    @abstractmethod
    def start(self, message_callback: MessageCallback) -> None:
        """Start listening for messages.
        
        Args:
            message_callback: Function to call when a message is received.
                             The callback receives an IncomingMessage object.
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop listening for messages and clean up resources."""
        pass

    @abstractmethod
    def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a plain text message.
        
        Args:
            chat_id: Target chat identifier
            text: Message text
            
        Returns:
            Message ID if available, None otherwise
        """
        pass

    @abstractmethod
    def send_card(self, chat_id: str, content: str, title: str = "") -> CardHandle | None:
        """Send a rich card/embed message that can be updated later.
        
        Args:
            chat_id: Target chat identifier
            content: Card content (markdown or platform-specific format)
            title: Optional card title
            
        Returns:
            CardHandle for updating the card, None if failed
        """
        pass

    @abstractmethod
    def update_card(self, handle: CardHandle, content: str, title: str = "") -> bool:
        """Update an existing card message.
        
        Args:
            handle: CardHandle returned from send_card
            content: New card content
            title: Optional new title
            
        Returns:
            True if update succeeded, False otherwise
        """
        pass

    def send_typing(self, chat_id: str) -> None:
        """Send a typing indicator (optional, default no-op).
        
        Args:
            chat_id: Target chat identifier
        """
        pass

    def supports_card_update(self) -> bool:
        """Whether this platform supports updating sent messages.
        
        Returns:
            True if update_card works, False otherwise
        """
        return True
