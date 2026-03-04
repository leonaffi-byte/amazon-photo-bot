"""
scheduler.py — Scheduled reports sent to all admin users.

Schedule (all in REPORT_TIMEZONE, default Asia/Jerusalem):
  Every day at REPORT_HOUR (default 08:00):
    → Daily report: last 24 hours
  Every Sunday at REPORT_HOUR:
    → Also weekly report: last 7 days
  Every 1st of month at REPORT_HOUR:
    → Also monthly report: last 30 days

Reports include:
  • Unique users
  • Photo analyses + text searches
  • Amazon link clicks
  • API costs (per provider breakdown)
  • Model health summary
  • Note about Amazon Associates earnings (manual check needed)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_stop_event: asyncio.Event | None = None


def _now_local() -> datetime:
    """Return current datetime in the configured local timezone."""
    try:
        from zoneinfo import ZoneInfo
        import config
        return datetime.now(ZoneInfo(config.REPORT_TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc)


def _format_report(stats: dict, period_label: str, since: datetime) -> str:
    """Format a usage stats dict into a MarkdownV2 report message."""
    from style import esc, DIV

    since_str = esc(since.strftime("%a %d %b %Y %H:%M"))
    now_str   = esc(_now_local().strftime("%a %d %b %Y %H:%M"))

    lines = [
        f"📊 *{esc(period_label)} REPORT*",
        f"{DIV}",
        f"🕐 {since_str} → {now_str}",
        "",
        f"👥 Unique users:    *{stats['unique_users']}*",
        f"📸 Photo analyses:  *{stats['photo_searches']}*",
        f"🔍 Text searches:   *{stats['text_searches']}*",
        f"🔗 Link clicks:     *{stats['link_clicks']}*",
        f"📨 Total searches:  *{stats['total_searches']}*",
    ]

    if stats["total_cost_usd"] > 0:
        total_cost_str = esc(f"${stats['total_cost_usd']:.4f}")
        lines += [
            "",
            f"💸 Total API cost: *{total_cost_str}*",
        ]
        if stats["cost_by_provider"]:
            lines.append("🤖 By model:")
            for provider, cost, calls in stats["cost_by_provider"]:
                short = esc(provider.split("/")[-1][:25])
                c     = esc(f"${cost:.4f}")
                lines.append(f"  `{short}` — {c} \\({calls} calls\\)")
    else:
        lines.append("")
        lines.append("💸 API costs: none tracked yet")

    lines += [
        "",
        "🛒 *Amazon purchases:*",
        "_Not available via API\\._",
        "_Check manually: associates\\.amazon\\.com_",
    ]

    return "\n".join(lines)


async def _send_report(period_label: str, hours: int) -> None:
    """Build and send a report covering the last *hours* hours."""
    import database as db
    import notifications

    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        stats = await db.get_stats_since(since)
        msg   = _format_report(stats, period_label, since)
        await notifications.admin(msg)
        logger.info("Sent %s report to admins.", period_label)
    except Exception as exc:
        logger.error("Failed to send %s report: %s", period_label, exc)


def _seconds_until_next_fire(report_hour: int) -> float:
    """Compute seconds from now until the next occurrence of report_hour in local time."""
    now = _now_local()
    target = now.replace(hour=report_hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _scheduler_loop() -> None:
    """Background coroutine — sleeps until next report time, fires, repeats."""
    import config

    # Use ISO date string to track last fired day (handles year boundaries correctly)
    last_fired_date: str = ""

    logger.info("📅 Scheduler started (reports at %02d:00 %s)",
                config.REPORT_HOUR, config.REPORT_TIMEZONE)

    while True:
        # Sleep until the next fire time (instead of polling every 30s)
        sleep_secs = _seconds_until_next_fire(config.REPORT_HOUR)
        # Clamp to reasonable bounds (avoid sleeping exactly 0 or negative)
        sleep_secs = max(10, min(sleep_secs + 5, 86400))

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=sleep_secs)
            # If we get here, stop_event was set — exit
            break
        except asyncio.TimeoutError:
            pass  # Timeout means it's time to check

        try:
            now = _now_local()
            if now.hour != config.REPORT_HOUR or now.minute > 2:
                continue

            today = now.strftime("%Y-%m-%d")
            if today == last_fired_date:
                continue

            last_fired_date = today
            logger.info("⏰ Firing scheduled reports for %s", today)

            await _send_report("DAILY", 24)

            if now.weekday() == 6:
                await _send_report("WEEKLY", 7 * 24)

            if now.day == 1:
                await _send_report("MONTHLY", 30 * 24)

        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)


def start(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
    """Start the scheduler as a background asyncio Task."""
    global _stop_event
    _stop_event = asyncio.Event()
    return asyncio.create_task(_scheduler_loop())


def stop() -> None:
    if _stop_event:
        _stop_event.set()
