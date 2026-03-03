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

class TestGetProxyUrl:
    """Tests for _get_proxy_url() — the proxy resolution logic."""

    @pytest.mark.asyncio
    async def test_decodo_takes_priority_over_wireguard(self):
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {
                "decodo_user":     "myuser",
                "decodo_password": "mypass",
                "israel_proxy_url": "socks5://wg:1080",
            }.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert "gate.decodo.com" in url
        assert "myuser-country-IL" in url
        assert "mypass" in url
        assert "socks5://wg:1080" not in url

    @pytest.mark.asyncio
    async def test_decodo_url_format(self):
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {"decodo_user": "u1", "decodo_password": "p1"}.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert url == "http://u1-country-IL:p1@gate.decodo.com:7000"

    @pytest.mark.asyncio
    async def test_falls_back_to_wireguard_when_no_decodo(self):
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {"israel_proxy_url": "socks5://1.2.3.4:1080"}.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert url == "socks5://1.2.3.4:1080"

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_configured(self):
        from israel_scraper import _get_proxy_url
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            url = await _get_proxy_url()
        assert url is None

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_wireguard_url(self):
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {"israel_proxy_url": "  socks5://1.2.3.4:1080  "}.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert url == "socks5://1.2.3.4:1080"

    @pytest.mark.asyncio
    async def test_decodo_only_user_no_password_falls_back(self):
        """If only one Decodo field is set, fall back to WireGuard."""
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {
                "decodo_user": "u1",
                # no decodo_password
                "israel_proxy_url": "socks5://1.2.3.4:1080",
            }.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert "1.2.3.4" in url   # fell back to WireGuard


class TestIsConfigured:
    @pytest.mark.asyncio
    async def test_not_configured_when_nothing_set(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            assert await is_configured() is False

    @pytest.mark.asyncio
    async def test_not_configured_when_empty_string(self):
        with patch("key_store.get", new=AsyncMock(return_value="  ")):
            assert await is_configured() is False

    @pytest.mark.asyncio
    async def test_configured_via_wireguard_url(self):
        async def fake_get(key):
            return {"israel_proxy_url": "socks5://1.2.3.4:1080"}.get(key)
        with patch("key_store.get", new=fake_get):
            assert await is_configured() is True

    @pytest.mark.asyncio
    async def test_configured_via_decodo(self):
        async def fake_get(key):
            return {"decodo_user": "u", "decodo_password": "p"}.get(key)
        with patch("key_store.get", new=fake_get):
            assert await is_configured() is True


# ── check_shipping: no proxy configured ───────────────────────────────────────

class TestCheckShippingNoProxy:
    @pytest.mark.asyncio
    async def test_returns_unverified_when_no_proxy(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            from israel_scraper import check_shipping
            result = await check_shipping("B08XYZ12AB")
        assert result.verified is False
        assert "proxy" in result.note.lower()   # "No proxy configured" or similar

    @pytest.mark.asyncio
    async def test_returns_unverified_when_only_one_decodo_field(self):
        """Partial Decodo config (only user, no password) + no WireGuard → unverified."""
        async def fake_get(key):
            return {"decodo_user": "user_only"}.get(key)   # no password, no WG
        with patch("key_store.get", new=fake_get):
            from israel_scraper import check_shipping
            result = await check_shipping("B08XYZ12AB")
        assert result.verified is False


# ── check_shipping: DB cache hit ──────────────────────────────────────────────

async def _wg_only_get(key):
    """Fake key_store.get that returns a WireGuard proxy for israel_proxy_url only."""
    return {
        "israel_proxy_url": "socks5://1.2.3.4:1080",
    }.get(key)


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
        with patch("key_store.get", new=_wg_only_get):
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
        with patch("key_store.get", new=_wg_only_get):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                with patch("database.set_israel_cache", new=AsyncMock()) as mock_set:
                    with patch("israel_scraper._scrape", new=AsyncMock(return_value=scraped)):
                        from israel_scraper import check_shipping
                        result = await check_shipping("B0SCRAPE001")

        assert result.verified is True
        assert result.ships_to_israel is True
        mock_set.assert_called_once()

    @pytest.mark.asyncio
    async def test_scrape_via_decodo(self):
        """When Decodo keys are set, proxy URL is built correctly."""
        scraped = IsraelShippingResult(
            asin="B0DECODO001", verified=True,
            ships_to_israel=True, is_free_shipping=True,
            note="✅ Verified",
        )
        async def decodo_get(key):
            return {"decodo_user": "myuser", "decodo_password": "mypass"}.get(key)

        with patch("key_store.get", new=decodo_get):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                with patch("database.set_israel_cache", new=AsyncMock()):
                    with patch("israel_scraper._scrape", new=AsyncMock(return_value=scraped)) as mock_scrape:
                        from israel_scraper import check_shipping
                        result = await check_shipping("B0DECODO001")

        # Verify Decodo proxy URL was passed to _scrape
        call_proxy = mock_scrape.call_args[0][1]   # second positional arg
        assert "gate.decodo.com" in call_proxy
        assert "myuser-country-IL" in call_proxy

    @pytest.mark.asyncio
    async def test_unverified_result_not_cached(self):
        """Unverified results (e.g. CAPTCHA) should not be stored in cache."""
        unverified = _unverified("B0CAPTCHA1", "CAPTCHA")
        with patch("key_store.get", new=_wg_only_get):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                with patch("database.set_israel_cache", new=AsyncMock()) as mock_set:
                    with patch("israel_scraper._scrape", new=AsyncMock(return_value=unverified)):
                        from israel_scraper import check_shipping
                        result = await check_shipping("B0CAPTCHA1")

        assert result.verified is False
        mock_set.assert_not_called()
