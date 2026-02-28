"""Feishu (Lark) chat adapter implementation."""

import base64
import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    P2ImMessageReceiveV1,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from .base import ChatAdapter, ChatType, IncomingMessage, CardHandle, MessageCallback

log = logging.getLogger(__name__)


class FeishuAdapter(ChatAdapter):
    """Feishu (Lark) implementation of ChatAdapter."""

    def __init__(self, app_id: str, app_secret: str, bot_name: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._bot_name = bot_name
        self._message_callback: MessageCallback | None = None
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._ws_client = None
        self._running = False

    @property
    def platform_name(self) -> str:
        return "feishu"

    def start(self, message_callback: MessageCallback) -> None:
        """Start WebSocket connection (blocking)."""
        self._message_callback = message_callback
        self._running = True
        
        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_event)
            .build()
        )
        self._ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        log.info("[Feishu] Starting WebSocket connection...")
        self._ws_client.start()

    def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        # Note: lark_oapi WebSocket client doesn't have a clean stop method
        log.info("[Feishu] Adapter stopped")

    def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a text message using card format for better markdown support."""
        handle = self.send_card(chat_id, text)
        return handle.message_id if handle else None

    def send_card(self, chat_id: str, content: str, title: str = "") -> CardHandle | None:
        """Send an interactive card message."""
        card = self._build_card(content, title)
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card, ensure_ascii=False)) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.create(req)
        
        if not resp.success():
            log.error("[Feishu] Send card failed: code=%s msg=%s", resp.code, resp.msg)
            return None
        
        log.info("[Feishu] Card sent successfully to %s", chat_id)
        message_id = resp.data.message_id if resp.data else None
        if message_id:
            return CardHandle(message_id=message_id, chat_id=chat_id)
        return None

    def update_card(self, handle: CardHandle, content: str, title: str = "") -> bool:
        """Update an existing card message."""
        if not handle or not handle.message_id:
            log.warning("[Feishu] Cannot update card: no message_id")
            return False
        
        card = self._build_card(content, title)
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps(card, ensure_ascii=False)) \
            .build()
        req = PatchMessageRequest.builder() \
            .message_id(handle.message_id) \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.patch(req)
        
        if not resp.success():
            log.error("[Feishu] Update card failed: code=%s msg=%s", resp.code, resp.msg)
            return False
        
        log.info("[Feishu] Card updated: %s", handle.message_id)
        return True

    def _build_card(self, markdown_text: str, title: str = "") -> dict:
        """Build a Feishu interactive card from markdown text."""
        elements = []
        parts = markdown_text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    elements.append({"tag": "markdown", "content": part.strip()})
            else:
                lines = part.split("\n", 1)
                lang = lines[0].strip() if lines else ""
                code = lines[1] if len(lines) > 1 else part
                elements.append({"tag": "markdown", "content": f"```{lang}\n{code.strip()}\n```"})
        
        if not elements:
            elements.append({"tag": "markdown", "content": markdown_text})
        
        card = {"config": {"wide_screen_mode": True}, "elements": elements}
        if title:
            card["header"] = {"title": {"tag": "plain_text", "content": title}}
        return card

    def _download_image(self, message_id: str, image_key: str) -> tuple[bytes, str] | None:
        """Download image from Feishu. Returns (data, mime_type) or None."""
        try:
            req = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(image_key) \
                .type("image") \
                .build()
            resp = self._client.im.v1.message_resource.get(req)
            if not resp.success():
                log.error("[Feishu] Download image failed: %s %s", resp.code, resp.msg)
                return None
            
            data = resp.file.read()
            # Detect mime type from magic bytes
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                mime = "image/png"
            elif data[:2] == b'\xff\xd8':
                mime = "image/jpeg"
            elif data[:6] in (b'GIF87a', b'GIF89a'):
                mime = "image/gif"
            elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                mime = "image/webp"
            else:
                mime = "image/png"
            
            log.info("[Feishu] Downloaded image: %d bytes, %s", len(data), mime)
            return (data, mime)
        except Exception as e:
            log.exception("[Feishu] Download image error: %s", e)
            return None

    def _handle_event(self, data: P2ImMessageReceiveV1):
        """Handle incoming Feishu message event."""
        if not self._message_callback:
            return

        try:
            msg = data.event.message
            sender = data.event.sender

            # Ignore bot messages
            if sender and sender.sender_type == "app":
                return

            chat_id = msg.chat_id
            feishu_chat_type = msg.chat_type  # "p2p" or "group"
            chat_type = ChatType.PRIVATE if feishu_chat_type == "p2p" else ChatType.GROUP
            msg_type = msg.message_type
            message_id = msg.message_id
            user_id = sender.sender_id.user_id if sender and sender.sender_id else ""

            # Check if bot is mentioned (for group chats)
            mentions_bot = False
            mention_map = {}
            if msg.mentions:
                for m in msg.mentions:
                    if m.name == self._bot_name:
                        mentions_bot = True
                    if m.key:
                        mention_map[m.key] = f"@{m.name}" if m.name else ""

            # Group chat: only process if bot is mentioned
            if chat_type == ChatType.GROUP and not mentions_bot:
                return

            text = ""
            images = []

            if msg_type == "text":
                content = json.loads(msg.content)
                text = content.get("text", "").strip()
                # Replace mention placeholders
                for key, name in mention_map.items():
                    if name == f"@{self._bot_name}":
                        text = text.replace(key, "").strip()
                    else:
                        text = text.replace(key, name)

            elif msg_type == "image":
                content = json.loads(msg.content)
                image_key = content.get("image_key", "")
                if image_key:
                    img_data = self._download_image(message_id, image_key)
                    if img_data:
                        data, mime = img_data
                        b64 = base64.b64encode(data).decode("ascii")
                        images.append((b64, mime))

            elif msg_type == "post":
                content = json.loads(msg.content)
                parts = []
                for lang_content in content.values():
                    if isinstance(lang_content, dict):
                        for item in lang_content.get("content", []):
                            if isinstance(item, list):
                                for elem in item:
                                    if isinstance(elem, dict):
                                        if elem.get("tag") == "text":
                                            parts.append(elem.get("text", ""))
                                        elif elem.get("tag") == "img":
                                            image_key = elem.get("image_key", "")
                                            if image_key:
                                                img_data = self._download_image(message_id, image_key)
                                                if img_data:
                                                    data, mime = img_data
                                                    b64 = base64.b64encode(data).decode("ascii")
                                                    images.append((b64, mime))
                text = " ".join(parts).strip()
                for key, name in mention_map.items():
                    if name == f"@{self._bot_name}":
                        text = text.replace(key, "").strip()
                    else:
                        text = text.replace(key, name)
            else:
                log.debug("[Feishu] Ignoring message type: %s", msg_type)
                return

            if not text and not images:
                return

            log.info("[Feishu] Message from %s (%s): text=%s, images=%d",
                     chat_id, feishu_chat_type, text[:50] if text else "(none)", len(images))

            # Create normalized message
            incoming = IncomingMessage(
                chat_id=chat_id,
                chat_type=chat_type,
                user_id=user_id,
                text=text,
                images=images if images else None,
                raw={
                    "_platform": "feishu",
                    "message_id": message_id,
                    "mentions_bot": mentions_bot,
                },
            )
            self._message_callback(incoming)

        except Exception as e:
            log.exception("[Feishu] Handle event error: %s", e)
