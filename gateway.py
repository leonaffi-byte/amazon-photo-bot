"""
gateway.py — Consolidated FastAPI gateway for the Amazon Photo Bot.

Merges previously separate HTTP servers into one FastAPI app on port 8080:
  - URL shortener (from shortener_server.py / shortener_routes.py)
  - Webhook receiver (from webhook_server.py / webhook_routes.py)
  - Israel Shipping API (from api_server.py)
  - Admin dashboard (from admin_dashboard/)

Route mounting order is critical:
  1. /api/v1/*  (API routes)
  2. /webhook/* (webhook routes — only if adapters present)
  3. /admin/*   (admin dashboard — with SessionMiddleware)
  4. /{code}    (shortener catch-all — MUST BE LAST)

Usage:
    from gateway import create_app
    app = create_app(webhook_adapters=[...])
"""
from __future__ import annotations

import logging
from typing import Any

import config
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger(__name__)


def create_app(webhook_adapters: list[Any] | None = None) -> FastAPI:
    """
    Build and return the consolidated FastAPI application.

    Args:
        webhook_adapters: List of platform adapters that implement handle_webhook().
                          Pass None or [] to skip webhook route registration.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title       = "Amazon Photo Bot",
        description = (
            "Consolidated gateway: URL shortener, webhook receiver, Israel "
            "shipping verification API, and admin dashboard — all on port 8080."
        ),
        version  = "1.0.0",
        docs_url = "/docs",
        redoc_url= "/redoc",
    )

    # ── SessionMiddleware (must be added BEFORE routes) ───────────────────────
    app.add_middleware(
        SessionMiddleware,
        secret_key   = config.ADMIN_SESSION_SECRET,
        session_cookie = "admin_session",
        max_age      = 86400,      # 24 hours
        same_site    = "lax",
        https_only   = False,      # allow HTTP in development
    )

    # ── Security headers middleware ────────────────────────────────────────────
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # ── Health endpoint (registered directly — never intercepted by catch-all) ─
    @app.get("/health", tags=["System"])
    async def health():
        """Public health check — no auth required."""
        return {"status": "ok"}

    # ── Mount routers — ORDER MATTERS ─────────────────────────────────────────

    # 1. API routes at /api/v1/*
    from api_server import router as api_router
    app.include_router(api_router)
    logger.info("Mounted API router at /api/v1/*")

    # 2. Webhook routes at /webhook/* (only if adapters are present)
    if webhook_adapters:
        from webhook_routes import router as webhook_router, set_adapters
        set_adapters(webhook_adapters)
        app.include_router(webhook_router)
        logger.info("Mounted webhook router (%d adapter(s))", len(webhook_adapters))

    # 3. Admin dashboard at /admin/* — must come before shortener catch-all
    from admin_dashboard import router as admin_router
    app.include_router(admin_router, prefix="/admin")
    logger.info("Mounted admin dashboard at /admin")

    # 4. Shortener catch-all at /{code} — MUST BE LAST to avoid catching other routes
    from shortener_routes import router as shortener_router
    app.include_router(shortener_router)
    logger.info("Mounted shortener router at /{code} (catch-all, last)")

    return app
