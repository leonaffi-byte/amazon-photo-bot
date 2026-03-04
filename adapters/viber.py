"""
Viber adapter — implements PlatformAdapter using the Viber Bot API
(https://chatapi.viber.com/pa/).

Webhook routes are exposed via handle_webhook and mounted by webhook_server.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Callable, Awaitable

import aiohttp
from aiohttp import web

import config
from adapters.base import Button, MessageRef, PlatformAdapter

logger = logging.getLogger(__name__)

VIBER_API_BASE = "https://chatapi.viber.com/pa"


class ViberAdapter(PlatformAdapter):
    """PlatformAdapter implementation for Viber Bot API."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "viber"
    max_caption_length: int = 512
    max_message_length: int = 7000
    supports_photo_edit: bool = False
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 6
    supports_carousels: bool = False

    def __init__(
        self,
        on_photo: Callable[["ViberAdapter", Any], Awaitable[None]],
        on_callback: Callable[["ViberAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["ViberAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["ViberAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command

        self._token: str = getattr(config, "VIBER_TOKEN", "")
        self._bot_name: str = getattr(config, "VIBER_BOT_NAME", "Bot")
        self._webhook_url: str = getattr(config, "VIBER_WEBHOOK_URL", "")
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        # Register webhook with Viber
        if self._webhook_url:
            data = {
                "url": self._webhook_url,
                "event_types": [
                    "delivered",
                    "seen",
                    "failed",
                    "message",
                    "subscribed",
                    "unsubscribed",
                    "conversation_started",
                ],
                "send_name": True,
                "send_photo": True,
            }
            resp = await self._viber_api("set_webhook", data)
            logger.info("Viber webhook registered: %s", resp)
        logger.info("Viber adapter started (bot_name=%s)", self._bot_name)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Viber adapter stopped.")

    # ── Viber API helper ───────────────────────────────────────────────────

    async def _viber_api(self, method: str, data: dict) -> dict:
        """POST to the Viber Bot API."""
        assert self._session is not None
        url = f"{VIBER_API_BASE}/{method}"
        data["auth_token"] = self._token
        async with self._session.post(url, json=data) as resp:
            body = await resp.json()
            if body.get("status") != 0:
                logger.error("Viber API error %s: %s", method, body)
            return body

    # ── Webhook signature verification ─────────────────────────────────────

    def _verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify X-Viber-Content-Signature (HMAC-SHA256 with token as key)."""
        if not signature:
            return False
        expected = hmac.new(
            self._token.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Webhook handler ────────────────────────────────────────────────────

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """POST handler — incoming Viber events."""
        payload = await request.read()
        signature = request.headers.get("X-Viber-Content-Signature", "")

        if self._token and not self._verify_signature(payload, signature):
            logger.warning("Viber webhook: invalid signature.")
            return web.Response(text="Invalid signature", status=403)

        body = await request.json()
        event_type = body.get("event", "")

        try:
            if event_type == "message":
                await self._process_message(body)
            elif event_type == "conversation_started":
                # User opened chat — can send a welcome message
                logger.info(
                    "Viber conversation started: %s",
                    body.get("user", {}).get("id"),
                )
        except Exception:
            logger.exception("Error processing Viber webhook")

        return web.Response(text="OK", status=200)

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_message(self, body: dict) -> None:
        """Route an incoming Viber message to the appropriate callback."""
        sender = body.get("sender", {})
        sender_id = sender.get("id", "")
        message = body.get("message", {})
        msg_type = message.get("type", "")

        event = {
            "body": body,
            "sender": sender,
            "user_id": sender_id,
            "message": message,
        }

        if msg_type == "picture":
            event["image_url"] = message.get("media", "")
            await self._on_photo(self, event)

        elif message.get("tracking_data"):
            # Button callback — tracking_data carries the callback payload
            chat_id = self.get_chat_id(event)
            await self._on_callback(self, sender_id, chat_id, message["tracking_data"], event)

        elif msg_type == "text":
            text = message.get("text", "")
            chat_id = self.get_chat_id(event)
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                command = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                await self._on_command(self, sender_id, chat_id, command, args, event)
            else:
                await self._on_text(self, sender_id, chat_id, text, event)

    # ── Incoming helpers ───────────────────────────────────────────────────

    async def download_photo(self, event: Any) -> bytes:
        image_url = event.get("image_url", "")
        if not image_url:
            raise ValueError("No image URL in event")
        assert self._session is not None
        async with self._session.get(image_url) as resp:
            return await resp.read()

    def get_user_id(self, event: Any) -> str:
        return event["user_id"]

    def get_platform_user_id(self, event: Any) -> str:
        return f"viber:{event['user_id']}"

    def get_chat_id(self, event: Any) -> str:
        return event["user_id"]

    # ── Keyboard builder ───────────────────────────────────────────────────

    def _build_keyboard(self, buttons: list[list[Button]]) -> dict | None:
        """Build a Viber keyboard object from button rows."""
        if not buttons:
            return None
        viber_buttons: list[dict[str, Any]] = []
        for row in buttons:
            columns = max(1, 6 // len(row)) if row else 6
            for btn in row:
                viber_btn: dict[str, Any] = {
                    "Columns": columns,
                    "Rows": 1,
                    "Text": btn.label,
                    "TextSize": "regular",
                }
                if btn.url:
                    viber_btn["ActionType"] = "open-url"
                    viber_btn["ActionBody"] = btn.url
                else:
                    viber_btn["ActionType"] = "reply"
                    viber_btn["ActionBody"] = btn.callback_data or btn.label
                viber_buttons.append(viber_btn)
        return {
            "Type": "keyboard",
            "Buttons": viber_buttons,
        }

    # ── Outgoing: text ─────────────────────────────────────────────────────

    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        data: dict[str, Any] = {
            "receiver": chat_id,
            "type": "text",
            "text": text,
            "sender": {"name": self._bot_name},
        }
        keyboard = self._build_keyboard(buttons) if buttons else None
        if keyboard:
            data["keyboard"] = keyboard

        resp = await self._viber_api("send_message", data)
        msg_token = str(resp.get("message_token", ""))
        return MessageRef(platform="viber", chat_id=chat_id, message_id=msg_token, raw=resp)

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        # Viber does not support editing — send a new message instead
        await self.send_text(ref.chat_id, text, buttons)

    # ── Outgoing: photo ────────────────────────────────────────────────────

    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        if isinstance(image, bytes):
            # Viber requires a URL — for bytes we would need to upload first.
            # For now, send as text with a note. A real implementation would
            # upload to an external host and use the resulting URL.
            logger.warning(
                "Viber send_photo with bytes not fully supported; sending caption only."
            )
            return await self.send_text(chat_id, caption or "(image)", buttons)

        data: dict[str, Any] = {
            "receiver": chat_id,
            "type": "picture",
            "media": image,
            "text": caption[:self.max_caption_length] if caption else "",
            "sender": {"name": self._bot_name},
        }
        keyboard = self._build_keyboard(buttons) if buttons else None
        if keyboard:
            data["keyboard"] = keyboard

        resp = await self._viber_api("send_message", data)
        msg_token = str(resp.get("message_token", ""))
        return MessageRef(platform="viber", chat_id=chat_id, message_id=msg_token, raw=resp)

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        # Viber does not support bot message deletion — no-op
        pass
