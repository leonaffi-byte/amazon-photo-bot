"""
tests/test_api_server.py — Tests for the Israel Shipping Verifier API

Uses FastAPI's TestClient (sync) and httpx AsyncClient for async tests.
All DB and israel_scraper calls are mocked — no real network/browser needed.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set a fake admin secret before importing the app
import os
os.environ.setdefault("API_ADMIN_SECRET", "test-admin-secret")

from api_server import app, _windows, PLAN_LIMITS


# ── Fixtures ───────────────────────────────────────────────────────────────────

VALID_KEY = "isk_" + "a" * 32
ADMIN_SECRET = "test-admin-secret"

def _make_key_row(
    key:           str  = VALID_KEY,
    name:          str  = "Test App",
    plan:          str  = "basic",
    daily_limit:   int  = 1000,
    total_requests:int  = 0,
    is_active:     bool = True,
    notes:         str  = "",
) -> dict:
    return {
        "key":            key,
        "name":           name,
        "plan":           plan,
        "daily_limit":    daily_limit,
        "total_requests": total_requests,
        "created_at":     "2026-01-01T00:00:00+00:00",
        "is_active":      is_active,
        "notes":          notes,
    }


@pytest.fixture(autouse=True)
def clear_rate_windows():
    """Reset in-memory rate-limit windows before every test."""
    _windows.clear()
    yield
    _windows.clear()


@pytest.fixture
def client():
    """Sync TestClient with DB init mocked out."""
    with patch("database.init_db", new=AsyncMock()):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ── /health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "israel-shipping-api" in r.json()["service"]

    def test_no_auth_required(self, client):
        r = client.get("/health")
        assert r.status_code == 200


# ── /v1/check ─────────────────────────────────────────────────────────────────

class TestCheckSingle:
    def _mock_result(self, ships=True, free=True):
        from israel_scraper import IsraelShippingResult
        return IsraelShippingResult(
            asin             = "B08XYZ12AB",
            verified         = True,
            ships_to_israel  = ships,
            is_free_shipping = free,
            note             = "✅ Verified: ships free to 🇮🇱 Israel",
        )

    def test_requires_auth(self, client):
        r = client.get("/v1/check?asin=B08XYZ12AB")
        assert r.status_code == 401

    def test_invalid_key_rejected(self, client):
        with patch("database.get_external_api_key", new=AsyncMock(return_value=None)):
            r = client.get("/v1/check?asin=B08XYZ12AB",
                           headers={"X-API-Key": "bad-key"})
        assert r.status_code == 401

    def test_revoked_key_rejected(self, client):
        row = _make_key_row(is_active=False)
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            r = client.get("/v1/check?asin=B08XYZ12AB",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 403

    def test_invalid_asin_length(self, client):
        row = _make_key_row()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            r = client.get("/v1/check?asin=SHORT",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 422

    def test_valid_request(self, client):
        row = _make_key_row()
        result = self._mock_result()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                with patch("database.log_api_request", new=AsyncMock()):
                    r = client.get("/v1/check?asin=B08XYZ12AB",
                                   headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        data = r.json()
        assert data["asin"] == "B08XYZ12AB"
        assert data["verified"] is True
        assert data["ships_to_israel"] is True
        assert data["is_free_shipping"] is True

    def test_fresh_param_clears_cache(self, client):
        row = _make_key_row()
        result = self._mock_result()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.delete_israel_cache", new=AsyncMock()) as mock_del:
                with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                    with patch("database.log_api_request", new=AsyncMock()):
                        r = client.get("/v1/check?asin=B08XYZ12AB&fresh=true",
                                       headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        mock_del.assert_called_once_with("B08XYZ12AB")

    def test_does_not_ship(self, client):
        row = _make_key_row()
        result = self._mock_result(ships=False, free=False)
        result.note = "❌ Verified: does not ship to 🇮🇱 Israel"
        result.ships_to_israel  = False
        result.is_free_shipping = False
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                with patch("database.log_api_request", new=AsyncMock()):
                    r = client.get("/v1/check?asin=B08XYZ12AB",
                                   headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        assert r.json()["ships_to_israel"] is False


# ── Rate limiting ──────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_rate_limit_enforced(self, client):
        """After hitting the daily limit, return 429."""
        row = _make_key_row(plan="free", daily_limit=2)
        from israel_scraper import IsraelShippingResult
        result = IsraelShippingResult(
            asin="B08XYZ12AB", verified=True,
            ships_to_israel=True, is_free_shipping=True,
            note="ok",
        )
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                with patch("database.log_api_request", new=AsyncMock()):
                    # First two succeed
                    r1 = client.get("/v1/check?asin=B08XYZ12AB", headers={"X-API-Key": VALID_KEY})
                    r2 = client.get("/v1/check?asin=B08XYZ12AB", headers={"X-API-Key": VALID_KEY})
                    # Third is over limit
                    r3 = client.get("/v1/check?asin=B08XYZ12AB", headers={"X-API-Key": VALID_KEY})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429


# ── /v1/batch ─────────────────────────────────────────────────────────────────

class TestBatch:
    def test_batch_valid(self, client):
        row = _make_key_row(daily_limit=100)
        from israel_scraper import IsraelShippingResult
        result = IsraelShippingResult(
            asin="B08XYZ12AB", verified=True,
            ships_to_israel=True, is_free_shipping=True, note="ok",
        )
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                with patch("database.log_api_request", new=AsyncMock()):
                    r = client.post(
                        "/v1/batch",
                        json={"asins": ["B08XYZ12AB", "B07ABC12DE"]},
                        headers={"X-API-Key": VALID_KEY},
                    )
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert len(data["results"]) == 2

    def test_batch_rejects_too_many(self, client):
        row = _make_key_row()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            r = client.post(
                "/v1/batch",
                json={"asins": [f"B{str(i).zfill(9)}" for i in range(11)]},
                headers={"X-API-Key": VALID_KEY},
            )
        assert r.status_code == 422


# ── /v1/cache/{asin} ──────────────────────────────────────────────────────────

class TestCache:
    def test_404_when_not_cached(self, client):
        row = _make_key_row()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=None)):
                r = client.get("/v1/cache/B08XYZ12AB",
                               headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 404

    def test_returns_cached(self, client):
        row = _make_key_row()
        from israel_scraper import IsraelShippingResult
        cached = IsraelShippingResult(
            asin="B08XYZ12AB", verified=True,
            ships_to_israel=True, is_free_shipping=True,
            note="✅ Verified: ships free",
        )
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.get_israel_cache", new=AsyncMock(return_value=cached)):
                r = client.get("/v1/cache/B08XYZ12AB",
                               headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        assert r.json()["cached"] is True

    def test_delete_cache(self, client):
        row = _make_key_row()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.delete_israel_cache", new=AsyncMock()) as mock_del:
                r = client.delete("/v1/cache/B08XYZ12AB",
                                  headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        mock_del.assert_called_once_with("B08XYZ12AB")


# ── /v1/quota ─────────────────────────────────────────────────────────────────

class TestQuota:
    def test_shows_usage(self, client):
        row = _make_key_row(plan="basic", daily_limit=1000)
        from israel_scraper import IsraelShippingResult
        result = IsraelShippingResult(
            asin="B08XYZ12AB", verified=True,
            ships_to_israel=True, is_free_shipping=True, note="ok",
        )
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("israel_scraper.check_shipping", new=AsyncMock(return_value=result)):
                with patch("database.log_api_request", new=AsyncMock()):
                    client.get("/v1/check?asin=B08XYZ12AB", headers={"X-API-Key": VALID_KEY})

            r = client.get("/v1/quota", headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        data = r.json()
        assert data["plan"]        == "basic"
        assert data["daily_limit"] == 1000
        assert data["used_today"]  == 2   # /check + /quota both consume a slot
        assert data["remaining"]   == 998


# ── Admin endpoints ────────────────────────────────────────────────────────────

class TestAdmin:
    HEADERS = {"X-Admin-Secret": ADMIN_SECRET}

    def test_create_key(self, client):
        new_row = _make_key_row(name="New App", plan="pro", daily_limit=10000)
        with patch("database.create_external_api_key", new=AsyncMock(return_value=new_row)):
            r = client.post(
                "/v1/admin/keys",
                json={"name": "New App", "plan": "pro"},
                headers=self.HEADERS,
            )
        assert r.status_code == 201
        assert r.json()["plan"] == "pro"

    def test_create_key_requires_admin(self, client):
        r = client.post(
            "/v1/admin/keys",
            json={"name": "Hack", "plan": "pro"},
            headers={"X-Admin-Secret": "wrong"},
        )
        assert r.status_code == 401

    def test_list_keys(self, client):
        rows = [_make_key_row(), _make_key_row(key="isk_" + "b" * 32, name="App2")]
        with patch("database.list_external_api_keys", new=AsyncMock(return_value=rows)):
            r = client.get("/v1/admin/keys", headers=self.HEADERS)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_revoke_key(self, client):
        row = _make_key_row()
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.revoke_external_api_key", new=AsyncMock()) as mock_rev:
                r = client.delete(f"/v1/admin/keys/{VALID_KEY}", headers=self.HEADERS)
        assert r.status_code == 200
        mock_rev.assert_called_once_with(VALID_KEY)

    def test_revoke_nonexistent_key_404(self, client):
        with patch("database.get_external_api_key", new=AsyncMock(return_value=None)):
            r = client.delete("/v1/admin/keys/isk_nonexistent",
                              headers=self.HEADERS)
        assert r.status_code == 404

    def test_update_key_plan(self, client):
        row = _make_key_row()
        updated = _make_key_row(plan="pro", daily_limit=10000)
        with patch("database.get_external_api_key", new=AsyncMock(return_value=row)):
            with patch("database.update_external_api_key", new=AsyncMock(return_value=updated)):
                r = client.patch(
                    f"/v1/admin/keys/{VALID_KEY}?plan=pro&limit=10000",
                    headers=self.HEADERS,
                )
        assert r.status_code == 200
        assert r.json()["plan"] == "pro"
