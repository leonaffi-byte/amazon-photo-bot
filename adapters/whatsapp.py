"""
WhatsApp adapter — implements PlatformAdapter using the WhatsApp Cloud API
(part of Meta Graph API).

Webhook routes are exposed via handle_webhook / handle_webhook_verify and
mounted by webhook_server.py.
"""
from __future__ import annotations

import logging
import time as _time
from typing import Any, Callable, Awaitable

import aiohttp
from fastapi import Request
from fastapi.responses import PlainTextResponse

import config
import database
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
        self._template_sent: dict[str, bool] = {}

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

    async def handle_webhook_verify(self, request: Request) -> PlainTextResponse:
        """GET handler — Meta webhook verification challenge."""
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            logger.info("WhatsApp webhook verified.")
            return PlainTextResponse(challenge or "", status_code=200)
        logger.warning("WhatsApp webhook verification failed (bad token).")
        return PlainTextResponse("Forbidden", status_code=403)

    async def handle_webhook(self, request: Request) -> PlainTextResponse:
        """POST handler — incoming WhatsApp messages."""
        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if self._app_secret and not verify_webhook_signature(payload, signature, self._app_secret):
            logger.warning("WhatsApp webhook: invalid signature.")
            return PlainTextResponse("Invalid signature", status_code=403)

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
        return PlainTextResponse("OK")

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_message(self, msg: dict, value: dict) -> None:
        """Route an incoming WhatsApp message to the appropriate callback."""
        user_id = msg.get("from", "")
        msg_type = msg.get("type", "")
        user_key = f"whatsapp:{user_id}"

        event = {
            "message": msg,
            "user_id": user_id,
            "value": value,
        }

        # Record last message timestamp for 24h window tracking
        await database.update_wa_last_msg_at(user_key, _time.time())
        # Reset template-sent flag so next closed-window event fires a new template
        self._template_sent.pop(user_id, None)

        # Check for opt-in callback first (before opt-in gate blocks it)
        if msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype = interactive.get("type", "")
            if itype == "button_reply":
                reply_id = interactive["button_reply"]["id"]
                if reply_id == "optin:agree":
                    await database.set_wa_opt_in(user_key, True)
                    await self.send_text(
                        user_id,
                        "Thank you! You can now send product photos and I'll find them on Amazon. "
                        "Just send any photo to get started.",
                    )
                    return

        # Allow slash commands through BEFORE opt-in gate (per locked decision:
        # "Full command set matching Telegram (/start, /help, /language, /providers)")
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
            if text.startswith("/"):
                chat_id = self.get_chat_id(event)
                parts = text.split(maxsplit=1)
                command = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                await self._on_command(self, user_id, chat_id, command, args, event)
                return

        # Opt-in gate: block all messages until user consents
        if not await database.get_wa_opt_in(user_key):
            await self._send_opt_in_prompt(user_id)
            return

        # Existing routing logic (image, interactive, text) — user has opted in
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
            await self._on_text(self, user_id, chat_id, text, event)

    async def _send_opt_in_prompt(self, chat_id: str) -> None:
        """Send the opt-in consent prompt to a new user."""
        assert self._session is not None
        data = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": (
                        "Welcome to Amazon Photo Bot!\n\n"
                        "Send a photo of any product to find it on Amazon with Israel shipping info.\n\n"
                        "By tapping 'I agree' you confirm you wish to receive product search results from us."
                    )
                },
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": "optin:agree", "title": "I agree"},
                        }
                    ]
                },
            },
        }
        await send_graph_api(
            f"{self._phone_number_id}/messages",
            self._token, data, self._session,
        )

    async def _is_window_open(self, user_key: str) -> bool:
        """Return True if the 24-hour customer care window is still open."""
        ts = await database.get_wa_last_msg_at(user_key)
        if ts is None:
            return False
        return (_time.time() - ts) < 86400

    async def _guard_window(self, chat_id: str) -> "MessageRef | None":
        """Check the 24-hour conversation window before dispatching outbound messages.

        Returns None if the window is open (caller should proceed normally).
        Returns a no-op MessageRef if the window is closed (caller should return it immediately).

        When the window is first detected as closed, fires send_template() exactly once
        per chat_id to re-engage the user via a pre-approved Meta template. Subsequent
        calls for the same closed window return the no-op MessageRef silently.
        """
        user_key = f"whatsapp:{chat_id}"
        if await self._is_window_open(user_key):
            return None

        # Window is closed — check whether we already sent the template for this user
        if not self._template_sent.get(chat_id, False):
            lang_code = await database.get_user_lang(user_key) or "en"
            await self.send_template(chat_id, template_name="product_results_ready", lang_code=lang_code)
            self._template_sent[chat_id] = True
            logger.info("WhatsApp 24h window closed for %s -- sent template re-engagement", chat_id)
        else:
            logger.debug("WhatsApp 24h window closed for %s -- no-op (template already sent)", chat_id)

        return MessageRef(platform="whatsapp", chat_id=chat_id, message_id="", raw=None)

    async def send_list_message(
        self,
        chat_id: str,
        body: str,
        button_label: str,
        sections: list[dict],
    ) -> MessageRef:
        """Send a WhatsApp list-type interactive message (up to 10 rows total)."""
        guard = await self._guard_window(chat_id)
        if guard is not None:
            return guard
        assert self._session is not None
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {
                    "button": button_label[:20],
                    "sections": sections,
                },
            },
        }
        resp = await send_graph_api(
            f"{self._phone_number_id}/messages",
            self._token, data, self._session,
        )
        msg_id = ""
        if "messages" in resp:
            msg_id = resp["messages"][0].get("id", "")
        return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)

    async def send_template(
        self,
        chat_id: str,
        template_name: str = "product_results_ready",
        lang_code: str = "en",
    ) -> MessageRef:
        """Send a pre-approved Meta template message (for use outside 24h window)."""
        assert self._session is not None
        data = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang_code},
            },
        }
        resp = await send_graph_api(
            f"{self._phone_number_id}/messages",
            self._token, data, self._session,
        )
        msg_id = ""
        if "messages" in resp:
            msg_id = resp["messages"][0].get("id", "")
        return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)

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
        guard = await self._guard_window(chat_id)
        if guard is not None:
            return guard
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
        if await self._guard_window(ref.chat_id) is not None:
            return
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
        guard = await self._guard_window(chat_id)
        if guard is not None:
            return guard
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
