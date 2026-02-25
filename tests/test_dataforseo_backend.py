"""
Tests for search_backends/dataforseo_backend.py.

Covers:
  - _parse_item: happy path, missing ASIN, ASIN from URL, price variants, rating
  - Israel delivery detection: is_prime, shipped by amazon, sold by Amazon.com, 3P
  - _extract_items: nested response unpacking, task error codes
  - search(): HTTP success, HTTP error mocking
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from search_backends.dataforseo_backend import DataForSEOBackend, _extract_items


@pytest.fixture
def backend():
    return DataForSEOBackend(login="test@example.com", password="secret")


# ── Sample raw item (realistic DataForSEO response) ──────────────────────────

def _make_raw(**overrides) -> dict:
    base = {
        "type":          "amazon_serp",
        "data_asin":     "B0DFS0001",
        "title":         "Sony WH-1000XM5 Wireless Headphones",
        "url":           "https://www.amazon.com/dp/B0DFS0001",
        "image_url":     "https://m.media-amazon.com/images/test.jpg",
        "price_from":    279.99,
        "price_to":      279.99,
        "is_prime":      True,
        "seller":        "Amazon.com",
        "delivery_info": [
            "FREE delivery Fri, Feb 28",
            "Ships from and sold by Amazon.com",
        ],
        "rating": {
            "rating_type": "Max5",
            "value":        4.4,
            "votes_count":  12543,
        },
    }
    base.update(overrides)
    return base


# ── _parse_item ───────────────────────────────────────────────────────────────

class TestParseItem:

    def test_happy_path(self, backend):
        item = backend._parse_item(_make_raw())
        assert item is not None
        assert item.asin          == "B0DFS0001"
        assert item.title         == "Sony WH-1000XM5 Wireless Headphones"
        assert item.price_usd     == 279.99
        assert item.rating        == 4.4
        assert item.review_count  == 12543
        assert item.image_url     == "https://m.media-amazon.com/images/test.jpg"
        assert item.is_prime      is True
        assert item.is_sold_by_amazon      is True   # "Amazon.com" seller
        assert item.is_amazon_fulfilled    is True
        assert item.qualifies_for_israel_free_delivery is True

    def test_asin_from_url_when_data_asin_missing(self, backend):
        raw  = _make_raw(data_asin="", url="https://www.amazon.com/dp/B0URLFND01/ref=sr_1")
        item = backend._parse_item(raw)
        assert item is not None
        assert item.asin == "B0URLFND01"

    def test_no_asin_at_all_returns_none(self, backend):
        raw = _make_raw(data_asin="", url="https://www.amazon.com/s?k=headphones")
        assert backend._parse_item(raw) is None

    def test_no_title_returns_none(self, backend):
        assert backend._parse_item(_make_raw(title="")) is None

    def test_price_from_price_to_fallback(self, backend):
        item = backend._parse_item(_make_raw(price_from=None, price_to=49.99))
        assert item is not None
        assert item.price_usd == 49.99

    def test_no_price_is_allowed(self, backend):
        item = backend._parse_item(_make_raw(price_from=None, price_to=None))
        assert item is not None
        assert item.price_usd is None

    def test_no_rating(self, backend):
        item = backend._parse_item(_make_raw(rating=None))
        assert item is not None
        assert item.rating       is None
        assert item.review_count is None

    def test_bad_data_returns_none(self, backend):
        assert backend._parse_item(None) is None   # type: ignore
        assert backend._parse_item({})  is None


# ── Israel delivery detection ─────────────────────────────────────────────────

class TestIsraelDetection:

    def test_prime_flag_qualifies(self, backend):
        item = backend._parse_item(_make_raw(
            is_prime=True, seller="some-seller", delivery_info=[]
        ))
        assert item.is_prime is True
        assert item.qualifies_for_israel_free_delivery is True

    def test_sold_by_amazon_qualifies(self, backend):
        item = backend._parse_item(_make_raw(
            is_prime=False, seller="Amazon.com", delivery_info=[]
        ))
        assert item.is_sold_by_amazon    is True
        assert item.is_amazon_fulfilled  is True
        assert item.qualifies_for_israel_free_delivery is True

    def test_shipped_by_amazon_in_delivery_qualifies(self, backend):
        item = backend._parse_item(_make_raw(
            is_prime=False, seller="third-party",
            delivery_info=["FREE delivery Mon, Mar 2 on $35 of items shipped by Amazon"],
        ))
        assert item.is_amazon_fulfilled is True
        assert item.qualifies_for_israel_free_delivery is True

    def test_third_party_no_fba_does_not_qualify(self, backend):
        item = backend._parse_item(_make_raw(
            is_prime=False, seller="some-brand-store",
            delivery_info=["FREE delivery Mon, Mar 2"],   # no "shipped by Amazon"
        ))
        assert item.is_prime             is False
        assert item.is_amazon_fulfilled  is False
        assert item.is_sold_by_amazon    is False
        assert item.qualifies_for_israel_free_delivery is False

    def test_fulfilled_by_amazon_phrase(self, backend):
        item = backend._parse_item(_make_raw(
            is_prime=False, seller="",
            delivery_info=["Fulfilled by Amazon"],
        ))
        assert item.is_amazon_fulfilled is True


# ── _extract_items ────────────────────────────────────────────────────────────

class TestExtractItems:

    def _wrap(self, items: list, status_code: int = 20000) -> dict:
        return {
            "tasks": [{
                "status_code": status_code,
                "status_message": "Ok." if status_code == 20000 else "Error",
                "result": [{"items": items}],
            }]
        }

    def test_extracts_amazon_serp_items(self):
        raw = self._wrap([
            {"type": "amazon_serp", "data_asin": "B001"},
            {"type": "amazon_banner", "data_asin": "B002"},  # filtered out
            {"type": "amazon_serp", "data_asin": "B003"},
        ])
        items = _extract_items(raw, "test")
        assert len(items) == 2
        assert items[0]["data_asin"] == "B001"
        assert items[1]["data_asin"] == "B003"

    def test_task_error_returns_empty(self):
        raw = self._wrap([], status_code=40501)
        assert _extract_items(raw, "test") == []

    def test_empty_response(self):
        assert _extract_items({}, "test") == []


# ── search() HTTP layer ───────────────────────────────────────────────────────

class TestSearch:

    def _response(self, items: list):
        return {
            "tasks": [{
                "status_code":    20000,
                "status_message": "Ok.",
                "result": [{
                    "items": [{"type": "amazon_serp", **item} for item in items],
                }],
            }]
        }

    @pytest.mark.asyncio
    async def test_returns_parsed_items(self, backend):
        payload = self._response([
            _make_raw(data_asin="B001", title="Product A", price_from=10.0),
            _make_raw(data_asin="B002", title="Product B", price_from=20.0),
        ])
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json   = AsyncMock(return_value=payload)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__  = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)
        mock_session.post       = MagicMock(return_value=mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            items = await backend.search("headphones")

        assert len(items) == 2
        asins = {i.asin for i in items}
        assert asins == {"B001", "B002"}

    @pytest.mark.asyncio
    async def test_http_error_raises(self, backend):
        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.text   = AsyncMock(return_value="Unauthorized")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__  = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)
        mock_session.post       = MagicMock(return_value=mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="DataForSEO error 401"):
                await backend.search("headphones")
