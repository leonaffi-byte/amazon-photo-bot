"""
tests/test_israel_scraper.py — Unit tests for israel_scraper.py

Tests cover:
- _parse_html: no-ship phrases, free shipping, unknown page, CAPTCHA detection
- _extract_csrf: various token formats
- _is_captcha: captcha signals
- _extract_delivery_section: basic extraction
- IsraelShippingResult: dataclass fields
- check_shipping: DB cache hit, proxy-not-configured path, scrape-and-cache flow
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from israel_scraper import (
    IsraelShippingResult,
    _extract_csrf,
    _is_captcha,
    _parse_html,
    _unverified,
    is_configured,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_valid_product_page(delivery_text: str = "") -> str:
    """Minimal Amazon product page HTML that passes the sanity check."""
    return f"""<html>
<head><title>Product Title</title></head>
<body>
  <span id="productTitle">Sony WH-1000XM5</span>
  <div id="deliveryBlockSelectAsin">
    {delivery_text}
  </div>
</body>
</html>"""


# ── _is_captcha ────────────────────────────────────────────────────────────────

class TestIsCaptcha:
    def test_normal_page_not_captcha(self):
        assert not _is_captcha("<html><body>Normal page</body></html>")

    def test_enter_characters_captcha(self):
        html = "Enter the characters you see below to verify you're a human."
        assert _is_captcha(html)

    def test_robot_check_captcha(self):
        html = "Sorry, we just need to make sure you're not a robot."
        assert _is_captcha(html)

    def test_amazon_support_email_captcha(self):
        html = "If you need help, contact api-services-support@amazon.com"
        assert _is_captcha(html)

    def test_type_characters_captcha(self):
        html = "Type the characters you see in this image:"
        assert _is_captcha(html)

    def test_case_insensitive(self):
        html = "ENTER THE CHARACTERS YOU SEE BELOW"
        assert _is_captcha(html)


# ── _extract_csrf ──────────────────────────────────────────────────────────────

class TestExtractCsrf:
    def test_json_style_double_quotes(self):
        html = '"anti-csrftoken-a2z": "ABCDEF1234567890"'
        assert _extract_csrf(html) == "ABCDEF1234567890"

    def test_json_style_single_quotes(self):
        html = "'anti-csrftoken-a2z' : 'XYZXYZXYZXYZXYZX'"
        assert _extract_csrf(html) == "XYZXYZXYZXYZXYZX"

    def test_html_input_name_value(self):
        html = 'name="anti-csrftoken-a2z" value="TOKEN12345678901"'
        assert _extract_csrf(html) == "TOKEN12345678901"

    def test_not_found_returns_none(self):
        assert _extract_csrf("<html>no token here</html>") is None

    def test_short_value_ignored(self):
        # Values shorter than 10 chars should not match (they're noise)
        html = '"anti-csrftoken-a2z": "short"'
        assert _extract_csrf(html) is None


# ── _parse_html ────────────────────────────────────────────────────────────────

class TestParseHtml:
    # ── No-ship cases ──────────────────────────────────────────────────────────
    def test_cannot_be_shipped(self):
        html = make_valid_product_page(
            "This item cannot be shipped to your selected delivery location."
        )
        result = _parse_html("ASIN123456", html)
        assert result.verified is True
        assert result.ships_to_israel is False
        assert result.is_free_shipping is False
        assert "does not ship" in result.note.lower() or "not ship" in result.note.lower()

    def test_does_not_ship_to_israel(self):
        html = make_valid_product_page(
            "This item does not ship to Israel."
        )
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is False

    def test_not_available_location(self):
        html = make_valid_product_page(
            "Not available for your location."
        )
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is False

    def test_cannot_be_delivered_to_israel(self):
        html = make_valid_product_page("Cannot be delivered to Israel.")
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is False

    # ── Free shipping ──────────────────────────────────────────────────────────
    def test_free_delivery_without_israel_mention(self):
        html = make_valid_product_page("FREE delivery Mon, Mar 3")
        result = _parse_html("ASIN123456", html)
        assert result.verified is True
        assert result.ships_to_israel is True
        assert result.is_free_shipping is True
        assert "free" in result.note.lower()

    def test_free_delivery_with_israel_mentioned(self):
        html = make_valid_product_page(
            "FREE delivery to Israel on orders over $49"
        )
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is True
        assert result.is_free_shipping is True
        # Should have higher-confidence note mentioning Israel
        assert "israel" in result.note.lower()

    def test_free_shipping_phrase(self):
        html = make_valid_product_page("Ships Free with Prime")
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is True
        assert result.is_free_shipping is True

    # ── Ships but paid ─────────────────────────────────────────────────────────
    def test_ships_paid(self):
        html = make_valid_product_page("$5.99 shipping to your location.")
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is True
        assert result.is_free_shipping is False
        assert "Verified" in result.note

    # ── Invalid / unknown page ─────────────────────────────────────────────────
    def test_empty_page_unverified(self):
        result = _parse_html("ASIN123456", "<html><body>Error</body></html>")
        assert result.verified is False

    def test_not_a_product_page(self):
        html = "<html><body>Search results</body></html>"
        result = _parse_html("ASIN123456", html)
        assert result.verified is False

    # ── ASIN preserved ─────────────────────────────────────────────────────────
    def test_asin_preserved(self):
        html = make_valid_product_page()
        result = _parse_html("B08XYZ12AB", html)
        assert result.asin == "B08XYZ12AB"


# ── _unverified ────────────────────────────────────────────────────────────────

class TestUnverified:
    def test_returns_unverified_result(self):
        result = _unverified("B0TEST00001", "CAPTCHA")
        assert result.asin == "B0TEST00001"
        assert result.verified is False
        assert result.ships_to_israel is None
        assert result.is_free_shipping is None
        assert "CAPTCHA" in result.note


# ── is_configured ──────────────────────────────────────────────────────────────

class TestIsConfigured:
    @pytest.mark.asyncio
    async def test_not_configured_when_no_key(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            assert await is_configured() is False

    @pytest.mark.asyncio
    async def test_not_configured_when_empty(self):
        with patch("key_store.get", new=AsyncMock(return_value="  ")):
            assert await is_configured() is False

    @pytest.mark.asyncio
    async def test_configured_when_url_set(self):
        with patch("key_store.get", new=AsyncMock(return_value="socks5://1.2.3.4:1080")):
            assert await is_configured() is True


# ── check_shipping: no proxy configured ───────────────────────────────────────

class TestCheckShippingNoProxy:
    @pytest.mark.asyncio
    async def test_returns_unverified_when_no_proxy(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            from israel_scraper import check_shipping
            result = await check_shipping("B08XYZ12AB")
        assert result.verified is False
        assert "not configured" in result.note.lower()


# ── check_shipping: DB cache hit ──────────────────────────────────────────────

class TestCheckShippingCacheHit:
    @pytest.mark.asyncio
    async def test_returns_cached_result(self):
        cached = IsraelShippingResult(
            asin             = "B0CACHED001",
            verified         = True,
            ships_to_israel  = True,
            is_free_shipping = True,
            note             = "✅ Verified: ships free to 🇮🇱 Israel",
        )
        with patch("key_store.get", new=AsyncMock(return_value="socks5://1.2.3.4:1080")):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=cached)):
                from israel_scraper import check_shipping
                result = await check_shipping("B0CACHED001")
        assert result is cached
        assert result.verified is True
        assert result.is_free_shipping is True


# ── check_shipping: scrape path ───────────────────────────────────────────────

class TestCheckShippingScrape:
    @pytest.mark.asyncio
    async def test_scrape_and_cache_result(self):
        """When cache misses, scrape runs and result is stored."""
        scraped = IsraelShippingResult(
            asin             = "B0SCRAPE001",
            verified         = True,
            ships_to_israel  = True,
            is_free_shipping = False,
            note             = "🟡 Verified: ships to 🇮🇱 Israel",
        )
        with patch("key_store.get", new=AsyncMock(return_value="http://proxy:8080")):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                with patch("database.set_israel_cache", new=AsyncMock()) as mock_set:
                    with patch("israel_scraper._scrape", new=AsyncMock(return_value=scraped)):
                        from israel_scraper import check_shipping
                        result = await check_shipping("B0SCRAPE001")

        assert result.verified is True
        assert result.ships_to_israel is True
        mock_set.assert_called_once()

    @pytest.mark.asyncio
    async def test_unverified_result_not_cached(self):
        """Unverified results (e.g. CAPTCHA) should not be stored in cache."""
        unverified = _unverified("B0CAPTCHA1", "CAPTCHA")
        with patch("key_store.get", new=AsyncMock(return_value="http://proxy:8080")):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                with patch("database.set_israel_cache", new=AsyncMock()) as mock_set:
                    with patch("israel_scraper._scrape", new=AsyncMock(return_value=unverified)):
                        from israel_scraper import check_shipping
                        result = await check_shipping("B0CAPTCHA1")

        assert result.verified is False
        mock_set.assert_not_called()   # should NOT cache unverified results
