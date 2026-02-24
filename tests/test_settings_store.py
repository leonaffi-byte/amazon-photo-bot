"""
Tests for settings_store.py.

Covers:
  - _cast(): str / int / float / bool conversions + invalid input
  - get(): DB value → env fallback → default priority chain
  - get_raw(): raw string representation
  - set(): persists to DB and applies to config module live
  - delete(): removes from DB and reverts config to env/default
  - get_all(): returns all settings as raw strings
  - _apply_to_config(): updates config attributes immediately
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import database as db
import settings_store
import config


@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── _cast ─────────────────────────────────────────────────────────────────────

class TestCast:
    def test_str(self):
        assert settings_store._cast("hello", "str") == "hello"

    def test_int(self):
        assert settings_store._cast("42", "int") == 42

    def test_float(self):
        assert abs(settings_store._cast("3.14", "float") - 3.14) < 1e-9

    def test_bool_true_variants(self):
        for v in ("true", "True", "TRUE", "1", "yes"):
            assert settings_store._cast(v, "bool") is True

    def test_bool_false_variants(self):
        for v in ("false", "False", "FALSE", "0", "no"):
            assert settings_store._cast(v, "bool") is False

    def test_invalid_int_raises(self):
        with pytest.raises((ValueError, TypeError)):
            settings_store._cast("not_a_number", "int")

    def test_invalid_float_raises(self):
        with pytest.raises((ValueError, TypeError)):
            settings_store._cast("abc", "float")

    def test_whitespace_stripped(self):
        assert settings_store._cast("  5  ", "int") == 5


# ── get() priority chain ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGet:
    async def test_db_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("VISION_MODE", "cheapest")
        await db.set_setting("vision_mode", "compare", admin_id=1)
        result = await settings_store.get("vision_mode")
        assert result == "compare"

    async def test_env_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("VISION_MODE", "cheapest")
        result = await settings_store.get("vision_mode")
        assert result == "cheapest"

    async def test_default_used_when_no_db_no_env(self, monkeypatch):
        monkeypatch.delenv("VISION_MODE", raising=False)
        result = await settings_store.get("vision_mode")
        assert result == "best"   # default value

    async def test_int_setting_returns_int(self):
        await db.set_setting("results_per_page", "7", admin_id=1)
        result = await settings_store.get("results_per_page")
        assert result == 7
        assert isinstance(result, int)

    async def test_bool_setting_returns_bool(self, monkeypatch):
        monkeypatch.delenv("SHOW_COST_INFO", raising=False)
        await db.set_setting("show_cost_info", "false", admin_id=1)
        result = await settings_store.get("show_cost_info")
        assert result is False

    async def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            await settings_store.get("nonexistent_setting_key")


# ── get_raw() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetRaw:
    async def test_returns_raw_string_from_db(self):
        await db.set_setting("vision_mode", "compare", admin_id=1)
        raw = await settings_store.get_raw("vision_mode")
        assert raw == "compare"

    async def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("VISION_MODE", raising=False)
        raw = await settings_store.get_raw("vision_mode")
        assert raw == "best"


# ── set() ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSet:
    async def test_persists_to_db(self):
        await settings_store.set("vision_mode", "compare", admin_id=1)
        db_val = await db.get_setting("vision_mode")
        assert db_val == "compare"

    async def test_applies_to_config_live(self):
        original = config.VISION_MODE
        try:
            await settings_store.set("vision_mode", "cheapest", admin_id=1)
            assert config.VISION_MODE == "cheapest"
        finally:
            config.VISION_MODE = original

    async def test_invalid_int_raises(self):
        with pytest.raises((ValueError, TypeError)):
            await settings_store.set("results_per_page", "not_a_number", admin_id=1)


# ── delete() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDelete:
    async def test_delete_removes_from_db(self):
        await settings_store.set("vision_mode", "compare", admin_id=1)
        await settings_store.delete("vision_mode")
        db_val = await db.get_setting("vision_mode")
        assert db_val is None

    async def test_delete_reverts_config_to_default(self, monkeypatch):
        monkeypatch.delenv("VISION_MODE", raising=False)
        await settings_store.set("vision_mode", "compare", admin_id=1)
        await settings_store.delete("vision_mode")
        # Config should revert to the default "best"
        assert config.VISION_MODE == "best"


# ── get_all() ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetAll:
    async def test_returns_all_keys(self):
        all_vals = await settings_store.get_all()
        for key in settings_store.SETTINGS_META:
            assert key in all_vals

    async def test_returns_strings(self):
        all_vals = await settings_store.get_all()
        for val in all_vals.values():
            assert isinstance(val, str)
