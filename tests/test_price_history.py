"""
tests/test_price_history.py — Tests for price_history.py

Covers:
  - PriceHistory.deal_label property
  - _label_and_price() regex helper
  - _parse_ccc_html() with real-ish HTML fixtures
  - _parse_keepa_response() with stats and raw csv paths
  - _derive_from_csv() maths
  - get_price_history() cache hit / miss / fallback chain
  - _price_history_line() style formatting
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from price_history import (
    PriceHistory,
    _derive_from_csv,
    _label_and_price,
    _parse_ccc_html,
    _parse_keepa_response,
    get_price_history,
)
from style import _price_history_line


# ── PriceHistory.deal_label ───────────────────────────────────────────────────

class TestDealLabel:
    def test_all_time_low(self):
        ph = PriceHistory("B001", "ccc", current=24.99, low_all_time=24.50)
        assert "low" in ph.deal_label.lower() or "🔥" in ph.deal_label

    def test_great_deal_below_85pct_avg(self):
        ph = PriceHistory("B001", "ccc", current=30.00, avg_90d=40.00)
        assert "💸" in ph.deal_label or "great" in ph.deal_label.lower()

    def test_below_avg(self):
        ph = PriceHistory("B001", "ccc", current=37.00, avg_90d=40.00)
        assert "✅" in ph.deal_label or "below" in ph.deal_label.lower()

    def test_no_deal(self):
        ph = PriceHistory("B001", "ccc", current=42.00, avg_90d=40.00)
        assert ph.deal_label == ""

    def test_no_current_no_label(self):
        ph = PriceHistory("B001", "ccc", current=None, avg_90d=38.00)
        assert ph.deal_label == ""


# ── _label_and_price() ────────────────────────────────────────────────────────

class TestLabelAndPrice:
    def test_lowest_price(self):
        label, val = _label_and_price("Lowest $24.99 on Jan 2024")
        assert label == "lowest"
        assert val == pytest.approx(24.99)

    def test_current_price(self):
        label, val = _label_and_price("Current $34.99")
        assert label == "current"
        assert val == pytest.approx(34.99)

    def test_average_price(self):
        label, val = _label_and_price("Average $38.50")
        assert label == "average"
        assert val == pytest.approx(38.50)

    def test_no_price_returns_none(self):
        label, val = _label_and_price("Lowest price not listed")
        assert label is None
        assert val is None

    def test_no_label_returns_none(self):
        label, val = _label_and_price("$29.99 great product")
        assert label is None

    def test_commas_in_price(self):
        label, val = _label_and_price("Lowest $1,299.00")
        assert val == pytest.approx(1299.00)


# ── _parse_ccc_html() ─────────────────────────────────────────────────────────

_CCC_HTML_GOOD = """
<html><body>
<div id="amazon">
  <ul class="stats">
    <li>Current $34.99</li>
    <li>Lowest $24.99 on Jan 15, 2024</li>
    <li>Highest $54.99</li>
    <li>Average $38.50</li>
  </ul>
</div>
</body></html>
"""

_CCC_HTML_NO_AMAZON_SECTION = """
<html><body>
<ul>
  <li>Current $34.99</li>
  <li>Lowest $24.99</li>
  <li>Average $38.50</li>
</ul>
</body></html>
"""

_CCC_HTML_NO_PRICES = """
<html><body><p>Product not found.</p></body></html>
"""


class TestParseCccHtml:
    def test_parses_amazon_section(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_GOOD)
        assert ph is not None
        assert ph.source == "camelcamelcamel"
        assert ph.asin   == "B08TEST001"

    def test_extracts_current_price(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_GOOD)
        assert ph.current == pytest.approx(34.99)

    def test_extracts_atl(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_GOOD)
        assert ph.low_all_time == pytest.approx(24.99)

    def test_extracts_avg(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_GOOD)
        assert ph.avg_90d == pytest.approx(38.50)

    def test_fallback_to_fullpage_scan(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_NO_AMAZON_SECTION)
        assert ph is not None
        assert ph.current == pytest.approx(34.99)

    def test_returns_none_when_no_prices(self):
        ph = _parse_ccc_html("B08TEST001", _CCC_HTML_NO_PRICES)
        assert ph is None

    def test_returns_none_on_empty_html(self):
        ph = _parse_ccc_html("B08TEST001", "")
        assert ph is None


# ── _parse_keepa_response() ───────────────────────────────────────────────────

def _keepa_resp(current=3499, avg30=3850, avg90=3850, atl=2499, min90=2999):
    return {
        "products": [{
            "stats": {
                "current": [current, -1, -1],
                "avg30":   [avg30,   -1, -1],
                "avg90":   [avg90,   -1, -1],
                "atl":     [atl,     -1, -1],
                "min90":   [min90,   -1, -1],
            },
            "csv": [],
        }]
    }


class TestParseKeepaResponse:
    def test_parses_stats_block(self):
        ph = _parse_keepa_response("B08TEST001", _keepa_resp())
        assert ph is not None
        assert ph.source == "keepa"

    def test_converts_cents_to_dollars(self):
        ph = _parse_keepa_response("B08TEST001", _keepa_resp(current=3499))
        assert ph.current == pytest.approx(34.99)

    def test_extracts_atl(self):
        ph = _parse_keepa_response("B08TEST001", _keepa_resp(atl=2499))
        assert ph.low_all_time == pytest.approx(24.99)

    def test_extracts_avg90(self):
        ph = _parse_keepa_response("B08TEST001", _keepa_resp(avg90=3850))
        assert ph.avg_90d == pytest.approx(38.50)

    def test_negative_price_treated_as_none(self):
        ph = _parse_keepa_response("B08TEST001", {"products": [{"stats": {
            "current": [-1, -1],
            "avg90":   [-1, -1],
            "atl":     [-1, -1],
        }, "csv": []}]})
        assert ph is None

    def test_empty_products_returns_none(self):
        assert _parse_keepa_response("B001", {"products": []}) is None

    def test_missing_products_key_returns_none(self):
        assert _parse_keepa_response("B001", {}) is None


# ── _derive_from_csv() ────────────────────────────────────────────────────────

class TestDeriveFromCsv:
    def _km(self, days_ago: int) -> int:
        """Convert days-ago to keepa-minutes."""
        now_km = int((time.time() - 1325376000) / 60)
        return now_km - days_ago * 24 * 60

    def test_latest_price_is_current(self):
        csv = [self._km(5), 3499, self._km(3), 3299, self._km(1), 3099]
        current, _, _, _, _ = _derive_from_csv(csv)
        assert current == pytest.approx(30.99)

    def test_all_time_low(self):
        csv = [self._km(200), 2000, self._km(100), 2499, self._km(1), 3499]
        _, _, low, _, _ = _derive_from_csv(csv)
        assert low == pytest.approx(20.00)

    def test_negative_prices_ignored(self):
        csv = [self._km(5), -1, self._km(1), 3499]
        current, _, _, _, _ = _derive_from_csv(csv)
        assert current == pytest.approx(34.99)

    def test_empty_csv_returns_all_none(self):
        result = _derive_from_csv([])
        assert all(v is None for v in result)

    def test_avg_90d_excludes_old_prices(self):
        csv = [
            self._km(200), 2000,   # old, excluded from 90d
            self._km(60),  3000,   # in 90d
            self._km(10),  4000,   # in 90d
        ]
        _, avg90, _, _, _ = _derive_from_csv(csv)
        assert avg90 == pytest.approx(35.00)   # (30+40)/2


# ── get_price_history() — integration ────────────────────────────────────────

class TestGetPriceHistory:
    @pytest.mark.asyncio
    async def test_returns_cached_result(self):
        cached = PriceHistory("B001", "camelcamelcamel", current=34.99)
        with patch("database.get_price_cache", new=AsyncMock(return_value=cached)):
            result = await get_price_history("B001")
        assert result is cached

    @pytest.mark.asyncio
    async def test_tries_ccc_on_cache_miss(self):
        ph = PriceHistory("B001", "camelcamelcamel", current=34.99, low_all_time=24.99)
        with patch("database.get_price_cache",  new=AsyncMock(return_value=None)):
            with patch("database.set_price_cache", new=AsyncMock()):
                with patch("price_history._from_camelcamelcamel", new=AsyncMock(return_value=ph)):
                    with patch("price_history._from_keepa",        new=AsyncMock(return_value=None)):
                        result = await get_price_history("B001")
        assert result is ph

    @pytest.mark.asyncio
    async def test_falls_back_to_keepa_when_ccc_fails(self):
        ph = PriceHistory("B001", "keepa", current=34.99)
        with patch("database.get_price_cache",  new=AsyncMock(return_value=None)):
            with patch("database.set_price_cache", new=AsyncMock()):
                with patch("price_history._from_camelcamelcamel", new=AsyncMock(return_value=None)):
                    with patch("price_history._from_keepa",        new=AsyncMock(return_value=ph)):
                        result = await get_price_history("B001")
        assert result is ph
        assert result.source == "keepa"

    @pytest.mark.asyncio
    async def test_returns_none_when_both_fail(self):
        with patch("database.get_price_cache",  new=AsyncMock(return_value=None)):
            with patch("database.set_price_cache", new=AsyncMock()):
                with patch("price_history._from_camelcamelcamel", new=AsyncMock(return_value=None)):
                    with patch("price_history._from_keepa",        new=AsyncMock(return_value=None)):
                        result = await get_price_history("B001")
        assert result is None

    @pytest.mark.asyncio
    async def test_caches_successful_result(self):
        ph = PriceHistory("B001", "camelcamelcamel", current=34.99)
        with patch("database.get_price_cache",  new=AsyncMock(return_value=None)):
            with patch("database.set_price_cache", new=AsyncMock()) as mock_set:
                with patch("price_history._from_camelcamelcamel", new=AsyncMock(return_value=ph)):
                    with patch("price_history._from_keepa",        new=AsyncMock(return_value=None)):
                        await get_price_history("B001")
        mock_set.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_cache_none_result(self):
        with patch("database.get_price_cache",  new=AsyncMock(return_value=None)):
            with patch("database.set_price_cache", new=AsyncMock()) as mock_set:
                with patch("price_history._from_camelcamelcamel", new=AsyncMock(return_value=None)):
                    with patch("price_history._from_keepa",        new=AsyncMock(return_value=None)):
                        await get_price_history("B001")
        mock_set.assert_not_called()


# ── _price_history_line() style formatting ────────────────────────────────────

class TestPriceHistoryLine:
    def test_returns_empty_for_none(self):
        assert _price_history_line(None) == ""

    def test_includes_atl(self):
        ph = PriceHistory("B001", "ccc", low_all_time=24.99)
        line = _price_history_line(ph)
        assert "24" in line

    def test_includes_avg_90d(self):
        ph = PriceHistory("B001", "ccc", avg_90d=38.50)
        line = _price_history_line(ph)
        assert "38" in line

    def test_prefers_avg_90d_over_avg_30d(self):
        ph = PriceHistory("B001", "ccc", avg_90d=38.00, avg_30d=35.00)
        line = _price_history_line(ph)
        assert "90d" in line

    def test_uses_avg_30d_when_no_90d(self):
        ph = PriceHistory("B001", "ccc", avg_30d=35.00)
        line = _price_history_line(ph)
        assert "30d" in line

    def test_includes_deal_label(self):
        ph = PriceHistory("B001", "ccc", current=25.00, low_all_time=24.50)
        line = _price_history_line(ph)
        assert "🔥" in line or "low" in line.lower()

    def test_returns_empty_when_no_data(self):
        ph = PriceHistory("B001", "ccc")   # no prices set
        assert _price_history_line(ph) == ""

    def test_starts_with_chart_emoji(self):
        ph = PriceHistory("B001", "ccc", low_all_time=24.99, avg_90d=38.50)
        line = _price_history_line(ph)
        assert line.startswith("\n📊")


# ── render_price_bar() ────────────────────────────────────────────────────────

from style import render_price_bar


class TestPriceBar:
    def test_normal_case_returns_multiline_string(self):
        """Bar for current=112, low_90d=89, avg_90d=149 is non-empty and multi-line."""
        ph = PriceHistory("B001", "ccc", current=112.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert bar != ""
        assert "\n" in bar

    def test_normal_case_contains_price_labels(self):
        """Bar output contains $ amounts for low and high boundaries."""
        ph = PriceHistory("B001", "ccc", current=112.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert "$89" in bar
        assert "$149" in bar

    def test_normal_case_contains_current_price(self):
        """Bar output contains the current price."""
        ph = PriceHistory("B001", "ccc", current=112.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert "$112" in bar

    def test_current_at_low_pointer_at_left(self):
        """When current == low_90d, pointer is at left edge (position 0 or close)."""
        ph = PriceHistory("B001", "ccc", current=89.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert bar != ""
        # The pointer line should start with ^ near the beginning
        lines = bar.splitlines()
        pointer_line = next((l for l in lines if "^" in l), "")
        assert pointer_line != ""
        # ^ should appear before position 5 (left-leaning)
        caret_pos = pointer_line.index("^")
        assert caret_pos <= 5

    def test_current_at_high_pointer_at_right(self):
        """When current == avg_90d, pointer is at right edge."""
        ph = PriceHistory("B001", "ccc", current=149.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert bar != ""
        lines = bar.splitlines()
        pointer_line = next((l for l in lines if "^" in l), "")
        assert pointer_line != ""
        # ^ should appear near the right side (position >= 10 in a 10-wide bar)
        caret_pos = pointer_line.index("^")
        assert caret_pos >= 8

    def test_current_none_returns_empty_string(self):
        """When current is None, render_price_bar returns empty string."""
        ph = PriceHistory("B001", "ccc", current=None, low_90d=89.0, avg_90d=149.0)
        assert render_price_bar(ph) == ""

    def test_low_90d_none_falls_back_to_low_all_time(self):
        """When low_90d is None but low_all_time is set, use low_all_time as range low."""
        ph = PriceHistory("B001", "ccc", current=112.0, low_90d=None, low_all_time=50.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        assert bar != ""
        assert "$50" in bar   # low_all_time used as range low

    def test_equal_range_handled_gracefully(self):
        """When low_90d == avg_90d, a synthetic range is created (no division by zero)."""
        ph = PriceHistory("B001", "ccc", current=100.0, low_90d=100.0, avg_90d=100.0)
        bar = render_price_bar(ph)
        # Should not raise, should return a non-empty bar
        assert bar != ""

    def test_bar_contains_block_chars_or_dashes(self):
        """Bar line contains Unicode block chars or ASCII fill/empty indicators."""
        ph = PriceHistory("B001", "ccc", current=112.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        # Should contain filled blocks (█) or dashes (─/-)
        has_fill = any(c in bar for c in ["█", "▓", "▒", "░", "■"])
        has_empty = any(c in bar for c in ["─", "-", "·", "·"])
        assert has_fill or has_empty

    def test_deal_label_included_when_present(self):
        """Deal label from ph.deal_label appears in the output when applicable."""
        ph = PriceHistory("B001", "ccc", current=25.0, low_all_time=24.50,
                          low_90d=24.0, avg_90d=40.0)
        bar = render_price_bar(ph)
        # deal_label should be "🔥 All-time low"
        assert "🔥" in bar or "low" in bar.lower()

    def test_no_deal_label_when_not_applicable(self):
        """When price is not a deal, deal label line is absent or empty."""
        ph = PriceHistory("B001", "ccc", current=149.0, low_90d=89.0, avg_90d=149.0)
        bar = render_price_bar(ph)
        # No deal label expected (current == avg = not a deal)
        assert "🔥" not in bar
        assert "💸" not in bar
        assert "✅" not in bar
