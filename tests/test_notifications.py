"""
Tests for notifications.py.

Covers:
  - admin() with no app initialized (logs warning, does not crash)
  - admin() with mocked app.bot.send_message — sends to all admin IDs
  - admin() with first send failing — verifies retry behaviour
  - init() sets the global _app
  - admin() deduplicates bootstrap + DB admin IDs
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import notifications


@pytest.fixture(autouse=True)
def reset_app():
    """Reset the module-level _app before each test."""
    original = notifications._app
    notifications._app = None
    yield
    notifications._app = original


# ── init() ────────────────────────────────────────────────────────────────────

class TestInit:
    def test_sets_global_app(self):
        fake_app = MagicMock()
        notifications.init(fake_app)
        assert notifications._app is fake_app

    def test_init_overwrites_previous_app(self):
        app1 = MagicMock()
        app2 = MagicMock()
        notifications.init(app1)
        notifications.init(app2)
        assert notifications._app is app2


# ── admin() — no app ─────────────────────────────────────────────────────────

class TestAdminNoApp:
    async def test_logs_warning_when_no_app(self, caplog):
        """admin() should log a warning and return without crashing."""
        notifications._app = None
        with caplog.at_level("WARNING", logger="notifications"):
            await notifications.admin("test message")
        assert "not initialised" in caplog.text

    async def test_returns_none_when_no_app(self):
        notifications._app = None
        result = await notifications.admin("hello")
        assert result is None


# ── admin() — sends to all admins ─────────────────────────────────────────────

class TestAdminSend:
    async def test_sends_to_all_admin_ids(self, monkeypatch):
        """admin() sends a message to every admin ID from config + DB."""
        import config

        monkeypatch.setattr(config, "ADMIN_IDS", {111, 222})

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        notifications.init(mock_app)

        # database is imported locally as `db` inside admin(), so patch at module level
        with patch("database.get_all_admins", new_callable=AsyncMock, return_value=[]):
            await notifications.admin("Hello admins")

        called_ids = {call.kwargs["chat_id"] for call in mock_bot.send_message.call_args_list}
        assert called_ids == {111, 222}

    async def test_message_text_forwarded(self, monkeypatch):
        """The exact text passed to admin() is forwarded to send_message."""
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {100})

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        notifications.init(mock_app)

        with patch("database.get_all_admins", new_callable=AsyncMock, return_value=[]):
            await notifications.admin("Report text here")

        mock_bot.send_message.assert_called_once()
        assert mock_bot.send_message.call_args.kwargs["text"] == "Report text here"

    async def test_deduplicates_config_and_db_admins(self, monkeypatch):
        """An admin in both config.ADMIN_IDS and DB should receive only one message."""
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {100})

        # Create a mock DB admin with user_id = 100 (same as config)
        db_admin = MagicMock()
        db_admin.user_id = 100

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        notifications.init(mock_app)

        with patch("database.get_all_admins", new_callable=AsyncMock, return_value=[db_admin]):
            await notifications.admin("Dedup test")

        # Should only be called once, not twice
        assert mock_bot.send_message.call_count == 1


# ── admin() — retry on failure ────────────────────────────────────────────────

class TestAdminRetry:
    async def test_retries_on_first_failure(self, monkeypatch):
        """If send_message fails on attempt 0, it sleeps and retries (attempt 1)."""
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {999})

        mock_bot = MagicMock()
        # First call raises, second succeeds
        mock_bot.send_message = AsyncMock(
            side_effect=[Exception("network error"), None]
        )
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        notifications.init(mock_app)

        with patch("database.get_all_admins", new_callable=AsyncMock, return_value=[]):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await notifications.admin("Retry test")

        # First attempt fails, sleep(2) is called, then second attempt succeeds
        mock_sleep.assert_awaited_once_with(2)
        assert mock_bot.send_message.call_count == 2

    async def test_logs_warning_after_both_attempts_fail(self, monkeypatch, caplog):
        """If both attempts fail, a warning is logged for the admin ID."""
        import config
        monkeypatch.setattr(config, "ADMIN_IDS", {777})

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(
            side_effect=Exception("permanent failure")
        )
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        notifications.init(mock_app)

        with patch("database.get_all_admins", new_callable=AsyncMock, return_value=[]):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with caplog.at_level("WARNING", logger="notifications"):
                    await notifications.admin("Fail test")

        assert "777" in caplog.text
        assert "Failed to notify admin" in caplog.text
