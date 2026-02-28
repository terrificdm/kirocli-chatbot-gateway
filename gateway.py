"""Gateway: connects chat adapters to Kiro CLI via ACP protocol.

Platform-agnostic gateway that works with any ChatAdapter implementation.
Each platform gets its own Kiro CLI instance for fault isolation.
workspace_mode only affects session working directories, not Kiro CLI instances.
"""

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

from adapters.base import ChatAdapter, IncomingMessage, CardHandle
from acp_client import ACPClient, PromptResult, PermissionRequest
from config import Config

log = logging.getLogger(__name__)

# Permission request timeout (seconds)
_PERMISSION_TIMEOUT = 60


def format_response(result: PromptResult) -> str:
    """Format Kiro's response with tool call info."""
    parts = []

    # Show tool calls
    for tc in result.tool_calls:
        icon = {"fs": "📄", "edit": "📝", "terminal": "⚡", "other": "🔧"}.get(tc.kind, "🔧")
        if result.stop_reason == "refusal" and tc.status != "completed":
            status_icon = "🚫"
        else:
            status_icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "⏳")
        line = f"{icon} {tc.title} {status_icon}"
        parts.append(line)

    if parts:
        parts.append("")

    if result.stop_reason == "refusal":
        if result.text:
            parts.append(result.text)
        else:
            parts.append("🚫 Operation cancelled")
        parts.append("")
        parts.append("💬 You can continue the conversation")
    elif result.text:
        parts.append(result.text)

    return "\n".join(parts) if parts else "(No response)"


@dataclass
class ChatContext:
    """Context for a chat conversation."""
    chat_id: str
    platform: str
    session_id: str | None = None


class Gateway:
    """Platform-agnostic gateway between chat adapters and Kiro CLI.
    
    Each platform gets its own Kiro CLI instance for:
    - Fault isolation (one crash doesn't affect others)
    - Independent idle timeout
    - Platform-specific working directories
    
    workspace_mode affects session working directories:
    - fixed: all sessions share the same directory
    - per_chat: each session gets its own subdirectory
    """

    def __init__(self, config: Config, adapters: list[ChatAdapter]):
        self._config = config
        self._adapters = adapters
        self._adapter_map: dict[str, ChatAdapter] = {a.platform_name: a for a in adapters}
        
        # Per-platform ACP clients: platform -> ACPClient
        self._acp_clients: dict[str, ACPClient] = {}
        self._acp_lock = threading.Lock()
        
        # Per-platform last activity time: platform -> timestamp
        self._last_activity: dict[str, float] = {}
        
        # Chat context: "platform:chat_id" -> ChatContext
        self._contexts: dict[str, ChatContext] = {}
        self._contexts_lock = threading.Lock()
        
        # Processing state: "platform:chat_id" -> True if processing
        self._processing: dict[str, bool] = {}
        self._processing_lock = threading.Lock()
        
        # Message queue: "platform:chat_id" -> list of (text, images)
        self._message_queue: dict[str, list] = {}
        self._queue_lock = threading.Lock()
        
        # Pending permission requests: "platform:chat_id" -> (event, result_holder)
        self._pending_permissions: dict[str, tuple[threading.Event, list]] = {}
        self._pending_permissions_lock = threading.Lock()
        
        # session_id -> "platform:chat_id" mapping
        self._session_to_key: dict[str, str] = {}
        
        # Idle checker
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

    def _make_key(self, platform: str, chat_id: str) -> str:
        """Create unique key for platform:chat_id combination."""
        return f"{platform}:{chat_id}"

    def start(self):
        """Start the gateway and all adapters."""
        log.info("[Gateway] Starting with per-platform Kiro CLI instances (workspace_mode=%s)", 
                 self._config.kiro.workspace_mode)

        # Start idle checker
        self._idle_checker_stop.clear()
        self._idle_checker_thread = threading.Thread(target=self._idle_checker_loop, daemon=True)
        self._idle_checker_thread.start()

        # Setup graceful shutdown
        def shutdown(sig, frame):
            log.info("[Gateway] Shutting down...")
            self._idle_checker_stop.set()
            self._stop_all_acp()
            for adapter in self._adapters:
                adapter.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        # Start adapters
        if not self._adapters:
            log.error("[Gateway] No adapters configured")
            return

        # Start all but last adapter in threads
        for adapter in self._adapters[:-1]:
            log.info("[Gateway] Starting %s adapter in thread...", adapter.platform_name)
            t = threading.Thread(
                target=adapter.start,
                args=(self._on_message,),
                daemon=True,
            )
            t.start()

        # Start last adapter in main thread (blocking)
        last_adapter = self._adapters[-1]
        log.info("[Gateway] Starting %s adapter (blocking)...", last_adapter.platform_name)
        last_adapter.start(self._on_message)

    def _start_acp(self, platform: str) -> ACPClient:
        """Start ACP client for a specific platform if not running."""
        with self._acp_lock:
            if platform in self._acp_clients and self._acp_clients[platform].is_running():
                return self._acp_clients[platform]
            
            log.info("[Gateway] [%s] Starting kiro-cli...", platform)
            acp = ACPClient(cli_path=self._config.kiro.path)
            
            # Get cwd based on workspace_mode:
            # - fixed mode: pass platform cwd (loads project-level .kiro/ config)
            # - per_chat mode: pass None (loads global ~/.kiro/ config)
            cwd = self._config.get_kiro_cwd(platform)
            acp.start(cwd=cwd)
            # Use default argument to capture platform value (avoid closure issue)
            acp.on_permission_request(lambda req, p=platform: self._handle_permission(req, p))
            
            self._acp_clients[platform] = acp
            self._last_activity[platform] = time.time()
            
            # Clear sessions for this platform
            with self._contexts_lock:
                keys_to_remove = [k for k in self._contexts if k.startswith(f"{platform}:")]
                for k in keys_to_remove:
                    ctx = self._contexts.pop(k, None)
                    if ctx and ctx.session_id:
                        self._session_to_key.pop(ctx.session_id, None)
            
            mode = self._config.get_workspace_mode(platform)
            log.info("[Gateway] [%s] kiro-cli started (mode=%s, cwd=%s)", platform, mode, cwd)
            return acp

    def _stop_acp(self, platform: str):
        """Stop ACP client for a specific platform."""
        with self._acp_lock:
            acp = self._acp_clients.pop(platform, None)
            self._last_activity.pop(platform, None)
            
        if acp is not None:
            log.info("[Gateway] [%s] Stopping kiro-cli...", platform)
            acp.stop()
            
            # Clear sessions for this platform
            with self._contexts_lock:
                keys_to_remove = [k for k in self._contexts if k.startswith(f"{platform}:")]
                for k in keys_to_remove:
                    ctx = self._contexts.pop(k, None)
                    if ctx and ctx.session_id:
                        self._session_to_key.pop(ctx.session_id, None)
            
            log.info("[Gateway] [%s] kiro-cli stopped", platform)

    def _stop_all_acp(self):
        """Stop all ACP clients."""
        with self._acp_lock:
            platforms = list(self._acp_clients.keys())
        for platform in platforms:
            self._stop_acp(platform)

    def _ensure_acp(self, platform: str) -> ACPClient:
        """Ensure ACP client is running for a platform."""
        acp = self._start_acp(platform)
        with self._acp_lock:
            self._last_activity[platform] = time.time()
        return acp

    def _get_acp(self, platform: str) -> ACPClient | None:
        """Get ACP client for a platform if running."""
        with self._acp_lock:
            acp = self._acp_clients.get(platform)
            if acp and acp.is_running():
                return acp
        return None

    def _idle_checker_loop(self):
        """Background thread for per-platform idle timeout."""
        idle_timeout = self._config.kiro.idle_timeout
        if idle_timeout <= 0:
            log.info("[Gateway] Idle timeout disabled")
            return
        
        while not self._idle_checker_stop.wait(timeout=30):
            platforms_to_stop = []
            
            with self._acp_lock:
                now = time.time()
                for platform, last in self._last_activity.items():
                    idle_time = now - last
                    if idle_time > idle_timeout:
                        if platform in self._acp_clients and self._acp_clients[platform].is_running():
                            log.info("[Gateway] [%s] Idle timeout (%.0fs)", platform, idle_time)
                            platforms_to_stop.append(platform)
            
            # Stop outside the lock
            for platform in platforms_to_stop:
                self._stop_acp(platform)

    def _get_adapter(self, platform: str) -> ChatAdapter | None:
        """Get adapter by platform name."""
        return self._adapter_map.get(platform)

    def _send_text(self, platform: str, chat_id: str, text: str):
        """Send text message via appropriate adapter."""
        adapter = self._get_adapter(platform)
        if adapter:
            adapter.send_text(chat_id, text)

    def _send_card(self, platform: str, chat_id: str, content: str, title: str = "") -> CardHandle | None:
        """Send card via appropriate adapter."""
        adapter = self._get_adapter(platform)
        if adapter:
            return adapter.send_card(chat_id, content, title)
        return None

    def _update_card(self, platform: str, handle: CardHandle, content: str, title: str = "") -> bool:
        """Update card via appropriate adapter."""
        adapter = self._get_adapter(platform)
        if adapter:
            return adapter.update_card(handle, content, title)
        return False

    def _handle_permission(self, request: PermissionRequest, platform: str) -> str | None:
        """Handle permission request from Kiro."""
        session_id = request.session_id
        key = self._session_to_key.get(session_id)
        if not key:
            log.warning("[Gateway] [%s] No chat found for session %s, auto-denying", platform, session_id)
            return "deny"

        _, chat_id = key.split(":", 1)
        
        msg = f"🔐 **Kiro requests permission:**\n\n"
        msg += f"📋 {request.title}\n\n"
        msg += "Reply: **y**(allow) / **n**(deny) / **t**(trust)\n"
        msg += f"⏱️ Auto-deny in {_PERMISSION_TIMEOUT}s"

        self._send_text(platform, chat_id, msg)
        log.info("[Gateway] [%s] Sent permission request: %s", platform, request.title)

        evt = threading.Event()
        result_holder: list = []

        with self._pending_permissions_lock:
            self._pending_permissions[key] = (evt, result_holder)

        try:
            if evt.wait(timeout=_PERMISSION_TIMEOUT):
                if result_holder:
                    decision = result_holder[0]
                    log.info("[Gateway] [%s] User decision: %s", platform, decision)
                    return decision
            
            self._send_text(platform, chat_id, "⏱️ Timeout, auto-denied")
            log.warning("[Gateway] [%s] Permission timed out: %s", platform, request.title)
            return "deny"
        finally:
            with self._pending_permissions_lock:
                self._pending_permissions.pop(key, None)

    def _on_message(self, msg: IncomingMessage):
        """Handle incoming message from any adapter."""
        platform = msg.raw.get("_platform", "")
        if not platform:
            log.warning("[Gateway] Message missing _platform in raw data")
            if self._adapters:
                platform = self._adapters[0].platform_name
            else:
                return
        
        chat_id = msg.chat_id
        text = msg.text.strip()
        text_lower = text.lower()
        images = msg.images
        key = self._make_key(platform, chat_id)

        if images:
            log.info("[Gateway] [%s] Received %d image(s)", key, len(images))

        # Check for permission response
        with self._pending_permissions_lock:
            pending = self._pending_permissions.get(key)
        
        if pending:
            evt, result_holder = pending
            if text_lower in ('y', 'yes', 'ok'):
                result_holder.append("allow_once")
                evt.set()
                return
            elif text_lower in ('n', 'no'):
                result_holder.append("deny")
                evt.set()
                return
            elif text_lower in ('t', 'trust', 'always'):
                result_holder.append("allow_always")
                evt.set()
                return
            else:
                self._send_text(platform, chat_id, "⚠️ Please reply y/n/t")
                return

        # Cancel command
        if text_lower in ("cancel", "stop"):
            self._handle_cancel(platform, chat_id, key)
            return

        # Slash commands
        if text.startswith("/"):
            self._handle_command(platform, chat_id, key, text)
            return

        # Process message in thread
        threading.Thread(
            target=self._process_message,
            args=(platform, chat_id, key, text, images),
            daemon=True,
        ).start()

    def _handle_cancel(self, platform: str, chat_id: str, key: str):
        """Handle cancel command."""
        queue_cleared = 0
        with self._queue_lock:
            if key in self._message_queue:
                queue_cleared = len(self._message_queue[key])
                del self._message_queue[key]
        
        with self._contexts_lock:
            ctx = self._contexts.get(key)
            session_id = ctx.session_id if ctx else None

        if not session_id:
            if queue_cleared:
                self._send_text(platform, chat_id, f"🗑️ Cleared {queue_cleared} queued message(s)")
            else:
                self._send_text(platform, chat_id, "❌ No active session")
            return

        acp = self._get_acp(platform)
        if not acp:
            if queue_cleared:
                self._send_text(platform, chat_id, f"🗑️ Cleared {queue_cleared} queued message(s)")
            else:
                self._send_text(platform, chat_id, "❌ Kiro is not running")
            return

        try:
            acp.session_cancel(session_id)
            msg = "⏹️ Cancel request sent"
            if queue_cleared:
                msg += f"\n🗑️ Cleared {queue_cleared} queued message(s)"
            self._send_text(platform, chat_id, msg)
        except Exception as e:
            log.error("[Gateway] [%s] Cancel failed: %s", key, e)
            self._send_text(platform, chat_id, f"❌ Cancel failed: {e}")

    def _handle_command(self, platform: str, chat_id: str, key: str, text: str):
        """Handle slash commands."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/agent":
            self._handle_agent_command(platform, chat_id, key, arg)
        elif cmd == "/model":
            self._handle_model_command(platform, chat_id, key, arg)
        elif cmd == "/help":
            self._handle_help_command(platform, chat_id)
        else:
            self._send_text(platform, chat_id, f"❓ Unknown command: {cmd}\n💡 Send /help for available commands")

    def _handle_agent_command(self, platform: str, chat_id: str, key: str, mode_arg: str):
        """Handle /agent command."""
        with self._contexts_lock:
            ctx = self._contexts.get(key)
            session_id = ctx.session_id if ctx else None

        if not session_id:
            self._send_text(platform, chat_id, "❌ No session yet. Send a message first.")
            return

        acp = self._get_acp(platform)
        if not acp:
            self._send_text(platform, chat_id, "❌ Kiro is not running")
            return

        if not mode_arg:
            modes_data = acp.get_session_modes(session_id)
            if not modes_data:
                self._send_text(platform, chat_id, "❓ No agent info available")
                return
            
            current_mode = modes_data.get("currentModeId", "")
            available_modes = modes_data.get("availableModes", [])
            
            if not available_modes:
                self._send_text(platform, chat_id, "❓ No agents available")
                return
            
            lines = ["📋 **Available Agents:**", ""]
            for mode in available_modes:
                mode_id = mode.get("id", "") if isinstance(mode, dict) else str(mode)
                mode_name = mode.get("name", mode_id) if isinstance(mode, dict) else str(mode)
                marker = " ✓" if mode_id == current_mode else ""
                if mode_id == mode_name:
                    lines.append(f"• {mode_id}{marker}")
                else:
                    lines.append(f"• {mode_id} - {mode_name}{marker}")
            lines.append("")
            lines.append(f"Current: **{current_mode}**")
            lines.append("💡 Use /agent agent_name to switch")
            self._send_text(platform, chat_id, "\n".join(lines))
            return

        # Validate and switch
        modes_data = acp.get_session_modes(session_id)
        valid_mode_ids = set()
        if modes_data:
            for mode in modes_data.get("availableModes", []):
                mid = mode.get("id", "") if isinstance(mode, dict) else str(mode)
                if mid:
                    valid_mode_ids.add(mid)
        
        if valid_mode_ids and mode_arg not in valid_mode_ids:
            self._send_text(platform, chat_id, f"❌ Invalid agent: {mode_arg}\n\n💡 Use /agent to see available agents")
            return

        try:
            acp.session_set_mode(session_id, mode_arg)
            self._send_text(platform, chat_id, f"✅ Switched to agent: **{mode_arg}**")
        except Exception as e:
            log.error("[Gateway] [%s] Set mode failed: %s", key, e)
            self._send_text(platform, chat_id, f"❌ Switch failed: {e}")

    def _handle_model_command(self, platform: str, chat_id: str, key: str, model_arg: str):
        """Handle /model command."""
        with self._contexts_lock:
            ctx = self._contexts.get(key)
            session_id = ctx.session_id if ctx else None

        if not session_id:
            self._send_text(platform, chat_id, "❌ No session yet. Send a message first.")
            return

        acp = self._get_acp(platform)
        if not acp:
            self._send_text(platform, chat_id, "❌ Kiro is not running")
            return

        if not model_arg:
            options = acp.get_model_options(session_id)
            current_model = acp.get_current_model(session_id)
            
            if options:
                lines = ["📋 **Available Models:**", ""]
                for opt in options:
                    if isinstance(opt, dict):
                        model_id = opt.get("modelId", "") or opt.get("id", "")
                        model_name = opt.get("name", model_id)
                    else:
                        model_id = str(opt)
                        model_name = model_id
                    
                    if model_id:
                        marker = " ✓" if model_id == current_model else ""
                        if model_id == model_name:
                            lines.append(f"• {model_id}{marker}")
                        else:
                            lines.append(f"• {model_id} - {model_name}{marker}")
                lines.append("")
                lines.append(f"Current: **{current_model}**")
                lines.append("💡 Use /model model_name to switch")
                self._send_text(platform, chat_id, "\n".join(lines))
            else:
                self._send_text(platform, chat_id, "❓ Cannot get model list")
            return

        # Validate and switch
        options = acp.get_model_options(session_id)
        valid_model_ids = set()
        if options:
            for opt in options:
                if isinstance(opt, dict):
                    mid = opt.get("modelId", "") or opt.get("id", "")
                    if mid:
                        valid_model_ids.add(mid)
                else:
                    valid_model_ids.add(str(opt))
        
        if valid_model_ids and model_arg not in valid_model_ids:
            self._send_text(platform, chat_id, f"❌ Invalid model: {model_arg}\n\n💡 Use /model to see available models")
            return

        try:
            acp.session_set_model(session_id, model_arg)
            self._send_text(platform, chat_id, f"✅ Switched to model: **{model_arg}**")
        except Exception as e:
            log.error("[Gateway] [%s] Set model failed: %s", key, e)
            self._send_text(platform, chat_id, f"❌ Switch failed: {e}")

    def _handle_help_command(self, platform: str, chat_id: str):
        """Show help."""
        help_text = """📚 **Available Commands:**

**Agent:**
• /agent - List available agents
• /agent agent_name - Switch agent

**Model:**
• /model - List available models
• /model model_name - Switch model

**Other:**
• /help - Show this help"""
        self._send_text(platform, chat_id, help_text)

    def _process_message(self, platform: str, chat_id: str, key: str, text: str, images: list[tuple[str, str]] | None = None):
        """Process a message, queuing if busy."""
        with self._processing_lock:
            if self._processing.get(key):
                with self._queue_lock:
                    if key not in self._message_queue:
                        self._message_queue[key] = []
                    queue = self._message_queue[key]
                    if len(queue) < 5:
                        queue.append((text, images))
                        self._send_text(platform, chat_id, f"📥 Queued #{len(queue)}\n💡 Send 'cancel' to clear")
                        log.info("[Gateway] [%s] Message queued, size: %d", key, len(queue))
                    else:
                        self._send_text(platform, chat_id, "⚠️ Queue full (max 5)")
                return
            self._processing[key] = True

        try:
            self._process_message_loop(platform, chat_id, key, text, images)
        finally:
            with self._processing_lock:
                self._processing[key] = False

    def _process_message_loop(self, platform: str, chat_id: str, key: str, text: str, images: list[tuple[str, str]] | None = None):
        """Process current and queued messages."""
        while True:
            self._process_single_message(platform, chat_id, key, text, images)
            
            with self._queue_lock:
                queue = self._message_queue.get(key, [])
                if not queue:
                    if key in self._message_queue:
                        del self._message_queue[key]
                    break
                text, images = queue.pop(0)
                log.info("[Gateway] [%s] Processing queued, remaining: %d", key, len(queue))

    def _process_single_message(self, platform: str, chat_id: str, key: str, text: str, images: list[tuple[str, str]] | None = None):
        """Process a single message."""
        card_handle = None
        try:
            card_handle = self._send_card(platform, chat_id, "🤔 Thinking...")

            try:
                acp = self._ensure_acp(platform)
            except Exception as e:
                log.error("[Gateway] [%s] Failed to start kiro-cli: %s", platform, e)
                error_msg = f"❌ Failed to start Kiro: {e}"
                if card_handle:
                    self._update_card(platform, card_handle, error_msg)
                else:
                    self._send_text(platform, chat_id, error_msg)
                return

            session_id = self._get_or_create_session(platform, chat_id, key, acp)
            self._session_to_key[session_id] = key

            # Send to Kiro
            max_retries = 3
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    result = acp.session_prompt(session_id, text, images=images)
                    break
                except RuntimeError as e:
                    last_error = e
                    error_str = str(e)
                    if "ValidationException" in error_str or "Internal error" in error_str:
                        if attempt < max_retries - 1:
                            log.warning("[Gateway] [%s] Transient error (attempt %d/%d): %s", platform, attempt + 1, max_retries, e)
                            time.sleep(1)
                            continue
                    raise
            else:
                raise last_error

            # Update activity
            with self._acp_lock:
                self._last_activity[platform] = time.time()

            response = format_response(result)
            if card_handle:
                self._update_card(platform, card_handle, response)
            else:
                self._send_text(platform, chat_id, response)

        except Exception as e:
            log.exception("[Gateway] [%s] Error: %s", platform, e)
            error_msg = str(e)
            if "cancelled" in error_msg.lower():
                error_text = "⏹️ Operation cancelled"
            else:
                error_text = f"❌ Error: {e}"
            
            if card_handle:
                self._update_card(platform, card_handle, error_text)
            else:
                self._send_text(platform, chat_id, error_text)
            
            with self._contexts_lock:
                self._contexts.pop(key, None)
            
            # Check if this platform's ACP died
            with self._acp_lock:
                acp = self._acp_clients.get(platform)
                if acp is not None and not acp.is_running():
                    log.warning("[Gateway] [%s] kiro-cli died, will restart on next message", platform)
                    self._acp_clients.pop(platform, None)
                    self._last_activity.pop(platform, None)

    def _get_or_create_session(self, platform: str, chat_id: str, key: str, acp: ACPClient) -> str:
        """Get or create ACP session for a chat."""
        # Get working directory based on workspace_mode (fixed or per_chat)
        work_dir = self._config.get_session_cwd(platform, chat_id)
        os.makedirs(work_dir, exist_ok=True)

        with self._contexts_lock:
            ctx = self._contexts.get(key)
            if ctx and ctx.session_id:
                try:
                    acp.session_load(ctx.session_id, work_dir)
                    log.info("[Gateway] [%s] Loaded session", key)
                    return ctx.session_id
                except Exception as e:
                    log.warning("[Gateway] [%s] Failed to load session: %s", key, e)

        session_id, modes = acp.session_new(work_dir)
        log.info("[Gateway] [%s] Created session %s (cwd: %s)", key, session_id, work_dir)

        with self._contexts_lock:
            self._contexts[key] = ChatContext(
                chat_id=chat_id,
                platform=platform,
                session_id=session_id,
            )
        self._session_to_key[session_id] = key
        return session_id
