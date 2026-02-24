"""
shortener_server.py — Self-hosted URL shortener.

Runs as an aiohttp web server in the same asyncio event loop as the Telegram bot.
Handles redirect requests and logs every click to SQLite for analytics.

Endpoints:
  GET /{code}        → 301 redirect to the long URL (logs click)
  GET /health        → plain-text health check (for uptime monitors / nginx)
  GET /stats/{code}  → JSON click stats for a code (admin use)

Setup:
  1. Set SHORTENER_BASE_URL=https://yourdomain.com in .env
  2. Set SHORTENER_PORT=8080 (or any free port)
  3. Point your domain to this server (nginx example in README)
  4. Bot generates links like https://yourdomain.com/abc123 automatically

Nginx minimal config:
  server {
      listen 80;
      server_name go.yourdomain.com;
      location / {
          proxy_pass http://127.0.0.1:8080;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      }
  }
"""
from __future__ import annotations

import logging
from typing import Optional

from aiohttp import web

import config
import database as db

logger = logging.getLogger(__name__)


# ── Request handlers ───────────────────────────────────────────────────────────

async def handle_redirect(request: web.Request) -> web.Response:
    """
    Main handler: look up the code, log the click, issue a 301 redirect.
    Uses 301 (permanent) so browsers cache it — reduces server load for
    repeat visitors to the same link.
    """
    code = request.match_info["code"]

    # Strip any file extension someone might have appended (e.g. .html)
    code = code.split(".")[0]

    long_url = await db.get_long_url_by_code(code)
    if not long_url:
        raise web.HTTPNotFound(
            text="Link not found or expired.",
            content_type="text/plain",
        )

    # Log click asynchronously (don't await — let redirect happen immediately)
    import asyncio
    asyncio.create_task(
        db.log_click(
            code=code,
            user_agent=request.headers.get("User-Agent", ""),
            referrer=request.headers.get("Referer", ""),
            ip=request.headers.get("X-Real-IP") or request.remote or "",
        )
    )

    # 302 (temporary) works better than 301 in Telegram's in-app browser
    return web.HTTPFound(location=long_url)


async def handle_health(request: web.Request) -> web.Response:
    """Health check — returns 200 OK. Use with uptime monitors."""
    total = await db.get_short_link_count()
    return web.Response(
        text=f"OK — {total} links stored",
        content_type="text/plain",
    )


async def handle_stats(request: web.Request) -> web.Response:
    """Return JSON click stats for a specific code."""
    code  = request.match_info["code"]
    stats = await db.get_link_stats(code)
    if not stats:
        raise web.HTTPNotFound(text="Code not found.")
    return web.json_response(stats)


# ── App factory ────────────────────────────────────────────────────────────────

def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health",        handle_health)
    app.router.add_get("/stats/{code}",  handle_stats)
    app.router.add_get("/{code}",        handle_redirect)
    return app


async def start_shortener() -> web.AppRunner:
    """Start the web server. Returns runner so caller can shut it down cleanly."""
    app    = build_web_app()
    runner = web.AppRunner(app, access_log=logger)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.SHORTENER_PORT)
    await site.start()
    logger.info(
        "🔗 URL shortener listening on port %d  (base: %s)",
        config.SHORTENER_PORT,
        config.SHORTENER_BASE_URL or "not configured",
    )
    return runner
