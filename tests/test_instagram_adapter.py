"""
Tests for adapters/instagram.py.

Covers:
  - TestOptIn: opt-in gate flow (block new users, accept agree reply, allow opted-in users,
    pass commands through pre-opt-in)
  - TestQuickReplies: send_text with buttons produces quick_replies payload;
    quick reply callback dispatches to _on_callback
  - TestPhotoHandling: image attachment in webhook triggers _on_photo
  - TestWebhookMigration: webhook verify challenge/reject; webhook signature validation
  - TestGraphApiAuth: download_photo includes Bearer token header
  - TestAnnotatedPhoto: adapter.send_photo with bytes calls session.post (form upload)
  - TestTranslation: translator.detect_language is invoked in the photo pipeline
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.instagram import InstagramAdapter
from adapters.base import Button, MessageRef


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_adapter(*, on_photo=None, on_callback=None, on_text=None, on_command=None):
    adapter = InstagramAdapter(
        on_photo=on_photo or AsyncMock(),
        on_callback=on_callback or AsyncMock(),
        on_text=on_text or AsyncMock(),
        on_command=on_command or AsyncMock(),
    )
    adapter._token = "test-token"
    adapter._page_id = "111"
    adapter._app_secret = "test-secret"
    adapter._verify_token = "verify-me"
    adapter._session = AsyncMock()
    return adapter


def _make_request(*, body: bytes = b"{}", headers: dict | None = None, query_params: dict | None = None):
    """Build a minimal mock FastAPI Request."""
    req = MagicMock()
    req.body = AsyncMock(return_value=body)
    req.json = AsyncMock(return_value=json.loads(body) if body else {})
    req.headers = headers or {}
    req.query_params = query_params or {}
    return req


def _make_messaging_event(sender_id: str, message: dict) -> dict:
    return {
        "sender": {"id": sender_id},
        "message": message,
    }


def _webhook_payload(sender_id: str, message: dict) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "messaging": [_make_messaging_event(sender_id, message)]
            }
        ],
    }


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ── TestOptIn ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestOptIn:
    async def test_new_user_gets_opt_in_prompt(self):
        """First-time user (not opted in) triggers _send_opt_in_prompt."""
        adapter = _make_adapter()
        event = _make_messaging_event("user1", {"text": "hello"})

        with (
            patch("database.get_ig_opt_in", AsyncMock(return_value=False)) as mock_get,
            patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "m1"})) as mock_api,
        ):
            await adapter._process_message(event)

        mock_get.assert_awaited_once_with("instagram:user1")
        # send_graph_api called for the opt-in prompt
        assert mock_api.called
        call_data = mock_api.call_args[0][2]  # positional: endpoint, token, data, session
        assert "quick_replies" in call_data["message"]
        payloads = [qr["payload"] for qr in call_data["message"]["quick_replies"]]
        assert "optin:agree" in payloads

    async def test_agree_quick_reply_sets_db_flag(self):
        """When user sends 'optin:agree' quick reply, DB flag is set and confirmation sent."""
        adapter = _make_adapter()
        event = _make_messaging_event(
            "user2",
            {"text": "I agree", "quick_reply": {"payload": "optin:agree"}},
        )

        with (
            patch("database.set_ig_opt_in", AsyncMock()) as mock_set,
            patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "m2"})),
        ):
            await adapter._process_message(event)

        mock_set.assert_awaited_once_with("instagram:user2", True)

    async def test_opted_in_user_photo_dispatches_to_on_photo(self):
        """Opted-in user photo attachment calls _on_photo callback."""
        on_photo = AsyncMock()
        adapter = _make_adapter(on_photo=on_photo)
        event = _make_messaging_event(
            "user3",
            {
                "attachments": [
                    {"type": "image", "payload": {"url": "https://example.com/img.jpg"}}
                ]
            },
        )

        with patch("database.get_ig_opt_in", AsyncMock(return_value=True)):
            await adapter._process_message(event)

        on_photo.assert_awaited_once()
        call_event = on_photo.call_args[0][1]
        assert call_event["image_url"] == "https://example.com/img.jpg"

    async def test_opted_in_user_text_dispatches_to_on_text(self):
        """Opted-in user plain text calls _on_text callback."""
        on_text = AsyncMock()
        adapter = _make_adapter(on_text=on_text)
        event = _make_messaging_event("user4", {"text": "show me shoes"})

        with patch("database.get_ig_opt_in", AsyncMock(return_value=True)):
            await adapter._process_message(event)

        on_text.assert_awaited_once()
        # third positional arg to _on_text is the text
        assert on_text.call_args[0][3] == "show me shoes"

    async def test_slash_command_bypasses_opt_in(self):
        """Slash commands dispatch to _on_command even before opt-in."""
        on_command = AsyncMock()
        adapter = _make_adapter(on_command=on_command)

        for cmd_text in ["/start", "/help", "/language", "/providers"]:
            on_command.reset_mock()
            event = _make_messaging_event("user5", {"text": cmd_text})

            # get_ig_opt_in should NOT be called for commands
            with patch("database.get_ig_opt_in", AsyncMock(return_value=False)) as mock_get:
                await adapter._process_message(event)

            on_command.assert_awaited_once()
            mock_get.assert_not_awaited()

    async def test_command_with_args_bypasses_opt_in(self):
        """/language en command passes args correctly."""
        on_command = AsyncMock()
        adapter = _make_adapter(on_command=on_command)
        event = _make_messaging_event("user6", {"text": "/language en"})

        with patch("database.get_ig_opt_in", AsyncMock(return_value=False)):
            await adapter._process_message(event)

        on_command.assert_awaited_once()
        # args is 5th positional arg (adapter, user_id, chat_id, command, args, event)
        call_args = on_command.call_args[0]
        assert call_args[4] == "en"  # args


# ── TestQuickReplies ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestQuickReplies:
    async def test_send_text_with_buttons_produces_quick_replies(self):
        """send_text with buttons sends quick_replies payload to Graph API."""
        adapter = _make_adapter()
        buttons = [[Button(label="Option A", callback_data="cb_a"), Button(label="Option B", callback_data="cb_b")]]

        with patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "m3"})) as mock_api:
            ref = await adapter.send_text("chat1", "Pick one", buttons)

        assert mock_api.called
        data = mock_api.call_args[0][2]
        assert "quick_replies" in data["message"]
        titles = [qr["title"] for qr in data["message"]["quick_replies"]]
        assert "Option A" in titles
        assert "Option B" in titles
        assert isinstance(ref, MessageRef)

    async def test_quick_reply_callback_dispatches_to_on_callback(self):
        """Non-optin quick reply payload dispatches to _on_callback."""
        on_callback = AsyncMock()
        adapter = _make_adapter(on_callback=on_callback)
        event = _make_messaging_event(
            "user7",
            {"quick_reply": {"payload": "nav:next"}},
        )

        with patch("database.get_ig_opt_in", AsyncMock(return_value=True)):
            await adapter._process_message(event)

        on_callback.assert_awaited_once()
        # payload is 4th positional arg
        assert on_callback.call_args[0][3] == "nav:next"


# ── TestPhotoHandling ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPhotoHandling:
    async def test_image_attachment_dispatches_to_on_photo(self):
        """Image attachment in messaging event triggers _on_photo with image_url."""
        on_photo = AsyncMock()
        adapter = _make_adapter(on_photo=on_photo)
        event = _make_messaging_event(
            "user8",
            {
                "attachments": [
                    {"type": "image", "payload": {"url": "https://cdn.example.com/photo.jpg"}}
                ]
            },
        )

        with patch("database.get_ig_opt_in", AsyncMock(return_value=True)):
            await adapter._process_message(event)

        on_photo.assert_awaited_once()
        call_event = on_photo.call_args[0][1]
        assert call_event["image_url"] == "https://cdn.example.com/photo.jpg"
        assert call_event["user_id"] == "user8"

    async def test_non_image_attachment_does_not_dispatch_to_on_photo(self):
        """A video attachment (not image) does not trigger _on_photo."""
        on_photo = AsyncMock()
        adapter = _make_adapter(on_photo=on_photo)
        event = _make_messaging_event(
            "user9",
            {
                "attachments": [
                    {"type": "video", "payload": {"url": "https://cdn.example.com/video.mp4"}}
                ]
            },
        )

        with patch("database.get_ig_opt_in", AsyncMock(return_value=True)):
            await adapter._process_message(event)

        on_photo.assert_not_awaited()


# ── TestWebhookMigration ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestWebhookMigration:
    async def test_handle_webhook_verify_returns_challenge(self):
        """Valid verify_token returns 200 with challenge."""
        adapter = _make_adapter()
        req = _make_request(
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "abc123",
            }
        )
        resp = await adapter.handle_webhook_verify(req)
        assert resp.status_code == 200
        # PlainTextResponse body is the challenge
        assert b"abc123" in resp.body

    async def test_handle_webhook_verify_rejects_bad_token(self):
        """Wrong verify_token returns 403."""
        adapter = _make_adapter()
        req = _make_request(
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "abc123",
            }
        )
        resp = await adapter.handle_webhook_verify(req)
        assert resp.status_code == 403

    async def test_handle_webhook_rejects_invalid_signature(self):
        """POST webhook with bad X-Hub-Signature-256 returns 403."""
        adapter = _make_adapter()
        body = b'{"entry":[]}'
        req = _make_request(
            body=body,
            headers={"X-Hub-Signature-256": "sha256=badbad"},
        )
        resp = await adapter.handle_webhook(req)
        assert resp.status_code == 403

    async def test_handle_webhook_accepts_valid_signature(self):
        """POST webhook with correct HMAC signature is processed (returns OK)."""
        adapter = _make_adapter()
        payload_dict = _webhook_payload("userX", {"text": "hi"})
        body = json.dumps(payload_dict).encode()
        sig = _sign(body, "test-secret")
        req = _make_request(body=body, headers={"X-Hub-Signature-256": sig})
        req.json = AsyncMock(return_value=payload_dict)

        with (
            patch("database.get_ig_opt_in", AsyncMock(return_value=True)),
            patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "m4"})),
        ):
            resp = await adapter.handle_webhook(req)

        assert resp.status_code == 200


# ── TestGraphApiAuth ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGraphApiAuth:
    async def test_download_photo_includes_bearer_token(self):
        """download_photo GETs the image URL with Authorization: Bearer header."""
        adapter = _make_adapter()
        # Set up mock response as an async context manager
        mock_resp = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"fake_image_bytes")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        adapter._session.get = MagicMock(return_value=mock_resp)

        event = {"image_url": "https://example.com/photo.jpg", "user_id": "u1"}
        result = await adapter.download_photo(event)

        assert result == b"fake_image_bytes"
        adapter._session.get.assert_called_once()
        call_args = adapter._session.get.call_args
        # First positional arg is the URL, headers is a keyword arg
        headers = call_args[1].get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token"

    async def test_download_photo_raises_on_missing_url(self):
        """download_photo raises ValueError if event has no image_url."""
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="No image URL"):
            await adapter.download_photo({"user_id": "u1"})


# ── TestAnnotatedPhoto ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnnotatedPhoto:
    async def test_send_photo_bytes_uses_form_upload(self):
        """send_photo with bytes triggers form-data upload via session.post directly."""
        adapter = _make_adapter()

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"message_id": "m5"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        adapter._session.post = MagicMock(return_value=mock_resp)

        # Also patch send_graph_api to handle caption send_text call
        with patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "cap1"})):
            ref = await adapter.send_photo("chat2", image=b"fake_annotated_bytes", caption="test")

        assert isinstance(ref, MessageRef)
        assert ref.platform == "instagram"
        # session.post should be called with "data" keyword (form upload), not "json"
        adapter._session.post.assert_called_once()
        call_kwargs = adapter._session.post.call_args[1]
        assert "data" in call_kwargs
        assert "json" not in call_kwargs

    async def test_send_photo_url_uses_graph_api(self):
        """send_photo with URL string goes through send_graph_api."""
        adapter = _make_adapter()

        with patch("adapters.instagram.send_graph_api", AsyncMock(return_value={"message_id": "m6"})) as mock_api:
            ref = await adapter.send_photo("chat3", image="https://example.com/img.jpg")

        assert mock_api.called
        assert isinstance(ref, MessageRef)


# ── TestTranslation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTranslation:
    async def test_detect_language_called_with_caption_in_photo_pipeline(self):
        """
        When handle_photo processes a photo with a caption, translator.detect_language
        is called — confirming Hebrew/English support works through Instagram pipeline.
        """
        import bot_core

        adapter = _make_adapter()
        core = bot_core.BotCore(adapter)

        adapter.download_photo = AsyncMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)
        adapter.send_text = AsyncMock(return_value=MessageRef("instagram", "u1", "m1", None))
        adapter.edit_text = AsyncMock()

        fake_event = {"user_id": "42", "image_url": "https://x.com/img.jpg"}

        with (
            patch("database.ensure_user", AsyncMock()),
            patch("database.get_user_lang", AsyncMock(return_value="en")),
            patch("bot_core.get_providers", AsyncMock(return_value=[MagicMock(name="prov")])),
            patch("bot_core.analyse_image", AsyncMock(side_effect=RuntimeError("no vision"))),
            patch("bot_core.detect_language", MagicMock(return_value="he")) as mock_detect,
            patch("bot_core.translate_and_refine", AsyncMock(return_value=("english text", "search query"))),
            patch("bot_core._compress_image", MagicMock(return_value=b"compressed")),
            patch("bot_core.new_correlation_id", MagicMock(return_value="cid-1")),
            patch("database.get_user_rate_limit", AsyncMock(return_value=None)),
            patch("log_group.log", AsyncMock()),
        ):
            await core.handle_photo(fake_event, context_hint="מצלמה")

        # detect_language must have been called with the Hebrew caption
        mock_detect.assert_called_with("מצלמה")

    async def test_detect_language_module_importable(self):
        """translator.detect_language is importable and works correctly."""
        from translator import detect_language
        assert detect_language("hello") == "en"
        assert detect_language("שלום") == "he"
