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

_running = False


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
        lines += [
            "",
            f"💸 Total API cost: *{esc(f'${stats[\"total_cost_usd\"]:.4f}')}*",
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


async def _scheduler_loop() -> None:
    """Background coroutine — wakes every 30 s and fires reports at the right time."""
    import config

    last_fired_day: int = -1   # day-of-year we last fired reports

    logger.info("📅 Scheduler started (reports at %02d:00 %s)",
                config.REPORT_HOUR, config.REPORT_TIMEZONE)

    while _running:
        await asyncio.sleep(30)
        try:
            now = _now_local()
            if now.hour != config.REPORT_HOUR or now.minute > 1:
                continue
            if now.timetuple().tm_yday == last_fired_day:
                continue   # already fired today

            last_fired_day = now.timetuple().tm_yday
            logger.info("⏰ Firing scheduled reports for %s", now.strftime("%Y-%m-%d"))

            # Always send daily report
            await _send_report("DAILY", 24)

            # Sunday (weekday 6) → weekly report
            if now.weekday() == 6:
                await _send_report("WEEKLY", 7 * 24)

            # 1st of month → monthly report
            if now.day == 1:
                await _send_report("MONTHLY", 30 * 24)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)


def start(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
    """Start the scheduler as a background asyncio Task."""
    global _running
    _running = True
    return asyncio.create_task(_scheduler_loop())


def stop() -> None:
    global _running
    _running = False
