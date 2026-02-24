"""
Tests for search_backends/paapi_backend.py.

Covers:
  - AWS SigV4 signing: canonical request format, signing key derivation
  - _parse_item: happy path + missing optional fields + bad data
  - search(): pagination — multiple page calls, dedup, early stop on empty page
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from search_backends.paapi_backend import PaapiBackend


@pytest.fixture
def backend():
    return PaapiBackend(
        access_key="AKIATEST",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        associate_tag="testtag-20",
        marketplace="www.amazon.com",
    )


# ── SigV4 signing ─────────────────────────────────────────────────────────────

class TestSigV4:
    def test_signing_key_is_bytes(self, backend):
        key = backend._get_signing_key("20250101")
        assert isinstance(key, bytes)
        assert len(key) == 32   # SHA-256 digest length

    def test_signing_key_changes_with_date(self, backend):
        k1 = backend._get_signing_key("20250101")
        k2 = backend._get_signing_key("20250102")
        assert k1 != k2

    def test_signed_headers_contains_required_fields(self, backend):
        payload = {"Keywords": "test", "PartnerTag": "tag-20"}
        headers = backend._signed_headers(payload)
        required = [
            "Authorization",
            "X-Amz-Date",
            "Content-Type",
            "X-Amz-Target",
            "Content-Encoding",
            "Host",
        ]
        for field in required:
            assert field in headers, f"Missing header: {field}"

    def test_authorization_header_format(self, backend):
        payload = {"Keywords": "test"}
        headers = backend._signed_headers(payload)
        auth = headers["Authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/")
        assert "SignedHeaders=" in auth
        assert "Signature=" in auth

    def test_canonical_headers_use_body_hash(self, backend):
        """Different payloads must produce different signatures."""
        h1 = backend._signed_headers({"Keywords": "keyboard"})
        h2 = backend._signed_headers({"Keywords": "mouse"})
        # Signatures must differ
        sig1 = h1["Authorization"].split("Signature=")[1]
        sig2 = h2["Authorization"].split("Signature=")[1]
        assert sig1 != sig2

    def test_amz_date_format(self, backend):
        payload = {}
        headers = backend._signed_headers(payload)
        amz_date = headers["X-Amz-Date"]
        # Must be YYYYMMDDTHHmmSSZ
        assert len(amz_date) == 16
        assert amz_date.endswith("Z")
        assert "T" in amz_date


# ── _parse_item ────────────────────────────────────────────────────────────────

class TestParseItem:
    def _full_raw(self, **overrides) -> dict:
        base = {
            "ASIN": "B0EXAMPLE1",
            "ItemInfo": {"Title": {"DisplayValue": "Test Keyboard"}},
            "Images": {"Primary": {"Medium": {"URL": "https://img.amazon.com/k.jpg"}}},
            "Offers": {"Listings": [{
                "Price": {"Amount": 49.99, "Currency": "USD"},
                "DeliveryInfo": {"IsAmazonFulfilled": True, "IsFreeShippingEligible": True},
                "MerchantInfo": {"Name": "Amazon.com"},
                "Availability": {"Message": "In Stock"},
            }]},
            "CustomerReviews": {
                "StarRating": {"Value": "4.5"},
                "Count": {"Value": "1234"},
            },
        }
        base.update(overrides)
        return base

    def test_happy_path(self, backend):
        item = backend._parse_item(self._full_raw())
        assert item is not None
        assert item.asin == "B0EXAMPLE1"
        assert item.title == "Test Keyboard"
        assert item.price_usd == 49.99
        assert item.is_amazon_fulfilled is True
        assert item.is_sold_by_amazon is True
        assert item.is_prime is True
        assert item.rating == 4.5
        assert item.review_count == 1234
        assert item.score > 0

    def test_missing_image_returns_none_url(self, backend):
        raw = self._full_raw()
        del raw["Images"]
        item = backend._parse_item(raw)
        assert item is not None
        assert item.image_url is None

    def test_missing_offers_gives_no_price(self, backend):
        raw = self._full_raw()
        del raw["Offers"]
        item = backend._parse_item(raw)
        assert item is not None
        assert item.price_usd is None

    def test_missing_reviews_gives_none_rating(self, backend):
        raw = self._full_raw()
        del raw["CustomerReviews"]
        item = backend._parse_item(raw)
        assert item is not None
        assert item.rating is None
        assert item.review_count is None
        assert item.score == 0.0

    def test_third_party_merchant(self, backend):
        raw = self._full_raw()
        raw["Offers"]["Listings"][0]["MerchantInfo"] = {"Name": "SomeThirdParty"}
        raw["Offers"]["Listings"][0]["DeliveryInfo"] = {
            "IsAmazonFulfilled": False,
            "IsFreeShippingEligible": False,
        }
        item = backend._parse_item(raw)
        assert item is not None
        assert not item.is_sold_by_amazon
        assert not item.is_amazon_fulfilled

    def test_completely_bad_data_returns_none(self, backend):
        item = backend._parse_item({"garbage": True})
        assert item is None

    def test_missing_title_returns_none(self, backend):
        raw = self._full_raw()
        del raw["ItemInfo"]
        item = backend._parse_item(raw)
        assert item is None


# ── search() pagination ───────────────────────────────────────────────────────

def _make_page_response(asins: list[str]) -> dict:
    """Build a mock PA-API SearchResult page with the given ASINs."""
    return {
        "SearchResult": {
            "Items": [
                {
                    "ASIN": asin,
                    "ItemInfo": {"Title": {"DisplayValue": f"Product {asin}"}},
                    "Offers": {"Listings": [{
                        "Price": {"Amount": 9.99},
                        "DeliveryInfo": {"IsAmazonFulfilled": False, "IsFreeShippingEligible": False},
                        "MerchantInfo": {"Name": "Seller"},
                        "Availability": {"Message": "In Stock"},
                    }]},
                }
                for asin in asins
            ]
        }
    }


@pytest.mark.asyncio
class TestSearch:
    async def test_single_page_when_few_results_requested(self, backend):
        """With max_results=5 and page_size=10, only 1 page call needed."""
        page1 = _make_page_response([f"B{i:010d}" for i in range(10)])

        with patch.object(backend, "_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = page1
            results = await backend.search("keyboard", max_results=5)

        assert mock_call.call_count == 1
        assert len(results) <= 5

    async def test_two_pages_when_more_results_needed(self, backend):
        """With max_results=15 and page_size=10, two page calls needed."""
        page1 = _make_page_response([f"B1{i:09d}" for i in range(10)])
        page2 = _make_page_response([f"B2{i:09d}" for i in range(10)])

        call_responses = [page1, page2]

        async def fake_call(keyword, item_count, item_page=1):
            return call_responses[item_page - 1]

        with patch.object(backend, "_call", side_effect=fake_call):
            with patch("search_backends.paapi_backend.asyncio.sleep", new_callable=AsyncMock):
                results = await backend.search("keyboard", max_results=15)

        assert len(results) == 15

    async def test_stops_early_on_empty_page(self, backend):
        """If a page returns no items, stop fetching more pages."""
        page1    = _make_page_response([f"B{i:010d}" for i in range(10)])
        empty    = {"SearchResult": {"Items": []}}

        async def fake_call(keyword, item_count, item_page=1):
            return page1 if item_page == 1 else empty

        with patch.object(backend, "_call", side_effect=fake_call):
            with patch("search_backends.paapi_backend.asyncio.sleep", new_callable=AsyncMock):
                results = await backend.search("keyboard", max_results=20)

        # Only the first page had items
        assert len(results) == 10

    async def test_deduplication(self, backend):
        """Items with duplicate ASINs should appear only once."""
        dupe_asin = "B0DUPLICATE"
        page1 = _make_page_response([dupe_asin, "B0UNIQUE001"])
        page2 = _make_page_response([dupe_asin, "B0UNIQUE002"])   # same ASIN again

        async def fake_call(keyword, item_count, item_page=1):
            return page1 if item_page == 1 else page2

        with patch.object(backend, "_call", side_effect=fake_call):
            with patch("search_backends.paapi_backend.asyncio.sleep", new_callable=AsyncMock):
                results = await backend.search("keyboard", max_results=20)

        asins = [r.asin for r in results]
        assert len(asins) == len(set(asins)), "Duplicate ASINs in results"

    async def test_page_call_failure_returns_partial_results(self, backend):
        """If page 2 fails, return whatever was gathered from page 1."""
        page1 = _make_page_response([f"B{i:010d}" for i in range(10)])

        async def fake_call(keyword, item_count, item_page=1):
            if item_page == 1:
                return page1
            raise RuntimeError("PA-API error on page 2")

        with patch.object(backend, "_call", side_effect=fake_call):
            with patch("search_backends.paapi_backend.asyncio.sleep", new_callable=AsyncMock):
                results = await backend.search("keyboard", max_results=15)

        assert len(results) == 10

    async def test_results_sorted_by_score(self, backend):
        """Results must come out sorted by score descending."""
        # Inject items with explicit ratings
        raw_items = [
            {
                "ASIN": f"B{i:010d}",
                "ItemInfo": {"Title": {"DisplayValue": f"Product {i}"}},
                "Offers": {"Listings": [{
                    "Price": {"Amount": 9.99},
                    "DeliveryInfo": {"IsAmazonFulfilled": False, "IsFreeShippingEligible": False},
                    "MerchantInfo": {"Name": "Seller"},
                    "Availability": {"Message": "In Stock"},
                }]},
                "CustomerReviews": {
                    "StarRating": {"Value": str(5 - i)},
                    "Count": {"Value": "1000"},
                },
            }
            for i in range(3)
        ]
        page = {"SearchResult": {"Items": raw_items}}

        with patch.object(backend, "_call", new_callable=AsyncMock, return_value=page):
            results = await backend.search("keyboard", max_results=10)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
