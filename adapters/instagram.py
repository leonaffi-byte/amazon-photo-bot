"""
Instagram adapter — implements PlatformAdapter using the Instagram Messaging API
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
    send_graph_api,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)


class InstagramAdapter(PlatformAdapter):
    """PlatformAdapter implementation for Instagram DMs via Meta Graph API."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "instagram"
    max_caption_length: int = 1000
    max_message_length: int = 1000
    supports_photo_edit: bool = False
    supports_inline_buttons: bool = False  # Instagram uses quick replies instead
    max_buttons_per_row: int = 13
    supports_carousels: bool = False

    def __init__(
        self,
        on_photo: Callable[["InstagramAdapter", Any], Awaitable[None]],
        on_callback: Callable[["InstagramAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["InstagramAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["InstagramAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command

        self._token: str = getattr(config, "INSTAGRAM_TOKEN", "")
        self._page_id: str = getattr(config, "INSTAGRAM_PAGE_ID", "")
        self._app_secret: str = getattr(config, "META_APP_SECRET", "")
        self._verify_token: str = getattr(config, "META_VERIFY_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("Instagram adapter started (page_id=%s)", self._page_id)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Instagram adapter stopped.")

    # ── Webhook handlers (mounted by webhook_server.py) ────────────────────

    async def handle_webhook_verify(self, request: web.Request) -> web.Response:
        """GET handler — Meta webhook verification challenge."""
        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            logger.info("Instagram webhook verified.")
            return web.Response(text=challenge or "", status=200)
        logger.warning("Instagram webhook verification failed (bad token).")
        return web.Response(text="Forbidden", status=403)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """POST handler — incoming Instagram messages."""
        payload = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if self._app_secret and not verify_webhook_signature(payload, signature, self._app_secret):
            logger.warning("Instagram webhook: invalid signature.")
            return web.Response(text="Invalid signature", status=403)

        body = await request.json()

        try:
            for entry in body.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    await self._process_message(messaging_event)
        except Exception:
            logger.exception("Error processing Instagram webhook")

        return web.Response(text="OK", status=200)

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_message(self, messaging_event: dict) -> None:
        """Route an incoming Instagram message to the appropriate callback."""
        sender_id = messaging_event.get("sender", {}).get("id", "")
        message = messaging_event.get("message", {})

        event = {
            "messaging_event": messaging_event,
            "user_id": sender_id,
            "message": message,
        }

        # Check for attachments with image type
        attachments = message.get("attachments", [])
        for attachment in attachments:
            if attachment.get("type") == "image":
                event["image_url"] = attachment.get("payload", {}).get("url", "")
                await self._on_photo(self, event)
                return

        # Quick reply callback
        quick_reply = message.get("quick_reply", {})
        if quick_reply.get("payload"):
            chat_id = self.get_chat_id(event)
            await self._on_callback(self, sender_id, chat_id, quick_reply["payload"], event)
            return

        # Text message
        text = message.get("text", "")
        if text:
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
        headers = {"Authorization": f"Bearer {self._token}"}
        async with self._session.get(image_url, headers=headers) as resp:
            return await resp.read()

    def get_user_id(self, event: Any) -> str:
        return event["user_id"]

    def get_platform_user_id(self, event: Any) -> str:
        return f"instagram:{event['user_id']}"

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
        endpoint = f"{self._page_id}/messages"

        if buttons:
            # Instagram uses quick_replies (max 13)
            flat_buttons = [b for row in buttons for b in row][:13]
            data = {
                "recipient": {"id": chat_id},
                "message": {
                    "text": text,
                    "quick_replies": [
                        {
                            "content_type": "text",
                            "title": btn.label[:20],
                            "payload": btn.callback_data or btn.label,
                        }
                        for btn in flat_buttons
                    ],
                },
            }
        else:
            data = {
                "recipient": {"id": chat_id},
                "message": {"text": text},
            }

        resp = await send_graph_api(endpoint, self._token, data, self._session)
        msg_id = resp.get("message_id", "")
        return MessageRef(platform="instagram", chat_id=chat_id, message_id=msg_id, raw=resp)

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        # Instagram does not support editing — send a new message instead
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
        endpoint = f"{self._page_id}/messages"

        if isinstance(image, str):
            data = {
                "recipient": {"id": chat_id},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": image},
                    }
                },
            }
        else:
            # Upload bytes as form data
            from adapters.shared_meta import GRAPH_API_BASE
            url = f"{GRAPH_API_BASE}/{endpoint}"
            headers = {"Authorization": f"Bearer {self._token}"}
            form = aiohttp.FormData()
            form.add_field("recipient", f'{{"id":"{chat_id}"}}')
            form.add_field(
                "message",
                '{"attachment":{"type":"image","payload":{"is_reusable":true}}}',
            )
            form.add_field("filedata", image, filename="photo.jpg", content_type="image/jpeg")
            async with self._session.post(url, data=form, headers=headers) as resp:
                result = await resp.json()
            msg_id = result.get("message_id", "")
            ref = MessageRef(platform="instagram", chat_id=chat_id, message_id=msg_id, raw=result)
            # Send caption as separate text if present
            if caption:
                await self.send_text(chat_id, caption[:self.max_caption_length], buttons)
            return ref

        resp = await send_graph_api(endpoint, self._token, data, self._session)
        msg_id = resp.get("message_id", "")
        ref = MessageRef(platform="instagram", chat_id=chat_id, message_id=msg_id, raw=resp)

        # Send caption as follow-up text (Instagram image messages lack inline captions)
        if caption:
            await self.send_text(chat_id, caption[:self.max_caption_length], buttons)

        return ref

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        # Instagram does not support bot message deletion — no-op
        pass
