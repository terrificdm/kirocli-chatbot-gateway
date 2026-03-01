"""Discord chat adapter implementation using discord.py."""

import asyncio
import base64
import logging
import os
import threading
from typing import Any, Callable

import discord
from discord import Intents, Message, Embed, app_commands

from .base import ChatAdapter, ChatType, IncomingMessage, CardHandle, MessageCallback
from config import DiscordPolicy

log = logging.getLogger(__name__)

# Command handler type: (platform, chat_id, command, args) -> response text
SlashCommandHandler = Callable[[str, str, str, str], str | None]

# Message limits
TEXT_CHUNK_LIMIT = 2000      # Discord's limit for regular messages
EMBED_CHUNK_LIMIT = 4096     # Discord's limit for embed description
MAX_LINES_PER_MESSAGE = 40   # Soft limit for readability


class DiscordAdapter(ChatAdapter):
    """Discord implementation of ChatAdapter using discord.py.
    
    Runs discord.py's async event loop in a dedicated thread.
    Sync methods use run_coroutine_threadsafe to bridge to async.
    """

    def __init__(self, bot_token: str, policy: DiscordPolicy | None = None):
        self._bot_token = bot_token
        self._policy = policy or DiscordPolicy()
        self._message_callback: MessageCallback | None = None
        self._running = False
        
        # Async loop and client (set in start)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: discord.Client | None = None
        self._ready_event = threading.Event()
        
        # Typing loop tasks: chat_id -> asyncio.Task
        self._typing_tasks: dict[str, asyncio.Task] = {}
        
        # Slash commands
        self._slash_handler: SlashCommandHandler | None = None
        self._slash_enabled = os.getenv("DISCORD_SLASH_COMMANDS", "true").lower() == "true"
        self._slash_guild_ids = [x.strip() for x in os.getenv("DISCORD_GUILD_ID", "").split(",") if x.strip()]

    @property
    def platform_name(self) -> str:
        return "discord"

    def set_slash_handler(self, handler: SlashCommandHandler) -> None:
        """Set handler for slash commands.
        
        Handler signature: (platform, chat_id, command, args) -> response_text or None
        """
        self._slash_handler = handler

    def start(self, message_callback: MessageCallback) -> None:
        """Start Discord bot connection (blocking).
        
        Sets up intents and runs the discord.py event loop.
        """
        self._message_callback = message_callback
        self._running = True
        
        # Setup intents - need message content for reading messages
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.dm_messages = True
        
        self._client = discord.Client(intents=intents)
        
        # Setup slash commands if enabled
        if self._slash_enabled:
            self._tree = app_commands.CommandTree(self._client)
            self._setup_slash_commands()
        
        @self._client.event
        async def on_ready():
            log.info("[Discord] Logged in as %s (ID: %s)", 
                     self._client.user.name, self._client.user.id)
            
            # Sync slash commands on ready
            if self._slash_enabled:
                await self._sync_slash_commands()
            
            self._ready_event.set()
        
        @self._client.event
        async def on_message(message: Message):
            await self._handle_message(message)
        
        # Run the client (blocking)
        log.info("[Discord] Starting bot...")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._loop.run_until_complete(self._client.start(self._bot_token))
        except KeyboardInterrupt:
            log.info("[Discord] Received keyboard interrupt")
        finally:
            self._loop.run_until_complete(self._client.close())
            self._loop.close()
            self._running = False
            log.info("[Discord] Bot stopped")

    def stop(self) -> None:
        """Stop Discord bot."""
        self._running = False
        if self._client and self._loop and not self._loop.is_closed():
            # Schedule close from potentially different thread
            asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)
        log.info("[Discord] Adapter stop requested")

    def _run_async(self, coro) -> Any:
        """Run async coroutine from sync context."""
        if not self._loop or self._loop.is_closed():
            log.error("[Discord] Event loop not available")
            return None
        
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=30)
        except Exception as e:
            log.error("[Discord] Async call failed: %s", e)
            return None

    async def _send_with_retry(self, coro_func, *args, max_retries: int = 3, **kwargs):
        """Execute a coroutine with retry on rate limit.
        
        Args:
            coro_func: Async function to call (not the coroutine itself)
            *args, **kwargs: Arguments to pass to coro_func
            max_retries: Maximum number of retry attempts
        
        Returns:
            Result of the coroutine, or raises on persistent failure
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return await coro_func(*args, **kwargs)
            except discord.HTTPException as e:
                last_error = e
                if e.status == 429:  # Rate limited
                    retry_after = getattr(e, 'retry_after', None) or 1.0
                    log.warning(
                        "[Discord] Rate limited (attempt %d/%d), retry in %.1fs",
                        attempt + 1, max_retries, retry_after
                    )
                    await asyncio.sleep(retry_after)
                else:
                    # Non-rate-limit error, don't retry
                    raise
        
        # All retries exhausted
        log.error("[Discord] Max retries exceeded for rate limit")
        raise last_error

    def _split_text(self, text: str, max_len: int = TEXT_CHUNK_LIMIT) -> list[str]:
        """Split text into chunks, respecting line boundaries.
        
        Tries to split at:
        1. Blank lines (paragraph boundaries)
        2. Newlines
        3. Character limit (last resort)
        """
        if len(text) <= max_len:
            return [text]
        
        chunks = []
        remaining = text
        
        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break
            
            # Try to find a good split point
            split_at = max_len
            
            # Look for blank line (paragraph break) within limit
            blank_line_pos = remaining.rfind('\n\n', 0, max_len)
            if blank_line_pos > max_len // 2:  # Only use if reasonably far in
                split_at = blank_line_pos + 1
            else:
                # Look for any newline
                newline_pos = remaining.rfind('\n', 0, max_len)
                if newline_pos > max_len // 2:
                    split_at = newline_pos + 1
                else:
                    # Look for space
                    space_pos = remaining.rfind(' ', 0, max_len)
                    if space_pos > max_len // 2:
                        split_at = space_pos + 1
            
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        
        return chunks

    def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a plain text message, splitting into chunks if needed."""
        async def _send():
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                try:
                    channel = await self._client.fetch_channel(int(chat_id))
                except discord.NotFound:
                    log.error("[Discord] Channel not found: %s", chat_id)
                    return None
            
            chunks = self._split_text(text, TEXT_CHUNK_LIMIT)
            last_msg_id = None
            
            for i, chunk in enumerate(chunks):
                try:
                    msg = await self._send_with_retry(channel.send, chunk)
                    last_msg_id = str(msg.id)
                    if len(chunks) > 1:
                        log.debug("[Discord] Sent chunk %d/%d", i + 1, len(chunks))
                except discord.HTTPException as e:
                    log.error("[Discord] Failed to send chunk %d: %s", i + 1, e)
                    break
            
            return last_msg_id
        
        return self._run_async(_send())

    def send_text_nowait(self, chat_id: str, text: str) -> None:
        """Send a text message without blocking the event loop.
        
        Use this for command responses to avoid blocking Discord's heartbeat.
        """
        async def _send():
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                try:
                    channel = await self._client.fetch_channel(int(chat_id))
                except discord.NotFound:
                    log.error("[Discord] Channel not found: %s", chat_id)
                    return
            
            chunks = self._split_text(text, TEXT_CHUNK_LIMIT)
            for i, chunk in enumerate(chunks):
                try:
                    await self._send_with_retry(channel.send, chunk)
                    if len(chunks) > 1:
                        log.debug("[Discord] Sent chunk %d/%d (nowait)", i + 1, len(chunks))
                except discord.HTTPException as e:
                    log.error("[Discord] Failed to send chunk %d: %s", i + 1, e)
                    break
        
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def send_card(self, chat_id: str, content: str, title: str = "") -> CardHandle | None:
        """Discord doesn't use updatable card embeds for responses.
        
        Returns None to trigger Gateway fallback to send_text().
        The Gateway handles typing indicators via start_typing_loop().
        """
        log.debug("[Discord] Card skipped for %s (Gateway will use send_text)", chat_id)
        return None

    def update_card(self, handle: CardHandle, content: str, title: str = "") -> bool:
        """Update an existing embed message.
        
        If content exceeds embed limit, updates the original embed with first chunk
        and sends additional messages for remaining content.
        """
        if not handle or not handle.message_id:
            log.warning("[Discord] Cannot update card: no handle")
            return False
        
        async def _update():
            channel = self._client.get_channel(int(handle.chat_id))
            if not channel:
                try:
                    channel = await self._client.fetch_channel(int(handle.chat_id))
                except discord.NotFound:
                    log.error("[Discord] Channel not found: %s", handle.chat_id)
                    return False
            
            try:
                msg = await channel.fetch_message(int(handle.message_id))
            except discord.NotFound:
                log.error("[Discord] Message not found: %s", handle.message_id)
                return False
            except discord.Forbidden:
                log.error("[Discord] No permission to access message: %s", handle.message_id)
                return False
            
            # Split content for embed
            chunks = self._split_text(content, EMBED_CHUNK_LIMIT)
            
            # Update original message with first chunk
            try:
                embed = self._build_embed(chunks[0], title)
                await self._send_with_retry(msg.edit, embed=embed)
                log.info("[Discord] Card updated: %s", handle.message_id)
            except discord.Forbidden:
                log.error("[Discord] No permission to edit message: %s", handle.message_id)
                return False
            
            # Send remaining chunks as new messages
            if len(chunks) > 1:
                for i, chunk in enumerate(chunks[1:], start=2):
                    try:
                        # Send as plain text for continuation (cleaner than multiple embeds)
                        await self._send_with_retry(channel.send, chunk)
                        log.debug("[Discord] Sent continuation %d/%d", i, len(chunks))
                    except discord.HTTPException as e:
                        log.error("[Discord] Failed to send continuation %d: %s", i, e)
                        break
            
            return True
        
        result = self._run_async(_update())
        return result if result else False

    def send_typing(self, chat_id: str) -> None:
        """Send typing indicator."""
        async def _typing():
            channel = self._client.get_channel(int(chat_id))
            if channel:
                await channel.typing()
        
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(_typing(), self._loop)

    def start_typing_loop(self, chat_id: str) -> None:
        """Start a background loop that sends typing indicators every 8 seconds.
        
        Discord typing indicator lasts ~10 seconds, so we refresh every 8s.
        """
        async def _typing_loop():
            try:
                channel = self._client.get_channel(int(chat_id))
                if not channel:
                    channel = await self._client.fetch_channel(int(chat_id))
                
                while True:
                    await channel.typing()
                    await asyncio.sleep(8)  # Refresh before 10s expiry
            except asyncio.CancelledError:
                pass  # Normal cancellation
            except Exception as e:
                log.debug("[Discord] Typing loop error for %s: %s", chat_id, e)
        
        async def _start():
            # Cancel existing task if any
            if chat_id in self._typing_tasks:
                self._typing_tasks[chat_id].cancel()
            
            task = asyncio.create_task(_typing_loop())
            self._typing_tasks[chat_id] = task
            log.debug("[Discord] Started typing loop for %s", chat_id)
        
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(_start(), self._loop)

    def stop_typing_loop(self, chat_id: str) -> None:
        """Stop the typing indicator loop for a chat."""
        async def _stop():
            if chat_id in self._typing_tasks:
                self._typing_tasks[chat_id].cancel()
                del self._typing_tasks[chat_id]
                log.debug("[Discord] Stopped typing loop for %s", chat_id)
        
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(_stop(), self._loop)

    def _build_embed(self, content: str, title: str = "") -> Embed:
        """Build a Discord embed from markdown content."""
        embed = Embed(description=content, color=0x5865F2)  # Discord blurple
        if title:
            embed.title = title
        return embed

    async def _download_attachment(self, attachment: discord.Attachment) -> tuple[str, str] | None:
        """Download attachment and return (base64_data, mime_type)."""
        try:
            # Only handle images
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                return None
            
            data = await attachment.read()
            b64 = base64.b64encode(data).decode("ascii")
            mime = attachment.content_type.split(";")[0]  # Remove charset if present
            log.info("[Discord] Downloaded attachment: %d bytes, %s", len(data), mime)
            return (b64, mime)
        except Exception as e:
            log.error("[Discord] Failed to download attachment: %s", e)
            return None

    async def _handle_message(self, message: Message) -> None:
        """Handle incoming Discord message."""
        if not self._message_callback:
            return
        
        # Ignore own messages
        if message.author == self._client.user:
            return
        
        # Ignore bot messages (unless policy allows)
        if message.author.bot and not self._policy.allow_bots:
            return
        
        user_id = str(message.author.id)
        
        # Determine chat type and check access
        if isinstance(message.channel, discord.DMChannel):
            chat_type = ChatType.PRIVATE
            chat_id = str(message.channel.id)
            
            # Check DM access policy
            allowed, reason = self._policy.check_dm_access(user_id)
            if not allowed:
                log.info("[Discord] DM denied for user %s: %s", user_id, reason)
                return
            
        elif isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            chat_type = ChatType.GROUP
            chat_id = str(message.channel.id)
            guild_id = str(message.guild.id) if message.guild else ""
            
            # Check guild access policy
            allowed, reason = self._policy.check_guild_access(guild_id, chat_id, user_id)
            if not allowed:
                log.debug("[Discord] Guild access denied for %s/%s/%s: %s", 
                         guild_id, chat_id, user_id, reason)
                return
            
            # Check if mention is required
            require_mention = self._policy.get_require_mention(guild_id, chat_id)
            mentioned = self._client.user.mentioned_in(message) if self._client.user else False
            
            if require_mention and not mentioned:
                return  # Silently ignore (no log, too noisy)
                
        else:
            # Ignore other channel types (voice, stage, etc.)
            return
        
        # Extract text
        text = message.content
        
        # Remove bot mention from text
        if self._client.user:
            text = text.replace(f"<@{self._client.user.id}>", "").strip()
            text = text.replace(f"<@!{self._client.user.id}>", "").strip()  # Nickname mention
        
        # Handle images
        images = []
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                img_data = await self._download_attachment(attachment)
                if img_data:
                    images.append(img_data)
        
        # Also check embeds for images (e.g., linked images)
        for embed in message.embeds:
            if embed.image and embed.image.url:
                # We can't easily download embed images, skip for now
                pass
        
        if not text and not images:
            return
        
        log.info("[Discord] Message from %s in %s: text=%s, images=%d",
                 message.author.name, chat_id, text[:50] if text else "(none)", len(images))
        
        # Check if bot was mentioned (for raw data)
        mentions_bot = self._client.user.mentioned_in(message) if self._client.user else False
        
        # Create normalized message
        incoming = IncomingMessage(
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            text=text,
            images=images if images else None,
            raw={
                "_platform": "discord",
                "message_id": str(message.id),
                "mentions_bot": mentions_bot,
                "guild_id": str(message.guild.id) if message.guild else None,
                "channel_name": getattr(message.channel, "name", "DM"),
            },
        )
        
        # Call callback (runs synchronously in the event loop;
        # Gateway must use non-blocking sends (_send_text_nowait) for any
        # direct responses here to avoid deadlocking the event loop)
        self._message_callback(incoming)

    # ==================== Slash Commands ====================
    
    def _setup_slash_commands(self):
        """Register slash commands with the command tree."""
        
        @self._tree.command(name="help", description="Show available commands")
        async def help_cmd(interaction: discord.Interaction):
            await self._handle_slash_interaction(interaction, "help", "")
        
        @self._tree.command(name="agent", description="List or switch agents")
        @app_commands.describe(name="Agent name to switch to (leave empty to list)")
        async def agent_cmd(interaction: discord.Interaction, name: str = ""):
            await self._handle_slash_interaction(interaction, "agent", name)
        
        @self._tree.command(name="model", description="List or switch models")
        @app_commands.describe(name="Model name to switch to (leave empty to list)")
        async def model_cmd(interaction: discord.Interaction, name: str = ""):
            await self._handle_slash_interaction(interaction, "model", name)
        
        log.info("[Discord] Slash commands defined: /help, /agent, /model")
    
    async def _sync_slash_commands(self):
        """Sync slash commands with Discord API.
        
        Always syncs globally (for DM access).
        Additionally syncs to specific guilds for instant availability.
        Global sync can take up to 1 hour to propagate.
        """
        try:
            # Global sync (needed for DM access)
            synced = await self._tree.sync()
            log.info("[Discord] Synced %d slash commands globally", len(synced))
            
            # Guild-specific sync (instant availability in servers)
            for gid in self._slash_guild_ids:
                guild = discord.Object(id=int(gid))
                self._tree.copy_global_to(guild=guild)
                synced = await self._tree.sync(guild=guild)
                log.info("[Discord] Synced %d slash commands to guild %s", 
                         len(synced), gid)
        except Exception as e:
            log.error("[Discord] Failed to sync slash commands: %s", e)
    
    async def _handle_slash_interaction(self, interaction: discord.Interaction, 
                                        cmd: str, args: str):
        """Handle a slash command interaction."""
        chat_id = str(interaction.channel_id)
        
        # Defer response (shows "thinking..." while we process)
        await interaction.response.defer()
        
        if not self._slash_handler:
            await interaction.followup.send("❌ Commands not configured")
            return
        
        try:
            # Call the handler (runs in executor to not block)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, 
                self._slash_handler, 
                "discord", chat_id, cmd, args
            )
            
            if response:
                # Split response if too long
                chunks = self._split_text(response, TEXT_CHUNK_LIMIT)
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await interaction.followup.send(chunk)
                    else:
                        # Send additional chunks as new messages
                        channel = interaction.channel
                        if channel:
                            await channel.send(chunk)
            else:
                await interaction.followup.send("✓ Done")
                
        except Exception as e:
            log.error("[Discord] Slash command error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}")
