"""
main.py — Single entry point.

Runs the Telegram bot and the custom URL shortener web server
in the same asyncio event loop — no threads, no subprocesses.

Architecture:
  asyncio event loop
    ├── python-telegram-bot (polling)
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
from bot import build_application

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
    # ── Database bootstrap (must happen before anything else) ─────────────────
    # Call init_db() here explicitly so that:
    #   a) any failure is immediately visible in the logs (not swallowed)
    #   b) the DB is ready before PTB builds the application
    # (PTB's post_init hook also calls this, but it can be silently skipped in
    #  some PTB 20.x versions when the event loop is already running.)
    import database as _db
    import settings_store as _ss
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

    # ── Notifications module (must be before scheduler) ───────────────────────
    import notifications
    ptb_app = build_application()
    notifications.init(ptb_app)

    # ── Start custom URL shortener server if configured ────────────────────────
    web_runner = None
    if config.SHORTENER_ENABLED and config.SHORTENER_BASE_URL:
        from shortener_server import start_shortener
        try:
            web_runner = await start_shortener()
        except Exception as exc:
            logger.error("Failed to start shortener server: %s", exc)
            logger.warning("Continuing without custom shortener.")

    # ── Run PTB in async context (PTB v20 pattern for custom event loops) ──────
    stop_event = asyncio.Event()

    def _stop(*_):
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            # Windows doesn't support add_signal_handler for all signals
            pass

    async with ptb_app:
        await ptb_app.initialize()
        await ptb_app.start()
        await ptb_app.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )

        # ── Start scheduled reports ────────────────────────────────────────────
        import scheduler as sched
        sched_task = sched.start()

        logger.info("✅ Bot is running. Press Ctrl+C to stop.")
        if web_runner:
            logger.info(
                "🔗 Shortener: %s  (port %d)",
                config.SHORTENER_BASE_URL,
                config.SHORTENER_PORT,
            )

        # Block until signal received
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass

        # Graceful shutdown
        logger.info("Shutting down…")
        sched.stop()
        sched_task.cancel()
        await ptb_app.updater.stop()
        await ptb_app.stop()

    if web_runner:
        await web_runner.cleanup()
        logger.info("Shortener server stopped.")

    logger.info("Goodbye.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
