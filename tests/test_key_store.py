"""
Tests for key_store.py.

Covers:
  - get(): DB first → env var fallback → None
  - set(): saves to DB, overrides env
  - delete(): removes from DB, falls back to env
  - get_all_keys(): returns all known key names
  - mask(): various masking scenarios
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import database as db
import key_store


@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── get() ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGet:
    async def test_db_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        await db.set_api_key("openai_api_key", "sk-db", admin_id=1)
        result = await key_store.get("openai_api_key")
        assert result == "sk-db"

    async def test_env_var_used_as_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        result = await key_store.get("openai_api_key")
        assert result == "sk-from-env"

    async def test_returns_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = await key_store.get("openai_api_key")
        assert result is None

    async def test_env_var_name_is_uppercased(self, monkeypatch):
        monkeypatch.setenv("RAPIDAPI_KEY", "rapid-key-value")
        result = await key_store.get("rapidapi_key")
        assert result == "rapid-key-value"


# ── set() ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSetKey:
    async def test_saves_to_db(self):
        await key_store.set("openai_api_key", "sk-test", admin_id=1)
        db_val = await db.get_api_key("openai_api_key")
        assert db_val == "sk-test"

    async def test_overrides_env_value(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        await key_store.set("openai_api_key", "sk-db", admin_id=1)
        result = await key_store.get("openai_api_key")
        assert result == "sk-db"


# ── delete() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDeleteKey:
    async def test_removes_from_db(self):
        await key_store.set("openai_api_key", "sk-test", admin_id=1)
        await key_store.delete("openai_api_key")
        db_val = await db.get_api_key("openai_api_key")
        assert db_val is None

    async def test_falls_back_to_env_after_delete(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback")
        await key_store.set("openai_api_key", "sk-db", admin_id=1)
        await key_store.delete("openai_api_key")
        result = await key_store.get("openai_api_key")
        assert result == "sk-env-fallback"


# ── get_all_keys() ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetAllKeys:
    async def test_returns_all_known_keys(self):
        keys = await key_store.get_all_keys()
        expected = [
            "openai_api_key",
            "anthropic_api_key",
            "google_api_key",
            "rapidapi_key",
            "amazon_access_key",
            "amazon_secret_key",
            "amazon_associate_tag",
            "bitly_token",
        ]
        for k in expected:
            assert k in keys

    async def test_set_key_appears_in_get_all(self):
        await key_store.set("openai_api_key", "sk-all-test", admin_id=1)
        keys = await key_store.get_all_keys()
        assert keys["openai_api_key"] == "sk-all-test"


# ── mask() ────────────────────────────────────────────────────────────────────

class TestMask:
    def test_none_shows_not_set(self):
        assert "not set" in key_store.mask(None)

    def test_empty_shows_not_set(self):
        assert "not set" in key_store.mask("")

    def test_short_key_shows_stars(self):
        result = key_store.mask("sk-ab")
        assert "✅" in result
        assert "****" in result

    def test_long_key_shows_partial(self):
        result = key_store.mask("sk-1234567890abcdef")
        assert "✅" in result
        # First 4 chars visible
        assert "sk-1" in result
        # Last 4 chars visible
        assert "cdef" in result
        # Middle is masked
        assert "***" in result

    def test_exactly_8_chars_shows_stars(self):
        result = key_store.mask("abcdefgh")
        assert "****" in result
