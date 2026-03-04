"""
LINE adapter — implements PlatformAdapter using the LINE Messaging API.

Webhook routes are exposed via handle_webhook and mounted by webhook_server.py.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Callable, Awaitable

import aiohttp
from aiohttp import web

import config
from adapters.base import Button, CarouselItem, MessageRef, PlatformAdapter

logger = logging.getLogger(__name__)

LINE_API_BASE = "https://api.line.me/v2/bot"
LINE_DATA_API_BASE = "https://api-data.line.me/v2/bot"


class LineAdapter(PlatformAdapter):
    """PlatformAdapter implementation for LINE Messaging API."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "line"
    max_caption_length: int = 2000
    max_message_length: int = 5000
    supports_photo_edit: bool = False
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 3
    supports_carousels: bool = True

    def __init__(
        self,
        on_photo: Callable[["LineAdapter", Any], Awaitable[None]],
        on_callback: Callable[["LineAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["LineAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["LineAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command

        self._channel_secret: str = getattr(config, "LINE_CHANNEL_SECRET", "")
        self._channel_token: str = getattr(config, "LINE_CHANNEL_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("LINE adapter started.")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("LINE adapter stopped.")

    # ── Webhook signature verification ─────────────────────────────────────

    def _verify_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify X-Line-Signature header.
        HMAC-SHA256 of body with channel secret, base64-encoded.
        """
        if not signature:
            return False
        expected = base64.b64encode(
            hmac.new(
                self._channel_secret.encode(),
                payload,
                hashlib.sha256,
            ).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)

    # ── Webhook handler ────────────────────────────────────────────────────

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """POST handler — incoming LINE events."""
        payload = await request.read()
        signature = request.headers.get("X-Line-Signature", "")

        if self._channel_secret and not self._verify_signature(payload, signature):
            logger.warning("LINE webhook: invalid signature.")
            return web.Response(text="Invalid signature", status=403)

        body = await request.json()

        try:
            for event in body.get("events", []):
                await self._process_event(event)
        except Exception:
            logger.exception("Error processing LINE webhook")

        return web.Response(text="OK", status=200)

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_event(self, line_event: dict) -> None:
        """Route an incoming LINE event to the appropriate callback."""
        event_type = line_event.get("type", "")
        source = line_event.get("source", {})
        user_id = source.get("userId", "")

        event = {
            "line_event": line_event,
            "user_id": user_id,
            "source": source,
        }

        if event_type == "message":
            message = line_event.get("message", {})
            msg_type = message.get("type", "")
            event["message"] = message

            if msg_type == "image":
                event["message_id"] = message.get("id", "")
                await self._on_photo(self, event)

            elif msg_type == "text":
                text = message.get("text", "")
                chat_id = self.get_chat_id(event)
                if text.startswith("/"):
                    parts = text.split(maxsplit=1)
                    command = parts[0]
                    args = parts[1] if len(parts) > 1 else ""
                    await self._on_command(self, user_id, chat_id, command, args, event)
                else:
                    await self._on_text(self, user_id, chat_id, text, event)

        elif event_type == "postback":
            postback_data = line_event.get("postback", {}).get("data", "")
            chat_id = self.get_chat_id(event)
            await self._on_callback(self, user_id, chat_id, postback_data, event)

    # ── Incoming helpers ───────────────────────────────────────────────────

    async def download_photo(self, event: Any) -> bytes:
        message_id = event.get("message_id", "")
        if not message_id:
            message_id = event.get("message", {}).get("id", "")
        if not message_id:
            raise ValueError("No message_id in event for photo download")
        assert self._session is not None
        url = f"{LINE_DATA_API_BASE}/message/{message_id}/content"
        headers = {"Authorization": f"Bearer {self._channel_token}"}
        async with self._session.get(url, headers=headers) as resp:
            return await resp.read()

    def get_user_id(self, event: Any) -> str:
        return event["user_id"]

    def get_platform_user_id(self, event: Any) -> str:
        return f"line:{event['user_id']}"

    def get_chat_id(self, event: Any) -> str:
        # LINE: use group/room ID if available, else user ID
        source = event.get("source", {})
        return source.get("groupId") or source.get("roomId") or event["user_id"]

    # ── LINE API helper ────────────────────────────────────────────────────

    async def _line_api(self, endpoint: str, data: dict) -> dict:
        """POST JSON to the LINE Messaging API."""
        assert self._session is not None
        url = f"{LINE_API_BASE}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._channel_token}",
            "Content-Type": "application/json",
        }
        async with self._session.post(url, json=data, headers=headers) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error("LINE API error %s %s: %s", resp.status, endpoint, body)
                return {}
            # LINE push message returns 200 with empty body on success
            if resp.content_length and resp.content_length > 0:
                return await resp.json()
            return {}

    # ── Button builders ────────────────────────────────────────────────────

    def _build_button_actions(self, buttons: list[list[Button]]) -> list[dict]:
        """Flatten button rows into LINE action objects."""
        actions = []
        for row in buttons:
            for btn in row:
                if btn.url:
                    actions.append({
                        "type": "uri",
                        "label": btn.label[:20],
                        "uri": btn.url,
                    })
                else:
                    actions.append({
                        "type": "postback",
                        "label": btn.label[:20],
                        "data": btn.callback_data or btn.label,
                        "displayText": btn.label[:300],
                    })
        return actions

    def _build_button_template(
        self, text: str, buttons: list[list[Button]]
    ) -> dict:
        """Build a LINE buttons template message."""
        actions = self._build_button_actions(buttons)[:4]  # LINE max 4 actions
        return {
            "type": "template",
            "altText": text[:400],
            "template": {
                "type": "buttons",
                "text": text[:160],
                "actions": actions,
            },
        }

    # ── Outgoing: text ─────────────────────────────────────────────────────

    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        if buttons:
            message = self._build_button_template(text, buttons)
        else:
            message = {"type": "text", "text": text}

        data = {"to": chat_id, "messages": [message]}
        resp = await self._line_api("message/push", data)
        return MessageRef(platform="line", chat_id=chat_id, message_id="", raw=resp)

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        # LINE does not support editing — send a new message instead
        await self.send_text(ref.chat_id, text, buttons)

    # ── Outgoing: photo ────────────────────────────────────────────────────

    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        messages: list[dict[str, Any]] = []

        if isinstance(image, str):
            messages.append({
                "type": "image",
                "originalContentUrl": image,
                "previewImageUrl": image,
            })
        else:
            # LINE requires URLs — for bytes we would need to upload externally.
            logger.warning(
                "LINE send_photo with bytes not fully supported; sending caption only."
            )
            if caption:
                return await self.send_text(chat_id, caption, buttons)
            return await self.send_text(chat_id, "(image)", buttons)

        # Add caption as a follow-up text message
        if caption:
            if buttons:
                messages.append(self._build_button_template(caption, buttons))
            else:
                messages.append({
                    "type": "text",
                    "text": caption[:self.max_caption_length],
                })
        elif buttons:
            messages.append(self._build_button_template("Options:", buttons))

        data = {"to": chat_id, "messages": messages[:5]}  # LINE max 5 messages per push
        resp = await self._line_api("message/push", data)
        return MessageRef(platform="line", chat_id=chat_id, message_id="", raw=resp)

    # ── Outgoing: carousel ─────────────────────────────────────────────────

    async def send_carousel(
        self,
        chat_id: str,
        items: list[CarouselItem],
    ) -> list[MessageRef]:
        """Send a LINE carousel template (up to 10 columns)."""
        columns = []
        for item in items[:10]:
            actions: list[dict[str, Any]] = []
            for btn in item.buttons[:3]:
                if btn.url:
                    actions.append({
                        "type": "uri",
                        "label": btn.label[:20],
                        "uri": btn.url,
                    })
                else:
                    actions.append({
                        "type": "postback",
                        "label": btn.label[:20],
                        "data": btn.callback_data or btn.label,
                        "displayText": btn.label[:300],
                    })
            # LINE carousel columns need at least one action
            if not actions:
                actions.append({
                    "type": "uri",
                    "label": "View",
                    "uri": item.url or "https://example.com",
                })

            column: dict[str, Any] = {
                "title": item.title[:40],
                "text": (item.subtitle or item.title)[:60],
                "actions": actions,
            }
            if item.image_url:
                column["thumbnailImageUrl"] = item.image_url
            columns.append(column)

        message = {
            "type": "template",
            "altText": "Product carousel",
            "template": {
                "type": "carousel",
                "columns": columns,
            },
        }

        data = {"to": chat_id, "messages": [message]}
        resp = await self._line_api("message/push", data)
        return [MessageRef(platform="line", chat_id=chat_id, message_id="", raw=resp)]

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        # LINE does not support bot message deletion — no-op
        pass
