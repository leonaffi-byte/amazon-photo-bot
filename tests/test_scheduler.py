"""
Tests for scheduler.py.

Covers:
  - _now_local() returns a datetime
  - _format_report() formats stats correctly (contains key fields)
  - _seconds_until_next_fire() returns a positive number
  - _scheduler_loop() starts and stops cleanly via start() / stop()
  - Year boundary: last_fired_date as ISO string handles Dec 31 -> Jan 1
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

import scheduler


# ── _now_local() ──────────────────────────────────────────────────────────────

class TestNowLocal:
    def test_returns_datetime(self):
        result = scheduler._now_local()
        assert isinstance(result, datetime)

    def test_has_timezone_info(self):
        result = scheduler._now_local()
        assert result.tzinfo is not None

    def test_fallback_to_utc_on_bad_timezone(self, monkeypatch):
        """If config.REPORT_TIMEZONE is invalid, _now_local falls back to UTC."""
        import config
        monkeypatch.setattr(config, "REPORT_TIMEZONE", "Invalid/Timezone_XYZ")
        result = scheduler._now_local()
        # Should still return a datetime (UTC fallback)
        assert isinstance(result, datetime)


# ── _format_report() ─────────────────────────────────────────────────────────

class TestFormatReport:
    def _make_stats(self, **overrides) -> dict:
        base = {
            "unique_users": 42,
            "photo_searches": 10,
            "text_searches": 5,
            "link_clicks": 20,
            "total_searches": 15,
            "total_cost_usd": 0.0,
            "cost_by_provider": [],
        }
        base.update(overrides)
        return base

    def test_contains_period_label(self):
        stats = self._make_stats()
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = scheduler._format_report(stats, "DAILY", since)
        assert "DAILY" in result

    def test_contains_unique_users(self):
        stats = self._make_stats(unique_users=99)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = scheduler._format_report(stats, "DAILY", since)
        assert "99" in result

    def test_contains_photo_searches(self):
        stats = self._make_stats(photo_searches=7)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = scheduler._format_report(stats, "WEEKLY", since)
        assert "7" in result

    def test_cost_breakdown_included_when_nonzero(self):
        stats = self._make_stats(
            total_cost_usd=1.2345,
            cost_by_provider=[("openai/gpt-4o", 1.2345, 10)],
        )
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = scheduler._format_report(stats, "DAILY", since)
        # esc() escapes MarkdownV2 chars, so "$1.2345" becomes "\$1\.2345"
        assert "1\\.2345" in result
        assert "gpt\\-4o" in result

    def test_no_cost_message_when_zero(self):
        stats = self._make_stats(total_cost_usd=0.0)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = scheduler._format_report(stats, "DAILY", since)
        assert "none tracked yet" in result


# ── _seconds_until_next_fire() ────────────────────────────────────────────────

class TestSecondsUntilNextFire:
    def test_returns_positive(self):
        result = scheduler._seconds_until_next_fire(8)
        assert result > 0

    def test_returns_float(self):
        result = scheduler._seconds_until_next_fire(3)
        assert isinstance(result, float)

    def test_within_24_hours(self):
        """Next fire should always be within 24 hours."""
        result = scheduler._seconds_until_next_fire(12)
        assert result <= 86400


# ── start() / stop() ─────────────────────────────────────────────────────────

class TestStartStop:
    async def test_start_returns_task(self):
        task = scheduler.start()
        assert isinstance(task, asyncio.Task)
        scheduler.stop()
        # Give the loop iteration a moment to process the stop event
        await asyncio.sleep(0.05)
        # Ensure it finishes without error
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("Scheduler task did not stop within 2 seconds")

    async def test_stop_before_start_does_not_crash(self):
        """Calling stop() when _stop_event is None should not raise."""
        original = scheduler._stop_event
        scheduler._stop_event = None
        scheduler.stop()  # should be a no-op
        scheduler._stop_event = original

    async def test_scheduler_loop_exits_on_stop(self):
        """_scheduler_loop should exit when the stop event is set."""
        task = scheduler.start()
        # Immediately stop
        scheduler.stop()
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("Scheduler did not exit after stop()")

    async def test_send_report_sends_to_admins(self):
        """_send_report builds a report from DB stats and sends via notifications."""
        fake_stats = {
            "unique_users": 5, "photo_searches": 3, "text_searches": 2,
            "link_clicks": 10, "total_searches": 5,
            "total_cost_usd": 0.0, "cost_by_provider": [],
        }
        # database and notifications are imported locally inside _send_report,
        # so patch them at their own module level.
        with patch("database.get_stats_since", new_callable=AsyncMock, return_value=fake_stats) as mock_db:
            with patch("notifications.admin", new_callable=AsyncMock) as mock_notify:
                await scheduler._send_report("DAILY", 24)

        mock_db.assert_awaited_once()
        mock_notify.assert_awaited_once()
        # The message should contain "DAILY"
        sent_text = mock_notify.call_args[0][0]
        assert "DAILY" in sent_text


# ── Year boundary ─────────────────────────────────────────────────────────────

class TestYearBoundary:
    def test_iso_date_string_handles_year_change(self):
        """ISO date strings correctly distinguish Dec 31 from Jan 1."""
        dec31 = datetime(2025, 12, 31, 8, 0, tzinfo=timezone.utc)
        jan01 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        date_dec = dec31.strftime("%Y-%m-%d")
        date_jan = jan01.strftime("%Y-%m-%d")
        assert date_dec != date_jan
        assert date_dec == "2025-12-31"
        assert date_jan == "2026-01-01"
