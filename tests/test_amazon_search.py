"""
Tests for amazon_search.py.

Covers:
  - get_backend(): auto / rapidapi / paapi mode selection
  - search_amazon(): primary query success, fallback to alternative_query,
    deduplication, Israel filter, sort order, max_results limit
  - backend_name(): returns human-readable string
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import amazon_search
import config
from image_analyzer import ProductInfo
from search_backends.base import AmazonItem


@pytest.fixture(autouse=True)
def reset_backend():
    """Each test gets a fresh backend."""
    amazon_search._backend = None
    yield
    amazon_search._backend = None


def make_product(**kwargs) -> ProductInfo:
    defaults = dict(
        product_name="Wireless Keyboard",
        brand="TestBrand",
        category="Electronics",
        key_features=["Wireless", "Backlit"],
        amazon_search_query="TestBrand wireless keyboard",
        alternative_query="wireless keyboard backlit",
        confidence="high",
        notes="",
    )
    defaults.update(kwargs)
    return ProductInfo(**defaults)


def make_item(asin: str, rating: float = 4.0, review_count: int = 100,
              fba: bool = False) -> AmazonItem:
    return AmazonItem(
        asin=asin,
        title=f"Product {asin}",
        image_url=None,
        price_usd=29.99,
        currency="USD",
        rating=rating,
        review_count=review_count,
        is_amazon_fulfilled=fba,
        is_sold_by_amazon=False,
        is_prime=fba,
        availability="In Stock",
    )


# ── get_backend() ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetBackend:
    async def test_auto_prefers_paapi_when_both_keys_set(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")

        async def mock_get(key_name):
            return {
                "amazon_access_key":    "AKIATEST",
                "amazon_secret_key":    "secret",
                "amazon_associate_tag": "tag-20",
                "rapidapi_key":         "rapid-key",
            }.get(key_name)

        # key_store is imported lazily inside _build_backend → patch at module level
        with patch("key_store.get", side_effect=mock_get):
            with patch("search_backends.paapi_backend.PaapiBackend") as MockPaapi:
                MockPaapi.return_value = MagicMock(name="PA-API 5.0")
                backend = await amazon_search._build_backend()

        MockPaapi.assert_called_once()

    async def test_auto_falls_back_to_rapidapi_when_no_paapi(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")

        async def mock_get(key_name):
            if key_name == "rapidapi_key":
                return "rapid-key"
            return None

        with patch("key_store.get", side_effect=mock_get):
            with patch("search_backends.rapidapi_backend.RapidAPIBackend") as MockRapid:
                MockRapid.return_value = MagicMock(name="RapidAPI")
                backend = await amazon_search._build_backend()

        MockRapid.assert_called_once()

    async def test_no_keys_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")

        with patch("key_store.get", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError):
                await amazon_search._build_backend()

    async def test_explicit_rapidapi_requires_key(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "rapidapi")

        with patch("key_store.get", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError, match="RAPIDAPI_KEY"):
                await amazon_search._build_backend()

    async def test_explicit_paapi_requires_keys(self, monkeypatch):
        monkeypatch.setattr(config, "SEARCH_BACKEND", "paapi")

        with patch("key_store.get", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError, match="PA-API"):
                await amazon_search._build_backend()


# ── search_amazon() ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSearchAmazon:
    def _mock_backend(self, primary_results=None, alt_results=None):
        """Return a mock SearchBackend."""
        backend = MagicMock()
        backend.name = "MockBackend"
        call_count = [0]

        async def fake_search(query, max_results):
            call_count[0] += 1
            if call_count[0] == 1:
                return primary_results or []
            return alt_results or []

        backend.search = fake_search
        return backend

    async def test_returns_primary_results(self):
        items = [make_item(f"B{i:010d}") for i in range(5)]
        backend = self._mock_backend(primary_results=items)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(make_product(), max_results=10)

        assert len(results) == 5

    async def test_falls_back_to_alternative_query_when_few_results(self):
        # Primary returns only 2 items (< 3) → triggers fallback
        primary = [make_item(f"B{i:010d}") for i in range(2)]
        alt     = [make_item(f"C{i:010d}") for i in range(5)]
        backend = self._mock_backend(primary_results=primary, alt_results=alt)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(
                make_product(
                    amazon_search_query="primary query",
                    alternative_query="alternative query",
                ),
                max_results=10,
            )

        # Should have items from both queries (deduped)
        assert len(results) == 7

    async def test_no_fallback_when_same_queries(self):
        """When primary == alternative query, should NOT make a second API call."""
        primary = [make_item(f"B{i:010d}") for i in range(2)]
        call_count = [0]

        async def fake_search(query, max_results):
            call_count[0] += 1
            return primary

        backend = MagicMock()
        backend.name = "MB"
        backend.search = fake_search

        product = make_product(
            amazon_search_query="same query",
            alternative_query="same query",   # intentionally same
        )

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            await amazon_search.search_amazon(product, max_results=10)

        assert call_count[0] == 1   # Only one search call

    async def test_deduplication(self):
        """Items returned by both primary and alternative must appear once."""
        shared = make_item("B0SHARED001")
        primary = [shared, make_item("B0UNIQUE001")]
        alt     = [shared, make_item("B0UNIQUE002")]
        backend = self._mock_backend(primary_results=primary, alt_results=alt)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(make_product(), max_results=10)

        asins = [r.asin for r in results]
        assert len(asins) == len(set(asins))

    async def test_results_sorted_by_score_descending(self):
        items = [
            make_item(f"B{i:010d}", rating=float(5 - i), review_count=1000)
            for i in range(5)
        ]
        # Shuffle so we verify sort happens in search_amazon, not in the backend
        import random
        shuffled = list(items)
        random.shuffle(shuffled)

        backend = MagicMock()
        backend.name = "MB"
        backend.search = AsyncMock(return_value=shuffled)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(make_product(), max_results=10)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_max_results_respected(self):
        items = [make_item(f"B{i:010d}") for i in range(30)]
        backend = MagicMock()
        backend.name = "MB"
        backend.search = AsyncMock(return_value=items)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(make_product(), max_results=10)

        assert len(results) <= 10

    async def test_israel_filter_applied(self):
        fba_items     = [make_item(f"B{i:010d}", fba=True) for i in range(3)]
        non_fba_items = [make_item(f"C{i:010d}", fba=False) for i in range(5)]
        all_items = fba_items + non_fba_items

        backend = MagicMock()
        backend.name = "MB"
        backend.search = AsyncMock(return_value=all_items)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(
                make_product(), max_results=20, israel_free_delivery_only=True
            )

        assert all(r.qualifies_for_israel_free_delivery for r in results)
        assert len(results) == 3

    async def test_israel_filter_falls_back_to_all_when_no_eligible(self):
        """If israel filter would remove all results, return unfiltered."""
        items = [make_item(f"B{i:010d}", fba=False) for i in range(5)]

        backend = MagicMock()
        backend.name = "MB"
        backend.search = AsyncMock(return_value=items)

        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=backend):
            results = await amazon_search.search_amazon(
                make_product(), max_results=20, israel_free_delivery_only=True
            )

        # Falls back to unfiltered
        assert len(results) == 5


# ── backend_name() ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBackendName:
    async def test_returns_string(self):
        mock_backend = MagicMock()
        mock_backend.name = "Amazon PA-API 5.0"
        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock, return_value=mock_backend):
            name = await amazon_search.backend_name()
        assert name == "Amazon PA-API 5.0"

    async def test_returns_not_configured_on_error(self):
        with patch.object(amazon_search, "get_backend", new_callable=AsyncMock,
                          side_effect=RuntimeError("no keys")):
            name = await amazon_search.backend_name()
        assert name == "not configured"
