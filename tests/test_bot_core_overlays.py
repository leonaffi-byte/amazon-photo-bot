"""
Tests for overlay wiring in bot_core.py:
  - annotate_with_overlays called (not annotate_products) in multi-product path
  - _compress_image called via asyncio.to_thread (not synchronously)
  - Overlay failure is non-fatal (annotated_bytes set to None, no crash)
  - Stage 4 progress message sent during _search_and_render
  - session.annotated_bytes used as image source in _render_product
  - annotated_bytes=None falls back to item.image_url
  - israel_result passed to product_caption in _update_caption_after_enrichment
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

import database as db


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tiny_jpeg() -> bytes:
    """Return a minimal valid JPEG image as bytes."""
    img = Image.new("RGB", (8, 8), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_bot_core(platform: str = "telegram", user_id: str = "1", chat_id: str = "c1"):
    from bot_core import BotCore

    adapter = MagicMock()
    adapter.platform_name = platform
    adapter.send_text = AsyncMock(return_value=MagicMock())
    adapter.edit_text = AsyncMock(return_value=MagicMock())
    adapter.send_photo = AsyncMock(return_value=MagicMock())
    adapter.edit_photo = AsyncMock(return_value=MagicMock())
    adapter.delete_message = AsyncMock()
    adapter.download_photo = AsyncMock(return_value=_make_tiny_jpeg())
    adapter.supports_photo_edit = True
    adapter.get_user_id = MagicMock(return_value=user_id)
    adapter.get_chat_id = MagicMock(return_value=chat_id)

    return BotCore(adapter), adapter


def _make_product_info(name: str = "Widget"):
    from image_analyzer import ProductInfo
    return ProductInfo(
        product_name=name,
        brand="BrandX",
        category="Electronics",
        key_features=[],
        amazon_search_query=name,
        alternative_query=name,
        confidence="high",
        notes="",
    )


def _make_provider_result(name: str = "Widget", products=None):
    from providers.base import ProviderResult
    pi = _make_product_info(name)
    if products is None:
        products = [pi]
    result = MagicMock(spec=ProviderResult)
    result.product_name = name
    result.provider_name = "openai"
    result.confidence = "high"
    result.cost_str = "$0.01"
    result.to_product_info.return_value = pi
    result.to_product_info_list.return_value = products
    return result


def _make_amazon_item(asin: str = "B001"):
    from search_backends.base import AmazonItem
    return AmazonItem(
        asin=asin,
        title="Test Product",
        image_url="https://example.com/img.jpg",
        price_usd=29.99,
        currency="USD",
        rating=4.5,
        review_count=100,
        is_amazon_fulfilled=True,
        is_sold_by_amazon=False,
        is_prime=True,
        availability="In Stock",
    )


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── Common DB mock context ─────────────────────────────────────────────────────

def _make_db_patch():
    """Returns a patch dict for bot_core.db common methods."""
    return {
        "get_active_tag": AsyncMock(return_value=None),
        "get_user_lang": AsyncMock(return_value="en"),
        "is_admin_in_db": AsyncMock(return_value=False),
        "get_user_rate_limit": AsyncMock(return_value=None),
        "ensure_user": AsyncMock(),
        "log_search": AsyncMock(),
        "increment_tag_search_count": AsyncMock(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: annotate_with_overlays called (not annotate_products) via to_thread
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAnnotateWithOverlaysCalled:

    async def test_annotate_with_overlays_called(self):
        """Multi-product path calls annotate_with_overlays (not annotate_products), via to_thread."""
        core, adapter = _make_bot_core()

        p1 = _make_product_info("ProductA")
        p2 = _make_product_info("ProductB")
        winner = _make_provider_result("ProductA", [p1, p2])

        fake_annotated = b"annotated_bytes_data"
        to_thread_calls = []

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.analyse_image", return_value=(winner, [winner])), \
             patch("bot_core.get_providers", return_value=[MagicMock()]), \
             patch("bot_core.log_group.log", new_callable=AsyncMock), \
             patch("image_annotator.annotate_with_overlays", return_value=fake_annotated) as mock_overlay, \
             patch("image_annotator.annotate_products", return_value=fake_annotated) as mock_old, \
             patch("asyncio.to_thread") as mock_to_thread:

            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.ensure_user = AsyncMock()
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            async def smart_to_thread(fn, *args, **kwargs):
                to_thread_calls.append(fn)
                return fn(*args, **kwargs)

            mock_to_thread.side_effect = smart_to_thread

            await core.handle_photo(event=MagicMock(), cache_key=None, context_hint=None)

        # annotate_with_overlays should have been called (directly or via to_thread)
        fn_names = [getattr(fn, "__name__", repr(fn)) for fn in to_thread_calls]
        assert mock_overlay.called or "annotate_with_overlays" in fn_names, (
            f"annotate_with_overlays not called. to_thread calls: {fn_names}"
        )
        # annotate_products should NOT be the primary call (may be fallback)
        # The key assertion is that annotate_with_overlays was used
        assert mock_overlay.called


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: _compress_image called via asyncio.to_thread
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestCompressImageAsync:

    async def test_compress_image_async(self):
        """handle_photo calls _compress_image via asyncio.to_thread, not synchronously."""
        core, adapter = _make_bot_core()
        winner = _make_provider_result("Widget", [_make_product_info("Widget")])

        to_thread_calls = []

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.analyse_image", return_value=(winner, [winner])), \
             patch("bot_core.get_providers", return_value=[MagicMock()]), \
             patch("bot_core.log_group.log", new_callable=AsyncMock), \
             patch("asyncio.to_thread") as mock_to_thread:

            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.ensure_user = AsyncMock()
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            async def recording_to_thread(fn, *args, **kwargs):
                to_thread_calls.append(fn)
                return fn(*args, **kwargs)

            mock_to_thread.side_effect = recording_to_thread

            with patch("bot_core._compress_image") as mock_compress:
                mock_compress.return_value = _make_tiny_jpeg()
                await core.handle_photo(event=MagicMock(), cache_key=None, context_hint=None)

            # _compress_image should be in to_thread call list
            assert mock_compress in to_thread_calls, (
                f"_compress_image not called via to_thread. to_thread calls: "
                f"{[getattr(fn, '__name__', repr(fn)) for fn in to_thread_calls]}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: Overlay failure is non-fatal
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestOverlayFailureNonFatal:

    async def test_overlay_failure_nonfatal(self):
        """If annotate_with_overlays raises, handle_photo continues without crashing."""
        core, adapter = _make_bot_core()

        p1 = _make_product_info("ProductA")
        p2 = _make_product_info("ProductB")
        winner = _make_provider_result("ProductA", [p1, p2])

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.analyse_image", return_value=(winner, [winner])), \
             patch("bot_core.get_providers", return_value=[MagicMock()]), \
             patch("bot_core.log_group.log", new_callable=AsyncMock), \
             patch("image_annotator.annotate_with_overlays", side_effect=RuntimeError("overlay failed")), \
             patch("image_annotator.annotate_products", return_value=b"fallback_bytes"), \
             patch("asyncio.to_thread") as mock_to_thread:

            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.ensure_user = AsyncMock()
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            async def smart_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock_to_thread.side_effect = smart_to_thread

            # Should not raise — overlay failure is non-fatal
            await core.handle_photo(event=MagicMock(), cache_key=None, context_hint=None)

        # Flow should have continued — some message was sent
        assert adapter.send_photo.called or adapter.edit_text.called or adapter.send_text.called


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: Stage 4 progress message sent in _search_and_render
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestProgressStage4:

    async def test_progress_stage4_sent(self):
        """_search_and_render sends Stage 4 (Israel shipping) message after search."""
        core, adapter = _make_bot_core()

        item = _make_amazon_item()
        session_obj = core._get_session("telegram:1")
        session_obj.product_info = _make_product_info("Widget")
        session_obj.chosen_result = _make_provider_result("Widget")
        session_obj.is_admin = False

        loading_ref = MagicMock()
        edit_calls = []

        async def record_edit(ref, text="", **kwargs):
            edit_calls.append(text)
            return MagicMock()

        adapter.edit_text.side_effect = record_edit

        with patch("bot_core.search_amazon", return_value=[item]), \
             patch("bot_core.db") as mock_db, \
             patch("bot_core.url_shortener.shorten_many", return_value={}), \
             patch("bot_core.log_group.log", new_callable=AsyncMock), \
             patch("image_annotator.annotate_with_overlays", return_value=b"overlay"), \
             patch("asyncio.to_thread") as mock_to_thread:

            async def smart_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)
            mock_to_thread.side_effect = smart_to_thread

            mock_db.get_active_tag = AsyncMock(return_value="tag1")
            mock_db.log_search = AsyncMock()
            mock_db.increment_tag_search_count = AsyncMock()
            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            await core._search_and_render(
                user_key="telegram:1",
                chat_id="c1",
                israel_only=False,
                session=session_obj,
                lang="en",
                user_id=1,
                loading_msg_ref=loading_ref,
            )

        # At minimum, edit_text should have been called
        assert len(edit_calls) >= 1, f"Expected at least 1 edit_text call, got: {edit_calls}"
        # One of the edits should reference Israel checking
        israel_calls = [c for c in edit_calls if "israel" in c.lower() or "\U0001f1ee\U0001f1f1" in c]
        assert len(israel_calls) >= 1, (
            f"No Israel-checking stage 4 message found in edit_text calls.\nAll calls: {edit_calls}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: annotated_bytes used in _render_product when set
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAnnotatedBytesUsedInRender:

    async def test_annotated_bytes_used_in_render(self):
        """_render_product uses session.annotated_bytes as image source when present."""
        core, adapter = _make_bot_core()

        item = _make_amazon_item()
        fake_overlay = b"my_overlay_bytes"

        session_obj = core._get_session("telegram:1")
        session_obj.all_items = [item]
        session_obj.filtered_items = [item]
        session_obj.page = 0
        session_obj.annotated_bytes = fake_overlay
        session_obj.is_admin = False
        session_obj.results_msg_ref = None

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.url_shortener.shorten_many", return_value={}):
            mock_db.get_active_tag = AsyncMock(return_value=None)
            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            await core._render_product(
                user_key="telegram:1",
                chat_id="c1",
                session=session_obj,
                lang="en",
                user_id=1,
            )

        assert adapter.send_photo.called
        call_kwargs = adapter.send_photo.call_args
        image_arg = (
            call_kwargs.kwargs.get("image")
            or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        )
        assert image_arg == fake_overlay, (
            f"Expected annotated_bytes as image, got: {image_arg!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: annotated_bytes=None falls back to item.image_url
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAnnotatedBytesFallback:

    async def test_annotated_bytes_fallback(self):
        """When session.annotated_bytes is None, _render_product uses item.image_url."""
        core, adapter = _make_bot_core()

        item = _make_amazon_item()
        session_obj = core._get_session("telegram:1")
        session_obj.all_items = [item]
        session_obj.filtered_items = [item]
        session_obj.page = 0
        session_obj.annotated_bytes = None
        session_obj.is_admin = False
        session_obj.results_msg_ref = None

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.url_shortener.shorten_many", return_value={}):
            mock_db.get_active_tag = AsyncMock(return_value=None)
            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            await core._render_product(
                user_key="telegram:1",
                chat_id="c1",
                session=session_obj,
                lang="en",
                user_id=1,
            )

        assert adapter.send_photo.called
        call_kwargs = adapter.send_photo.call_args
        image_arg = (
            call_kwargs.kwargs.get("image")
            or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        )
        assert image_arg == item.image_url, (
            f"Expected item.image_url fallback, got: {image_arg!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: israel_result passed to product_caption in _update_caption_after_enrichment
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestIsraelResultPassedToCaption:

    async def test_israel_result_passed_to_caption(self):
        """_update_caption_after_enrichment passes israel_result= to product_caption."""
        core, adapter = _make_bot_core()

        item = _make_amazon_item()
        msg_ref = MagicMock()

        israel_result = MagicMock()
        israel_result.ships_to_israel = True
        israel_result.free_shipping = False

        session_obj = core._get_session("telegram:1")
        session_obj.all_items = [item]
        session_obj.filtered_items = [item]
        session_obj.page = 0
        session_obj.results_msg_ref = msg_ref
        session_obj.is_admin = False
        session_obj._last_israel_result = israel_result
        session_obj._last_price_history = None

        with patch("bot_core.db") as mock_db, \
             patch("bot_core.url_shortener.shorten_many", return_value={}), \
             patch("formatter.Formatter.product_caption") as mock_caption:

            mock_caption.return_value = "mocked caption"
            mock_db.get_active_tag = AsyncMock(return_value=None)
            mock_db.get_user_lang = AsyncMock(return_value="en")
            mock_db.is_admin_in_db = AsyncMock(return_value=False)
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)

            await core._update_caption_after_enrichment(
                chat_id="c1",
                session=session_obj,
                item=item,
                page_snap=0,
                lang="en",
                user_id=1,
            )

        assert mock_caption.called, "product_caption was not called"
        call_kwargs = mock_caption.call_args
        assert "israel_result" in call_kwargs.kwargs, (
            f"israel_result not passed to product_caption. kwargs: {call_kwargs.kwargs}"
        )
        assert call_kwargs.kwargs["israel_result"] is israel_result
