"""
tests/test_formatter_visual.py -- Unit tests for visual formatter features.

Tests for shipping badge and price bar rendering in product_caption().
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from formatter import Formatter


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_item(**kwargs):
    defaults = dict(
        title="Test Product",
        price_usd=29.99,
        url="https://amazon.com/dp/B001",
        rating=4.2,
        review_count=1500,
        is_prime=False,
        is_sold_by_amazon=False,
        image_url=None,
        asin="B001TEST00",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_israel_result(verified=True, ships_to_israel=True, is_free_shipping=True):
    return SimpleNamespace(
        verified=verified,
        ships_to_israel=ships_to_israel,
        is_free_shipping=is_free_shipping,
    )


def _make_price_history(
    current=25.0,
    low_90d=20.0,
    high_90d=40.0,
    avg_90d=30.0,
    avg_30d=31.0,
    low_all_time=18.0,
    deal_label="",
):
    return SimpleNamespace(
        current=current,
        low_90d=low_90d,
        high_90d=high_90d,
        avg_90d=avg_90d,
        avg_30d=avg_30d,
        low_all_time=low_all_time,
        deal_label=deal_label,
    )


# ── Shipping badge tests ───────────────────────────────────────────────────────

def test_shipping_badge_green():
    """israel_result with verified=True, ships_to_israel=True, is_free_shipping=True
    → caption contains green circle emoji and ships free text."""
    fmt = Formatter("telegram")
    item = _make_item()
    israel_result = _make_israel_result(verified=True, ships_to_israel=True, is_free_shipping=True)
    caption = fmt.product_caption(item, 1, 5, israel_result=israel_result)
    assert "\U0001f7e2" in caption  # green circle
    assert "Ships free to Israel" in caption


def test_shipping_badge_yellow():
    """israel_result with verified=True, ships_to_israel=True, is_free_shipping=False
    → caption contains yellow circle emoji."""
    fmt = Formatter("telegram")
    item = _make_item()
    israel_result = _make_israel_result(verified=True, ships_to_israel=True, is_free_shipping=False)
    caption = fmt.product_caption(item, 1, 5, israel_result=israel_result)
    assert "\U0001f7e1" in caption  # yellow circle


def test_shipping_badge_red():
    """israel_result with verified=True, ships_to_israel=False
    → caption contains red circle emoji."""
    fmt = Formatter("telegram")
    item = _make_item()
    israel_result = _make_israel_result(verified=True, ships_to_israel=False, is_free_shipping=False)
    caption = fmt.product_caption(item, 1, 5, israel_result=israel_result)
    assert "\U0001f534" in caption  # red circle


def test_shipping_badge_none():
    """israel_result=None → no badge line (backward compatible)."""
    fmt = Formatter("telegram")
    item = _make_item()
    caption = fmt.product_caption(item, 1, 5, israel_result=None)
    # Should not contain any shipping badge emojis when no israel_result
    assert "\U0001f7e2" not in caption  # no green
    assert "\U0001f7e1" not in caption  # no yellow
    assert "\U0001f534" not in caption  # no red


# ── Price bar tests ────────────────────────────────────────────────────────────

def test_price_bar_in_caption():
    """product_caption with price_history (current=25, low_90d=20, high_90d=40, avg_90d=30)
    contains block characters."""
    fmt = Formatter("telegram")
    item = _make_item()
    ph = _make_price_history(current=25.0, low_90d=20.0, high_90d=40.0, avg_90d=30.0)
    caption = fmt.product_caption(item, 1, 5, price_history=ph)
    # The bar contains block characters (filled) and/or dashes (empty)
    assert "\u2588" in caption or "\u2014" in caption or "\u2500" in caption or "█" in caption


def test_price_bar_telegram_monospace():
    """Formatter('telegram') wraps bar lines in backtick blocks."""
    fmt = Formatter("telegram")
    item = _make_item()
    ph = _make_price_history(current=25.0, low_90d=20.0, high_90d=40.0, avg_90d=30.0)
    caption = fmt.product_caption(item, 1, 5, price_history=ph)
    # Telegram monospace wrapping uses backticks
    assert "`" in caption


def test_price_bar_whatsapp_plain():
    """Formatter('whatsapp') renders bar WITHOUT backticks."""
    fmt = Formatter("whatsapp")
    item = _make_item()
    ph = _make_price_history(current=25.0, low_90d=20.0, high_90d=40.0, avg_90d=30.0)
    caption = fmt.product_caption(item, 1, 5, price_history=ph)
    # WhatsApp should NOT have backtick monospace wrapping
    assert "`" not in caption


def test_deal_label_in_caption():
    """price_history with deal_label='Good deal' includes 'Good deal' in output."""
    fmt = Formatter("telegram")
    item = _make_item()
    ph = _make_price_history(
        current=25.0, low_90d=20.0, high_90d=40.0, avg_90d=30.0,
        deal_label="Good deal",
    )
    caption = fmt.product_caption(item, 1, 5, price_history=ph)
    assert "Good deal" in caption


def test_no_price_history():
    """price_history=None → no bar (backward compatible)."""
    fmt = Formatter("telegram")
    item = _make_item()
    caption = fmt.product_caption(item, 1, 5, price_history=None)
    # Should not contain block characters (no bar rendered)
    assert "█" not in caption
    assert "─" not in caption


# ── Backward compatibility tests ──────────────────────────────────────────────

def test_israel_status_still_works():
    """israel_status='yes' without israel_result still shows the old text line."""
    fmt = Formatter("telegram")
    item = _make_item()
    caption = fmt.product_caption(item, 1, 5, israel_status="yes", israel_result=None)
    # Should still contain the Israel flag and shipping info
    assert "\U0001f1ee\U0001f1f1" in caption  # Israel flag


def test_israel_result_overrides_status():
    """When both israel_result and israel_status provided, israel_result badge takes priority."""
    fmt = Formatter("telegram")
    item = _make_item()
    # israel_status says "no" but israel_result says free shipping (green)
    israel_result = _make_israel_result(verified=True, ships_to_israel=True, is_free_shipping=True)
    caption = fmt.product_caption(
        item, 1, 5,
        israel_status="no",
        israel_result=israel_result,
    )
    # Should show green (from israel_result), not red (from israel_status="no")
    assert "\U0001f7e2" in caption  # green circle
    assert "\U0001f534" not in caption  # no red circle
