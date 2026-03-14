"""
tests/test_gateway.py — Integration tests for the consolidated FastAPI gateway.

Verifies:
  - /health returns 200 and is NOT intercepted by the shortener catch-all
  - /{code} with a known code returns 302 redirect
  - /{code} with an unknown code returns 404
  - /api/v1/ routes require X-API-Key (401 without it)
  - /api/v1/ routes return correct data with valid auth (mocked)
  - /docs is accessible and not intercepted by catch-all
  - Security headers (X-Content-Type-Options, X-Frame-Options) are in all responses
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Ensure project root on path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set a fake admin secret before importing gateway (which imports api_server)
os.environ.setdefault("API_ADMIN_SECRET", "test-admin-secret")

# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_KEY = "isk_" + "b" * 32
VALID_KEY_ROW = {
    "key":            VALID_KEY,
    "name":           "Test App",
    "plan":           "basic",
    "daily_limit":    1000,
    "total_requests": 0,
    "created_at":     "2026-01-01T00:00:00+00:00",
    "is_active":      True,
    "notes":          "",
}


@pytest.fixture
def client():
    """
    Build a gateway TestClient with all DB calls mocked out.
    Uses a fresh gateway app per test to avoid router state bleed.
    """
    from gateway import create_app
    from api_server import _windows
    _windows.clear()

    with patch("database.init_db", new=AsyncMock()):
        app = create_app(webhook_adapters=None)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture
def client_with_webhook():
    """Gateway TestClient that includes a mock webhook adapter."""
    from gateway import create_app
    from api_server import _windows
    _windows.clear()

    mock_adapter = MagicMock()
    mock_adapter.platform_name = "testplatform"
    mock_adapter.handle_webhook = AsyncMock(return_value={"ok": True})
    mock_adapter.handle_webhook_verify = AsyncMock(return_value={"verified": True})

    with patch("database.init_db", new=AsyncMock()):
        app = create_app(webhook_adapters=[mock_adapter])
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, mock_adapter


# ── Health endpoint ────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_ok_status(self, client):
        resp = client.get("/health")
        assert resp.json()["status"] == "ok"

    def test_health_not_intercepted_by_shortener(self, client):
        """
        /health must NOT be intercepted by the /{code} catch-all route.
        If it were, we'd get a DB lookup for 'health' (404), not the health handler.
        """
        with patch("database.get_long_url_by_code", new=AsyncMock(return_value=None)):
            resp = client.get("/health")
        # Should be 200 from health handler, not 404 from shortener
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Docs endpoint ─────────────────────────────────────────────────────────────

class TestDocsEndpoint:
    def test_docs_not_intercepted_by_shortener(self, client):
        """/docs must not be caught by /{code} catch-all."""
        resp = client.get("/docs", follow_redirects=True)
        # Docs returns 200 when FastAPI auto-docs are enabled
        assert resp.status_code == 200


# ── Shortener redirect ────────────────────────────────────────────────────────

class TestShortenerRedirect:
    def test_known_code_redirects_302(self, client):
        """/{code} with a known code should redirect with 302."""
        with patch("database.get_long_url_by_code", new=AsyncMock(return_value="https://amazon.com/dp/B0001")):
            with patch("database.log_click", new=AsyncMock()):
                resp = client.get("/abc123", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://amazon.com/dp/B0001"

    def test_unknown_code_returns_404(self, client):
        """/{code} with an unknown code should return 404."""
        with patch("database.get_long_url_by_code", new=AsyncMock(return_value=None)):
            resp = client.get("/unknowncode", follow_redirects=False)
        assert resp.status_code == 404

    def test_non_alphanumeric_code_returns_404(self, client):
        """/{code} with a non-alphanumeric code should return 404."""
        with patch("database.get_long_url_by_code", new=AsyncMock(return_value=None)):
            resp = client.get("/bad-code!", follow_redirects=False)
        # non-alphanumeric codes are rejected before DB lookup
        assert resp.status_code in (404, 422)

    def test_stats_endpoint_accessible(self, client):
        """/stats/{code} should be accessible and return stats."""
        stats_data = {"code": "abc123", "clicks": 5, "created_at": "2026-01-01"}
        with patch("database.get_link_stats", new=AsyncMock(return_value=stats_data)):
            resp = client.get("/stats/abc123")
        assert resp.status_code == 200
        assert resp.json()["code"] == "abc123"

    def test_stats_not_intercepted_by_code_catch_all(self, client):
        """/stats/{code} must be served by the stats handler, not /{code}."""
        stats_data = {"code": "abc123", "clicks": 3}
        with patch("database.get_link_stats", new=AsyncMock(return_value=stats_data)):
            with patch("database.get_long_url_by_code", new=AsyncMock(return_value=None)):
                resp = client.get("/stats/abc123")
        # Must return stats JSON, not a redirect or 404 from shortener
        assert resp.status_code == 200
        assert "clicks" in resp.json()


# ── API routes ────────────────────────────────────────────────────────────────

class TestAPIRoutes:
    def test_api_check_requires_auth(self, client):
        """/api/v1/check without X-API-Key returns 401."""
        resp = client.get("/api/v1/check?asin=B08XYZ12AB")
        assert resp.status_code == 401

    def test_api_check_with_invalid_key_returns_401(self, client):
        """/api/v1/check with an invalid key returns 401."""
        with patch("database.get_external_api_key", new=AsyncMock(return_value=None)):
            resp = client.get("/api/v1/check?asin=B08XYZ12AB", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 401

    def test_api_check_with_valid_key(self, client):
        """/api/v1/check with a valid key and mocked scraper returns 200."""
        from api_server import ShippingResult
        mock_result = MagicMock()
        mock_result.asin             = "B08XYZ12AB"
        mock_result.verified         = True
        mock_result.ships_to_israel  = True
        mock_result.is_free_shipping = True
        mock_result.note             = "Ships to Israel"

        with patch("database.get_external_api_key", new=AsyncMock(return_value=VALID_KEY_ROW)):
            with patch("database.log_api_request", new=AsyncMock()):
                with patch("database.delete_israel_cache", new=AsyncMock()):
                    with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=mock_result)):
                        resp = client.get(
                            "/api/v1/check?asin=B08XYZ12AB",
                            headers={"X-API-Key": VALID_KEY},
                        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["asin"] == "B08XYZ12AB"

    def test_api_quota_requires_auth(self, client):
        """/api/v1/quota without X-API-Key returns 401."""
        resp = client.get("/api/v1/quota")
        assert resp.status_code == 401

    def test_api_admin_requires_admin_secret(self, client):
        """/api/v1/admin/keys without X-Admin-Secret returns 401/503."""
        resp = client.get("/api/v1/admin/keys")
        assert resp.status_code in (401, 503)


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_health_has_security_headers(self, client):
        resp = client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"

    def test_404_has_security_headers(self, client):
        with patch("database.get_long_url_by_code", new=AsyncMock(return_value=None)):
            resp = client.get("/unknownxyz999")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"

    def test_api_route_has_security_headers(self, client):
        resp = client.get("/api/v1/check?asin=B08XYZ12AB")
        # Even 401 responses should have security headers
        assert resp.headers.get("x-content-type-options") == "nosniff"


# ── Webhook routes ────────────────────────────────────────────────────────────

class TestWebhookRoutes:
    def test_webhook_dispatches_to_adapter(self, client_with_webhook):
        """POST /webhook/{platform} should reach the registered adapter."""
        c, mock_adapter = client_with_webhook
        resp = c.post("/webhook/testplatform", json={"event": "test"})
        # The mock adapter's handle_webhook should have been called
        assert mock_adapter.handle_webhook.called

    def test_webhook_unknown_platform_returns_404(self, client_with_webhook):
        """POST /webhook/unknownplatform should return 404."""
        c, _ = client_with_webhook
        resp = c.post("/webhook/unknownplatform", json={})
        assert resp.status_code == 404

    def test_no_webhook_adapters_skips_router(self, client):
        """Without webhook adapters, /webhook/* returns 404."""
        resp = client.post("/webhook/telegram", json={})
        assert resp.status_code == 404
