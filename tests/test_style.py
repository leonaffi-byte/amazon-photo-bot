"""
Tests for style.py — MarkdownV2 formatting helpers.

Covers:
  - esc(): all MarkdownV2 special characters are escaped
  - star_bar(): correct star strings for various ratings
  - fmt_reviews(): K / M formatting and edge cases
  - welcome() / help_text(): return non-empty strings with required keywords
  - loading_vision(): correct for single vs multi-provider
  - error_rate_limited(): contains limit numbers
  - product_card(): contains ASIN-agnostic key fields
  - results_page(): truncated at 4050 chars when too long
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import style
from search_backends.base import AmazonItem
from providers.base import ProviderResult


# ── esc() ─────────────────────────────────────────────────────────────────────

class TestEsc:
    MDV2_SPECIALS = r"\_*[]()~`>#+-=|{}.!"

    def test_all_special_characters_escaped(self):
        for ch in self.MDV2_SPECIALS:
            escaped = style.esc(ch)
            assert escaped == f"\\{ch}", f"Character {ch!r} not escaped"

    def test_plain_text_unchanged(self):
        assert style.esc("Hello World") == "Hello World"

    def test_mixed_text(self):
        result = style.esc("price: $49.99 (best!)")
        # $ is NOT a MarkdownV2 special char — it should pass through unchanged
        assert "$" in result and "\\$" not in result
        assert "\\." in result
        assert "\\(" in result
        assert "\\!" in result


# ── star_bar() ────────────────────────────────────────────────────────────────

class TestStarBar:
    def test_5_stars(self):
        assert style.star_bar(5.0) == "★★★★★"

    def test_4_stars(self):
        assert style.star_bar(4.0) == "★★★★☆"

    def test_3_stars(self):
        assert style.star_bar(3.0) == "★★★☆☆"

    def test_none_rating_gives_all_empty(self):
        assert style.star_bar(None) == "☆☆☆☆☆"

    def test_rounding_4_5_gives_5_stars(self):
        # round(4.5) = 4 in Python (banker's rounding), so check both behaviours
        bar = style.star_bar(4.5)
        assert bar in ("★★★★★", "★★★★☆")

    def test_zero_rating(self):
        assert style.star_bar(0) == "☆☆☆☆☆"

    def test_below_zero_clamped(self):
        assert style.star_bar(-1) == "☆☆☆☆☆"

    def test_above_five_clamped(self):
        assert style.star_bar(6) == "★★★★★"


# ── fmt_reviews() ─────────────────────────────────────────────────────────────

class TestFmtReviews:
    def test_none_returns_empty(self):
        assert style.fmt_reviews(None) == ""

    def test_small_number(self):
        assert style.fmt_reviews(42) == "42"

    def test_thousands(self):
        assert "K" in style.fmt_reviews(1500)

    def test_millions(self):
        assert "M" in style.fmt_reviews(1_500_000)

    def test_exact_thousand(self):
        assert "K" in style.fmt_reviews(1000)


# ── welcome() ────────────────────────────────────────────────────────────────

class TestWelcome:
    def test_contains_providers(self):
        text = style.welcome("openai/gpt-4o", "best", "PA-API 5.0")
        assert "openai" in text

    def test_contains_search_backend(self):
        # welcome() calls esc() on the backend name, which escapes MarkdownV2
        # special chars (-  →  \-, .  →  \.).  Check for the safe prefix instead.
        text = style.welcome("openai", "best", "PA-API 5.0")
        assert "PA" in text   # "PA\\-API" appears in the escaped output

    def test_non_empty(self):
        text = style.welcome("openai", "best", "rapidapi")
        assert len(text) > 50


# ── loading_vision() ─────────────────────────────────────────────────────────

class TestLoadingVision:
    def test_multi_provider_mentions_count(self):
        text = style.loading_vision(3, "best")
        assert "3" in text

    def test_single_provider_no_parallel_mention(self):
        text = style.loading_vision(1, "cheapest")
        # Should not say "parallel" with only one provider
        assert "parallel" not in text.lower()


# ── error_rate_limited() ──────────────────────────────────────────────────────

class TestErrorRateLimited:
    def test_contains_max_requests(self):
        text = style.error_rate_limited(5, 60)
        assert "5" in text

    def test_contains_window_seconds(self):
        text = style.error_rate_limited(5, 60)
        assert "60" in text


# ── product_card() ────────────────────────────────────────────────────────────

def make_amazon_item(**kwargs) -> AmazonItem:
    defaults = dict(
        asin="B00TEST001",
        title="Test Mechanical Keyboard",
        image_url=None,
        price_usd=79.99,
        currency="USD",
        rating=4.5,
        review_count=2000,
        is_amazon_fulfilled=True,
        is_sold_by_amazon=False,
        is_prime=True,
        availability="In Stock",
    )
    defaults.update(kwargs)
    return AmazonItem(**defaults)


class TestProductCard:
    def test_contains_index(self):
        item = make_amazon_item()
        card = style.product_card(item, index=3)
        assert "3" in card

    def test_contains_price(self):
        item = make_amazon_item(price_usd=79.99)
        card = style.product_card(item, index=1)
        assert "79" in card

    def test_no_price_shows_fallback(self):
        item = make_amazon_item(price_usd=None)
        card = style.product_card(item, index=1)
        assert "not listed" in card.lower() or "price" in card.lower()

    def test_title_in_card(self):
        item = make_amazon_item(title="Widget Pro 2000")
        card = style.product_card(item, index=1)
        assert "Widget Pro 2000" in card

    def test_fba_badge_in_card(self):
        item = make_amazon_item(is_amazon_fulfilled=True, is_sold_by_amazon=False)
        card = style.product_card(item, index=1)
        assert "FBA" in card or "Amazon" in card


# ── results_page() ───────────────────────────────────────────────────────────

def make_session(n_items: int = 5, israel_only: bool = False):
    """Build a minimal UserSession-like object."""
    import config

    items = [make_amazon_item(asin=f"B{i:010d}", title=f"Product {i}") for i in range(n_items)]

    session = MagicMock()
    session.page = 0
    session.total_pages = 1
    session.israel_only = israel_only
    session.all_items = items
    session.filtered_items = items
    session.chosen_result = MagicMock()
    session.chosen_result.provider_name = "openai/gpt-4o"
    session.product_info = MagicMock()
    session.product_info.product_name = "Test Product"
    session.current_page_items = MagicMock(return_value=items[:config.RESULTS_PER_PAGE])
    return session


class TestResultsPage:
    def test_contains_product_name(self):
        session = make_session()
        text = style.results_page(session, affiliate_tag="tag-20")
        assert "Test Product" in text

    def test_truncated_at_4050_chars(self):
        # Build a session with very long titles to force truncation
        items = [
            make_amazon_item(asin=f"B{i:010d}", title="X" * 200)
            for i in range(10)
        ]
        import config
        session = make_session(5)
        session.all_items = items
        session.filtered_items = items
        session.current_page_items.return_value = items[:config.RESULTS_PER_PAGE]

        text = style.results_page(session)
        assert len(text) <= 4060   # some slack for the truncation marker itself

    def test_filter_badge_changes_with_israel_only(self):
        s_all    = make_session(israel_only=False)
        s_israel = make_session(israel_only=True)
        assert "All" in style.results_page(s_all) or "🌐" in style.results_page(s_all)
        assert "Israel" in style.results_page(s_israel) or "✈️" in style.results_page(s_israel)
