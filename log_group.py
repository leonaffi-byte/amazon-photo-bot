"""
log_group.py — Send structured action logs to a Telegram group.

Usage:
    import log_group
    log_group.init(app)                    # called once in main.py
    await log_group.log("emoji", "text")   # called from anywhere
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


async def log(emoji: str, text: str) -> None:
    """Send a log message to the configured log group. Never raises."""
    import config
    chat_id = config.LOG_GROUP_CHAT_ID
    if not chat_id or _app is None:
        return
    try:
        msg = f"{emoji} {text}"
        # Truncate to Telegram's 4096 char limit
        if len(msg) > 4000:
            msg = msg[:4000] + "..."
        await _app.bot.send_message(
            chat_id=int(chat_id),
            text=msg,
            parse_mode=None,  # plain text to avoid escaping issues
        )
    except Exception as exc:
        logger.debug("log_group send failed: %s", exc)
