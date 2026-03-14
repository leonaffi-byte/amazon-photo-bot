"""
admin_dashboard/router.py — FastAPI APIRouter for the web admin dashboard.

Routes:
  GET  /login             — Login page (Telegram widget + fallback form)
  GET  /auth/callback     — Telegram Login Widget HMAC callback
  POST /auth/token        — Fallback token form submission
  GET  /logout            — Clear session and redirect to login
  GET  /                  — Dashboard home (stat cards + provider health)
  GET  /partials/stats    — HTMX polling endpoint: stat cards fragment
  GET  /partials/health   — HTMX polling endpoint: provider health fragment
  GET  /partials/sidebar-toggle — Mobile hamburger sidebar toggle

All routes except /login, /auth/callback, /auth/token, /logout use
Depends(require_admin) to enforce authentication.
"""
from __future__ import annotations

import logging
from pathlib import Path

import config
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import admin_service
from admin_dashboard.auth import (
    generate_fallback_token,
    verify_fallback_token,
    verify_telegram_login,
)
from admin_dashboard.deps import require_admin
from admin_dashboard import sparklines

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)

# ── Login / Auth routes ───────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse, name="admin_login")
async def login_page(request: Request):
    """Render the admin login page with Telegram widget + fallback form."""
    bot_username = getattr(config, "TELEGRAM_BOT_USERNAME", "").lstrip("@") or ""
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "bot_username": bot_username,
            "has_fallback": True,
            "error": error,
        },
    )


@router.get("/auth/callback", name="telegram_callback")
async def telegram_callback(request: Request):
    """Handle Telegram Login Widget redirect callback."""
    params = dict(request.query_params)
    if not verify_telegram_login(params, config.TELEGRAM_BOT_TOKEN):
        return RedirectResponse("/admin/login?error=invalid_hmac", status_code=302)

    user_id = params.get("id")
    if not user_id:
        return RedirectResponse("/admin/login?error=missing_id", status_code=302)

    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return RedirectResponse("/admin/login?error=invalid_id", status_code=302)

    if not await admin_service.is_admin(uid):
        return RedirectResponse("/admin/login?error=unauthorized", status_code=302)

    request.session["admin_user_id"] = user_id
    return RedirectResponse("/admin/", status_code=302)


@router.post("/auth/token", name="admin_token_login")
async def token_login(request: Request, token: str = Form(...)):
    """Handle fallback token form submission."""
    if not verify_fallback_token(token):
        return RedirectResponse("/admin/login?error=invalid_token", status_code=302)

    request.session["admin_user_id"] = "0"
    return RedirectResponse("/admin/", status_code=302)


@router.get("/logout", name="admin_logout")
async def logout(request: Request):
    """Clear admin session and redirect to login."""
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)


# ── Dashboard routes ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, name="admin_home")
async def home(request: Request, admin_id: int = Depends(require_admin)):
    """Admin dashboard home — stat cards + provider health."""
    try:
        stats = await admin_service.get_stats()
    except Exception:
        logger.warning("get_stats() failed; using empty stats", exc_info=True)
        stats = None

    try:
        providers = await admin_service.get_provider_health()
    except Exception:
        logger.warning("get_provider_health() failed", exc_info=True)
        providers = []

    try:
        sparkline_svg = await sparklines.build_7day_sparkline()
    except Exception:
        logger.warning("build_7day_sparkline() failed", exc_info=True)
        sparkline_svg = sparklines.points_to_svg([])

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "stats": stats,
            "providers": providers,
            "sparkline_svg": sparkline_svg,
        },
    )


@router.get("/partials/stats", response_class=HTMLResponse, name="admin_partial_stats")
async def partial_stats(request: Request, admin_id: int = Depends(require_admin)):
    """HTMX polling endpoint — returns stat cards HTML fragment."""
    try:
        stats = await admin_service.get_stats()
        sparkline_svg = await sparklines.build_7day_sparkline()
        error = None
    except Exception as exc:
        logger.warning("partial_stats error: %s", exc)
        stats = None
        sparkline_svg = sparklines.points_to_svg([])
        error = "Stats unavailable"

    return templates.TemplateResponse(
        "partials/stat_cards.html",
        {
            "request": request,
            "stats": stats,
            "sparkline_svg": sparkline_svg,
            "error": error,
        },
    )


@router.get("/partials/health", response_class=HTMLResponse, name="admin_partial_health")
async def partial_health(request: Request, admin_id: int = Depends(require_admin)):
    """HTMX polling endpoint — returns provider health HTML fragment."""
    try:
        providers = await admin_service.get_provider_health()
        error = None
    except Exception as exc:
        logger.warning("partial_health error: %s", exc)
        providers = []
        error = "Health data unavailable"

    return templates.TemplateResponse(
        "partials/provider_health.html",
        {
            "request": request,
            "providers": providers,
            "error": error,
        },
    )


@router.get("/partials/sidebar-toggle", response_class=HTMLResponse, name="sidebar_toggle")
async def sidebar_toggle(request: Request):
    """Toggle sidebar visibility for mobile hamburger menu."""
    # Read current state from HX-Request header (or use a query param approach)
    # Simple approach: check if sidebar was hidden via query param
    hidden = request.query_params.get("hidden", "false").lower() == "true"
    if hidden:
        css_class = "fixed inset-y-0 left-0 w-64 bg-white shadow-sm block z-40"
        new_hidden = "false"
    else:
        css_class = "fixed inset-y-0 left-0 w-64 bg-white shadow-sm hidden z-40"
        new_hidden = "true"

    nav_links = [
        ("/admin/", "Dashboard"),
        ("/admin/tags", "Tags"),
        ("/admin/keys", "API Keys"),
        ("/admin/settings", "Settings"),
        ("/admin/health", "Health"),
        ("/admin/logout", "Logout"),
    ]
    links_html = "".join(
        f'<a href="{href}" class="block px-4 py-2 text-gray-700 hover:bg-gray-100">{label}</a>'
        for href, label in nav_links
    )

    return HTMLResponse(
        f'<nav id="sidebar" class="{css_class}" '
        f'hx-get="/admin/partials/sidebar-toggle?hidden={new_hidden}" '
        f'hx-trigger="click from:#hamburger" hx-target="#sidebar" hx-swap="outerHTML">'
        f"{links_html}</nav>"
    )
