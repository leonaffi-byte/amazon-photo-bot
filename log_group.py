"""
log_group.py — Send structured action logs to a Telegram group.

Usage:
    import log_group
    log_group.init(app)                    # called once in main.py
    await log_group.log("emoji", "text")   # called from anywhere
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

_app: "Application | None" = None

_pending_admin_ids: set[int] = set()


def start_listening(admin_id: int) -> None:
    _pending_admin_ids.add(admin_id)


def is_listening(admin_id: int) -> bool:
    return admin_id in _pending_admin_ids


def stop_listening(admin_id: int) -> None:
    _pending_admin_ids.discard(admin_id)


def set_group(chat_id: str) -> None:
    """Save the log group chat ID to config at runtime and DB."""
    import config
    config.LOG_GROUP_CHAT_ID = chat_id
    # Also persist to bot_settings
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_save_to_db(chat_id))
    except RuntimeError:
        pass


async def _save_to_db(chat_id: str) -> None:
    import database as db
    try:
        await db.set_setting("log_group_chat_id", chat_id, 0)
    except Exception:
        pass


async def _load_from_db() -> None:
    import config
    import database as db
    try:
        val = await db.get_setting("log_group_chat_id")
        if val:
            config.LOG_GROUP_CHAT_ID = val
    except Exception:
        pass


def init(app: "Application") -> None:
    global _app
    _app = app
    # Load saved log group from DB
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_load_from_db())
    except RuntimeError:
        pass


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
