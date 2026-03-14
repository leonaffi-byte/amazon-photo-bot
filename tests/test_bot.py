"""
tests/test_bot.py — Tests for the bot.py module (Telegram handlers & session state).

Covers:
  1. UserSession — creation, default values, property methods, filtering, append
  2. _is_rate_limited() — under limit, at limit, different users (supplements test_rate_limiter.py)
  3. _analysis_cache — dedup cache TTL logic
  4. cmd_start / cmd_help / cmd_providers — command handlers reply correctly
  5. handle_photo() — triggers analysis, handles rate limit, dedup cache hit
  6. handle_callback() — CB_NEXT/CB_PREV navigation, session expired, filter selection
  7. handle_text_search() — translates and builds ProductInfo
  8. get_session() — creates and returns sessions
  9. _spawn_background_check() — fire-and-forget helper
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

import config
from bot import (
    UserSession,
    _analysis_cache,
    _ANALYSIS_CACHE_TTL,
    _is_rate_limited,
    _rate_buckets,
    _sessions,
    _spawn_background_check,
    get_session,
    cmd_start,
    cmd_help,
    cmd_providers,
    handle_photo,
    handle_callback,
    handle_text_search,
    CB_FILTER_YES,
    CB_FILTER_NO,
    CB_NEXT,
    CB_PREV,
    CB_CHANGE_FILTER,
    CB_USE_RESULT,
    CB_TRY_DIFFERENTLY,
)
from image_analyzer import ProductInfo
from providers.base import ProviderResult
from search_backends.base import AmazonItem
import database as db
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_provider_result(
    provider_name: str = "openai/gpt-4o",
    confidence: str = "high",
    product_name: str = "Test Product",
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        model_id="gpt-4o",
        product_name=product_name,
        brand="TestBrand",
        category="Electronics",
        key_features=["feature1", "feature2"],
        amazon_search_query="test product query",
        alternative_query="test alt query",
        confidence=confidence,
        notes="test notes",
        latency_ms=500,
        input_tokens=700,
        output_tokens=120,
        cost_usd=0.002,
    )


def _make_amazon_item(
    asin: str = "B08TEST001",
    title: str = "Test Item",
    price: float = 29.99,
    is_prime: bool = True,
    is_fba: bool = False,
    is_sold_by_amazon: bool = False,
) -> AmazonItem:
    return AmazonItem(
        asin=asin,
        title=title,
        image_url="https://example.com/img.jpg",
        price_usd=price,
        currency="USD",
        rating=4.5,
        review_count=100,
        is_amazon_fulfilled=is_fba,
        is_sold_by_amazon=is_sold_by_amazon,
        is_prime=is_prime,
        availability="In Stock",
    )


def _make_update(user_id: int = 42, text: str = "", has_photo: bool = False) -> MagicMock:
    """Build a mock telegram Update."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock(return_value=MagicMock(
        edit_text=AsyncMock(),
        message_id=100,
    ))
    update.message.text = text
    update.message.caption = None

    if has_photo:
        photo_obj = MagicMock()
        photo_obj.file_id = "file_id_123"
        photo_obj.file_unique_id = "unique_123"
        photo_obj.file_size = 1024
        update.message.photo = [photo_obj]
    else:
        update.message.photo = []

    return update


def _make_context(args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot.get_file = AsyncMock(return_value=MagicMock(
        download_as_bytearray=AsyncMock(return_value=bytearray(b"fake_image_bytes")),
    ))
    return ctx


def _make_callback_update(user_id: int = 42, data: str = "") -> MagicMock:
    """Build a mock Update with a callback_query."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_media = AsyncMock()
    update.callback_query.edit_message_caption = AsyncMock()
    update.callback_query.message.chat_id = 1001
    update.callback_query.message.delete = AsyncMock()
    return update


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_bot_state():
    """Reset bot-level global state before each test."""
    _rate_buckets.clear()
    _sessions.clear()
    _analysis_cache.clear()
    yield
    _rate_buckets.clear()
    _sessions.clear()
    _analysis_cache.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 1. UserSession
# ══════════════════════════════════════════════════════════════════════════════

class TestUserSessionDefaults:
    def test_default_values(self):
        s = UserSession()
        assert s.all_provider_results == []
        assert s.chosen_result is None
        assert s.product_info is None
        assert s.chosen_provider_idx == 0
        assert s.all_items == []
        assert s.filtered_items == []
        assert s.israel_only is False
        assert s.page == 0
        assert s.amazon_page == 1
        assert s.more_available is True
        assert s.results_msg_id is None
        assert s.image_bytes is None
        assert s.is_admin is None

    def test_total_items_empty(self):
        s = UserSession()
        assert s.total_items == 1  # max(1, 0)

    def test_total_items_with_data(self):
        s = UserSession()
        s.filtered_items = [_make_amazon_item(asin=f"B0{i}") for i in range(5)]
        assert s.total_items == 5

    def test_current_item_empty(self):
        s = UserSession()
        assert s.current_item() is None

    def test_current_item_returns_page_item(self):
        s = UserSession()
        items = [_make_amazon_item(asin=f"ASIN00000{i}") for i in range(3)]
        s.filtered_items = items
        s.page = 1
        assert s.current_item() is items[1]

    def test_current_item_clamps_page(self):
        """Page beyond bounds is clamped to last item."""
        s = UserSession()
        items = [_make_amazon_item(asin="B000000001")]
        s.filtered_items = items
        s.page = 99
        assert s.current_item() is items[0]

    def test_current_page_items_returns_list(self):
        s = UserSession()
        item = _make_amazon_item()
        s.filtered_items = [item]
        s.page = 0
        result = s.current_page_items()
        assert result == [item]

    def test_current_page_items_empty(self):
        s = UserSession()
        assert s.current_page_items() == []


class TestUserSessionFilter:
    def test_apply_filter_israel_only(self):
        s = UserSession()
        prime_item = _make_amazon_item(asin="PRIME00001", is_prime=True)
        third_party = _make_amazon_item(asin="THIRD00001", is_prime=False, is_fba=False, is_sold_by_amazon=False)
        s.all_items = [prime_item, third_party]

        s.apply_filter(israel_only=True)
        assert s.israel_only is True
        assert s.page == 0
        # Only the prime item qualifies
        assert len(s.filtered_items) == 1
        assert s.filtered_items[0].asin == "PRIME00001"

    def test_apply_filter_show_all(self):
        s = UserSession()
        prime_item = _make_amazon_item(asin="PRIME00001", is_prime=True)
        third_party = _make_amazon_item(asin="THIRD00001", is_prime=False, is_fba=False, is_sold_by_amazon=False)
        s.all_items = [prime_item, third_party]

        s.apply_filter(israel_only=False)
        assert s.israel_only is False
        assert len(s.filtered_items) == 2

    def test_apply_filter_israel_no_eligible_falls_back_to_all(self):
        """When no items qualify for Israel, show all items anyway."""
        s = UserSession()
        third_party = _make_amazon_item(asin="THIRD00001", is_prime=False, is_fba=False, is_sold_by_amazon=False)
        s.all_items = [third_party]

        s.apply_filter(israel_only=True)
        assert len(s.filtered_items) == 1  # falls back to all items

    def test_append_items_extends_and_refilters(self):
        s = UserSession()
        s.all_items = [_make_amazon_item(asin="B000000001", is_prime=True)]
        s.israel_only = True
        s.apply_filter(True)
        assert len(s.filtered_items) == 1

        new_item = _make_amazon_item(asin="B000000002", is_prime=True)
        s.append_items([new_item])
        assert len(s.all_items) == 2
        assert len(s.filtered_items) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 2. get_session
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSession:
    def test_creates_new_session(self):
        s = get_session(999)
        assert isinstance(s, UserSession)
        assert 999 in _sessions

    def test_returns_existing_session(self):
        s1 = get_session(999)
        s1.page = 7
        s2 = get_session(999)
        assert s2.page == 7
        assert s1 is s2


# ══════════════════════════════════════════════════════════════════════════════
# 3. _analysis_cache — dedup TTL logic
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalysisCache:
    def test_cache_miss_when_empty(self):
        cached = _analysis_cache.get("some_key")
        assert cached is None

    def test_cache_hit_within_ttl(self):
        winner = _make_provider_result()
        all_results = [winner]
        _analysis_cache["unique_abc"] = (time.monotonic(), winner, all_results)

        cached = _analysis_cache.get("unique_abc")
        assert cached is not None
        ts, w, ar = cached
        assert (time.monotonic() - ts) < _ANALYSIS_CACHE_TTL
        assert w is winner

    def test_cache_expired_after_ttl(self, monkeypatch):
        winner = _make_provider_result()
        all_results = [winner]
        # Store with a timestamp in the past
        old_ts = time.monotonic() - _ANALYSIS_CACHE_TTL - 10
        _analysis_cache["unique_old"] = (old_ts, winner, all_results)

        cached = _analysis_cache.get("unique_old")
        assert cached is not None
        ts, _, _ = cached
        # The check in handle_photo: (time.monotonic() - ts) < TTL
        assert (time.monotonic() - ts) >= _ANALYSIS_CACHE_TTL


# ══════════════════════════════════════════════════════════════════════════════
# 4. Command handlers: cmd_start, cmd_help, cmd_providers
# ══════════════════════════════════════════════════════════════════════════════

class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_sends_welcome(self):
        update = _make_update()
        ctx = _make_context()
        with patch("style.welcome", return_value="Welcome!"):
            await cmd_start(update, ctx)
        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args
        assert call_kwargs[1]["parse_mode"] == "MarkdownV2"

    @pytest.mark.asyncio
    async def test_start_with_invite_delegates(self):
        update = _make_update()
        ctx = _make_context(args=["invite_abc123"])
        mock_invite = AsyncMock()
        with patch("admin.handle_start_invite", mock_invite):
            await cmd_start(update, ctx)
        mock_invite.assert_called_once_with(update, ctx)
        update.message.reply_text.assert_not_called()


class TestCmdHelp:
    @pytest.mark.asyncio
    async def test_help_sends_text(self):
        update = _make_update()
        ctx = _make_context()
        with patch("style.help_text", return_value="Help text"):
            await cmd_help(update, ctx)
        update.message.reply_text.assert_called_once()


class TestCmdProviders:
    @pytest.mark.asyncio
    async def test_providers_lists_providers(self):
        fake_providers = {"openai/gpt-4o": MagicMock()}
        update = _make_update()
        ctx = _make_context()
        with patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)):
            with patch("bot.backend_name", new=AsyncMock(return_value="rapidapi")):
                with patch("style.providers_info", return_value="Providers info"):
                    await cmd_providers(update, ctx)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_providers_no_providers_error(self):
        update = _make_update()
        ctx = _make_context()
        with patch("bot.get_providers", new=AsyncMock(side_effect=RuntimeError("No providers"))):
            with patch("style.error_no_providers", return_value="No providers!"):
                await cmd_providers(update, ctx)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_providers_backend_name_fails_gracefully(self):
        fake_providers = {"openai/gpt-4o": MagicMock()}
        update = _make_update()
        ctx = _make_context()
        with patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)):
            with patch("bot.backend_name", new=AsyncMock(side_effect=Exception("fail"))):
                with patch("style.providers_info", return_value="Info") as mock_info:
                    await cmd_providers(update, ctx)
        # Should use "not configured" as fallback
        _, kwargs = mock_info.call_args
        # positional args: (providers, vision_mode, search_backend_name)
        args = mock_info.call_args[0]
        assert args[2] == "not configured"


# ══════════════════════════════════════════════════════════════════════════════
# 5. handle_photo
# ══════════════════════════════════════════════════════════════════════════════

class TestHandlePhoto:
    @pytest.mark.asyncio
    async def test_rate_limited_user_gets_error(self):
        update = _make_update(user_id=77, has_photo=True)
        ctx = _make_context()
        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(True, 5, 60))):
            with patch("style.error_rate_limited", return_value="Rate limited"):
                await handle_photo(update, ctx)
        update.message.reply_text.assert_called_once()
        text_arg = update.message.reply_text.call_args[0][0]
        assert text_arg == "Rate limited"

    @pytest.mark.asyncio
    async def test_no_providers_shows_error(self):
        update = _make_update(user_id=10, has_photo=True)
        ctx = _make_context()
        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))):
            with patch("bot.get_providers", new=AsyncMock(side_effect=RuntimeError("none"))):
                with patch("style.error_no_providers", return_value="No providers"):
                    await handle_photo(update, ctx)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_photo_triggers_analysis(self):
        update = _make_update(user_id=10, has_photo=True)
        ctx = _make_context()

        winner = _make_provider_result()
        all_results = [winner]
        fake_providers = {"openai/gpt-4o": MagicMock()}
        reply_msg = AsyncMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)), \
             patch("bot.analyse_image", new=AsyncMock(return_value=(winner, all_results))), \
             patch("bot._compress_image", side_effect=lambda x: x), \
             patch("style.loading_vision", return_value="Loading..."), \
             patch("style.identification_card", return_value="ID card"), \
             patch.object(config, "VISION_MODE", "best"):
            await handle_photo(update, ctx)

        # Should have edited the loading message with the identification card
        reply_msg.edit_text.assert_called()

    @pytest.mark.asyncio
    async def test_photo_uses_dedup_cache(self):
        """When the same photo is submitted within TTL, cache is used instead of re-analyzing."""
        update = _make_update(user_id=10, has_photo=True)
        ctx = _make_context()

        winner = _make_provider_result()
        all_results = [winner]
        fake_providers = {"openai/gpt-4o": MagicMock()}
        reply_msg = AsyncMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        # Pre-populate cache
        cache_key = update.message.photo[-1].file_unique_id
        _analysis_cache[cache_key] = (time.monotonic(), winner, all_results)

        mock_analyse = AsyncMock()

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)), \
             patch("bot.analyse_image", mock_analyse), \
             patch("bot._compress_image", side_effect=lambda x: x), \
             patch("style.loading_vision", return_value="Loading..."), \
             patch("style.identification_card", return_value="ID card"), \
             patch.object(config, "VISION_MODE", "best"):
            await handle_photo(update, ctx)

        # analyse_image should NOT have been called (cache hit)
        mock_analyse.assert_not_called()

    @pytest.mark.asyncio
    async def test_photo_analysis_failure_shows_error(self):
        update = _make_update(user_id=10, has_photo=True)
        ctx = _make_context()
        fake_providers = {"openai/gpt-4o": MagicMock()}
        reply_msg = AsyncMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)), \
             patch("bot.analyse_image", new=AsyncMock(side_effect=Exception("API down"))), \
             patch("bot._compress_image", side_effect=lambda x: x), \
             patch("style.loading_vision", return_value="Loading..."), \
             patch("style.error_analysis_failed", return_value="Analysis failed"):
            await handle_photo(update, ctx)

        reply_msg.edit_text.assert_called()
        # The error message should have been shown
        last_call = reply_msg.edit_text.call_args
        assert last_call[0][0] == "Analysis failed"

    @pytest.mark.asyncio
    async def test_compare_mode_shows_compare_card(self):
        update = _make_update(user_id=10, has_photo=True)
        ctx = _make_context()

        result1 = _make_provider_result(provider_name="openai/gpt-4o", confidence="high")
        result2 = _make_provider_result(provider_name="google/gemini", confidence="medium")
        all_results = [result1, result2]
        fake_providers = {"openai/gpt-4o": MagicMock(), "google/gemini": MagicMock()}
        reply_msg = AsyncMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value=fake_providers)), \
             patch("bot.analyse_image", new=AsyncMock(return_value=(result1, all_results))), \
             patch("bot._compress_image", side_effect=lambda x: x), \
             patch("style.loading_vision", return_value="Loading..."), \
             patch("style.compare_card", return_value="Compare view"), \
             patch.object(config, "VISION_MODE", "compare"), \
             patch.object(config, "SHOW_COST_INFO", True):
            await handle_photo(update, ctx)

        # In compare mode with >1 result, compare_card should be shown
        reply_msg.edit_text.assert_called()
        call_args = reply_msg.edit_text.call_args
        assert call_args[0][0] == "Compare view"


# ══════════════════════════════════════════════════════════════════════════════
# 6. handle_callback — navigation and filter selection
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleCallbackNavigation:
    @pytest.mark.asyncio
    async def test_prev_decrements_page(self):
        user_id = 42
        session = get_session(user_id)
        items = [_make_amazon_item(asin=f"ASIN00000{i}") for i in range(5)]
        session.filtered_items = items
        session.all_items = items
        session.page = 3
        session.results_msg_id = 200
        session.is_admin = False
        session.chosen_result = _make_provider_result()

        update = _make_callback_update(user_id=user_id, data=CB_PREV)
        ctx = _make_context()
        ctx.bot.send_photo = AsyncMock()

        with patch("bot._render_results", new=AsyncMock()) as mock_render:
            await handle_callback(update, ctx)

        assert session.page == 2
        mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_prev_does_not_go_below_zero(self):
        user_id = 42
        session = get_session(user_id)
        items = [_make_amazon_item()]
        session.filtered_items = items
        session.all_items = items
        session.page = 0
        session.results_msg_id = 200

        update = _make_callback_update(user_id=user_id, data=CB_PREV)
        ctx = _make_context()

        with patch("bot._render_results", new=AsyncMock()):
            await handle_callback(update, ctx)

        assert session.page == 0

    @pytest.mark.asyncio
    async def test_next_increments_page(self):
        user_id = 42
        session = get_session(user_id)
        items = [_make_amazon_item(asin=f"ASIN00000{i}") for i in range(5)]
        session.filtered_items = items
        session.all_items = items
        session.page = 1
        session.more_available = False
        session.results_msg_id = 200

        update = _make_callback_update(user_id=user_id, data=CB_NEXT)
        ctx = _make_context()

        with patch("bot._render_results", new=AsyncMock()) as mock_render:
            await handle_callback(update, ctx)

        assert session.page == 2
        mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_next_at_end_no_more_stays_on_last(self):
        """When at the last item with no more available, page stays put."""
        user_id = 42
        session = get_session(user_id)
        items = [_make_amazon_item(asin=f"ASIN00000{i}") for i in range(3)]
        session.filtered_items = items
        session.all_items = items
        session.page = 2  # last item (index 2 of 3)
        session.more_available = False
        session.results_msg_id = 200

        update = _make_callback_update(user_id=user_id, data=CB_NEXT)
        ctx = _make_context()

        with patch("bot._render_results", new=AsyncMock()):
            await handle_callback(update, ctx)

        assert session.page == 2  # stayed at last

    @pytest.mark.asyncio
    async def test_next_at_end_with_more_triggers_lazy_load(self):
        """When at the last item but more_available, lazy-load fetches more."""
        user_id = 42
        session = get_session(user_id)
        items = [_make_amazon_item(asin=f"ASIN00000{i}") for i in range(3)]
        session.filtered_items = items
        session.all_items = items
        session.page = 2  # last item
        session.more_available = True
        session.results_msg_id = 200
        session.product_info = ProductInfo(
            product_name="Test", brand=None, category="All",
            key_features=[], amazon_search_query="test",
            alternative_query="test", confidence="high", notes="",
        )

        new_items = [_make_amazon_item(asin=f"NEW00000{i}") for i in range(3)]

        update = _make_callback_update(user_id=user_id, data=CB_NEXT)
        ctx = _make_context()

        with patch("bot.search_amazon", new=AsyncMock(return_value=new_items)), \
             patch("bot._render_results", new=AsyncMock()):
            await handle_callback(update, ctx)

        # Items should have been appended
        assert len(session.all_items) == 6
        assert session.page == 3


class TestHandleCallbackFilter:
    @pytest.mark.asyncio
    async def test_filter_expired_session(self):
        """Selecting filter when session has no product_info shows expired message."""
        user_id = 42
        session = get_session(user_id)
        # product_info is None by default

        update = _make_callback_update(user_id=user_id, data=CB_FILTER_YES)
        ctx = _make_context()

        await handle_callback(update, ctx)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "expired" in text.lower() or "Session expired" in text

    @pytest.mark.asyncio
    async def test_filter_yes_triggers_search(self):
        user_id = 42
        session = get_session(user_id)
        session.product_info = ProductInfo(
            product_name="Headphones", brand="Sony", category="Electronics",
            key_features=["noise cancelling"], amazon_search_query="sony headphones",
            alternative_query="wireless headphones", confidence="high", notes="",
        )
        session.chosen_result = _make_provider_result()

        items = [_make_amazon_item(asin="RESULT0001", is_prime=True)]
        update = _make_callback_update(user_id=user_id, data=CB_FILTER_YES)
        ctx = _make_context()
        ctx.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=300))

        with patch("bot.search_amazon", new=AsyncMock(return_value=items)), \
             patch("style.loading_search", return_value="Searching..."), \
             patch("bot._render_results", new=AsyncMock()) as mock_render, \
             patch("database.get_active_tag", new=AsyncMock(return_value="tag-20")), \
             patch("database.log_search", new=AsyncMock()), \
             patch("database.increment_tag_search_count", new=AsyncMock()), \
             patch.object(config, "MAX_RESULTS", 20):
            await handle_callback(update, ctx)

        assert session.israel_only is True
        mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_filter_no_shows_all_items(self):
        user_id = 42
        session = get_session(user_id)
        session.product_info = ProductInfo(
            product_name="Keyboard", brand=None, category="Electronics",
            key_features=[], amazon_search_query="keyboard",
            alternative_query="keyboard", confidence="high", notes="",
        )
        session.chosen_result = _make_provider_result()

        items = [_make_amazon_item(asin="RESULT0001", is_prime=False, is_fba=False, is_sold_by_amazon=False)]
        update = _make_callback_update(user_id=user_id, data=CB_FILTER_NO)
        ctx = _make_context()

        with patch("bot.search_amazon", new=AsyncMock(return_value=items)), \
             patch("style.loading_search", return_value="Searching..."), \
             patch("bot._render_results", new=AsyncMock()) as mock_render, \
             patch("database.get_active_tag", new=AsyncMock(return_value=None)), \
             patch("database.log_search", new=AsyncMock()), \
             patch.object(config, "MAX_RESULTS", 20):
            await handle_callback(update, ctx)

        assert session.israel_only is False
        mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_runtime_error_shows_no_backend(self):
        user_id = 42
        session = get_session(user_id)
        session.product_info = ProductInfo(
            product_name="Mouse", brand=None, category="Electronics",
            key_features=[], amazon_search_query="mouse",
            alternative_query="mouse", confidence="high", notes="",
        )

        update = _make_callback_update(user_id=user_id, data=CB_FILTER_YES)
        ctx = _make_context()

        with patch("bot.search_amazon", new=AsyncMock(side_effect=RuntimeError("No backend"))), \
             patch("style.loading_search", return_value="Searching..."), \
             patch("style.error_no_backend", return_value="No backend!"):
            await handle_callback(update, ctx)

        update.callback_query.edit_message_text.assert_called()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert text == "No backend!"


class TestHandleCallbackChangeFilter:
    @pytest.mark.asyncio
    async def test_change_filter_toggles(self):
        user_id = 42
        session = get_session(user_id)
        session.israel_only = False
        items = [_make_amazon_item(is_prime=True)]
        session.all_items = items
        session.filtered_items = items
        session.results_msg_id = 200

        update = _make_callback_update(user_id=user_id, data=CB_CHANGE_FILTER)
        ctx = _make_context()

        with patch("bot._render_results", new=AsyncMock()):
            await handle_callback(update, ctx)

        assert session.israel_only is True


class TestHandleCallbackUseResult:
    @pytest.mark.asyncio
    async def test_use_result_sets_chosen(self):
        user_id = 42
        session = get_session(user_id)
        r1 = _make_provider_result(provider_name="openai/gpt-4o")
        r2 = _make_provider_result(provider_name="google/gemini")
        session.all_provider_results = [r1, r2]

        update = _make_callback_update(user_id=user_id, data=f"{CB_USE_RESULT}1")
        ctx = _make_context()

        with patch("style.identification_card", return_value="ID card"), \
             patch.object(config, "SHOW_COST_INFO", True):
            await handle_callback(update, ctx)

        assert session.chosen_result is r2
        assert session.chosen_provider_idx == 1
        assert session.product_info is not None
        assert session.product_info.product_name == r2.product_name


class TestHandleCallbackTryDifferently:
    @pytest.mark.asyncio
    async def test_try_differently_cycles_provider(self):
        user_id = 42
        session = get_session(user_id)
        r1 = _make_provider_result(provider_name="openai/gpt-4o", product_name="Widget A")
        r2 = _make_provider_result(provider_name="google/gemini", product_name="Widget B")
        session.all_provider_results = [r1, r2]
        session.chosen_provider_idx = 0
        session.chosen_result = r1
        session.product_info = r1.to_product_info()
        session.israel_only = False
        session.results_msg_id = 200

        new_items = [_make_amazon_item(asin="NEW_RESULT1")]
        update = _make_callback_update(user_id=user_id, data=CB_TRY_DIFFERENTLY)
        ctx = _make_context()

        with patch("bot.search_amazon", new=AsyncMock(return_value=new_items)), \
             patch("style.loading_search", return_value="Loading..."), \
             patch("bot._render_results", new=AsyncMock()) as mock_render, \
             patch.object(config, "MAX_RESULTS", 20):
            await handle_callback(update, ctx)

        assert session.chosen_provider_idx == 1
        assert session.chosen_result is r2
        mock_render.assert_called_once()

    @pytest.mark.asyncio
    async def test_try_differently_wraps_around(self):
        user_id = 42
        session = get_session(user_id)
        r1 = _make_provider_result(provider_name="openai/gpt-4o")
        r2 = _make_provider_result(provider_name="google/gemini")
        session.all_provider_results = [r1, r2]
        session.chosen_provider_idx = 1  # currently at last
        session.chosen_result = r2
        session.product_info = r2.to_product_info()
        session.israel_only = False
        session.results_msg_id = 200

        new_items = [_make_amazon_item()]
        update = _make_callback_update(user_id=user_id, data=CB_TRY_DIFFERENTLY)
        ctx = _make_context()

        with patch("bot.search_amazon", new=AsyncMock(return_value=new_items)), \
             patch("style.loading_search", return_value="Loading..."), \
             patch("bot._render_results", new=AsyncMock()), \
             patch.object(config, "MAX_RESULTS", 20):
            await handle_callback(update, ctx)

        # Should wrap around to index 0
        assert session.chosen_provider_idx == 0
        assert session.chosen_result is r1


# ══════════════════════════════════════════════════════════════════════════════
# 7. handle_text_search
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleTextSearch:
    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        update = _make_update(user_id=10, text="")
        ctx = _make_context()
        await handle_text_search(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limited_shows_error(self):
        update = _make_update(user_id=10, text="headphones")
        ctx = _make_context()
        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(True, 5, 60))), \
             patch("style.error_rate_limited", return_value="Too many requests"):
            await handle_text_search(update, ctx)
        update.message.reply_text.assert_called_once()
        assert update.message.reply_text.call_args[0][0] == "Too many requests"

    @pytest.mark.asyncio
    async def test_text_search_creates_session_and_product_info(self):
        update = _make_update(user_id=10, text="wireless keyboard")
        ctx = _make_context()
        reply_msg = MagicMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.detect_language", return_value="en"), \
             patch("bot.translate_and_refine", new=AsyncMock(return_value=("wireless keyboard", "wireless keyboard"))), \
             patch("style.text_search_ready", return_value="Ready text"):
            await handle_text_search(update, ctx)

        session = _sessions[10]
        assert session.product_info is not None
        assert session.product_info.product_name == "wireless keyboard"
        # Should call reply_text twice: once for "Searching..." and once for filter keyboard
        assert update.message.reply_text.call_count >= 1

    @pytest.mark.asyncio
    async def test_text_search_translation_failure_uses_original(self):
        update = _make_update(user_id=10, text="клавиатура")
        ctx = _make_context()
        reply_msg = MagicMock()
        reply_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=reply_msg)

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.detect_language", return_value="ru"), \
             patch("bot.translate_and_refine", new=AsyncMock(side_effect=Exception("Translation API down"))), \
             patch("style.text_search_ready", return_value="Ready"):
            await handle_text_search(update, ctx)

        session = _sessions[10]
        # Falls back to original text
        assert session.product_info.product_name == "клавиатура"


# ══════════════════════════════════════════════════════════════════════════════
# 8. _spawn_background_check
# ══════════════════════════════════════════════════════════════════════════════

class TestSpawnBackgroundCheck:
    def test_no_chat_id_returns_early(self):
        session = UserSession()
        session.results_msg_id = 100
        coro = AsyncMock()()
        # chat_id is None → should not create task
        with patch("asyncio.create_task") as mock_task:
            _spawn_background_check(coro, MagicMock(), session, chat_id=None)
        mock_task.assert_not_called()
        # Clean up the coroutine to avoid RuntimeWarning
        coro.close()

    def test_no_msg_id_returns_early(self):
        session = UserSession()
        session.results_msg_id = None
        coro = AsyncMock()()
        with patch("asyncio.create_task") as mock_task:
            _spawn_background_check(coro, MagicMock(), session, chat_id=123)
        mock_task.assert_not_called()
        coro.close()

    def test_valid_params_creates_task(self):
        session = UserSession()
        session.results_msg_id = 100
        coro = AsyncMock()()
        with patch("asyncio.create_task") as mock_task:
            _spawn_background_check(coro, MagicMock(), session, chat_id=123)
        mock_task.assert_called_once()
        # Close the coroutine to avoid RuntimeWarning if create_task was mocked
        coro.close()


# ══════════════════════════════════════════════════════════════════════════════
# 9. Photo size validation and async Pillow offloading
# ══════════════════════════════════════════════════════════════════════════════

class TestOversizedPhoto:
    @pytest.mark.asyncio
    async def test_oversized_photo_rejected_with_friendly_message(self):
        """When photo.file_size > 10MB, handle_photo returns a friendly message."""
        update = _make_update(user_id=42, has_photo=True)
        ctx = _make_context()
        # Set file_size above the 10MB limit
        update.message.photo[-1].file_size = 11 * 1024 * 1024  # 11 MB

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value={"openai/gpt-4o": MagicMock()})):
            await handle_photo(update, ctx)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        # Must mention size problem in a user-friendly way
        assert "large" in call_text.lower() or "10" in call_text or "MB" in call_text

    @pytest.mark.asyncio
    async def test_oversized_photo_stops_processing(self):
        """After sending the size rejection, no further processing occurs."""
        update = _make_update(user_id=42, has_photo=True)
        ctx = _make_context()
        update.message.photo[-1].file_size = 15 * 1024 * 1024  # 15 MB

        mock_analyse = AsyncMock()

        with patch("bot._is_rate_limited", new=AsyncMock(return_value=(False, 5, 60))), \
             patch("bot.get_providers", new=AsyncMock(return_value={"openai/gpt-4o": MagicMock()})), \
             patch("bot.analyse_image", mock_analyse):
            await handle_photo(update, ctx)

        # analyse_image must NOT be called — we stopped at the size check
        mock_analyse.assert_not_called()


class TestCompressImageAsync:
    @pytest.mark.asyncio
    async def test_compress_image_async_uses_to_thread(self):
        """_compress_image_async must delegate to asyncio.to_thread."""
        from bot import _compress_image_async, _compress_image

        with patch("asyncio.to_thread", new=AsyncMock(return_value=b"compressed")) as mock_thread:
            result = await _compress_image_async(b"raw_image_data")

        mock_thread.assert_called_once_with(_compress_image, b"raw_image_data")
        assert result == b"compressed"

    @pytest.mark.asyncio
    async def test_compress_image_async_propagates_exception(self):
        """Exceptions from the sync compress function propagate correctly."""
        from bot import _compress_image_async

        with patch("asyncio.to_thread", new=AsyncMock(side_effect=ValueError("bad image"))):
            with pytest.raises(ValueError, match="bad image"):
                await _compress_image_async(b"broken")
