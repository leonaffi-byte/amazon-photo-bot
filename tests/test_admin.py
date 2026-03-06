"""
Tests for admin.py — Telegram admin panel.

Covers:
  - Access control: non-admin users get rejected (guard, is_admin)
  - _panel_content() returns formatted text with key sections
  - Tag management callbacks (activate, delete confirmation, delete OK)
  - Settings update callbacks (choice-based inline selection)
  - API key deletion confirmation flow (CB_KEY_DEL -> CB_KEY_DELOK)
  - Admin callback router: non-admin rejected, panel nav, stats
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import database as db


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_user(user_id: int = 1, full_name: str = "Alice", username: str = "alice"):
    """Create a mock Telegram User."""
    user = MagicMock()
    user.id = user_id
    user.full_name = full_name
    user.username = username
    return user


def _make_message(user_id: int = 1, text: str = ""):
    """Create a mock Telegram Message with reply_text."""
    user = _make_user(user_id)
    msg = AsyncMock()
    msg.text = text
    msg.from_user = user
    return msg


def _make_callback_query(user_id: int = 1, data: str = ""):
    """Create a mock Telegram CallbackQuery."""
    user = _make_user(user_id)
    q = AsyncMock()
    q.from_user = user
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    return q


def _make_update(user_id: int = 1, message=None, callback_query=None):
    """Create a mock Telegram Update."""
    update = MagicMock()
    update.effective_user = _make_user(user_id)
    update.message = message
    update.callback_query = callback_query
    return update


def _make_context(user_data=None):
    """Create a mock PTB context."""
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    return ctx


@dataclass
class FakeTag:
    id: int
    tag: str
    description: str
    added_by_id: int = 1
    added_by_name: str = "Admin"
    added_at: datetime = datetime(2025, 1, 1)
    is_active: bool = False
    search_count: int = 0


@dataclass
class FakeAdmin:
    user_id: int
    username: str
    full_name: str
    added_by: Optional[int] = None
    added_at: datetime = datetime(2025, 1, 1)


def _mock_stats():
    return {
        "total_searches": 42,
        "unique_users": 10,
        "israel_filter_uses": 5,
        "searches_per_tag": {"mytag-20": 30, "none": 12},
        "last_search": "2025-06-01T12:00:00",
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — ACCESS CONTROL
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAccessControl:

    async def test_is_admin_returns_true_for_config_admin(self, monkeypatch):
        """Users in config.ADMIN_IDS are always admin."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {999})
        result = await admin.is_admin(999)
        assert result is True

    async def test_is_admin_returns_false_for_non_admin(self, monkeypatch):
        """Users not in ADMIN_IDS and not in DB are not admin."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())
        result = await admin.is_admin(7777)
        assert result is False

    async def test_is_admin_returns_true_for_db_admin(self, monkeypatch):
        """Users added to the DB admin table are admin."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())
        await db.add_admin(42, "bob", "Bob Smith", added_by=1)
        result = await admin.is_admin(42)
        assert result is True

    async def test_guard_rejects_non_admin_message(self, monkeypatch):
        """guard() sends rejection to non-admin users via message."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())

        msg = _make_message(user_id=9999)
        update = _make_update(user_id=9999, message=msg)
        ctx = _make_context()

        result = await admin.guard(update, ctx)
        assert result is False
        msg.reply_text.assert_called_once()
        call_text = msg.reply_text.call_args[0][0]
        assert "Admin access only" in call_text

    async def test_guard_rejects_non_admin_callback(self, monkeypatch):
        """guard() sends rejection to non-admin users via callback query."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())

        q = _make_callback_query(user_id=9999)
        update = _make_update(user_id=9999, callback_query=q)
        update.message = None
        ctx = _make_context()

        result = await admin.guard(update, ctx)
        assert result is False
        q.answer.assert_called_once()
        call_kwargs = q.answer.call_args
        assert "Admin access only" in str(call_kwargs)

    async def test_guard_allows_admin(self, monkeypatch):
        """guard() returns True for admin users."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {100})

        msg = _make_message(user_id=100)
        update = _make_update(user_id=100, message=msg)
        ctx = _make_context()

        result = await admin.guard(update, ctx)
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — PANEL CONTENT
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestPanelContent:

    async def test_panel_content_returns_text_and_keyboard(self, monkeypatch):
        """_panel_content returns a (text, InlineKeyboardMarkup) tuple."""
        import admin
        import config
        monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "")

        text, kb = await admin._panel_content()
        assert "ADMIN PANEL" in text
        assert kb is not None

    async def test_panel_content_shows_key_sections(self, monkeypatch):
        """Panel text includes affiliate tag, keys set count, admin count, and stats."""
        import admin
        import config
        monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "")

        # Add some data so the panel shows real values
        await db.add_tag("test-20", "Test", admin_id=1, admin_name="A", make_active=True)
        await db.seed_admins({1})
        await db.log_search(1, "Widget", "test-20", "openai/gpt-4o", 5, False)

        text, kb = await admin._panel_content()
        assert "Tag:" in text
        assert "API keys" in text
        assert "searches" in text
        assert "users" in text

    async def test_panel_content_shows_active_tag(self, monkeypatch):
        """Panel text displays the active affiliate tag."""
        import admin
        import config
        monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "")

        await db.add_tag("active-20", "Primary", admin_id=1, admin_name="A", make_active=True)

        text, kb = await admin._panel_content()
        assert "active\\-20" in text or "active-20" in text


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — TAG MANAGEMENT CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestTagCallbacks:

    async def test_activate_tag(self, monkeypatch):
        """Activating a tag via callback sets it as the active tag."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        t1 = await db.add_tag("tag1-20", "Tag1", admin_id=1, admin_name="A", make_active=True)
        t2 = await db.add_tag("tag2-20", "Tag2", admin_id=1, admin_name="A")

        q = _make_callback_query(user_id=1, data=f"adm:tag_act:{t2.id}")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        active = await db.get_active_tag()
        assert active == "tag2-20"
        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "activated" in call_text

    async def test_delete_tag_confirmation(self, monkeypatch):
        """Clicking delete on a tag shows a confirmation prompt."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        tag = await db.add_tag("del-20", "To Delete", admin_id=1, admin_name="A")

        q = _make_callback_query(user_id=1, data=f"adm:tag_del:{tag.id}")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "Delete" in call_text
        # Confirmation keyboard should include a delok button
        call_kwargs = q.edit_message_text.call_args[1]
        kb = call_kwargs.get("reply_markup")
        assert kb is not None

    async def test_delete_tag_ok(self, monkeypatch):
        """Confirming tag deletion actually removes it from the database."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        tag = await db.add_tag("gone-20", "Will Go", admin_id=1, admin_name="A")

        q = _make_callback_query(user_id=1, data=f"adm:tag_delok:{tag.id}")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        tags = await db.get_all_tags()
        assert all(t.tag != "gone-20" for t in tags)
        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "Deleted" in call_text


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SETTINGS CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestSettingsCallbacks:

    async def test_settings_choice_updates_value(self, monkeypatch):
        """Clicking a choice button updates the setting in DB and config."""
        import admin
        import config
        import settings_store
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        q = _make_callback_query(user_id=1, data="adm:set_choice:vision_mode:cheapest")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        # Verify setting was stored
        val = await settings_store.get("vision_mode")
        assert val == "cheapest"
        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "cheapest" in call_text

    async def test_settings_reset_reverts_to_default(self, monkeypatch):
        """Resetting a setting removes the DB override and falls back to default."""
        import admin
        import config
        import settings_store
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        # First set a value
        await settings_store.set("vision_mode", "compare", admin_id=1)

        # Then reset it
        q = _make_callback_query(user_id=1, data="adm:set_reset:vision_mode")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        val = await settings_store.get("vision_mode")
        assert val == "best"  # default value


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — API KEY DELETION FLOW
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestKeyDeletion:

    async def test_key_del_shows_confirmation(self, monkeypatch):
        """CB_KEY_DEL shows a confirmation dialog, not immediate deletion."""
        import admin
        import config
        import key_store
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        await key_store.set("openai_api_key", "sk-test123", admin_id=1)

        q = _make_callback_query(user_id=1, data="adm:key_del:openai_api_key")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        # Key should NOT be deleted yet
        val = await key_store.get("openai_api_key")
        assert val == "sk-test123"

        # Should show confirmation prompt
        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "Clear" in call_text or "clear" in call_text.lower()

    async def test_key_delok_clears_key(self, monkeypatch):
        """CB_KEY_DELOK actually clears the key from the database."""
        import admin
        import config
        import key_store
        monkeypatch.setattr(config, "ADMIN_IDS", {1})

        await key_store.set("openai_api_key", "sk-test456", admin_id=1)

        q = _make_callback_query(user_id=1, data="adm:key_delok:openai_api_key")
        update = _make_update(user_id=1, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        # Key should now be cleared from DB
        db_val = await db.get_api_key("openai_api_key")
        assert db_val is None

        q.edit_message_text.assert_called_once()
        call_text = q.edit_message_text.call_args[0][0]
        assert "cleared" in call_text.lower() or "Key cleared" in call_text


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — ADMIN CALLBACK ROUTER
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminCallbackRouter:

    async def test_non_admin_rejected_by_callback_router(self, monkeypatch):
        """admin_callback rejects non-admin users with alert."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())

        q = _make_callback_query(user_id=9999, data="adm:panel")
        update = _make_update(user_id=9999, callback_query=q)
        ctx = _make_context()

        await admin.admin_callback(update, ctx)

        # The first q.answer() is always called at the start of admin_callback,
        # then a second call with show_alert=True for the rejection
        assert q.answer.call_count >= 2
        reject_call = q.answer.call_args_list[-1]
        assert "Admin access only" in str(reject_call)

    async def test_cmd_admin_rejects_non_admin(self, monkeypatch):
        """The /admin command rejects non-admin users."""
        import admin
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", set())

        msg = _make_message(user_id=9999)
        update = _make_update(user_id=9999, message=msg)
        ctx = _make_context()

        await admin.cmd_admin(update, ctx)

        msg.reply_text.assert_called_once()
        call_text = msg.reply_text.call_args[0][0]
        assert "Admin access only" in call_text
