"""
Webhook server — creates an aiohttp web application with routes for all
webhook-based platform adapters (WhatsApp, Instagram, Messenger, etc.).

Usage in main.py:

    from webhook_server import create_webhook_app
    app = create_webhook_app([whatsapp_adapter, instagram_adapter])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8443)
    await site.start()
"""
from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


def create_webhook_app(adapters: list) -> web.Application:
    """
    Create an aiohttp app with webhook routes for each adapter.

    For each adapter that exposes ``handle_webhook`` (POST) and/or
    ``handle_webhook_verify`` (GET), routes are registered at
    ``/webhook/{platform_name}``.

    A ``/health`` endpoint is always added for monitoring.
    """
    app = web.Application()

    for adapter in adapters:
        platform = adapter.platform_name
        if hasattr(adapter, "handle_webhook"):
            app.router.add_post(f"/webhook/{platform}", adapter.handle_webhook)
            logger.info("Registered POST /webhook/%s", platform)
        if hasattr(adapter, "handle_webhook_verify"):
            app.router.add_get(f"/webhook/{platform}", adapter.handle_webhook_verify)
            logger.info("Registered GET  /webhook/%s", platform)

    app.router.add_get("/health", _health)
    logger.info("Webhook server configured with %d adapter(s).", len(adapters))
    return app


async def _health(request: web.Request) -> web.Response:
    """Simple health-check endpoint for load balancers / monitoring."""
    return web.json_response({"status": "ok"})
