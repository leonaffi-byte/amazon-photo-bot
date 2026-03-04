"""Tests for dataforseo_labs.py"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from dataforseo_labs import DataForSEOLabs, RelatedKeyword, DFSProduct


# ── RelatedKeyword ────────────────────────────────────────────────────────────

def test_related_keyword_label_thousands():
    r = RelatedKeyword(keyword="wireless headphones", search_volume=76258)
    assert r.label() == "wireless headphones (76K/mo)"

def test_related_keyword_label_small():
    r = RelatedKeyword(keyword="headset", search_volume=500)
    assert r.label() == "headset (500/mo)"

def test_related_keyword_label_none_volume():
    r = RelatedKeyword(keyword="earbuds", search_volume=None)
    assert r.label() == "earbuds (?/mo)"

def test_related_keyword_label_exact_thousand():
    r = RelatedKeyword(keyword="speakers", search_volume=1000)
    assert r.label() == "speakers (1K/mo)"


# ── DFSProduct ────────────────────────────────────────────────────────────────

def test_dfs_product_defaults():
    p = DFSProduct(asin="B09XS7JWHH")
    assert p.title == ""
    assert p.price is None
    assert p.currency == "USD"
    assert p.rating is None
    assert p.image_url == ""

def test_dfs_product_full():
    p = DFSProduct(
        asin="B09XS7JWHH",
        title="Sony WH-1000XM5",
        price=298.0,
        currency="USD",
        rating=4.5,
        image_url="https://example.com/img.jpg",
        rank=1,
    )
    assert p.title == "Sony WH-1000XM5"
    assert p.price == 298.0
    assert p.rank == 1


# ── DataForSEOLabs._post ──────────────────────────────────────────────────────

def make_labs():
    return DataForSEOLabs("user@test.com", "password123")

def mock_response(status_code, items=None, extra=None):
    result = {"items": items or []}
    if extra:
        result.update(extra)
    return {
        "tasks": [{
            "status_code": status_code,
            "status_message": "Ok." if status_code == 20000 else "Error.",
            "result": [result],
        }]
    }


@pytest.mark.asyncio
async def test_post_success():
    labs = make_labs()
    with patch("aiohttp.ClientSession") as mock_session_cls:
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_response(20000, [{"keyword": "test"}]))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = await labs._post("related_keywords/live", [{}])
        assert result.get("items") == [{"keyword": "test"}]


@pytest.mark.asyncio
async def test_post_error_returns_empty():
    labs = make_labs()
    with patch("aiohttp.ClientSession") as mock_session_cls:
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_response(40402))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = await labs._post("related_keywords/live", [{}])
        assert result == {}


@pytest.mark.asyncio
async def test_post_exception_returns_empty():
    labs = make_labs()
    with patch("aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("connection refused")
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = await labs._post("related_keywords/live", [{}])
        assert result == {}


# ── related_keywords ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_related_keywords_filters_same_keyword():
    labs = make_labs()
    items = [
        {"keyword_data": {"keyword": "wireless headphones", "keyword_info": {"search_volume": 76000}}},
        {"keyword_data": {"keyword": "wireless earbuds",    "keyword_info": {"search_volume": 51000}}},
        {"keyword_data": {"keyword": "bluetooth headphones","keyword_info": {"search_volume": 20000}}},
    ]
    with patch.object(labs, "_post", AsyncMock(return_value={"items": items})):
        result = await labs.related_keywords("wireless headphones", limit=6)
    # "wireless headphones" should be filtered out (same as query)
    keywords = [r.keyword for r in result]
    assert "wireless headphones" not in keywords
    assert "wireless earbuds" in keywords
    assert "bluetooth headphones" in keywords


@pytest.mark.asyncio
async def test_related_keywords_empty_response():
    labs = make_labs()
    with patch.object(labs, "_post", AsyncMock(return_value={})):
        result = await labs.related_keywords("headphones")
    assert result == []


@pytest.mark.asyncio
async def test_related_keywords_respects_limit():
    labs = make_labs()
    items = [
        {"keyword_data": {"keyword": f"keyword {i}", "keyword_info": {"search_volume": i * 100}}}
        for i in range(1, 10)
    ]
    with patch.object(labs, "_post", AsyncMock(return_value={"items": items})):
        result = await labs.related_keywords("base", limit=4)
    assert len(result) <= 4


# ── enrich_asin ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_asin_returns_product():
    labs = make_labs()
    items = [{
        "ranked_serp_element": {
            "serp_item": {
                "title":      "Sony WH-1000XM5",
                "price_from": 298,
                "currency":   "USD",
                "rating":     {"value": 4.5},
                "image_url":  "https://example.com/img.jpg",
                "rank_absolute": 1,
            }
        }
    }]
    with patch.object(labs, "_post", AsyncMock(return_value={"items": items})):
        result = await labs.enrich_asin("B09XS7JWHH")
    assert result is not None
    assert result.asin  == "B09XS7JWHH"
    assert result.title == "Sony WH-1000XM5"
    assert result.price == 298.0
    assert result.rating == 4.5
    assert result.image_url == "https://example.com/img.jpg"
    assert result.rank == 1


@pytest.mark.asyncio
async def test_enrich_asin_no_items_returns_none():
    labs = make_labs()
    with patch.object(labs, "_post", AsyncMock(return_value={"items": []})):
        result = await labs.enrich_asin("BADASIN000")
    assert result is None


@pytest.mark.asyncio
async def test_enrich_asin_empty_serp_item_skipped():
    labs = make_labs()
    items = [
        {"ranked_serp_element": {"serp_item": {}}},   # no title or price → skip
        {"ranked_serp_element": {"serp_item": {"title": "Found it", "price_from": 50}}},
    ]
    with patch.object(labs, "_post", AsyncMock(return_value={"items": items})):
        result = await labs.enrich_asin("B09XS7JWHH")
    assert result is not None
    assert result.title == "Found it"
    assert result.price == 50.0


# ── get_competitors ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_competitors_excludes_self():
    labs = make_labs()
    items = [
        {"asin": "B09XS7JWHH"},   # same as query → excluded
        {"asin": "B0BS1QCFHX"},
        {"asin": "B0F3PQHWTZ"},
    ]
    with patch.object(labs, "_post", AsyncMock(return_value={"items": items})):
        result = await labs.get_competitors("B09XS7JWHH", limit=10)
    assert "B09XS7JWHH" not in result
    assert "B0BS1QCFHX" in result
    assert "B0F3PQHWTZ" in result


@pytest.mark.asyncio
async def test_get_competitors_empty():
    labs = make_labs()
    with patch.object(labs, "_post", AsyncMock(return_value={})):
        result = await labs.get_competitors("B09XS7JWHH")
    assert result == []


# ── enrich_many ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_many_concurrency():
    labs = make_labs()
    call_count = 0

    async def fake_enrich(asin):
        nonlocal call_count
        call_count += 1
        return DFSProduct(asin=asin, title=f"Product {asin}", price=99.0)

    with patch.object(labs, "enrich_asin", side_effect=fake_enrich):
        result = await labs.enrich_many(["A", "B", "C", "D"], concurrency=2)

    assert call_count == 4
    assert len(result) == 4
    assert result["A"].title == "Product A"


@pytest.mark.asyncio
async def test_enrich_many_skips_none():
    labs = make_labs()

    async def fake_enrich(asin):
        return DFSProduct(asin=asin, title="ok") if asin != "BAD" else None

    with patch.object(labs, "enrich_asin", side_effect=fake_enrich):
        result = await labs.enrich_many(["GOOD", "BAD"], concurrency=2)

    assert "GOOD" in result
    assert "BAD" not in result
