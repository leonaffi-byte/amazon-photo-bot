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
    _score_shipping_confidence,
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

    # ── Free shipping (confidence-based) ──────────────────────────────────────
    def test_free_delivery_without_fba_or_israel_scores_red(self):
        # "FREE delivery Mon, Mar 3" alone scores 0.35 (below 0.4 threshold)
        # → red tier: not enough confidence to confirm Israel shipping
        html = make_valid_product_page("FREE delivery Mon, Mar 3")
        result = _parse_html("ASIN123456", html)
        assert result.verified is True
        assert result.ships_to_israel is False

    def test_free_delivery_with_israel_mentioned_yellow_tier(self):
        # FREE delivery phrase (0.35) + "israel" in delivery section (0.20) = 0.55
        # → yellow tier: ships=True, is_free_shipping=False (not high enough for green)
        html = make_valid_product_page(
            "FREE delivery to Israel on orders over $49"
        )
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is True
        assert result.is_free_shipping is False

    def test_free_shipping_with_prime_yellow_tier(self):
        # "Ships Free" (0.35) + "prime" in delivery section (0.20) = 0.55
        # → yellow tier: ships=True, is_free_shipping=False
        html = make_valid_product_page("Ships Free with Prime")
        result = _parse_html("ASIN123456", html)
        assert result.ships_to_israel is True
        assert result.is_free_shipping is False

    # ── Ships but paid (now uses confidence scoring) ───────────────────────────
    def test_ships_paid_no_signals_returns_unlikely(self):
        # Under confidence scoring, "$5.99 shipping" with no FBA/Prime/Israel
        # signals scores below 0.4 → unlikely to ship to Israel
        html = make_valid_product_page("$5.99 shipping to your location.")
        result = _parse_html("ASIN123456", html)
        assert result.verified is True
        assert result.ships_to_israel is False

    def test_ships_fba_with_israel_mention(self):
        # FBA + Israel in delivery section → score >= 0.7 → ships_to_israel=True, free=True
        html = make_valid_product_page(
            "Ships from Amazon. Fulfilled by Amazon. Free delivery to Israel."
        )
        result = _parse_html("ASIN123456", html)
        assert result.verified is True
        assert result.ships_to_israel is True
        assert result.is_free_shipping is True

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
        assert "myuser-country-il" in url
        assert "mypass" in url
        assert "socks5://wg:1080" not in url

    @pytest.mark.asyncio
    async def test_decodo_url_format(self):
        from israel_scraper import _get_proxy_url
        async def fake_get(key):
            return {"decodo_user": "u1", "decodo_password": "p1"}.get(key)
        with patch("key_store.get", new=fake_get):
            url = await _get_proxy_url()
        assert url == "http://user-u1-country-il:p1@gate.decodo.com:7000"

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
        assert "myuser-country-il" in call_proxy

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


# ── _score_shipping_confidence ─────────────────────────────────────────────────

class TestScoreShippingConfidence:
    """Unit tests for _score_shipping_confidence() weighted scoring."""

    def test_empty_html_returns_zero(self):
        assert _score_shipping_confidence("", "") == 0.0

    def test_free_ship_phrase_alone_scores_035(self):
        score = _score_shipping_confidence("free delivery today", "free delivery today")
        assert score == pytest.approx(0.35)

    def test_fulfilled_by_amazon_alone_scores_035(self):
        score = _score_shipping_confidence("fulfilled by amazon", "")
        assert score == pytest.approx(0.35)

    def test_ships_from_amazon_alone_scores_035(self):
        score = _score_shipping_confidence("ships from amazon", "")
        assert score == pytest.approx(0.35)

    def test_prime_in_delivery_section_scores_02(self):
        score = _score_shipping_confidence("some content", "prime eligible")
        assert score == pytest.approx(0.20)

    def test_israel_in_delivery_section_scores_02(self):
        score = _score_shipping_confidence("some content", "deliver to israel")
        assert score == pytest.approx(0.20)

    def test_deliver_to_il_in_delivery_section_scores_02(self):
        score = _score_shipping_confidence("deliver to il available", "deliver to il available")
        assert score == pytest.approx(0.20)

    def test_add_to_cart_without_unavailable_scores_01(self):
        score = _score_shipping_confidence("add to cart", "")
        assert score == pytest.approx(0.10)

    def test_add_to_cart_with_currently_unavailable_no_weak_signal(self):
        score = _score_shipping_confidence("add to cart currently unavailable", "")
        assert score == pytest.approx(0.0)

    def test_green_tier_free_ship_plus_fba(self):
        # free_ship_phrase (0.35) + "fulfilled by amazon" (0.35) = 0.70
        score = _score_shipping_confidence(
            "free delivery fulfilled by amazon", "free delivery fulfilled by amazon"
        )
        assert score >= 0.7

    def test_green_tier_free_ship_plus_israel(self):
        # free_ship_phrase (0.35) + israel in delivery (0.20) = 0.55 → not green
        # But free_ship + fba + israel should be green
        score = _score_shipping_confidence(
            "free delivery fulfilled by amazon",
            "free delivery fulfilled by amazon deliver to israel"
        )
        assert score >= 0.7

    def test_yellow_tier_prime_plus_israel(self):
        # prime (0.20) + israel (0.20) = 0.40 → yellow tier
        score = _score_shipping_confidence(
            "prime eligible",
            "prime eligible deliver to israel"
        )
        assert 0.4 <= score < 0.7

    def test_score_capped_at_1(self):
        # All signals together should not exceed 1.0
        score = _score_shipping_confidence(
            "free delivery fulfilled by amazon add to cart ships from amazon",
            "free delivery prime israel deliver to il"
        )
        assert score <= 1.0

    def test_no_positive_signals_scores_zero(self):
        score = _score_shipping_confidence(
            "buy now click here",
            "some delivery info"
        )
        assert score == 0.0


# ── TestFalsePositive: known-negative fixtures ─────────────────────────────────

# These are HTML snippets for products that do NOT ship to Israel.
# Each should score below 0.4 (i.e., return ships_to_israel=False from _parse_html).
# Note: items caught by _NO_SHIP_PHRASES are handled before scoring (early return),
# so these fixtures test the scoring path for items that appear available but aren't.

_NEGATIVE_FIXTURES = [
    # 1. US-only marketplace seller with no international shipping
    make_valid_product_page(
        "Ships from and sold by US-Only-Seller. This seller does not offer "
        "international shipping. Delivery available within the United States only."
    ),
    # 2. Out-of-stock item with no shipping signals
    make_valid_product_page(
        "Currently unavailable. We don't know when or if this item will be back in stock."
    ),
    # 3. Sold by third-party, no Amazon fulfillment, no Israel/Prime/free signals
    make_valid_product_page(
        "Sold by LocalStore. Ships in 5-7 business days. Standard shipping applies."
    ),
    # 4. Product with only domestic US signals
    make_valid_product_page(
        "Get it by Thursday if you order within 2 hours. Free returns on orders over $35 within the US."
    ),
    # 5. Digital/downloadable product (no physical shipping)
    make_valid_product_page(
        "This is a digital download. No physical item will be shipped. "
        "Instant access after purchase."
    ),
]

_POSITIVE_FIXTURES = [
    # 1. FBA item with free shipping phrase
    make_valid_product_page(
        "FREE delivery Tue, Jan 14 to Israel. Ships from and Fulfilled by Amazon."
    ),
    # 2. Prime item with Israel in delivery section
    make_valid_product_page(
        "Prime eligible. FREE delivery to Israel on orders over $49."
    ),
    # 3. Item with "ships from Amazon" + Israel delivery
    make_valid_product_page(
        "Ships from Amazon. Free shipping to Israel available."
    ),
    # 4. FBA with Prime, Israel mentioned
    make_valid_product_page(
        "Fulfilled by Amazon. Prime. Deliver to IL — FREE delivery."
    ),
    # 5. Free delivery phrase + Israel in delivery block
    make_valid_product_page(
        "FREE delivery. Shipping to Israel available via Amazon International."
    ),
]


class TestFalsePositive:
    """Known-negative fixtures: items that do NOT ship to Israel should score < 0.4."""

    def test_us_only_seller_scores_below_threshold(self):
        result = _parse_html("ASIN_NEG_01", _NEGATIVE_FIXTURES[0])
        assert result.ships_to_israel is False, (
            f"Expected ships_to_israel=False for US-only seller, got note={result.note}"
        )

    def test_out_of_stock_scores_below_threshold(self):
        result = _parse_html("ASIN_NEG_02", _NEGATIVE_FIXTURES[1])
        assert result.ships_to_israel is False, (
            f"Expected ships_to_israel=False for OOS item, got note={result.note}"
        )

    def test_third_party_no_signals_scores_below_threshold(self):
        result = _parse_html("ASIN_NEG_03", _NEGATIVE_FIXTURES[2])
        assert result.ships_to_israel is False, (
            f"Expected ships_to_israel=False for 3P seller, got note={result.note}"
        )

    def test_us_domestic_only_scores_below_threshold(self):
        result = _parse_html("ASIN_NEG_04", _NEGATIVE_FIXTURES[3])
        assert result.ships_to_israel is False, (
            f"Expected ships_to_israel=False for US-domestic, got note={result.note}"
        )

    def test_digital_product_scores_below_threshold(self):
        result = _parse_html("ASIN_NEG_05", _NEGATIVE_FIXTURES[4])
        assert result.ships_to_israel is False, (
            f"Expected ships_to_israel=False for digital product, got note={result.note}"
        )

    def test_fp_rate(self):
        """FP rate across all negative fixtures must be below 10%."""
        false_positives = sum(
            1 for html in _NEGATIVE_FIXTURES
            if _parse_html("ASIN_FP", html).ships_to_israel is True
        )
        fp_rate = false_positives / len(_NEGATIVE_FIXTURES)
        assert fp_rate < 0.10, f"FP rate {fp_rate:.0%} >= 10% ({false_positives}/{len(_NEGATIVE_FIXTURES)} false positives)"


class TestFalseNegative:
    """Known-positive fixtures: items that DO ship to Israel should score >= 0.4."""

    def test_fba_free_delivery_to_israel(self):
        result = _parse_html("ASIN_POS_01", _POSITIVE_FIXTURES[0])
        assert result.ships_to_israel is True, (
            f"Expected ships_to_israel=True for FBA+Israel, got note={result.note}"
        )

    def test_prime_free_delivery_to_israel(self):
        result = _parse_html("ASIN_POS_02", _POSITIVE_FIXTURES[1])
        assert result.ships_to_israel is True, (
            f"Expected ships_to_israel=True for Prime+Israel, got note={result.note}"
        )

    def test_ships_from_amazon_plus_israel(self):
        result = _parse_html("ASIN_POS_03", _POSITIVE_FIXTURES[2])
        assert result.ships_to_israel is True, (
            f"Expected ships_to_israel=True for ships_from_amazon+Israel, got note={result.note}"
        )

    def test_fba_prime_deliver_to_il(self):
        result = _parse_html("ASIN_POS_04", _POSITIVE_FIXTURES[3])
        assert result.ships_to_israel is True, (
            f"Expected ships_to_israel=True for FBA+Prime+IL, got note={result.note}"
        )

    def test_free_delivery_amazon_international(self):
        result = _parse_html("ASIN_POS_05", _POSITIVE_FIXTURES[4])
        assert result.ships_to_israel is True, (
            f"Expected ships_to_israel=True for free_delivery+Israel, got note={result.note}"
        )

    def test_fn_rate(self):
        """FN rate across all positive fixtures must be below 15%."""
        false_negatives = sum(
            1 for html in _POSITIVE_FIXTURES
            if _parse_html("ASIN_FN", html).ships_to_israel is not True
        )
        fn_rate = false_negatives / len(_POSITIVE_FIXTURES)
        assert fn_rate < 0.15, f"FN rate {fn_rate:.0%} >= 15% ({false_negatives}/{len(_POSITIVE_FIXTURES)} false negatives)"
