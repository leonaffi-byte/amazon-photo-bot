"""
tests/test_cmd_shorten.py — Tests for the /shorten admin command

Tests cover:
- Non-admin silently ignored
- Missing argument shows usage
- Invalid URL (no ASIN) shows error
- Bare ASIN accepted
- Standard Amazon dp/ URL → extracts ASIN, builds affiliate link
- URL with product title + /dp/ASIN/ref=... → extracts ASIN
- No active affiliate tag → link without tag
- Shortener failure → original URL still returned
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from bot import cmd_shorten


# ── Helpers ────────────────────────────────────────────────────────────────────

ADMIN_ID     = 999
NON_ADMIN_ID = 123

def _make_update(text: str, user_id: int = ADMIN_ID) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text      = text
    update.message.reply_text = AsyncMock()
    return update


def _make_context() -> MagicMock:
    return MagicMock()


def _patch_admin(is_admin: bool = True):
    return patch("database.is_admin_in_db", new=AsyncMock(return_value=is_admin))


def _patch_tag(tag: str | None = "mytag-20"):
    return patch("database.get_active_tag", new=AsyncMock(return_value=tag))


def _patch_shorten(short: str = "https://amznl.cc/abc1234"):
    return patch("url_shortener.shorten", new=AsyncMock(return_value=short))


# ── Non-admin ignored ──────────────────────────────────────────────────────────

class TestNonAdmin:
    @pytest.mark.asyncio
    async def test_non_admin_ignored(self):
        update = _make_update("/shorten https://amazon.com/dp/B08XYZ12AB", NON_ADMIN_ID)
        with patch.object(config, "ADMIN_IDS", set()):
            with _patch_admin(False):
                await cmd_shorten(update, _make_context())
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_in_config_allowed(self):
        update = _make_update("/shorten B08XYZ12AB", ADMIN_ID)
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag():
                with _patch_shorten():
                    await cmd_shorten(update, _make_context())
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_in_db_allowed(self):
        update = _make_update("/shorten B08XYZ12AB", ADMIN_ID)
        with patch.object(config, "ADMIN_IDS", set()):   # not in config
            with _patch_admin(True):                       # but in DB
                with _patch_tag():
                    with _patch_shorten():
                        await cmd_shorten(update, _make_context())
        update.message.reply_text.assert_called_once()


# ── Argument validation ────────────────────────────────────────────────────────

class TestArgValidation:
    @pytest.mark.asyncio
    async def test_no_argument_shows_usage(self):
        update = _make_update("/shorten")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            await cmd_shorten(update, _make_context())
        call_args = update.message.reply_text.call_args
        assert "Usage" in call_args[0][0] or "usage" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_empty_argument_shows_usage(self):
        update = _make_update("/shorten   ")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            await cmd_shorten(update, _make_context())
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text or "usage" in text.lower()

    @pytest.mark.asyncio
    async def test_invalid_url_no_asin(self):
        update = _make_update("/shorten https://google.com/search?q=headphones")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            await cmd_shorten(update, _make_context())
        text = update.message.reply_text.call_args[0][0]
        assert "❌" in text or "ASIN" in text


# ── ASIN extraction ────────────────────────────────────────────────────────────

class TestAsinExtraction:
    @pytest.mark.asyncio
    async def test_bare_asin(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("mytag-20"):
                with _patch_shorten("https://amznl.cc/abc1234") as mock_short:
                    await cmd_shorten(update, _make_context())
        # Verify affiliate URL was built with the ASIN
        long_url_used = mock_short.call_args[0][0]
        assert "B08XYZ12AB" in long_url_used
        assert "mytag-20"    in long_url_used

    @pytest.mark.asyncio
    async def test_simple_dp_url(self):
        update = _make_update("/shorten https://www.amazon.com/dp/B09ABC12DE")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("mytag-20"):
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url_used = mock_short.call_args[0][0]
        assert "B09ABC12DE" in long_url_used

    @pytest.mark.asyncio
    async def test_dp_url_with_ref(self):
        url = "https://www.amazon.com/Sony-Headphones/dp/B08XYZ12AB/ref=sr_1_1?keywords=headphones"
        update = _make_update(f"/shorten {url}")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag():
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url_used = mock_short.call_args[0][0]
        assert "B08XYZ12AB" in long_url_used

    @pytest.mark.asyncio
    async def test_gp_product_url(self):
        url = "https://www.amazon.com/gp/product/B00EXAMP10"   # exactly 10 chars
        update = _make_update(f"/shorten {url}")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag():
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url_used = mock_short.call_args[0][0]
        assert "B00EXAMP10" in long_url_used

    @pytest.mark.asyncio
    async def test_asin_uppercased(self):
        update = _make_update("/shorten b08xyz12ab")   # lowercase
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag():
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url_used = mock_short.call_args[0][0]
        assert "B08XYZ12AB" in long_url_used   # uppercased


# ── Affiliate tag handling ─────────────────────────────────────────────────────

class TestAffiliateTag:
    @pytest.mark.asyncio
    async def test_affiliate_tag_injected(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("mystore-20"):
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url = mock_short.call_args[0][0]
        assert "tag=mystore-20"   in long_url
        assert "linkCode=ogi"      in long_url

    @pytest.mark.asyncio
    async def test_no_affiliate_tag_still_works(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag(None):    # no active tag
                with _patch_shorten() as mock_short:
                    await cmd_shorten(update, _make_context())
        long_url = mock_short.call_args[0][0]
        assert "B08XYZ12AB" in long_url
        assert "tag="        not in long_url


# ── Reply content ──────────────────────────────────────────────────────────────

class TestReplyContent:
    @pytest.mark.asyncio
    async def test_reply_contains_short_url(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("mytag-20"):
                with _patch_shorten("https://amznl.cc/testxyz"):
                    await cmd_shorten(update, _make_context())
        reply = update.message.reply_text.call_args[0][0]
        # Reply is MarkdownV2-escaped so dots become \.  — check the code part only
        assert "testxyz" in reply

    @pytest.mark.asyncio
    async def test_reply_contains_asin(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag():
                with _patch_shorten():
                    await cmd_shorten(update, _make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "B08XYZ12AB" in reply

    @pytest.mark.asyncio
    async def test_reply_shows_tag(self):
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("store-21"):
                with _patch_shorten():
                    await cmd_shorten(update, _make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "store" in reply   # tag mentioned in reply

    @pytest.mark.asyncio
    async def test_shortener_failure_still_replies(self):
        """Even if shortener fails (returns original URL), reply is still sent."""
        long_url = "https://www.amazon.com/dp/B08XYZ12AB?tag=t-20&linkCode=ogi&th=1&psc=1"
        update = _make_update("/shorten B08XYZ12AB")
        with patch.object(config, "ADMIN_IDS", {ADMIN_ID}):
            with _patch_tag("t-20"):
                # Shortener returns original URL (failure mode)
                with patch("url_shortener.shorten", new=AsyncMock(return_value=long_url)):
                    await cmd_shorten(update, _make_context())
        update.message.reply_text.assert_called_once()
