"""
Tests for progressive model health degradation (F9 enhancement).

Covers:
  - Database: new columns, failure timestamp tracking, state transitions
  - Manager: _failures_in_window helper
  - Manager: _handle_progressive_health at levels 1/2/3
  - Manager: auto-recovery after cooldown
  - Manager: model-gone immediate disable
  - Manager: success resets health state and sends recovery notification
  - Database: get_disabled_models excludes recovery-ready models
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import database as db
import providers.manager as manager_mod
from providers.manager import _failures_in_window


@pytest.fixture(autouse=True)
def ensure_bot_token(monkeypatch):
    """Ensure TELEGRAM_BOT_TOKEN is set so config.py can be imported."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-000:XXXXXXXXXXX")


@pytest_asyncio.fixture(autouse=True)
async def init(tmp_data_dir):
    """Initialise the DB schema before every test."""
    await db.init_db()
    manager_mod._providers = {}
    yield
    manager_mod._providers = {}


# -- _failures_in_window helper ------------------------------------------------

class TestFailuresInWindow:
    def test_empty_list(self):
        assert _failures_in_window([], 300) == 0

    def test_all_within_window(self):
        now = time.time()
        timestamps = [now - 10, now - 60, now - 120]
        assert _failures_in_window(timestamps, 300) == 3

    def test_some_outside_window(self):
        now = time.time()
        timestamps = [now - 400, now - 350, now - 60, now - 10]
        assert _failures_in_window(timestamps, 300) == 2

    def test_all_outside_window(self):
        now = time.time()
        timestamps = [now - 600, now - 500, now - 400]
        assert _failures_in_window(timestamps, 300) == 0


# -- Database: model health with progressive fields ---------------------------

@pytest.mark.asyncio
class TestDbProgressiveHealth:
    async def test_increment_records_timestamps(self):
        consec = await db.increment_model_failures("test/model", "some error")
        assert consec == 1

        row = await db.get_model_health_row("test/model")
        assert row is not None
        assert row["consecutive_failures"] == 1
        assert row["total_failures"] == 1
        assert len(row["failure_timestamps"]) == 1
        assert row["failure_timestamps"][0] > time.time() - 10

    async def test_multiple_failures_append_timestamps(self):
        await db.increment_model_failures("test/model", "err1")
        await db.increment_model_failures("test/model", "err2")
        await db.increment_model_failures("test/model", "err3")

        row = await db.get_model_health_row("test/model")
        assert row["consecutive_failures"] == 3
        assert row["total_failures"] == 3
        assert len(row["failure_timestamps"]) == 3

    async def test_record_success_resets_state(self):
        await db.increment_model_failures("test/model", "err1")
        await db.update_model_health_state(
            "test/model", state="degraded", last_notification_level=2,
        )

        await db.record_model_success("test/model")

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "healthy"
        assert row["consecutive_failures"] == 0
        assert row["last_notification_level"] == 0
        assert row["is_disabled"] is False
        assert len(row["success_timestamps"]) == 1

    async def test_update_model_health_state(self):
        await db.increment_model_failures("test/model", "err")

        disabled_until = time.time() + 600
        await db.update_model_health_state(
            "test/model",
            state="disabled",
            is_disabled=True,
            disabled_until=disabled_until,
            last_notification_level=3,
        )

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "disabled"
        assert row["is_disabled"] is True
        assert row["disabled_until"] is not None
        assert abs(row["disabled_until"] - disabled_until) < 1
        assert row["last_notification_level"] == 3

    async def test_get_disabled_excludes_recovery_ready(self):
        # Insert a disabled model whose recovery time has passed
        await db.increment_model_failures("test/model", "err")
        past_time = time.time() - 10  # 10 seconds ago
        await db.update_model_health_state(
            "test/model",
            state="disabled",
            is_disabled=True,
            disabled_until=past_time,
            last_notification_level=3,
        )

        disabled = await db.get_disabled_models()
        assert "test/model" not in disabled

    async def test_get_disabled_includes_not_yet_recovered(self):
        await db.increment_model_failures("test/model", "err")
        future_time = time.time() + 600  # 10 minutes from now
        await db.update_model_health_state(
            "test/model",
            state="disabled",
            is_disabled=True,
            disabled_until=future_time,
            last_notification_level=3,
        )

        disabled = await db.get_disabled_models()
        assert "test/model" in disabled

    async def test_get_models_ready_for_recovery(self):
        await db.increment_model_failures("test/model", "err")
        past_time = time.time() - 10
        await db.update_model_health_state(
            "test/model",
            state="disabled",
            is_disabled=True,
            disabled_until=past_time,
            last_notification_level=3,
        )

        ready = await db.get_models_ready_for_recovery()
        assert "test/model" in ready

    async def test_re_enable_clears_all_health_state(self):
        await db.increment_model_failures("test/model", "err")
        await db.update_model_health_state(
            "test/model",
            state="disabled",
            is_disabled=True,
            disabled_until=time.time() + 600,
            last_notification_level=3,
        )

        await db.re_enable_model("test/model")

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "healthy"
        assert row["is_disabled"] is False
        assert row["disabled_until"] is None
        assert row["last_notification_level"] == 0
        assert row["consecutive_failures"] == 0

    async def test_get_all_model_health_returns_new_fields(self):
        await db.increment_model_failures("test/a", "err")
        await db.update_model_health_state("test/a", state="degraded")

        rows = await db.get_all_model_health()
        assert len(rows) == 1
        row = rows[0]
        assert "state" in row
        assert "disabled_until" in row
        assert "last_notification_level" in row
        assert "failure_timestamps" in row
        assert "success_timestamps" in row
        assert row["state"] == "degraded"


# -- Manager: _handle_progressive_health --------------------------------------

@pytest.mark.asyncio
class TestHandleProgressiveHealth:
    async def _setup_failures(self, provider_name: str, count: int):
        """Insert N failures within the current time window."""
        for i in range(count):
            await db.increment_model_failures(provider_name, f"err{i}")

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_single_failure_sets_degraded(self, mock_notify):
        await self._setup_failures("test/model", 1)
        await manager_mod._handle_progressive_health("test/model", "err0", False)

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "degraded"
        assert row["is_disabled"] is False
        # Level 1 notification sent
        mock_notify.assert_called_once()
        call_text = mock_notify.call_args[0][0]
        assert "1 failure" in call_text

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_two_failures_stays_degraded_sends_alert(self, mock_notify):
        # Set up 2 failures in the window
        await self._setup_failures("test/model", 2)

        # Handle health — with 2 failures in window, should send level 2 alert
        await manager_mod._handle_progressive_health("test/model", "err1", False)

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "degraded"
        assert row["is_disabled"] is False
        # Level 2 notification should have been sent
        mock_notify.assert_called()
        call_text = mock_notify.call_args[0][0]
        assert "2 failures" in call_text

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_three_failures_disables_with_recovery(self, mock_notify, monkeypatch):
        monkeypatch.setattr("config.HEALTH_DISABLE_THRESHOLD", 3)
        monkeypatch.setattr("config.HEALTH_RECOVERY_COOLDOWN", 600)

        await self._setup_failures("test/model", 3)
        await manager_mod._handle_progressive_health("test/model", "err2", False)

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "disabled"
        assert row["is_disabled"] is True
        assert row["disabled_until"] is not None
        assert row["disabled_until"] > time.time()
        assert row["last_notification_level"] == 3

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_model_gone_disables_immediately(self, mock_notify, monkeypatch):
        monkeypatch.setattr("config.HEALTH_RECOVERY_COOLDOWN", 600)
        await self._setup_failures("test/model", 1)

        await manager_mod._handle_progressive_health("test/model", "404 not found", True)

        row = await db.get_model_health_row("test/model")
        assert row["state"] == "disabled"
        assert row["is_disabled"] is True
        # Notification sent for model-gone
        mock_notify.assert_called()
        call_text = mock_notify.call_args[0][0]
        assert "model not found" in call_text

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_no_duplicate_notifications(self, mock_notify):
        """Same notification level should not be sent twice."""
        await self._setup_failures("test/model", 1)

        # First call sends level 1 notification
        await manager_mod._handle_progressive_health("test/model", "err0", False)
        assert mock_notify.call_count == 1
        mock_notify.reset_mock()

        # Second call with same failure count — notification level already 1
        # so no new notification
        await manager_mod._handle_progressive_health("test/model", "err0", False)
        assert mock_notify.call_count == 0


# -- Manager: analyse_image with progressive health ----------------------------

@pytest.mark.asyncio
class TestAnalyseImageProgressiveHealth:
    def _make_provider(self, name: str, model: str) -> MagicMock:
        from providers.base import ProviderResult, VisionProvider
        p = MagicMock(spec=VisionProvider)
        p.name = name
        p.model_id = model
        p.full_name = f"{name}/{model}"
        p.cost_per_image = 0.001
        p.cost_per_1k_input_tokens = 0.001
        p.cost_per_1k_output_tokens = 0.003
        return p

    def _make_result(self, provider_name: str) -> MagicMock:
        from providers.base import ProviderResult
        return ProviderResult(
            provider_name=provider_name,
            model_id="model",
            product_name="Test Product",
            brand="TestBrand",
            category="Electronics",
            key_features=["A"],
            amazon_search_query="test",
            alternative_query="test alt",
            confidence="high",
            notes="",
            latency_ms=100,
            input_tokens=500,
            output_tokens=100,
            cost_usd=0.001,
        )

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_success_after_degraded_sends_recovery(self, mock_notify):
        """When a degraded model succeeds, recovery notification should be sent."""
        p = self._make_provider("openai", "gpt-4o")
        result = self._make_result("openai/gpt-4o")
        p.analyse = AsyncMock(return_value=result)
        manager_mod._providers = {"openai/gpt-4o": p}

        # Simulate a previous degraded state with notification level >= 2
        await db.increment_model_failures("openai/gpt-4o", "err1")
        await db.increment_model_failures("openai/gpt-4o", "err2")
        await db.update_model_health_state(
            "openai/gpt-4o", state="degraded", last_notification_level=2,
        )

        winner, results = await manager_mod.analyse_image(b"fake", mode="best")
        assert winner.provider_name == "openai/gpt-4o"

        # Check recovery notification was sent
        found_recovery = any(
            "recovered" in str(call) for call in mock_notify.call_args_list
        )
        assert found_recovery

        # State should be healthy now
        row = await db.get_model_health_row("openai/gpt-4o")
        assert row["state"] == "healthy"

    @patch("notifications.admin", new_callable=AsyncMock)
    async def test_failure_triggers_progressive_degradation(self, mock_notify, monkeypatch):
        monkeypatch.setattr("config.HEALTH_DISABLE_THRESHOLD", 3)

        p = self._make_provider("openai", "gpt-4o")
        p.analyse = AsyncMock(side_effect=Exception("API error"))
        manager_mod._providers = {"openai/gpt-4o": p}

        # First failure
        with pytest.raises(RuntimeError, match="All vision providers failed"):
            await manager_mod.analyse_image(b"fake", mode="best")

        row = await db.get_model_health_row("openai/gpt-4o")
        assert row["state"] == "degraded"
        assert row["is_disabled"] is False
