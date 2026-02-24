"""
Tests for search_backends/base.py — AmazonItem dataclass.

Covers:
  - Score calculation (Bayesian rating × log10 reviews)
  - Israel delivery qualification logic (FBA / Prime / sold by Amazon)
  - Delivery badge text
  - Israel delivery note text
  - Affiliate URL construction (with and without tag)
"""
import math

import pytest

from search_backends.base import AmazonItem


def make_item(**kwargs) -> AmazonItem:
    """Create an AmazonItem with sensible defaults — override via kwargs."""
    defaults = dict(
        asin="B00TEST001",
        title="Test Product",
        image_url="https://images.amazon.com/test.jpg",
        price_usd=19.99,
        currency="USD",
        rating=None,
        review_count=None,
        is_amazon_fulfilled=False,
        is_sold_by_amazon=False,
        is_prime=False,
        availability="In Stock",
    )
    defaults.update(kwargs)
    return AmazonItem(**defaults)


# ── Score calculation ──────────────────────────────────────────────────────────

class TestScore:
    def test_no_rating_gives_zero_score(self):
        item = make_item(rating=None, review_count=None)
        assert item.score == 0.0

    def test_rating_without_reviews_gives_zero_score(self):
        item = make_item(rating=4.5, review_count=None)
        assert item.score == 0.0

    def test_zero_reviews_gives_zero_score(self):
        item = make_item(rating=4.5, review_count=0)
        assert item.score == 0.0

    def test_score_formula(self):
        item = make_item(rating=4.5, review_count=1000)
        expected = 4.5 * math.log10(1001)
        assert abs(item.score - expected) < 1e-9

    def test_higher_rating_better_score_same_reviews(self):
        hi = make_item(rating=5.0, review_count=100)
        lo = make_item(rating=3.0, review_count=100)
        assert hi.score > lo.score

    def test_more_reviews_better_score_same_rating(self):
        many = make_item(rating=4.0, review_count=10_000)
        few  = make_item(rating=4.0, review_count=10)
        assert many.score > few.score

    def test_single_review(self):
        item = make_item(rating=5.0, review_count=1)
        expected = 5.0 * math.log10(2)
        assert abs(item.score - expected) < 1e-9


# ── Israel delivery qualification ─────────────────────────────────────────────

class TestIsraelDelivery:
    def test_third_party_seller_does_not_qualify(self):
        item = make_item(is_amazon_fulfilled=False, is_prime=False, is_sold_by_amazon=False)
        assert not item.qualifies_for_israel_free_delivery

    def test_fba_qualifies(self):
        item = make_item(is_amazon_fulfilled=True)
        assert item.qualifies_for_israel_free_delivery

    def test_prime_qualifies(self):
        item = make_item(is_prime=True)
        assert item.qualifies_for_israel_free_delivery

    def test_sold_by_amazon_qualifies(self):
        item = make_item(is_sold_by_amazon=True)
        assert item.qualifies_for_israel_free_delivery

    def test_all_flags_qualifies(self):
        item = make_item(is_amazon_fulfilled=True, is_prime=True, is_sold_by_amazon=True)
        assert item.qualifies_for_israel_free_delivery


# ── Delivery badge ─────────────────────────────────────────────────────────────

class TestDeliveryBadge:
    def test_sold_by_amazon_badge(self):
        item = make_item(is_sold_by_amazon=True)
        assert "Amazon.com" in item.delivery_badge

    def test_fba_badge(self):
        item = make_item(is_amazon_fulfilled=True, is_sold_by_amazon=False)
        assert "FBA" in item.delivery_badge

    def test_prime_badge(self):
        item = make_item(is_prime=True, is_amazon_fulfilled=False, is_sold_by_amazon=False)
        assert "Prime" in item.delivery_badge

    def test_third_party_badge(self):
        item = make_item()
        assert "Third-party" in item.delivery_badge


# ── Israel delivery note ──────────────────────────────────────────────────────

class TestIsraelDeliveryNote:
    def test_qualifying_item_shows_free_delivery(self):
        item = make_item(is_prime=True)
        assert "Free delivery" in item.israel_delivery_note

    def test_non_qualifying_shows_warning(self):
        item = make_item()
        assert "May not qualify" in item.israel_delivery_note


# ── Affiliate URL ─────────────────────────────────────────────────────────────

class TestAffiliateUrl:
    def test_url_without_tag(self):
        item = make_item(asin="B0ABCDEF12")
        url  = item.affiliate_url(None)
        assert url == "https://www.amazon.com/dp/B0ABCDEF12"
        assert "tag=" not in url

    def test_url_with_tag(self):
        item = make_item(asin="B0ABCDEF12")
        url  = item.affiliate_url("mytag-20")
        assert "tag=mytag-20" in url
        assert "/dp/B0ABCDEF12" in url

    def test_url_with_empty_string_tag(self):
        item = make_item(asin="B0ABCDEF12")
        url  = item.affiliate_url("")
        # Empty string is falsy → no tag embedded
        assert "tag=" not in url
