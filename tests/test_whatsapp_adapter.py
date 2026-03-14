"""
tests/test_whatsapp_adapter.py — Unit tests for WhatsApp adapter compliance and messaging.

Covers:
  TestOptIn:            Opt-in gate, optin:agree callback, command passthrough pre-opt-in
  TestListMessage:      send_list_message payload shape and truncation
  TestTemplateSend:     send_template payload shape
  TestWindowTracking:   _is_window_open with various timestamp states
  TestWebhookMigration: Webhook verify challenge/reject and signature validation
  TestAnnotatedPhoto:   send_photo delivers annotated image bytes via Graph API
  TestTranslation:      translator.detect_language invoked through BotCore pipeline
  TestBotCoreListMessage: BotCore calls send_list_message for WhatsApp multi-product picker
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import database
from adapters.base import Button, MessageRef
from adapters.whatsapp import WhatsAppAdapter


# ── Patch targets ─────────────────────────────────────────────────────────────
# whatsapp.py imports: `import database` and `from adapters.shared_meta import send_graph_api`
# So patches must target:
#   adapters.whatsapp.database.<method>   for database calls
#   adapters.whatsapp.send_graph_api      for Graph API calls
_WA_DB   = "adapters.whatsapp.database"
_WA_GAPI = "adapters.whatsapp.send_graph_api"

# send_photo uses the session's .post directly for upload, then send_graph_api for the message
# The whatsapp module's send_photo also does `from adapters.shared_meta import GRAPH_API_BASE`
# so Graph API mock only covers the final send, not the upload.


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await database.init_db()


def _make_adapter(
    on_photo: AsyncMock | None = None,
    on_callback: AsyncMock | None = None,
    on_text: AsyncMock | None = None,
    on_command: AsyncMock | None = None,
) -> WhatsAppAdapter:
    """Create a WhatsAppAdapter with mocked callbacks and a fake session."""
    adapter = WhatsAppAdapter(
        on_photo=on_photo or AsyncMock(),
        on_callback=on_callback or AsyncMock(),
        on_text=on_text or AsyncMock(),
        on_command=on_command or AsyncMock(),
    )
    adapter._token = "test_token"
    adapter._phone_number_id = "12345"
    adapter._verify_token = "my_verify_token"
    adapter._app_secret = "test_secret"
    adapter._session = MagicMock()
    return adapter


def _make_text_msg(user_id: str, text: str) -> dict:
    return {"from": user_id, "type": "text", "text": {"body": text}}


def _make_image_msg(user_id: str) -> dict:
    return {"from": user_id, "type": "image", "image": {"id": "media123"}}


def _make_button_reply_msg(user_id: str, reply_id: str) -> dict:
    return {
        "from": user_id,
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": reply_id, "title": "Some title"},
        },
    }


_FAKE_GRAPH_RESPONSE = {"messages": [{"id": "msg_abc"}]}


# ── TestOptIn ─────────────────────────────────────────────────────────────────

class TestOptIn:
    async def test_first_time_user_gets_opt_in_prompt(self):
        """A user who has not opted in receives the opt-in prompt, not photo processing."""
        on_photo = AsyncMock()
        adapter = _make_adapter(on_photo=on_photo)

        with patch(_WA_GAPI, new=AsyncMock(return_value={})) as mock_api, \
             patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=False)):
            await adapter._process_message(_make_image_msg("111"), {})

        on_photo.assert_not_called()
        mock_api.assert_called_once()
        call_data = mock_api.call_args[0][2]
        assert call_data["type"] == "interactive"
        assert call_data["interactive"]["type"] == "button"
        assert call_data["interactive"]["action"]["buttons"][0]["reply"]["id"] == "optin:agree"

    async def test_opt_in_agree_sets_db_flag_and_confirms(self):
        """'optin:agree' button reply sets the opt-in DB flag and sends confirmation."""
        on_callback = AsyncMock()
        adapter = _make_adapter(on_callback=on_callback)

        mock_set = AsyncMock()
        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.set_wa_opt_in", new=mock_set):
            await adapter._process_message(_make_button_reply_msg("222", "optin:agree"), {})

        mock_set.assert_called_once_with("whatsapp:222", True)
        mock_api.assert_called_once()
        on_callback.assert_not_called()

    async def test_opted_in_user_photo_dispatches_to_on_photo(self):
        """A user who has opted in has their photo dispatched to on_photo."""
        on_photo = AsyncMock()
        adapter = _make_adapter(on_photo=on_photo)

        with patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=True)):
            await adapter._process_message(_make_image_msg("333"), {})

        on_photo.assert_called_once()

    async def test_slash_command_works_before_opt_in(self):
        """Slash commands work for users who have not opted in."""
        on_command = AsyncMock()
        adapter = _make_adapter(on_command=on_command)

        with patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=False)):
            await adapter._process_message(_make_text_msg("444", "/start"), {})

        on_command.assert_called_once()
        call_args = on_command.call_args[0]
        assert call_args[3] == "/start"

    async def test_all_commands_pass_through_pre_opt_in(self):
        """Multiple slash commands all pass through before opt-in."""
        for cmd in ["/start", "/help", "/language", "/providers"]:
            on_command = AsyncMock()
            adapter = _make_adapter(on_command=on_command)

            with patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
                 patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=False)):
                await adapter._process_message(_make_text_msg("555", cmd), {})

            assert on_command.called, f"Command {cmd} should have been dispatched"


# ── TestListMessage ───────────────────────────────────────────────────────────

class TestListMessage:
    async def test_send_list_message_correct_payload(self):
        """send_list_message sends a properly shaped interactive list payload."""
        adapter = _make_adapter()
        sections = [{"title": "Products", "rows": [{"id": "pick:0", "title": "Widget"}]}]

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result = await adapter.send_list_message(
                chat_id="777",
                body="Choose a product:",
                button_label="View",
                sections=sections,
            )

        assert isinstance(result, MessageRef)
        assert result.platform == "whatsapp"
        assert result.message_id == "msg_abc"
        mock_api.assert_called_once()
        data = mock_api.call_args[0][2]
        assert data["type"] == "interactive"
        assert data["interactive"]["type"] == "list"
        assert data["interactive"]["body"]["text"] == "Choose a product:"
        assert data["interactive"]["action"]["button"] == "View"
        assert data["interactive"]["action"]["sections"] == sections

    async def test_send_list_message_body_truncated_to_1024(self):
        """send_list_message truncates body to 1024 characters."""
        adapter = _make_adapter()
        long_body = "x" * 2000

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            await adapter.send_list_message(
                chat_id="777", body=long_body, button_label="View", sections=[],
            )

        data = mock_api.call_args[0][2]
        assert len(data["interactive"]["body"]["text"]) == 1024

    async def test_send_list_message_button_label_truncated_to_20(self):
        """send_list_message truncates button label to 20 characters."""
        adapter = _make_adapter()
        long_label = "A" * 50

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            await adapter.send_list_message(
                chat_id="777", body="Pick one", button_label=long_label, sections=[],
            )

        data = mock_api.call_args[0][2]
        assert len(data["interactive"]["action"]["button"]) == 20


# ── TestTemplateSend ──────────────────────────────────────────────────────────

class TestTemplateSend:
    async def test_send_template_correct_payload(self):
        """send_template sends a properly shaped template payload."""
        adapter = _make_adapter()

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result = await adapter.send_template(
                chat_id="888",
                template_name="product_results_ready",
                lang_code="en",
            )

        assert isinstance(result, MessageRef)
        assert result.platform == "whatsapp"
        assert result.message_id == "msg_abc"
        mock_api.assert_called_once()
        data = mock_api.call_args[0][2]
        assert data["type"] == "template"
        assert data["template"]["name"] == "product_results_ready"
        assert data["template"]["language"]["code"] == "en"

    async def test_send_template_default_name_and_lang(self):
        """send_template uses default template 'product_results_ready' and lang 'en'."""
        adapter = _make_adapter()

        with patch(_WA_GAPI, new=AsyncMock(return_value={})) as mock_api:
            await adapter.send_template(chat_id="999")

        data = mock_api.call_args[0][2]
        assert data["template"]["name"] == "product_results_ready"
        assert data["template"]["language"]["code"] == "en"


# ── TestWindowTracking ────────────────────────────────────────────────────────

class TestWindowTracking:
    async def test_window_open_when_recent_message(self):
        """_is_window_open returns True when last message was < 24h ago."""
        adapter = _make_adapter()
        recent_ts = time.time() - 3600  # 1 hour ago

        with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=recent_ts)):
            result = await adapter._is_window_open("whatsapp:111")

        assert result is True

    async def test_window_closed_when_old_message(self):
        """_is_window_open returns False when last message was > 24h ago."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago

        with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)):
            result = await adapter._is_window_open("whatsapp:222")

        assert result is False

    async def test_window_closed_when_no_timestamp(self):
        """_is_window_open returns False when no last message timestamp exists."""
        adapter = _make_adapter()

        with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=None)):
            result = await adapter._is_window_open("whatsapp:333")

        assert result is False


# ── TestWebhookMigration ──────────────────────────────────────────────────────

class TestWebhookMigration:
    def _make_request(
        self,
        query_params: dict | None = None,
        body: bytes = b"{}",
        headers: dict | None = None,
        json_body: dict | None = None,
    ) -> MagicMock:
        req = MagicMock()
        req.query_params = query_params or {}
        req.headers = headers or {}
        req.body = AsyncMock(return_value=body)
        req.json = AsyncMock(return_value=json_body or {})
        return req

    async def test_webhook_verify_returns_challenge_on_valid_token(self):
        """handle_webhook_verify returns 200 with the challenge on valid token."""
        adapter = _make_adapter()
        adapter._verify_token = "correct_token"
        req = self._make_request(query_params={
            "hub.mode": "subscribe",
            "hub.verify_token": "correct_token",
            "hub.challenge": "CHALLENGE123",
        })

        response = await adapter.handle_webhook_verify(req)
        assert response.status_code == 200
        assert response.body == b"CHALLENGE123"

    async def test_webhook_verify_returns_403_on_bad_token(self):
        """handle_webhook_verify returns 403 on wrong token."""
        adapter = _make_adapter()
        adapter._verify_token = "correct_token"
        req = self._make_request(query_params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "CHALLENGE123",
        })

        response = await adapter.handle_webhook_verify(req)
        assert response.status_code == 403

    async def test_webhook_rejects_invalid_signature(self):
        """handle_webhook returns 403 when HMAC signature is invalid."""
        adapter = _make_adapter()
        adapter._app_secret = "secret"
        req = self._make_request(
            body=b'{"entry":[]}',
            headers={"X-Hub-Signature-256": "sha256=invalidsignature"},
        )

        response = await adapter.handle_webhook(req)
        assert response.status_code == 403

    async def test_webhook_processes_valid_request(self):
        """handle_webhook returns 200 and processes with empty app_secret."""
        adapter = _make_adapter()
        adapter._app_secret = ""  # Empty secret skips signature check
        body = b'{"entry":[]}'
        req = self._make_request(body=body, json_body={"entry": []})

        response = await adapter.handle_webhook(req)
        assert response.status_code == 200


# ── TestAnnotatedPhoto ────────────────────────────────────────────────────────

class TestAnnotatedPhoto:
    async def test_send_photo_bytes_uploads_via_media_endpoint(self):
        """adapter.send_photo with bytes uploads to media endpoint then sends message."""
        adapter = _make_adapter()

        mock_upload_resp = MagicMock()
        mock_upload_resp.__aenter__ = AsyncMock(return_value=mock_upload_resp)
        mock_upload_resp.__aexit__ = AsyncMock(return_value=False)
        mock_upload_resp.json = AsyncMock(return_value={"id": "media_id_xyz"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_upload_resp)
        adapter._session = mock_session

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result = await adapter.send_photo(
                chat_id="user123",
                image=b"fake_annotated_bytes",
                caption="Test annotation",
            )

        assert isinstance(result, MessageRef)
        assert result.platform == "whatsapp"
        mock_api.assert_called_once()
        data = mock_api.call_args[0][2]
        assert data["type"] == "image"
        assert data["image"]["id"] == "media_id_xyz"

    async def test_send_photo_url_sends_directly(self):
        """adapter.send_photo with URL sends image directly without upload."""
        adapter = _make_adapter()

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result = await adapter.send_photo(
                chat_id="user456",
                image="https://example.com/photo.jpg",
                caption="A product",
            )

        assert isinstance(result, MessageRef)
        mock_api.assert_called_once()
        data = mock_api.call_args[0][2]
        assert data["type"] == "image"
        assert data["image"]["link"] == "https://example.com/photo.jpg"


# ── TestTranslation ───────────────────────────────────────────────────────────

class TestTranslation:
    async def test_handle_photo_invokes_translator(self):
        """BotCore.handle_photo calls translator.detect_language through the pipeline."""
        from bot_core import BotCore
        import database as db_module
        from providers.base import ProviderResult

        mock_adapter = MagicMock()
        mock_adapter.platform_name = "whatsapp"
        mock_adapter.get_user_id = MagicMock(return_value="9001")
        mock_adapter.get_chat_id = MagicMock(return_value="9001")
        mock_adapter.send_text = AsyncMock(return_value=MagicMock())
        mock_adapter.send_photo = AsyncMock(return_value=MagicMock())
        mock_adapter.edit_text = AsyncMock(return_value=None)
        mock_adapter.delete_message = AsyncMock(return_value=None)
        mock_adapter.download_photo = AsyncMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)

        user_key = "whatsapp:9001"
        await db_module.ensure_user(user_key, "whatsapp")
        await db_module.set_user_lang(user_key, "en")

        core = BotCore(adapter=mock_adapter)

        fake_result = ProviderResult(
            provider_name="openai/gpt-4o",
            model_id="gpt-4o",
            product_name="Sample Widget",
            brand="AcmeCo",
            category="Electronics",
            key_features=["feature1"],
            amazon_search_query="sample widget",
            alternative_query="widget",
            confidence="high",
            notes="",
            latency_ms=100,
            input_tokens=500,
            output_tokens=100,
            cost_usd=0.001,
        )

        event = {"user_id": "9001", "message": {}, "value": {}}

        from providers.base import ProviderResult as _PR
        mock_provider = MagicMock()
        mock_provider.name = "openai/gpt-4o"

        with patch("bot_core.analyse_image", new=AsyncMock(return_value=(fake_result, [fake_result]))), \
             patch("bot_core.detect_language", return_value="he") as mock_detect, \
             patch("bot_core.translate_and_refine", new=AsyncMock(return_value=("sample widget", "en"))), \
             patch("bot_core.get_providers", new=AsyncMock(return_value=[mock_provider])), \
             patch("bot_core._compress_image", return_value=b"fake_compressed"), \
             patch("log_group.log", new=AsyncMock()):
            try:
                await core.handle_photo(event, cache_key=None, context_hint="מוצר נהדר")
            except Exception:
                pass

        # detect_language is called at line ~940 to detect caption language
        mock_detect.assert_called()


# ── TestBotCoreListMessage ────────────────────────────────────────────────────

class TestBotCoreListMessage:
    async def test_botcore_calls_send_list_message_for_whatsapp_multi_product(self):
        """BotCore calls send_list_message when platform is 'whatsapp' with multiple products."""
        from bot_core import BotCore, CB_PICK_PRODUCT
        import database as db_module
        from image_analyzer import ProductInfo
        from providers.base import ProviderResult

        mock_loading_ref = MagicMock(spec=MessageRef)
        mock_loading_ref.platform = "whatsapp"
        mock_loading_ref.chat_id = "9002"
        mock_loading_ref.message_id = "loading_msg"

        mock_adapter = MagicMock()
        mock_adapter.platform_name = "whatsapp"
        mock_adapter.get_user_id = MagicMock(return_value="9002")
        mock_adapter.get_chat_id = MagicMock(return_value="9002")
        mock_adapter.send_text = AsyncMock(return_value=mock_loading_ref)
        mock_adapter.send_photo = AsyncMock(return_value=MagicMock())
        mock_adapter.send_list_message = AsyncMock(return_value=MagicMock())
        mock_adapter.edit_text = AsyncMock(return_value=None)
        mock_adapter.delete_message = AsyncMock(return_value=None)
        mock_adapter.download_photo = AsyncMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)

        user_key = "whatsapp:9002"
        await db_module.ensure_user(user_key, "whatsapp")
        await db_module.set_user_lang(user_key, "en")

        core = BotCore(adapter=mock_adapter)

        product_a = ProductInfo(
            product_name="Wireless Headphones",
            brand="Sony",
            category="Electronics",
            key_features=["noise cancelling"],
            amazon_search_query="wireless headphones",
            alternative_query="sony headphones",
            confidence="high",
            notes="",
        )
        product_b = ProductInfo(
            product_name="Phone Case",
            brand="OtterBox",
            category="Accessories",
            key_features=["drop protection"],
            amazon_search_query="phone case",
            alternative_query="otterbox case",
            confidence="high",
            notes="",
        )

        fake_result = MagicMock()
        fake_result.provider_name = "openai/gpt-4o"
        fake_result.product_name = "Wireless Headphones"
        fake_result.model_id = "gpt-4o"
        fake_result.to_product_info_list = MagicMock(return_value=[product_a, product_b])
        fake_result.to_product_info = MagicMock(return_value=product_a)
        fake_result.confidence = "high"
        fake_result.notes = ""
        fake_result.cost_str = "$0.001"

        mock_provider = MagicMock()
        mock_provider.name = "openai/gpt-4o"

        event = {"user_id": "9002", "message": {}, "value": {}}

        with patch("bot_core.analyse_image", new=AsyncMock(return_value=(fake_result, [fake_result]))), \
             patch("bot_core.get_providers", new=AsyncMock(return_value=[mock_provider])), \
             patch("bot_core.detect_language", return_value="en"), \
             patch("bot_core.translate_and_refine", new=AsyncMock(return_value=("search query", "en"))), \
             patch("bot_core._compress_image", return_value=b"fake_compressed"), \
             patch("image_annotator.annotate_products", return_value=b"annotated_bytes"), \
             patch("log_group.log", new=AsyncMock()):
            try:
                await core.handle_photo(event, cache_key=None, context_hint=None)
            except Exception:
                pass

        mock_adapter.send_list_message.assert_called_once()
        call_kwargs = mock_adapter.send_list_message.call_args[1]
        sections = call_kwargs["sections"]
        assert len(sections) == 1
        assert len(sections[0]["rows"]) == 2
        row_ids = [r["id"] for r in sections[0]["rows"]]
        assert f"{CB_PICK_PRODUCT}0" in row_ids
        assert f"{CB_PICK_PRODUCT}1" in row_ids


# ── TestWindowEnforcement ─────────────────────────────────────────────────────

class TestWindowEnforcement:
    async def test_send_text_window_open_proceeds_normally(self):
        """send_text with open window calls send_graph_api and returns real message_id."""
        adapter = _make_adapter()
        recent_ts = time.time() - 3600  # 1 hour ago — window open

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=recent_ts)):
            result = await adapter.send_text(chat_id="user123", text="Hello")

        assert isinstance(result, MessageRef)
        assert result.message_id == "msg_abc"
        mock_api.assert_called_once()

    async def test_send_text_window_closed_fires_template_once(self):
        """send_text with closed window fires send_template once, returns no-op MessageRef.
        Second call returns no-op without calling API again."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago — window closed

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="en")):
            result1 = await adapter.send_text(chat_id="user123", text="First message")
            result2 = await adapter.send_text(chat_id="user123", text="Second message")

        # Template call happens exactly once
        assert mock_api.call_count == 1
        template_data = mock_api.call_args[0][2]
        assert template_data["type"] == "template"

        # Both results are no-op (empty message_id)
        assert result1.message_id == ""
        assert result2.message_id == ""

    async def test_send_photo_window_closed_returns_noop(self):
        """send_photo with closed window returns no-op MessageRef without uploading media."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago — window closed

        mock_session = MagicMock()
        mock_session.post = MagicMock()  # Should NOT be called
        adapter._session = mock_session

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="en")):
            result = await adapter.send_photo(
                chat_id="user123",
                image=b"fake_image_bytes",
                caption="A product",
            )

        # Media upload should NOT have been attempted
        mock_session.post.assert_not_called()
        # Template was sent (exactly once), not a regular message
        assert mock_api.call_count == 1
        template_data = mock_api.call_args[0][2]
        assert template_data["type"] == "template"
        # Result is no-op
        assert result.message_id == ""

    async def test_send_list_message_window_closed_returns_noop(self):
        """send_list_message with closed window returns no-op MessageRef."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago — window closed
        sections = [{"title": "Products", "rows": [{"id": "pick:0", "title": "Widget"}]}]

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="en")):
            result = await adapter.send_list_message(
                chat_id="user123",
                body="Choose a product:",
                button_label="View",
                sections=sections,
            )

        # Template was sent (not a list message)
        assert mock_api.call_count == 1
        template_data = mock_api.call_args[0][2]
        assert template_data["type"] == "template"
        # Result is no-op
        assert result.message_id == ""

    async def test_edit_text_window_closed_returns_none(self):
        """edit_text with closed window returns None and does NOT call send_text."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago — window closed
        ref = MessageRef(platform="whatsapp", chat_id="user123", message_id="some_msg")

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="en")):
            result = await adapter.edit_text(ref, text="Updated text")

        # edit_text returns None on closed window
        assert result is None
        # Template was fired once (not a text message)
        assert mock_api.call_count == 1
        template_data = mock_api.call_args[0][2]
        assert template_data["type"] == "template"

    async def test_template_flag_resets_on_inbound_message(self):
        """After _template_sent is True, _process_message resets the flag on inbound."""
        adapter = _make_adapter()
        adapter._template_sent["user123"] = True

        with patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=True)):
            await adapter._process_message(_make_text_msg("user123", "Hello"), {})

        # Flag should be reset (falsy)
        assert not adapter._template_sent.get("user123")

    async def test_guard_window_uses_user_language(self):
        """When window is closed, _guard_window looks up user language and passes to send_template."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25 hours ago — window closed

        with patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api, \
             patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="he")) as mock_lang:
            await adapter.send_text(chat_id="user123", text="Hello")

        # Language lookup was called with correct user_key
        mock_lang.assert_called_once_with("whatsapp:user123")
        # Template was sent with the Hebrew language code
        template_data = mock_api.call_args[0][2]
        assert template_data["template"]["language"]["code"] == "he"
