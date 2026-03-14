"""
Tests for BotCore.handle_command("webtoken") branch.

Covers:
  - Admin user receives a message containing the generated token
  - Non-admin user receives "Unauthorized" message (or no message at all)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bot_core():
    """Create a BotCore with a fully mocked adapter."""
    from bot_core import BotCore

    adapter = MagicMock()
    adapter.platform_name = "telegram"
    adapter.send_text = AsyncMock()

    core = BotCore(adapter)
    return core, adapter


# ══════════════════════════════════════════════════════════════════════════════
# WEBTOKEN COMMAND TESTS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestWebtokenCommand:

    async def test_webtoken_admin_delivers_token(self):
        """Admin calling /webtoken receives a message containing the token."""
        import config

        core, adapter = _make_bot_core()
        admin_id = 12345
        chat_id = 99999
        known_token = "test-token-abc123"

        with patch.object(config, "ADMIN_IDS", {admin_id}):
            with patch(
                "admin_dashboard.auth.generate_fallback_token",
                return_value=known_token,
            ):
                await core.handle_command(admin_id, chat_id, "webtoken", [])

        adapter.send_text.assert_called_once()
        call_args = adapter.send_text.call_args
        # First positional arg is chat_id, second is message text
        sent_text = call_args[0][1] if call_args[0] else call_args[1].get("text", "")
        assert known_token in sent_text

    async def test_webtoken_nonadmin_unauthorized(self):
        """Non-admin calling /webtoken receives Unauthorized or no message."""
        import config

        core, adapter = _make_bot_core()
        nonadmin_id = 99999
        chat_id = 11111

        with patch.object(config, "ADMIN_IDS", set()):
            with patch.object(db, "is_admin_in_db", AsyncMock(return_value=False)):
                await core.handle_command(nonadmin_id, chat_id, "webtoken", [])

        # Either no message sent, or message contains "Unauthorized"
        if adapter.send_text.called:
            call_args = adapter.send_text.call_args
            sent_text = call_args[0][1] if call_args[0] else call_args[1].get("text", "")
            assert "Unauthorized" in sent_text
