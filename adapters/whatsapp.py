"""
WhatsApp adapter — implements PlatformAdapter using the WhatsApp Cloud API
(part of Meta Graph API).

Webhook routes are exposed via handle_webhook / handle_webhook_verify and
mounted by webhook_server.py.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

import aiohttp
from aiohttp import web

import config
from adapters.base import Button, MessageRef, PlatformAdapter
from adapters.shared_meta import (
    download_media,
    send_graph_api,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)


class WhatsAppAdapter(PlatformAdapter):
    """PlatformAdapter implementation for WhatsApp Cloud API."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "whatsapp"
    max_caption_length: int = 1024
    max_message_length: int = 4096
    supports_photo_edit: bool = False
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 1   # WhatsApp buttons are full-width
    max_buttons_total: int = 3
    supports_carousels: bool = False

    def __init__(
        self,
        on_photo: Callable[["WhatsAppAdapter", Any], Awaitable[None]],
        on_callback: Callable[["WhatsAppAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["WhatsAppAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["WhatsAppAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command

        self._token: str = getattr(config, "WHATSAPP_TOKEN", "")
        self._phone_number_id: str = getattr(config, "WHATSAPP_PHONE_NUMBER_ID", "")
        self._verify_token: str = getattr(config, "WHATSAPP_VERIFY_TOKEN", "")
        self._app_secret: str = getattr(config, "META_APP_SECRET", "")
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("WhatsApp adapter started (phone_number_id=%s)", self._phone_number_id)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("WhatsApp adapter stopped.")

    # ── Webhook handlers (mounted by webhook_server.py) ────────────────────

    async def handle_webhook_verify(self, request: web.Request) -> web.Response:
        """GET handler — Meta webhook verification challenge."""
        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            logger.info("WhatsApp webhook verified.")
            return web.Response(text=challenge or "", status=200)
        logger.warning("WhatsApp webhook verification failed (bad token).")
        return web.Response(text="Forbidden", status=403)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """POST handler — incoming WhatsApp messages."""
        payload = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if self._app_secret and not verify_webhook_signature(payload, signature, self._app_secret):
            logger.warning("WhatsApp webhook: invalid signature.")
            return web.Response(text="Invalid signature", status=403)

        body = await request.json()

        # WhatsApp Cloud API nests messages deeply
        try:
            for entry in body.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    for msg in messages:
                        await self._process_message(msg, value)
        except Exception:
            logger.exception("Error processing WhatsApp webhook")

        # Always return 200 to prevent retries
        return web.Response(text="OK", status=200)

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_message(self, msg: dict, value: dict) -> None:
        """Route an incoming WhatsApp message to the appropriate callback."""
        user_id = msg.get("from", "")
        msg_type = msg.get("type", "")

        event = {
            "message": msg,
            "user_id": user_id,
            "value": value,
        }

        if msg_type == "image":
            await self._on_photo(self, event)

        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype = interactive.get("type", "")
            if itype == "button_reply":
                reply_id = interactive["button_reply"]["id"]
                chat_id = self.get_chat_id(event)
                await self._on_callback(self, user_id, chat_id, reply_id, event)
            elif itype == "list_reply":
                reply_id = interactive["list_reply"]["id"]
                chat_id = self.get_chat_id(event)
                await self._on_callback(self, user_id, chat_id, reply_id, event)

        elif msg_type == "text":
            text = msg.get("text", {}).get("body", "")
            chat_id = self.get_chat_id(event)
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                command = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                await self._on_command(self, user_id, chat_id, command, args, event)
            else:
                await self._on_text(self, user_id, chat_id, text, event)

    # ── Incoming helpers ───────────────────────────────────────────────────

    async def download_photo(self, event: Any) -> bytes:
        msg = event["message"]
        image_id = msg["image"]["id"]
        assert self._session is not None
        return await download_media(image_id, self._token, self._session)

    def get_user_id(self, event: Any) -> str:
        return event["user_id"]

    def get_platform_user_id(self, event: Any) -> str:
        user_id = event["user_id"]
        return f"whatsapp:{user_id}"

    def get_chat_id(self, event: Any) -> str:
        return event["user_id"]

    # ── Outgoing: text ─────────────────────────────────────────────────────

    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        assert self._session is not None
        endpoint = f"{self._phone_number_id}/messages"

        if buttons:
            # Flatten button rows — WhatsApp supports max 3 reply buttons
            flat_buttons = [b for row in buttons for b in row][:3]
            data = {
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": btn.callback_data or btn.label,
                                    "title": btn.label[:20],
                                },
                            }
                            for btn in flat_buttons
                        ]
                    },
                },
            }
        else:
            data = {
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "text",
                "text": {"body": text},
            }

        resp = await send_graph_api(endpoint, self._token, data, self._session)
        msg_id = ""
        if "messages" in resp:
            msg_id = resp["messages"][0].get("id", "")
        return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        # WhatsApp does not support editing — send a new message instead
        await self.send_text(ref.chat_id, text, buttons)

    # ── Outgoing: photo ────────────────────────────────────────────────────

    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        assert self._session is not None
        endpoint = f"{self._phone_number_id}/messages"

        if isinstance(image, str):
            # URL-based image
            data = {
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "image",
                "image": {"link": image, "caption": caption[:self.max_caption_length]},
            }
            resp = await send_graph_api(endpoint, self._token, data, self._session)
        else:
            # For bytes, upload first via the media endpoint
            upload_endpoint = f"{self._phone_number_id}/media"
            form = aiohttp.FormData()
            form.add_field("messaging_product", "whatsapp")
            form.add_field("type", "image/jpeg")
            form.add_field("file", image, filename="photo.jpg", content_type="image/jpeg")

            from adapters.shared_meta import GRAPH_API_BASE
            url = f"{GRAPH_API_BASE}/{upload_endpoint}"
            headers = {"Authorization": f"Bearer {self._token}"}
            async with self._session.post(url, data=form, headers=headers) as upload_resp:
                upload_result = await upload_resp.json()
                media_id = upload_result.get("id", "")

            data = {
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "image",
                "image": {"id": media_id, "caption": caption[:self.max_caption_length]},
            }
            resp = await send_graph_api(endpoint, self._token, data, self._session)

        msg_id = ""
        if "messages" in resp:
            msg_id = resp["messages"][0].get("id", "")
        ref = MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)

        # WhatsApp cannot attach buttons to images — send follow-up interactive message
        if buttons:
            flat_buttons = [b for row in buttons for b in row][:3]
            await self.send_text(chat_id, caption[:20] or "Options:", buttons=[[b] for b in flat_buttons])

        return ref

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        # WhatsApp does not support bot message deletion — no-op
        pass
