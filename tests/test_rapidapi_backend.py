"""
Tests for search_backends/rapidapi_backend.py.

Covers:
  - _parse_price: various string formats
  - _parse_product: happy path + missing optional fields + bad data
  - Israel delivery detection from is_prime / delivery text / seller name
  - search(): HTTP success + HTTP error
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from search_backends.rapidapi_backend import RapidAPIBackend, _parse_price


@pytest.fixture
def backend():
    return RapidAPIBackend(api_key="test_rapidapi_key")


# ── _parse_price ───────────────────────────────────────────────────────────────

class TestParsePrice:
    def test_dollar_sign(self):
        assert _parse_price("$29.99") == 29.99

    def test_no_symbol(self):
        assert _parse_price("49.00") == 49.00

    def test_comma_thousands(self):
        assert _parse_price("$1,299.00") == 1299.00

    def test_integer_price(self):
        assert _parse_price("$15") == 15.0

    def test_empty_string_returns_none(self):
        assert _parse_price("") is None

    def test_non_numeric_returns_none(self):
        assert _parse_price("N/A") is None

    def test_none_input_returns_none(self):
        assert _parse_price(None) is None


# ── _parse_product ─────────────────────────────────────────────────────────────

class TestParseProduct:
    def _full_raw(self, **overrides) -> dict:
        base = {
            "asin": "B0RAPID0001",
            "product_title": "Test Wireless Keyboard",
            "product_price": "$39.99",
            "product_star_rating": "4.3",
            "product_num_ratings": "5678",
            "product_photo": "https://img.amazon.com/kb.jpg",
            "is_prime": True,
            "delivery": "FREE delivery Fri, Jan 10",
            "product_url": "https://www.amazon.com/dp/B0RAPID0001",
        }
        base.update(overrides)
        return base

    def test_happy_path(self, backend):
        item = backend._parse_product(self._full_raw())
        assert item is not None
        assert item.asin == "B0RAPID0001"
        assert item.title == "Test Wireless Keyboard"
        assert item.price_usd == 39.99
        assert item.rating == 4.3
        assert item.review_count == 5678
        assert item.is_prime is True
        # is_prime alone does not set is_amazon_fulfilled (only sold_by_amazon does);
        # but the item still qualifies for Israel via qualifies_for_israel_free_delivery
        assert item.qualifies_for_israel_free_delivery is True

    def test_missing_asin_returns_none(self, backend):
        raw = self._full_raw()
        del raw["asin"]
        item = backend._parse_product(raw)
        assert item is None

    def test_non_prime_non_amazon_is_third_party(self, backend):
        raw = self._full_raw(is_prime=False, delivery="Ships in 5-7 days")
        item = backend._parse_product(raw)
        assert item is not None
        assert not item.is_prime
        # If no free delivery text or amazon seller detected:
        assert not item.is_amazon_fulfilled

    def test_free_delivery_text_does_not_trigger_fba_flag(self, backend):
        # "FREE delivery" in US search results is US domestic shipping, NOT Israel.
        # It must NOT be used to flag an item as Amazon-fulfilled / Israel-eligible.
        raw = self._full_raw(is_prime=False, delivery="FREE delivery tomorrow")
        item = backend._parse_product(raw)
        assert item is not None
        assert not item.is_amazon_fulfilled   # US delivery text ≠ FBA
        assert not item.qualifies_for_israel_free_delivery  # not prime, not FBA, not sold-by-Amazon

    def test_sold_by_amazon_flag(self, backend):
        raw = self._full_raw(is_prime=False, delivery="")
        raw["product_details"] = {"seller": "Amazon.com"}
        item = backend._parse_product(raw)
        assert item is not None
        assert item.is_sold_by_amazon

    def test_missing_price_gives_none(self, backend):
        raw = self._full_raw()
        del raw["product_price"]
        item = backend._parse_product(raw)
        assert item is not None
        assert item.price_usd is None

    def test_missing_rating_gives_none(self, backend):
        raw = self._full_raw()
        del raw["product_star_rating"]
        item = backend._parse_product(raw)
        assert item is not None
        assert item.rating is None

    def test_bad_rating_string_gives_none(self, backend):
        raw = self._full_raw(product_star_rating="N/A")
        item = backend._parse_product(raw)
        assert item is not None
        assert item.rating is None

    def test_completely_bad_data_returns_none(self, backend):
        item = backend._parse_product(None)  # type: ignore
        assert item is None


# ── search() HTTP call ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSearch:
    async def _fake_response(self, products: list[dict], status: int = 200):
        """Build a fake aiohttp response object."""
        mock_resp = MagicMock()
        mock_resp.status = status
        mock_resp.json = AsyncMock(return_value={
            "data": {"products": products}
        })
        mock_resp.text = AsyncMock(return_value="error text")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    async def test_successful_search_returns_items(self, backend):
        products = [
            {
                "asin": f"B{i:010d}",
                "product_title": f"Product {i}",
                "is_prime": True,
                "product_url": "https://amazon.com/dp/xxx",
            }
            for i in range(5)
        ]
        mock_resp = await self._fake_response(products)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("search_backends.rapidapi_backend.aiohttp.ClientSession", return_value=mock_session):
            results = await backend.search("wireless keyboard", max_results=10)

        assert len(results) == 5

    async def test_http_error_raises_runtime_error(self, backend):
        mock_resp = await self._fake_response([], status=429)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("search_backends.rapidapi_backend.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="429"):
                await backend.search("keyboard", max_results=10)

    async def test_max_results_limit(self, backend):
        products = [
            {
                "asin": f"B{i:010d}",
                "product_title": f"Product {i}",
                "is_prime": False,
                "product_url": "https://amazon.com/dp/xxx",
            }
            for i in range(20)
        ]
        mock_resp = await self._fake_response(products)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("search_backends.rapidapi_backend.aiohttp.ClientSession", return_value=mock_session):
            results = await backend.search("keyboard", max_results=5)

        assert len(results) <= 5

    async def test_results_sorted_by_score_descending(self, backend):
        products = [
            {
                "asin": f"B{i:010d}",
                "product_title": f"Product {i}",
                "product_star_rating": str(5 - i),
                "product_num_ratings": "1000",
                "is_prime": True,
                "product_url": "https://amazon.com/dp/xxx",
            }
            for i in range(5)
        ]
        mock_resp = await self._fake_response(products)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("search_backends.rapidapi_backend.aiohttp.ClientSession", return_value=mock_session):
            results = await backend.search("keyboard", max_results=10)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
