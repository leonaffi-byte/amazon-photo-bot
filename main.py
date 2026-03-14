"""
main.py -- Single entry point.

Runs all configured platform adapters and the consolidated FastAPI gateway
in the same asyncio event loop -- no threads, no subprocesses.

Architecture:
  asyncio event loop
    +-- TelegramAdapter -> BotCore  (polling)
    +-- DiscordAdapter  -> BotCore  (gateway WebSocket)
    +-- FastAPI gateway on :8080   (shortener + webhooks + Israel API)
         Shortener only active when SHORTENER_ENABLED=true and SHORTENER_BASE_URL is set.
         Webhook routes registered for any adapter that implements handle_webhook().
"""
import asyncio
import logging
import os
import signal
import sys
import warnings

from telegram.warnings import PTBUserWarning
# PTB always warns when CallbackQueryHandler is used inside a ConversationHandler
# with per_message=False (the correct setting for our flows). The warning is
# purely informational -- behaviour is exactly what we want -- so silence it.
warnings.filterwarnings("ignore", category=PTBUserWarning, message=".*per_message=False.*")

import config

# Log file lives in the same data/ directory as the database so that a single
# Docker volume mount (./data:/app/data) captures both.
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

# ── In-flight task tracking for graceful shutdown ─────────────────────────────
# Tasks registered here are awaited during shutdown with a 10s grace period.
_active_tasks: set[asyncio.Task] = set()


def track_task(task: asyncio.Task) -> asyncio.Task:
    """Register a task for graceful-shutdown tracking. Returns the task."""
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


def _make_callbacks(bot_core_ref):
    """
    Build the standard on_photo/on_callback/on_text/on_command callbacks.

    bot_core_ref is a single-element list so the callbacks can reference
    the BotCore instance that is created *after* them (forward reference).
    """
    def _uid(uid):
        """Convert user ID to int if numeric, else keep as string."""
        return int(uid) if isinstance(uid, str) and uid.isdigit() else uid

    return dict(
        on_photo=lambda a, e: bot_core_ref[0].handle_photo(e),
        on_callback=lambda a, uid, cid, d, e: bot_core_ref[0].handle_callback(
            _uid(uid), cid, d, e,
        ),
        on_text=lambda a, uid, cid, t, e: bot_core_ref[0].handle_text_search(
            _uid(uid), cid, t, e,
        ),
        on_command=lambda a, uid, cid, c, args, e: bot_core_ref[0].handle_command(
            _uid(uid), cid, c, args.split() if args else [], e,
        ),
    )


async def run() -> None:
    # -- Database bootstrap (must happen before anything else) -------------
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

    # -- Load i18n locales -------------------------------------------------
    from i18n import load_locales
    load_locales()
    logger.info("Locales loaded.")

    # -- Wire up adapters + cores ------------------------------------------
    from bot_core import BotCore

    adapters = []
    bot_cores = []

    # --- Telegram ---
    tg_adapter = None
    if config.TELEGRAM_BOT_TOKEN:
        from adapters.telegram import TelegramAdapter
        _ref = [None]
        tg_adapter = TelegramAdapter(**_make_callbacks(_ref))
        tg_core = BotCore(tg_adapter)
        _ref[0] = tg_core
        adapters.append(tg_adapter)
        bot_cores.append(tg_core)
        logger.info("Telegram adapter configured.")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set -- Telegram adapter disabled.")

    # --- WhatsApp ---
    if config.WHATSAPP_TOKEN:
        from adapters.whatsapp import WhatsAppAdapter
        _ref = [None]
        wa_adapter = WhatsAppAdapter(**_make_callbacks(_ref))
        wa_core = BotCore(wa_adapter)
        _ref[0] = wa_core
        adapters.append(wa_adapter)
        bot_cores.append(wa_core)
        logger.info("WhatsApp adapter configured.")

    # --- Instagram ---
    if config.INSTAGRAM_TOKEN:
        from adapters.instagram import InstagramAdapter
        _ref = [None]
        ig_adapter = InstagramAdapter(**_make_callbacks(_ref))
        ig_core = BotCore(ig_adapter)
        _ref[0] = ig_core
        adapters.append(ig_adapter)
        bot_cores.append(ig_core)
        logger.info("Instagram adapter configured.")

    # --- Facebook Messenger ---
    if config.MESSENGER_TOKEN:
        from adapters.messenger import MessengerAdapter
        _ref = [None]
        fb_adapter = MessengerAdapter(**_make_callbacks(_ref))
        fb_core = BotCore(fb_adapter)
        _ref[0] = fb_core
        adapters.append(fb_adapter)
        bot_cores.append(fb_core)
        logger.info("Messenger adapter configured.")

    # --- Viber ---
    if config.VIBER_TOKEN:
        from adapters.viber import ViberAdapter
        _ref = [None]
        vb_adapter = ViberAdapter(**_make_callbacks(_ref))
        vb_core = BotCore(vb_adapter)
        _ref[0] = vb_core
        adapters.append(vb_adapter)
        bot_cores.append(vb_core)
        logger.info("Viber adapter configured.")

    # --- Discord ---
    if config.DISCORD_TOKEN:
        from adapters.discord_adapter import DiscordAdapter
        _ref = [None]
        dc_adapter = DiscordAdapter(**_make_callbacks(_ref))
        dc_core = BotCore(dc_adapter)
        _ref[0] = dc_core
        adapters.append(dc_adapter)
        bot_cores.append(dc_core)
        logger.info("Discord adapter configured.")

    # --- LINE ---
    if config.LINE_CHANNEL_TOKEN:
        from adapters.line import LineAdapter
        _ref = [None]
        ln_adapter = LineAdapter(**_make_callbacks(_ref))
        ln_core = BotCore(ln_adapter)
        _ref[0] = ln_core
        adapters.append(ln_adapter)
        bot_cores.append(ln_core)
        logger.info("LINE adapter configured.")

    if not adapters:
        logger.critical("No platform adapters configured -- set at least one token in .env")
        return

    # -- Start all adapters ------------------------------------------------
    for adapter in adapters:
        await adapter.start()

    # -- Notifications module (needs the underlying PTB Application) -------
    if tg_adapter is not None:
        import notifications
        notifications.init(tg_adapter._app)
        import log_group
        log_group.init(tg_adapter._app)

    # -- Generate web admin fallback token (for initial login via /webtoken) --
    try:
        from admin_dashboard.auth import generate_fallback_token
        generate_fallback_token()
        logger.info("Web admin fallback token generated. Use /webtoken in Telegram or paste at /admin/login")
    except Exception as _tok_exc:
        logger.warning("Could not pre-generate web admin fallback token: %s", _tok_exc)

    # -- Start consolidated FastAPI gateway (shortener + webhooks + API) ------
    webhook_adapters = [a for a in adapters if hasattr(a, "handle_webhook")]
    from gateway import create_app
    import uvicorn
    gateway_app = create_app(webhook_adapters=webhook_adapters if webhook_adapters else None)
    uvi_config = uvicorn.Config(
        gateway_app,
        host      = "0.0.0.0",
        port      = int(os.getenv("SHORTENER_PORT", str(config.SHORTENER_PORT))),
        log_level = "info",
        loop      = "none",   # Don't create a new event loop — use the running one
    )
    uvi_server = uvicorn.Server(uvi_config)
    server_task = track_task(asyncio.create_task(uvi_server.serve()))
    logger.info(
        "Gateway listening on port %d (shortener=%s, webhook_adapters=%d)",
        uvi_config.port,
        "enabled" if config.SHORTENER_ENABLED and config.SHORTENER_BASE_URL else "disabled",
        len(webhook_adapters),
    )

    # -- Start periodic cleanup tasks --------------------------------------
    cleanup_tasks = []
    for core in bot_cores:
        cleanup_tasks.append(asyncio.create_task(core.periodic_cleanup()))

    # -- Start scheduled reports -------------------------------------------
    import scheduler as sched
    sched_task = sched.start()

    # -- Signal handling ---------------------------------------------------
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

    logger.info(
        "Bot is running with %d adapter(s): %s. Press Ctrl+C to stop.",
        len(adapters),
        ", ".join(a.platform_name for a in adapters),
    )
    if config.SHORTENER_ENABLED and config.SHORTENER_BASE_URL:
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

    # -- Graceful shutdown -------------------------------------------------
    logger.info("Shutting down...")

    # 1. Stop the Uvicorn gateway and await in-flight analysis tasks
    uvi_server.should_exit = True

    # Wait for in-flight tasks (analysis, click logging, etc.) with 10s grace period
    # Remove the server_task itself from the set so we don't double-await it
    _active_tasks.discard(server_task)
    if _active_tasks:
        logger.info("Waiting for %d in-flight task(s)...", len(_active_tasks))
        done, pending = await asyncio.wait(list(_active_tasks), timeout=10.0)
        if pending:
            logger.warning("Cancelling %d timed-out task(s)", len(pending))
            for t in pending:
                t.cancel()
            # Give cancelled tasks a moment to clean up
            await asyncio.gather(*pending, return_exceptions=True)

    # Await the server task itself
    try:
        await asyncio.wait_for(server_task, timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    logger.info("Gateway stopped.")

    # 2. Stop scheduler
    sched.stop()
    sched_task.cancel()
    try:
        await asyncio.wait_for(sched_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    # 3. Stop periodic cleanup tasks
    for task in cleanup_tasks:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    # 4. Stop platform adapters
    for adapter in reversed(adapters):
        await adapter.stop()

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
