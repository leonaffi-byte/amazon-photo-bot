"""
main.py — Single entry point.

Runs the Telegram bot and the custom URL shortener web server
in the same asyncio event loop — no threads, no subprocesses.

Architecture:
  asyncio event loop
    ├── TelegramAdapter → BotCore  (polling)
    └── aiohttp web server  (redirect + click tracking)
         Only started when SHORTENER_ENABLED=true and SHORTENER_BASE_URL is set.
"""
import asyncio
import logging
import signal
import sys
import warnings

from telegram.warnings import PTBUserWarning
# PTB always warns when CallbackQueryHandler is used inside a ConversationHandler
# with per_message=False (the correct setting for our flows). The warning is
# purely informational — behaviour is exactly what we want — so silence it.
warnings.filterwarnings("ignore", category=PTBUserWarning, message=".*per_message=False.*")

import config

# Log file lives in the same data/ directory as the database so that a single
# Docker volume mount (./data:/app/data) captures both.
import os
from pathlib import Path
_data_dir = Path(os.getenv("DATA_DIR", "data"))
_data_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(_data_dir / "bot.log"), encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def run() -> None:
    # ── Database bootstrap (must happen before anything else) ─────────────
    import database as _db
    try:
        await _db.init_db()
        logger.info("Database ready at %s", _db.DB_PATH)
        if config.ADMIN_IDS:
            await _db.seed_admins(config.ADMIN_IDS)
            logger.info("Seeded %d bootstrap admin(s)", len(config.ADMIN_IDS))
        await config.apply_db_settings()
        logger.info("DB settings applied.")
    except Exception as exc:
        logger.critical("FATAL: database init failed: %s", exc, exc_info=True)
        raise

    # ── Load i18n locales ─────────────────────────────────────────────────
    from i18n import load_locales
    load_locales()
    logger.info("Locales loaded.")

    # ── Wire up adapter + core ────────────────────────────────────────────
    from adapters.telegram import TelegramAdapter
    from bot_core import BotCore

    adapter = TelegramAdapter(
        on_photo=lambda a, e: bot_core.handle_photo(e),
        on_callback=lambda a, uid, cid, d, e: bot_core.handle_callback(
            int(uid), cid, d, e,
        ),
        on_text=lambda a, uid, cid, t, e: bot_core.handle_text_search(
            int(uid), cid, t, e,
        ),
        on_command=lambda a, uid, cid, c, args, e: bot_core.handle_command(
            int(uid), cid, c, args.split() if args else [], e,
        ),
    )
    bot_core = BotCore(adapter)

    # ── Start the adapter (builds PTB app, registers handlers, polls) ─────
    await adapter.start()

    # ── Notifications module (needs the underlying PTB Application) ───────
    import notifications
    notifications.init(adapter._app)

    # ── Start custom URL shortener server if configured ────────────────────
    web_runner = None
    if config.SHORTENER_ENABLED and config.SHORTENER_BASE_URL:
        from shortener_server import start_shortener
        try:
            web_runner = await start_shortener()
        except Exception as exc:
            logger.error("Failed to start shortener server: %s", exc)
            logger.warning("Continuing without custom shortener.")

    # ── Start periodic cleanup task ───────────────────────────────────────
    cleanup_task = asyncio.create_task(bot_core.periodic_cleanup())

    # ── Start scheduled reports ───────────────────────────────────────────
    import scheduler as sched
    sched_task = sched.start()

    # ── Signal handling ───────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _stop(*_):
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            logger.warning("Signal handler for %s not supported on this platform", sig.name)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    if web_runner:
        logger.info(
            "Shortener: %s  (port %d)",
            config.SHORTENER_BASE_URL,
            config.SHORTENER_PORT,
        )

    # Block until signal received
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    # ── Graceful shutdown ─────────────────────────────────────────────────
    logger.info("Shutting down…")

    sched.stop()
    sched_task.cancel()
    try:
        await asyncio.wait_for(sched_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    cleanup_task.cancel()
    try:
        await asyncio.wait_for(cleanup_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    await adapter.stop()

    if web_runner:
        await web_runner.cleanup()
        logger.info("Shortener server stopped.")

    from database import close_db
    await close_db()
    logger.info("Goodbye.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
