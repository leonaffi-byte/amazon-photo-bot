"""
tests/test_web_app.py — Tests for the public web application (Phase 05, Plan 01).

Covers:
  - Homepage rendering (WEBA-01, WEBA-05)
  - Photo upload endpoint (validation, session creation)
  - SSE stream (4-stage progress events)
  - search_store DB functions (save, get, purge)
  - Result image endpoint
  - Result page (stub) behavior
"""
from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Ensure project root on path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required env vars before importing gateway
os.environ.setdefault("API_ADMIN_SECRET", "test-admin-secret")


# ── Test image factory ────────────────────────────────────────────────────────

def _make_png_bytes(width: int = 10, height: int = 10) -> bytes:
    """Create a minimal PNG image for testing using Pillow."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Shared app fixture ────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """
    Build a gateway TestClient with DB init mocked out.
    Uses a fresh gateway app per test to avoid router state bleed.
    """
    from gateway import create_app

    with patch("database.init_db", new=AsyncMock()):
        app = create_app(webhook_adapters=None)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ── TestHomePage ───────────────────────────────────────────────────────────────

class TestHomePage:
    def test_homepage_returns_200(self, client):
        """GET / returns 200 with HTML containing upload form."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "upload" in resp.text.lower()

    def test_mobile_viewport(self, client):
        """Response HTML contains <meta name='viewport'> tag (WEBA-05)."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="viewport"' in resp.text

    def test_rtl_default_hebrew(self, client):
        """GET / (no lang param) has dir='rtl' and lang='he' (WEBA-05)."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'dir="rtl"' in resp.text
        assert 'lang="he"' in resp.text

    def test_ltr_english(self, client):
        """GET /?lang=en has dir='ltr' and lang='en'."""
        resp = client.get("/?lang=en")
        assert resp.status_code == 200
        assert 'dir="ltr"' in resp.text
        assert 'lang="en"' in resp.text

    def test_affiliate_disclosure(self, client):
        """Response contains Amazon Associate disclosure text."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Amazon Associate" in resp.text

    def test_htmx_extension_included(self, client):
        """Base template includes htmx-ext-sse CDN link."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "htmx-ext-sse" in resp.text


# ── TestUpload ────────────────────────────────────────────────────────────────

class TestUpload:
    def test_valid_upload(self, client):
        """POST /upload with a small valid PNG returns 200 with SSE connect fragment."""
        png = _make_png_bytes()
        resp = client.post(
            "/upload",
            files={"photo": ("test.png", png, "image/png")},
        )
        assert resp.status_code == 200
        assert "sse-connect" in resp.text

    def test_oversized_file_rejected(self, client):
        """POST /upload with >10MB data returns 413."""
        large_data = b"x" * (10 * 1024 * 1024 + 1)
        resp = client.post(
            "/upload",
            files={"photo": ("big.jpg", large_data, "image/jpeg")},
        )
        assert resp.status_code == 413

    def test_non_image_rejected(self, client):
        """POST /upload with content_type='text/plain' returns 400."""
        resp = client.post(
            "/upload",
            files={"photo": ("doc.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_returns_session_in_html(self, client):
        """Response HTML from valid upload contains session_id for SSE stream."""
        png = _make_png_bytes()
        resp = client.post(
            "/upload",
            files={"photo": ("img.png", png, "image/png")},
        )
        assert resp.status_code == 200
        # Should contain /stream/ URL with a session_id
        assert "/stream/" in resp.text


# ── TestSSE ───────────────────────────────────────────────────────────────────

# Mock ProductInfo dataclass for tests
@dataclass
class _MockProductInfo:
    product_name: str = "Test Product"
    brand: Optional[str] = "TestBrand"
    category: str = "Electronics"
    key_features: list = field(default_factory=list)
    amazon_search_query: str = "test product query"
    alternative_query: str = "test product"
    confidence: str = "high"
    notes: str = ""
    bbox: Optional[tuple] = None


# Mock AmazonItem for tests
@dataclass
class _MockAmazonItem:
    asin: str = "B001TEST01"
    title: str = "Test Item"
    image_url: Optional[str] = None
    price_usd: Optional[float] = 29.99
    currency: str = "USD"
    rating: Optional[float] = 4.5
    review_count: Optional[int] = 100
    is_amazon_fulfilled: bool = True
    is_sold_by_amazon: bool = False
    is_prime: bool = True
    availability: str = "In Stock"
    free_delivery_likely: bool = False
    score: float = field(init=False, default=0.0)

    def __post_init__(self):
        import math
        if self.rating and self.review_count:
            self.score = self.rating * math.log10(self.review_count + 1)


class TestSSE:
    def _seed_pending(self, session_id: str, data: bytes) -> None:
        """Directly seed the _pending dict to simulate an upload."""
        import importlib
        router_mod = importlib.import_module("web_app.router")
        router_mod._pending[session_id] = (data, time.time())

    def test_stream_stages(self, client):
        """
        GET /stream/{session_id} returns text/event-stream content with all 4 stage
        keywords: Analyzing, Found 2 products, Searching, and a done event.
        """
        import secrets
        session_id = secrets.token_urlsafe(16)
        png = _make_png_bytes()
        self._seed_pending(session_id, png)

        mock_product1 = _MockProductInfo(product_name="Widget A")
        mock_product2 = _MockProductInfo(product_name="Widget B")
        mock_amazon_item = _MockAmazonItem()

        # Build a mock ProviderResult
        mock_winner = MagicMock()
        mock_winner.to_product_info_list.return_value = [mock_product1, mock_product2]

        with patch("providers.manager.analyse_image", new=AsyncMock(return_value=(mock_winner, []))) as mock_ai, \
             patch("amazon_search.search_amazon", new=AsyncMock(return_value=[mock_amazon_item])), \
             patch("image_annotator.annotate_products", return_value=b"fake_image_bytes"), \
             patch("web_app.search_store.save_web_search", new=AsyncMock(return_value="abc123")):

            resp = client.get(f"/stream/{session_id}")

        assert resp.status_code == 200
        content = resp.text
        assert "Analyzing" in content
        assert "Found 2 product" in content
        assert "Searching" in content
        # Done event with redirect URL
        assert "abc123" in content

    def test_stream_invalid_session(self, client):
        """GET /stream/nonexistent returns an error progress event."""
        resp = client.get("/stream/nonexistent-session-id-xyz")
        assert resp.status_code == 200
        content = resp.text
        # Should contain some error indication
        assert "expired" in content.lower() or "error" in content.lower() or "❌" in content


# ── TestSearchStore ───────────────────────────────────────────────────────────

class TestSearchStore:
    async def test_save_and_get(self, tmp_data_dir):
        """save_web_search then get_web_search returns matching row."""
        import database
        await database.init_db()

        from web_app.search_store import save_web_search, get_web_search

        products = [_MockProductInfo()]
        items = [_MockAmazonItem()]
        short_id = await save_web_search(
            photo_bytes=b"fake_photo",
            annotated_bytes=b"fake_annotated",
            products=products,
            all_results=[[items[0]]],
            lang="he",
        )

        assert short_id is not None
        assert len(short_id) > 0

        row = await get_web_search(short_id)
        assert row is not None
        assert row["short_id"] == short_id
        assert row["lang"] == "he"
        assert row["annotated_photo"] is not None

    async def test_get_nonexistent(self, tmp_data_dir):
        """get_web_search('nonexistent') returns None."""
        import database
        await database.init_db()

        from web_app.search_store import get_web_search
        result = await get_web_search("nonexistent_id_xyz")
        assert result is None

    async def test_purge_expired(self, tmp_data_dir):
        """Insert row with past expires_at, call purge_expired(), verify deleted."""
        import database
        await database.init_db()

        from web_app.search_store import purge_expired
        import time

        # Insert a row with expired timestamp directly
        past = time.time() - 1000
        async with database._get_conn() as db:
            await db.execute(
                """INSERT INTO web_searches
                   (short_id, photo_hash, annotated_photo, results_json, products_json, lang, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("expired_id", "hash123", None, "[]", "[]", "he", past - 3600, past),
            )
            await db.commit()

        count = await purge_expired()
        assert count == 1

        # Verify it's gone
        async with database._get_conn() as db:
            async with db.execute(
                "SELECT id FROM web_searches WHERE short_id = ?", ("expired_id",)
            ) as cursor:
                row = await cursor.fetchone()
        assert row is None

    async def test_purge_leaves_non_expired(self, tmp_data_dir):
        """purge_expired() does not delete rows that haven't expired yet."""
        import database
        await database.init_db()

        from web_app.search_store import save_web_search, purge_expired

        await save_web_search(
            photo_bytes=b"photo",
            annotated_bytes=None,
            products=[],
            all_results=[],
            lang="en",
        )
        count = await purge_expired()
        assert count == 0  # Fresh row should not be purged


# ── TestResultImage ───────────────────────────────────────────────────────────

class TestResultImage:
    async def test_image_endpoint_returns_jpeg(self, tmp_data_dir):
        """Save a web search, GET /search/{id}/image returns 200 with image/jpeg."""
        import database
        await database.init_db()

        from web_app.search_store import save_web_search

        short_id = await save_web_search(
            photo_bytes=b"photo",
            annotated_bytes=b"FAKE_JPEG_BYTES",
            products=[],
            all_results=[],
        )

        # Build a fresh client using real (already-initialized) DB
        with patch("database.init_db", new=AsyncMock()):
            from gateway import create_app
            app = create_app(webhook_adapters=None)
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get(f"/search/{short_id}/image")

        assert resp.status_code == 200
        assert "image" in resp.headers.get("content-type", "")

    async def test_image_404_for_missing(self, tmp_data_dir):
        """GET /search/nonexistent/image returns 404."""
        import database
        await database.init_db()

        with patch("database.init_db", new=AsyncMock()):
            from gateway import create_app
            app = create_app(webhook_adapters=None)
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/search/nonexistent-xyz-999/image")
        assert resp.status_code == 404


# ── TestOGTags (stub for Plan 02) ─────────────────────────────────────────────

class TestOGTags:
    async def test_result_page_exists(self, tmp_data_dir):
        """GET /search/{valid_id} returns 200 for a known result."""
        import database
        await database.init_db()

        from web_app.search_store import save_web_search

        short_id = await save_web_search(
            photo_bytes=b"photo",
            annotated_bytes=None,
            products=[],
            all_results=[],
        )

        with patch("database.init_db", new=AsyncMock()):
            from gateway import create_app
            app = create_app(webhook_adapters=None)
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200

    async def test_expired_result(self, tmp_data_dir):
        """Save with past expires_at, GET /search/{id} returns 410."""
        import database
        import time
        await database.init_db()

        past = time.time() - 1000
        async with database._get_conn() as db:
            await db.execute(
                """INSERT INTO web_searches
                   (short_id, photo_hash, annotated_photo, results_json, products_json, lang, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("exp_id_test", "hash456", None, "[]", "[]", "he", past - 3600, past),
            )
            await db.commit()

        with patch("database.init_db", new=AsyncMock()):
            from gateway import create_app
            app = create_app(webhook_adapters=None)
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/search/exp_id_test")

        assert resp.status_code == 410


# ── Helper for result page tests ──────────────────────────────────────────────

async def _save_mock_search(
    num_products: int = 1,
    expired: bool = False,
    is_sold_by_amazon: bool = False,
    is_amazon_fulfilled: bool = False,
    is_prime: bool = True,
    price_usd: float = 29.99,
    rating: float = 4.5,
    review_count: int = 100,
    asin: str = "B001TEST01",
    title: str = "Test Product Title",
) -> str:
    """
    Insert a minimal valid web_search row and return the short_id.

    Directly inserts into the DB (avoiding dataclass dependencies) so tests
    control the data precisely.
    """
    import json
    import secrets
    import time
    import database

    short_id = secrets.token_urlsafe(8)
    now = time.time()
    expires_at = (now - 1000) if expired else (now + 86400 * 30)

    item_dict = {
        "asin": asin,
        "title": title,
        "image_url": "https://example.com/img.jpg",
        "price_usd": price_usd,
        "currency": "USD",
        "rating": rating,
        "review_count": review_count,
        "is_amazon_fulfilled": is_amazon_fulfilled,
        "is_sold_by_amazon": is_sold_by_amazon,
        "is_prime": is_prime,
        "availability": "In Stock",
        "free_delivery_likely": False,
        "score": 0.0,
    }
    product_dict = {
        "product_name": f"Test Product {asin}",
        "brand": "TestBrand",
        "category": "Electronics",
        "key_features": [],
        "amazon_search_query": "test product",
        "alternative_query": "test",
        "confidence": "high",
        "notes": "",
        "bbox": None,
    }

    # Build per-product result lists
    results_json = json.dumps([[item_dict]] * num_products)
    products_json = json.dumps([product_dict] * num_products)

    async with database._get_conn() as db:
        await db.execute(
            """INSERT INTO web_searches
               (short_id, photo_hash, annotated_photo, results_json, products_json, lang, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (short_id, "testhash", b"FAKE_JPEG", results_json, products_json, "he", now, expires_at),
        )
        await db.commit()

    return short_id


def _make_app():
    """Create a TestClient using the real gateway with mocked DB init."""
    with patch("database.init_db", new=AsyncMock()):
        from gateway import create_app
        return create_app(webhook_adapters=None)


# ── TestResultPage ─────────────────────────────────────────────────────────────

class TestResultPage:
    async def test_product_card_fields(self, tmp_data_dir):
        """GET /search/{id} returns HTML with price, rating, and View on Amazon link."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(price_usd=49.99, rating=4.2, asin="B001CARD01")

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "49.99" in resp.text
        assert "4.2" in resp.text
        assert "View on Amazon" in resp.text

    async def test_affiliate_url(self, tmp_data_dir):
        """View on Amazon link contains the active affiliate tag."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(asin="B001AFFTAG")

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value="test-tag-20")):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "tag=test-tag-20" in resp.text

    async def test_shipping_badge_green(self, tmp_data_dir):
        """is_amazon_fulfilled=True produces a green ships-to-Israel badge."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(is_amazon_fulfilled=True, is_prime=False)

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "bg-green" in resp.text
        assert "Ships to Israel" in resp.text

    async def test_shipping_badge_red(self, tmp_data_dir):
        """All shipping flags False produces a red may-not-ship badge."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(
            is_sold_by_amazon=False,
            is_amazon_fulfilled=False,
            is_prime=False,
        )

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "bg-red" in resp.text
        assert "May not ship" in resp.text

    async def test_product_tabs(self, tmp_data_dir):
        """Two-product result shows Product 1 and Product 2 tab text."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(num_products=2)

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "Product 1" in resp.text
        assert "Product 2" in resp.text

    async def test_product_tab_switch(self, tmp_data_dir):
        """GET /search/{id}?product=1 shows the second product's results."""
        import json
        import database
        import secrets
        import time
        await database.init_db()

        # Build two distinct products
        item1 = {
            "asin": "B001TAB001", "title": "Product One Widget", "image_url": None,
            "price_usd": 10.0, "currency": "USD", "rating": 4.0, "review_count": 50,
            "is_amazon_fulfilled": False, "is_sold_by_amazon": False, "is_prime": False,
            "availability": "In Stock", "free_delivery_likely": False, "score": 0.0,
        }
        item2 = {
            "asin": "B002TAB002", "title": "Product Two Gadget", "image_url": None,
            "price_usd": 20.0, "currency": "USD", "rating": 3.5, "review_count": 25,
            "is_amazon_fulfilled": False, "is_sold_by_amazon": False, "is_prime": False,
            "availability": "In Stock", "free_delivery_likely": False, "score": 0.0,
        }
        product_dict = {
            "product_name": "Widget", "brand": None, "category": "Electronics",
            "key_features": [], "amazon_search_query": "widget", "alternative_query": "widget",
            "confidence": "high", "notes": "", "bbox": None,
        }
        results_json = json.dumps([[item1], [item2]])
        products_json = json.dumps([product_dict, {**product_dict, "product_name": "Gadget"}])

        short_id = secrets.token_urlsafe(8)
        now = time.time()
        async with database._get_conn() as db:
            await db.execute(
                """INSERT INTO web_searches
                   (short_id, photo_hash, annotated_photo, results_json, products_json, lang, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (short_id, "hash789", b"FAKE", results_json, products_json, "he", now, now + 86400),
            )
            await db.commit()

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}?product=1")

        assert resp.status_code == 200
        assert "Product Two Gadget" in resp.text

    async def test_og_tags(self, tmp_data_dir):
        """GET /search/{id} response contains og:image, og:title, og:url meta tags."""
        import database
        await database.init_db()

        short_id = await _save_mock_search()

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert 'property="og:image"' in resp.text
        assert 'property="og:title"' in resp.text
        assert 'property="og:url"' in resp.text

    async def test_noindex_meta(self, tmp_data_dir):
        """GET /search/{id} response contains noindex in a meta robots tag."""
        import database
        await database.init_db()

        short_id = await _save_mock_search()

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "noindex" in resp.text

    async def test_expired_result_410(self, tmp_data_dir):
        """Save with past expires_at, GET /search/{id} returns 410."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(expired=True)

        with patch("database.init_db", new=AsyncMock()):
            app = _make_app()
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 410

    async def test_missing_result_404(self, tmp_data_dir):
        """GET /search/nonexistent returns 404."""
        import database
        await database.init_db()

        with patch("database.init_db", new=AsyncMock()):
            app = _make_app()
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/search/nonexistent-xyz-does-not-exist")

        assert resp.status_code == 404


# ── TestPriceHistoryBar ────────────────────────────────────────────────────────

class TestPriceHistoryBar:
    async def test_price_history_bar_rendered(self, tmp_data_dir):
        """Price history bar and deal label appear when get_price_history returns data."""
        import database
        from price_history import PriceHistory
        await database.init_db()

        short_id = await _save_mock_search(asin="B001PHHIST")

        mock_ph = PriceHistory(
            asin="B001PHHIST",
            source="camelcamelcamel",
            current=25.0,
            low_all_time=18.0,
            avg_90d=30.0,
            avg_30d=28.0,
            low_90d=20.0,
        )

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=mock_ph)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        # Deal label should appear (25.0 <= 30.0 * 0.85 is False; 25.0 <= 30.0 * 0.95 → "Below avg")
        assert "Below avg" in resp.text or "Great deal" in resp.text or "All-time low" in resp.text
        # Price bar markup: a div with width style
        assert "width:" in resp.text or "w-" in resp.text

    async def test_price_history_unavailable(self, tmp_data_dir):
        """When get_price_history returns None, show placeholder text."""
        import database
        await database.init_db()

        short_id = await _save_mock_search(asin="B001PHNONE")

        with patch("database.init_db", new=AsyncMock()):
            with patch("database.get_active_tag", new=AsyncMock(return_value=None)):
                with patch("price_history.get_price_history", new=AsyncMock(return_value=None)):
                    app = _make_app()
                    with TestClient(app, raise_server_exceptions=True) as c:
                        resp = c.get(f"/search/{short_id}")

        assert resp.status_code == 200
        assert "Price history unavailable" in resp.text
