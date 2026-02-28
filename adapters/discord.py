"""Discord chat adapter implementation (stub)."""

import logging
from .base import ChatAdapter, CardHandle, MessageCallback

log = logging.getLogger(__name__)


class DiscordAdapter(ChatAdapter):
    """Discord implementation of ChatAdapter.
    
    TODO: Implement using discord.py library.
    """

    def __init__(self, bot_token: str):
        self._bot_token = bot_token
        self._message_callback: MessageCallback | None = None
        self._running = False

    @property
    def platform_name(self) -> str:
        return "discord"

    def start(self, message_callback: MessageCallback) -> None:
        """Start Discord bot connection."""
        self._message_callback = message_callback
        self._running = True
        # TODO: Initialize discord.py client and start event loop
        raise NotImplementedError("Discord adapter not yet implemented")

    def stop(self) -> None:
        """Stop Discord bot."""
        self._running = False
        # TODO: Close discord client
        log.info("[Discord] Adapter stopped")

    def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a text message to a Discord channel."""
        # TODO: Use discord.py to send message
        raise NotImplementedError("Discord adapter not yet implemented")

    def send_card(self, chat_id: str, content: str, title: str = "") -> CardHandle | None:
        """Send an embed message (Discord's card equivalent)."""
        # TODO: Create discord.Embed and send
        raise NotImplementedError("Discord adapter not yet implemented")

    def update_card(self, handle: CardHandle, content: str, title: str = "") -> bool:
        """Update an existing embed message."""
        # TODO: Edit message with new embed
        raise NotImplementedError("Discord adapter not yet implemented")

    def send_typing(self, chat_id: str) -> None:
        """Send typing indicator."""
        # TODO: channel.typing()
        pass
