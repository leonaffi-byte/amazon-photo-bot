"""
notifications.py — Send async admin notifications from anywhere in the codebase.

Usage:
    import notifications
    notifications.init(app)          # called once in main.py
    await notifications.admin(text)  # called from manager, scheduler, etc.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

_app: "Application | None" = None


def init(app: "Application") -> None:
    global _app
    _app = app


async def admin(text: str, parse_mode: str = "MarkdownV2") -> None:
    """Send *text* to every admin user. Failures are logged, not raised."""
    if _app is None:
        logger.warning("notifications.admin: app not initialised yet")
        return

    import config
    import database as db

    # Merge bootstrap IDs + DB admins (deduplicated)
    try:
        db_admins = await db.get_all_admins()
        admin_ids: set[int] = set(config.ADMIN_IDS) | {a.user_id for a in db_admins}
    except Exception:
        admin_ids = set(config.ADMIN_IDS)

    for uid in admin_ids:
        try:
            await _app.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Failed to notify admin %d: %s", uid, exc)
