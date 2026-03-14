"""
tests/test_admin_web.py — Test stubs for admin web dashboard (ADMN-01 through ADMN-06).

Test coverage:
  ADMN-01: Authentication (Telegram Login Widget HMAC + fallback token)
  ADMN-02: Dashboard home page (stat cards, HTMX polling)
  ADMN-03: API Key management (list, save, masking)
  ADMN-04: Tag management (list, activate, add)
  ADMN-05: Settings page (list, update)
  ADMN-06: Provider health table
"""
from __future__ import annotations

import hashlib
import hmac
import time
import unittest.mock
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── ADMN-01: Auth — Telegram Login Widget HMAC ───────────────────────────────

class TestTelegramLoginVerification:
    """Tests for verify_telegram_login HMAC-SHA256 verification."""

    def _make_valid_data(self, bot_token: str = "test_bot_token_123") -> dict:
        """Build valid Telegram login data with correct HMAC."""
        data = {
            "id": "12345678",
            "first_name": "Test",
            "auth_date": str(int(time.time())),
        }
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        data["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        return data

    def test_valid_hmac_accepted(self):
        """verify_telegram_login returns True for valid data within 24h."""
        from admin_dashboard.auth import verify_telegram_login
        bot_token = "test_bot_token_123"
        data = self._make_valid_data(bot_token)
        assert verify_telegram_login(data, bot_token) is True

    def test_tampered_hash_rejected(self):
        """verify_telegram_login returns False for tampered hash."""
        from admin_dashboard.auth import verify_telegram_login
        bot_token = "test_bot_token_123"
        data = self._make_valid_data(bot_token)
        data["hash"] = "deadbeef" * 8  # wrong hash
        assert verify_telegram_login(data, bot_token) is False

    def test_stale_data_rejected(self):
        """verify_telegram_login returns False for auth_date older than 24h."""
        from admin_dashboard.auth import verify_telegram_login
        bot_token = "test_bot_token_123"
        # Use stale timestamp (25 hours ago)
        stale_time = int(time.time()) - 25 * 3600
        data = {
            "id": "12345678",
            "first_name": "Test",
            "auth_date": str(stale_time),
        }
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        data["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        assert verify_telegram_login(data, bot_token) is False

    def test_original_data_not_mutated(self):
        """verify_telegram_login does not mutate the input dict."""
        from admin_dashboard.auth import verify_telegram_login
        bot_token = "test_bot_token_123"
        data = self._make_valid_data(bot_token)
        original_keys = set(data.keys())
        verify_telegram_login(data, bot_token)
        assert set(data.keys()) == original_keys
        assert "hash" in data  # hash field preserved in original


# ── ADMN-01: Auth — Fallback Token ───────────────────────────────────────────

class TestFallbackToken:
    """Tests for generate_fallback_token / verify_fallback_token."""

    def test_generate_returns_nonempty_string(self):
        """generate_fallback_token returns a non-empty urlsafe string."""
        from admin_dashboard.auth import generate_fallback_token
        token = generate_fallback_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_valid_token(self):
        """verify_fallback_token returns True for current token within 24h."""
        from admin_dashboard.auth import generate_fallback_token, verify_fallback_token
        token = generate_fallback_token()
        assert verify_fallback_token(token) is True

    def test_verify_wrong_token_rejected(self):
        """verify_fallback_token returns False for a wrong token."""
        from admin_dashboard.auth import generate_fallback_token, verify_fallback_token
        generate_fallback_token()
        assert verify_fallback_token("wrong_token_here") is False

    def test_verify_expired_token_rejected(self):
        """verify_fallback_token returns False when token is older than 24h."""
        import admin_dashboard.auth as auth_mod
        from admin_dashboard.auth import generate_fallback_token, verify_fallback_token
        token = generate_fallback_token()
        # Manually backdate the issued_at time
        original = auth_mod._token_issued_at
        auth_mod._token_issued_at = time.time() - 25 * 3600
        try:
            assert verify_fallback_token(token) is False
        finally:
            auth_mod._token_issued_at = original

    def test_verify_without_generating_first(self):
        """verify_fallback_token returns False if no token has been generated."""
        import admin_dashboard.auth as auth_mod
        from admin_dashboard.auth import verify_fallback_token
        original_token = auth_mod._fallback_token
        auth_mod._fallback_token = None
        try:
            assert verify_fallback_token("any_token") is False
        finally:
            auth_mod._fallback_token = original_token


# ── ADMN-02: Sparklines ───────────────────────────────────────────────────────

class TestSparklines:
    """Tests for sparkline SVG generation."""

    def test_valid_data_returns_svg(self):
        """points_to_svg with valid data returns a string starting with <svg."""
        from admin_dashboard.sparklines import points_to_svg
        result = points_to_svg([10, 20, 15, 30, 5, 25, 18])
        assert isinstance(result, str)
        assert result.startswith("<svg")

    def test_empty_list_returns_flat_svg(self):
        """points_to_svg([]) returns a flat-line SVG without crashing."""
        from admin_dashboard.sparklines import points_to_svg
        result = points_to_svg([])
        assert isinstance(result, str)
        assert result.startswith("<svg")

    def test_all_zeros_returns_flat_svg(self):
        """points_to_svg([0, 0, 0, 0, 0, 0, 0]) returns a flat-line SVG."""
        from admin_dashboard.sparklines import points_to_svg
        result = points_to_svg([0, 0, 0, 0, 0, 0, 0])
        assert isinstance(result, str)
        assert result.startswith("<svg")
        assert "polyline" in result

    def test_svg_contains_polyline(self):
        """points_to_svg returns SVG with a polyline element."""
        from admin_dashboard.sparklines import points_to_svg
        result = points_to_svg([1, 2, 3, 4, 5])
        assert "polyline" in result

    def test_single_value_no_crash(self):
        """points_to_svg with a single value does not crash."""
        from admin_dashboard.sparklines import points_to_svg
        result = points_to_svg([42])
        assert result.startswith("<svg")


# ── ADMN-02: Database daily search counts ────────────────────────────────────

class TestDailySearchCounts:
    """Tests for get_daily_search_counts added to database.py."""

    @pytest.mark.asyncio
    async def test_returns_list_of_correct_length(self):
        """get_daily_search_counts(days=7) returns a list of 7 ints."""
        import database
        await database.init_db()
        from database import get_daily_search_counts
        result = await get_daily_search_counts(days=7)
        assert isinstance(result, list)
        assert len(result) == 7

    @pytest.mark.asyncio
    async def test_all_elements_are_ints(self):
        """All elements in the returned list are integers."""
        import database
        await database.init_db()
        from database import get_daily_search_counts
        result = await get_daily_search_counts(days=7)
        for item in result:
            assert isinstance(item, int)

    @pytest.mark.asyncio
    async def test_custom_days_length(self):
        """get_daily_search_counts(days=14) returns a list of 14 ints."""
        import database
        await database.init_db()
        from database import get_daily_search_counts
        result = await get_daily_search_counts(days=14)
        assert len(result) == 14


# ── ADMN-02: Dashboard home page ─────────────────────────────────────────────

class TestDashboardHomePage:
    """Tests for authenticated dashboard home page."""

    @pytest.fixture
    def admin_client(self):
        """Build FastAPI test app with pre-seeded admin session."""
        pytest.skip("Wave 1 integration stub — requires full app setup")

    @pytest.mark.asyncio
    async def test_unauthenticated_redirects_to_login(self, admin_client):
        """GET /admin/ with no session returns redirect to /admin/login."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_authenticated_returns_200(self, admin_client):
        """GET /admin/ with valid session returns 200 with stat cards."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_partial_stats_returns_fragment(self, admin_client):
        """GET /admin/partials/stats returns HTML fragment, not full page."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_partial_health_returns_fragment(self, admin_client):
        """GET /admin/partials/health returns HTML fragment with provider table."""
        pytest.skip("Wave 1 integration stub")


# ── ADMN-03: API Key management ───────────────────────────────────────────────

class TestApiKeyManagement:
    """Tests for API key listing, saving, and masking."""

    @pytest.mark.asyncio
    async def test_keys_page_loads(self):
        """GET /admin/keys returns 200 with key group list."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_save_key_redirects(self):
        """POST /admin/keys/{group} saves key and redirects."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_key_masking_in_response(self):
        """API key values are masked in template responses."""
        pytest.skip("Wave 1 integration stub")


# ── ADMN-04: Tag management ───────────────────────────────────────────────────

class TestTagManagement:
    """Tests for tag listing, activation, and creation."""

    @pytest.mark.asyncio
    async def test_tags_page_loads(self):
        """GET /admin/tags returns 200 with tag list."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_activate_tag(self):
        """POST /admin/tags/{id}/activate activates the tag."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_add_tag(self):
        """POST /admin/tags adds a new tag."""
        pytest.skip("Wave 1 integration stub")


# ── ADMN-05: Settings ─────────────────────────────────────────────────────────

class TestSettings:
    """Tests for settings listing and update."""

    @pytest.mark.asyncio
    async def test_settings_page_loads(self):
        """GET /admin/settings returns 200 with settings list."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_update_setting(self):
        """POST /admin/settings/{key} updates setting value."""
        pytest.skip("Wave 1 integration stub")


# ── ADMN-06: Provider health ──────────────────────────────────────────────────

class TestProviderHealth:
    """Tests for provider health table."""

    @pytest.mark.asyncio
    async def test_health_page_loads(self):
        """GET /admin/health returns 200 with provider health table."""
        pytest.skip("Wave 1 integration stub")

    @pytest.mark.asyncio
    async def test_partial_health_endpoint(self):
        """GET /admin/partials/health returns HTML fragment."""
        pytest.skip("Wave 1 integration stub")
