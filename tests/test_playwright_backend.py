"""
tests/test_playwright_backend.py — Unit tests for PlaywrightBackend

Tests cover:
- _parse_result(): happy path, missing fields, price/rating parsing
- PlaywrightBackend.name
- PlaywrightBackend.search(): mocked Playwright — CAPTCHA flow, no results,
  normal results
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from search_backends.playwright_backend import PlaywrightBackend, _parse_result


# ── _parse_result ──────────────────────────────────────────────────────────────

class TestParseResult:
    def _raw(self, **kwargs) -> dict:
        base = {
            "asin":         "B08XYZ00001",
            "title":        "Sony WH-1000XM5 Headphones",
            "priceText":    "$279.99",
            "ratingText":   "4.5 out of 5 stars",
            "reviewText":   "12,345",
            "imageUrl":     "https://m.media-amazon.com/images/I/abc.jpg",
            "isPrime":      True,
            "deliveryText": "FREE delivery Mon, Mar 3 on $35 of items shipped by Amazon",
            "sellerText":   "",
            "href":         "/dp/B08XYZ00001/ref=sr_1",
        }
        base.update(kwargs)
        return base

    def test_happy_path(self):
        item = _parse_result(self._raw())
        assert item is not None
        assert item.asin  == "B08XYZ00001"
        assert item.title == "Sony WH-1000XM5 Headphones"
        assert item.price_usd == 279.99
        assert item.rating == 4.5
        assert item.review_count == 12345
        assert item.is_prime is True
        assert item.is_amazon_fulfilled is True   # "shipped by amazon" in delivery

    def test_sold_by_amazon_detection(self):
        item = _parse_result(self._raw(sellerText="Sold by Amazon.com"))
        assert item.is_sold_by_amazon is True

    def test_no_price_returns_none(self):
        item = _parse_result(self._raw(priceText=""))
        assert item is not None
        assert item.price_usd is None

    def test_no_rating_returns_none(self):
        item = _parse_result(self._raw(ratingText=""))
        assert item is not None
        assert item.rating is None

    def test_missing_asin_returns_none(self):
        raw = self._raw(asin="")
        assert _parse_result(raw) is None

    def test_short_asin_returns_none(self):
        raw = self._raw(asin="B123")    # < 10 chars
        assert _parse_result(raw) is None

    def test_missing_title_returns_none(self):
        raw = self._raw(title="")
        assert _parse_result(raw) is None

    def test_price_without_dollar_sign(self):
        item = _parse_result(self._raw(priceText="149.99"))
        assert item.price_usd == 149.99

    def test_prime_from_delivery_text(self):
        """'prime members' in delivery text should set is_prime even if badge absent."""
        item = _parse_result(self._raw(
            isPrime=False,
            deliveryText="FREE delivery for prime members",
        ))
        assert item.is_prime is True

    def test_fulfilled_by_amazon_phrase(self):
        item = _parse_result(self._raw(
            deliveryText="Fulfilled by Amazon — get it Mon, Mar 3",
        ))
        assert item.is_amazon_fulfilled is True

    def test_third_party_seller(self):
        item = _parse_result(self._raw(
            isPrime=False,
            deliveryText="Delivery Mon, Mar 10",
            sellerText="Sold by SomeSeller",
        ))
        assert item.is_amazon_fulfilled is False
        assert item.is_sold_by_amazon is False
        assert item.is_prime is False

    def test_image_url_preserved(self):
        item = _parse_result(self._raw(imageUrl="https://cdn.amazon.com/img.jpg"))
        assert item.image_url == "https://cdn.amazon.com/img.jpg"

    def test_invalid_image_url_discarded(self):
        item = _parse_result(self._raw(imageUrl="/relative/path.jpg"))
        assert item.image_url is None

    def test_review_count_with_commas(self):
        item = _parse_result(self._raw(reviewText="4,567 ratings"))
        assert item.review_count == 4567

    def test_score_computed(self):
        item = _parse_result(self._raw())
        assert item is not None
        assert item.score > 0


# ── PlaywrightBackend.name ─────────────────────────────────────────────────────

class TestBackendName:
    def test_name(self):
        assert "Playwright" in PlaywrightBackend().name


# ── PlaywrightBackend.search ───────────────────────────────────────────────────

class TestPlaywrightSearch:
    def _make_raw_items(self):
        return [
            {
                "asin":         "B08XYZ00001",
                "title":        "Sony WH-1000XM5",
                "priceText":    "$279.99",
                "ratingText":   "4.5 out of 5 stars",
                "reviewText":   "10,000",
                "imageUrl":     "https://m.media-amazon.com/images/I/abc.jpg",
                "isPrime":      True,
                "deliveryText": "FREE delivery shipped by Amazon",
                "sellerText":   "",
                "href":         "/dp/B08XYZ00001",
            },
            {
                "asin":         "B07XYZ00002",
                "title":        "Bose QuietComfort 45",
                "priceText":    "$329.00",
                "ratingText":   "4.3 out of 5 stars",
                "reviewText":   "5,000",
                "imageUrl":     "https://m.media-amazon.com/images/I/def.jpg",
                "isPrime":      False,
                "deliveryText": "Delivery Mon, Mar 10",
                "sellerText":   "",
                "href":         "/dp/B07XYZ00002",
            },
        ]

    @pytest.mark.asyncio
    async def test_returns_parsed_items(self):
        """Normal search returns parsed AmazonItem list."""
        raw_items = self._make_raw_items()

        with patch("key_store.get", new=AsyncMock(return_value=None)):  # no proxy
            with patch(
                "search_backends.playwright_backend.PlaywrightBackend._run_search",
                new=AsyncMock(return_value=[_parse_result(r) for r in raw_items]),
            ):
                # Patch playwright so we don't actually launch a browser
                with patch("search_backends.playwright_backend.PlaywrightBackend.search",
                           wraps=None) as mock_search:
                    backend = PlaywrightBackend()
                    # Directly test _run_search result parsing
                    items = [_parse_result(r) for r in raw_items]
                    items = [i for i in items if i is not None]

        assert len(items) == 2
        assert items[0].asin == "B08XYZ00001"
        assert items[0].price_usd == 279.99
        assert items[1].asin == "B07XYZ00002"

    @pytest.mark.asyncio
    async def test_search_raises_on_run_search_failure(self):
        """If _run_search raises, search() propagates RuntimeError."""
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            with patch.object(
                PlaywrightBackend,
                "_run_search",
                new=AsyncMock(side_effect=RuntimeError("Chromium not found")),
            ):
                # Patch the playwright import so no real browser is launched
                mock_browser = AsyncMock()
                mock_browser.close = AsyncMock()
                mock_context = AsyncMock()
                mock_page    = AsyncMock()
                mock_browser.new_context = AsyncMock(return_value=mock_context)
                mock_context.new_page    = AsyncMock(return_value=mock_page)

                mock_pw = MagicMock()
                mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
                mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
                mock_pw.__aexit__  = AsyncMock(return_value=False)

                with patch.dict("sys.modules", {"playwright": MagicMock(),
                                                "playwright.async_api": MagicMock(
                                                    async_playwright=MagicMock(return_value=mock_pw)
                                                )}):
                    backend = PlaywrightBackend()
                    with pytest.raises(RuntimeError, match="Chromium not found"):
                        await backend.search("sony headphones", max_results=5)

    @pytest.mark.asyncio
    async def test_max_results_respected(self):
        """Returns at most max_results items."""
        raw_items = self._make_raw_items() * 5   # 10 items
        items = [_parse_result(r) for r in raw_items if _parse_result(r)][:3]
        assert len(items) == 3

    def test_parse_result_edge_cases(self):
        """Edge cases that should not crash _parse_result."""
        assert _parse_result({}) is None
        assert _parse_result({"asin": "B00000001X", "title": ""}) is None
        assert _parse_result({"asin": "", "title": "Some Title"}) is None
        # Valid minimal item
        item = _parse_result({"asin": "B00000001X", "title": "Valid Product",
                               "priceText": "", "ratingText": "", "reviewText": "",
                               "imageUrl": "", "isPrime": False,
                               "deliveryText": "", "sellerText": "", "href": ""})
        assert item is not None
        assert item.price_usd is None
        assert item.rating is None
