"""
Tests for admin_service.py — Admin business logic service layer.

Covers:
  - All service functions return plain dataclasses or dicts (no Telegram types)
  - TagInfo returned by list_tags and add_tag
  - BotStats returned by get_stats
  - KeyGroupStatus returned by list_key_groups and get_key_group
  - SettingInfo returned by list_settings
  - ProviderHealth returned by get_provider_health
  - list_admins and is_admin return plain Python types
  - No Telegram imports anywhere in admin_service module
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import is_dataclass
from unittest.mock import AsyncMock, patch

import pytest

import database as db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── Helper: assert no telegram types in return value ─────────────────────────

def _assert_no_telegram(result) -> None:
    """Assert that the result contains no Telegram types."""
    result_type_str = str(type(result))
    assert "telegram" not in result_type_str, (
        f"Result type contains 'telegram': {result_type_str}"
    )
    if isinstance(result, list):
        for item in result:
            _assert_no_telegram(item)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — MODULE INTEGRITY: NO TELEGRAM IMPORTS
# ══════════════════════════════════════════════════════════════════════════════

class TestModuleIntegrity:

    def test_admin_service_has_no_telegram_import(self):
        """admin_service.py must not import anything from the telegram package."""
        import admin_service
        source_file = admin_service.__file__
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        # Check for direct telegram imports
        assert "from telegram" not in source, (
            "admin_service.py must not contain 'from telegram' imports"
        )
        assert "import telegram" not in source, (
            "admin_service.py must not contain 'import telegram'"
        )

    def test_admin_service_imports_succeed(self):
        """admin_service module can be imported without errors."""
        import admin_service  # noqa: F401
        assert admin_service is not None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — TAGS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestTagService:

    async def test_list_tags_returns_list_of_taginfo(self):
        """list_tags() returns a list of TagInfo dataclasses."""
        import admin_service
        await db.add_tag("mytag-20", "My Tag", admin_id=1, admin_name="Admin")

        result = await admin_service.list_tags()

        assert isinstance(result, list)
        assert len(result) >= 1
        tag = result[0]
        assert is_dataclass(tag)
        assert isinstance(tag, admin_service.TagInfo)
        _assert_no_telegram(tag)

    async def test_list_tags_empty_returns_empty_list(self):
        """list_tags() returns empty list when no tags exist."""
        import admin_service
        result = await admin_service.list_tags()
        assert result == []

    async def test_add_tag_returns_taginfo(self):
        """add_tag() returns a TagInfo dataclass."""
        import admin_service

        result = await admin_service.add_tag(
            tag="newtag-20",
            description="New Tag",
            admin_id=1,
        )

        assert is_dataclass(result)
        assert isinstance(result, admin_service.TagInfo)
        assert result.name == "newtag-20"
        assert result.description == "New Tag"
        _assert_no_telegram(result)

    async def test_add_tag_with_make_active(self):
        """add_tag(make_active=True) creates an active tag."""
        import admin_service

        result = await admin_service.add_tag(
            tag="active-20",
            description="Active Tag",
            admin_id=1,
            make_active=True,
        )

        assert isinstance(result, admin_service.TagInfo)
        assert result.is_active is True

    async def test_taginfo_has_expected_fields(self):
        """TagInfo has id, name, description, is_active, search_count fields."""
        import admin_service

        tag = await admin_service.add_tag("fields-20", "Fields Test", admin_id=1)

        assert hasattr(tag, "id")
        assert hasattr(tag, "name")
        assert hasattr(tag, "description")
        assert hasattr(tag, "is_active")
        assert hasattr(tag, "search_count")

    async def test_remove_tag_returns_bool(self):
        """remove_tag() returns a bool."""
        import admin_service
        t = await db.add_tag("removeme-20", "Remove", admin_id=1, admin_name="A")

        result = await admin_service.remove_tag(t.id)
        assert isinstance(result, bool)

    async def test_set_active_tag_returns_bool(self):
        """set_active_tag() returns a bool."""
        import admin_service
        t = await db.add_tag("activate-20", "Activate", admin_id=1, admin_name="A")

        result = await admin_service.set_active_tag(t.id)
        assert isinstance(result, bool)

    async def test_deactivate_all_tags_returns_none(self):
        """deactivate_all_tags() returns None."""
        import admin_service
        await db.add_tag("deact-20", "Deact", admin_id=1, admin_name="A", make_active=True)

        result = await admin_service.deactivate_all_tags()
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — API KEYS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestKeyService:

    async def test_list_key_groups_returns_list_of_keygroupstatus(self):
        """list_key_groups() returns a list of KeyGroupStatus dataclasses."""
        import admin_service

        result = await admin_service.list_key_groups()

        assert isinstance(result, list)
        assert len(result) > 0
        for group in result:
            assert is_dataclass(group)
            assert isinstance(group, admin_service.KeyGroupStatus)
            _assert_no_telegram(group)

    async def test_keygroupstatus_has_expected_fields(self):
        """KeyGroupStatus has group_name, label, keys, all_required_set fields."""
        import admin_service

        groups = await admin_service.list_key_groups()
        group = groups[0]

        assert hasattr(group, "group_name")
        assert hasattr(group, "label")
        assert hasattr(group, "keys")
        assert hasattr(group, "all_required_set")

    async def test_list_key_groups_keys_are_bool_values(self):
        """KeyGroupStatus.keys values are booleans."""
        import admin_service

        groups = await admin_service.list_key_groups()
        for group in groups:
            for key_name, is_set in group.keys.items():
                assert isinstance(is_set, bool), (
                    f"Expected bool for {key_name}, got {type(is_set)}"
                )

    async def test_get_key_group_returns_keygroupstatus(self):
        """get_key_group() returns a KeyGroupStatus for a valid group name."""
        import admin_service

        result = await admin_service.get_key_group("openai")

        assert result is not None
        assert isinstance(result, admin_service.KeyGroupStatus)
        assert result.group_name == "openai"
        _assert_no_telegram(result)

    async def test_get_key_group_returns_none_for_unknown(self):
        """get_key_group() returns None for unknown group name."""
        import admin_service

        result = await admin_service.get_key_group("nonexistent_group")
        assert result is None

    async def test_set_api_key_stores_value(self):
        """set_api_key() stores a key in the database."""
        import admin_service
        import key_store

        await admin_service.set_api_key("openai_api_key", "sk-test123", admin_id=1)
        val = await key_store.get("openai_api_key")

        assert val == "sk-test123"

    async def test_delete_api_key_removes_value(self):
        """delete_api_key() removes a key from the database."""
        import admin_service
        import key_store

        await key_store.set("openai_api_key", "sk-test456", admin_id=1)
        await admin_service.delete_api_key("openai_api_key")

        db_val = await db.get_api_key("openai_api_key")
        assert db_val is None

    async def test_key_set_updates_all_required_set(self):
        """all_required_set is True when all required keys for a group are set."""
        import admin_service

        await db.set_api_key("openai_api_key", "sk-test", admin_id=1)
        groups = await admin_service.list_key_groups()
        openai_group = next(g for g in groups if g.group_name == "openai")

        assert openai_group.all_required_set is True


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestSettingsService:

    async def test_list_settings_returns_list_of_settinginfo(self):
        """list_settings() returns a list of SettingInfo dataclasses."""
        import admin_service

        result = await admin_service.list_settings()

        assert isinstance(result, list)
        assert len(result) > 0
        for setting in result:
            assert is_dataclass(setting)
            assert isinstance(setting, admin_service.SettingInfo)
            _assert_no_telegram(setting)

    async def test_settinginfo_has_expected_fields(self):
        """SettingInfo has key, value, default, type, description fields."""
        import admin_service

        settings = await admin_service.list_settings()
        s = settings[0]

        assert hasattr(s, "key")
        assert hasattr(s, "value")
        assert hasattr(s, "default")
        assert hasattr(s, "type")
        assert hasattr(s, "description")

    async def test_set_setting_persists_value(self):
        """set_setting() persists a value to the DB."""
        import admin_service
        import settings_store

        await admin_service.set_setting("vision_mode", "cheapest", admin_id=1)
        val = await settings_store.get("vision_mode")

        assert val == "cheapest"

    async def test_reset_setting_reverts_to_default(self):
        """reset_setting() removes DB override and falls back to default."""
        import admin_service
        import settings_store

        await settings_store.set("vision_mode", "compare", admin_id=1)
        await admin_service.reset_setting("vision_mode")
        val = await settings_store.get("vision_mode")

        assert val == "best"  # default value


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — STATS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestStatsService:

    async def test_get_stats_returns_botstats(self):
        """get_stats() returns a BotStats dataclass."""
        import admin_service

        result = await admin_service.get_stats()

        assert is_dataclass(result)
        assert isinstance(result, admin_service.BotStats)
        _assert_no_telegram(result)

    async def test_botstats_has_expected_fields(self):
        """BotStats has total_users, total_searches, total_clicks fields."""
        import admin_service

        stats = await admin_service.get_stats()

        assert hasattr(stats, "total_users")
        assert hasattr(stats, "total_searches")
        assert hasattr(stats, "total_clicks")
        assert hasattr(stats, "today_searches")
        assert hasattr(stats, "today_users")

    async def test_botstats_fields_are_integers(self):
        """BotStats numeric fields are integers."""
        import admin_service

        stats = await admin_service.get_stats()

        assert isinstance(stats.total_users, int)
        assert isinstance(stats.total_searches, int)
        assert isinstance(stats.total_clicks, int)

    async def test_get_stats_reflects_logged_searches(self):
        """get_stats() reflects actual data after logging a search."""
        import admin_service
        await db.seed_admins({1})
        await db.log_search(1, "Widget", "test-20", "openai/gpt-4o", 5, False)

        stats = await admin_service.get_stats()
        assert stats.total_searches >= 1
        assert stats.total_users >= 1

    async def test_get_shortener_stats_returns_dict(self):
        """get_shortener_stats() returns a dict."""
        import admin_service

        result = await admin_service.get_shortener_stats()

        assert isinstance(result, dict)
        _assert_no_telegram(result)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PROVIDER HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestHealthService:

    async def test_get_provider_health_returns_list(self):
        """get_provider_health() returns a list."""
        import admin_service

        result = await admin_service.get_provider_health()

        assert isinstance(result, list)
        _assert_no_telegram(result)

    async def test_get_provider_health_empty_when_no_data(self):
        """get_provider_health() returns empty list when no health records exist."""
        import admin_service

        result = await admin_service.get_provider_health()
        assert result == []

    async def test_providerhealth_has_expected_fields(self):
        """ProviderHealth has name, status, failure_count, last_failure fields."""
        import admin_service

        # Insert a health record
        await db.increment_model_failures("openai/gpt-4o", "test error")
        result = await admin_service.get_provider_health()

        assert len(result) >= 1
        h = result[0]
        assert is_dataclass(h)
        assert isinstance(h, admin_service.ProviderHealth)
        assert hasattr(h, "name")
        assert hasattr(h, "status")
        assert hasattr(h, "failure_count")
        assert hasattr(h, "last_failure")

    async def test_providerhealth_status_values(self):
        """ProviderHealth.status is one of 'healthy', 'degraded', 'disabled'."""
        import admin_service

        await db.increment_model_failures("openai/gpt-4o", "test error")
        result = await admin_service.get_provider_health()

        for h in result:
            assert h.status in ("healthy", "degraded", "disabled"), (
                f"Unexpected status: {h.status}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — ADMIN MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminManagement:

    async def test_list_admins_returns_list_of_ints(self):
        """list_admins() returns a list of integer user IDs."""
        import admin_service

        await db.add_admin(42, "bob", "Bob Smith", added_by=1)
        result = await admin_service.list_admins()

        assert isinstance(result, list)
        for uid in result:
            assert isinstance(uid, int)

    async def test_list_admins_includes_added_admin(self):
        """list_admins() includes admins added to the DB."""
        import admin_service

        await db.add_admin(99, "carol", "Carol Jones", added_by=1)
        result = await admin_service.list_admins()

        assert 99 in result

    async def test_is_admin_returns_bool(self):
        """is_admin() returns a bool."""
        import admin_service

        result = await admin_service.is_admin(12345)
        assert isinstance(result, bool)

    async def test_is_admin_false_for_unknown_user(self):
        """is_admin() returns False for a user not in DB."""
        import admin_service

        result = await admin_service.is_admin(99999)
        assert result is False

    async def test_is_admin_true_for_db_admin(self):
        """is_admin() returns True for a user in the DB admin table."""
        import admin_service

        await db.add_admin(77, "dave", "Dave Evans", added_by=1)
        result = await admin_service.is_admin(77)

        assert result is True
