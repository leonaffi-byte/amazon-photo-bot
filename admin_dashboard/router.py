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
  GET  /keys              — API key management page
  POST /keys/{group_name}/{key_name}/save   — Save an API key value
  POST /keys/{group_name}/{key_name}/delete — Delete an API key value
  GET  /tags              — Affiliate tag management page
  POST /tags/{tag_id}/activate   — Activate a tag
  POST /tags/{tag_id}/deactivate — Deactivate all tags (deactivates active one)
  POST /tags/add          — Add a new affiliate tag
  POST /tags/{tag_id}/remove — Remove a tag
  GET  /settings          — Bot settings editor
  POST /settings/{key}/update — Update a single setting and return updated row fragment
  POST /settings/{key}/reset  — Reset a setting to default and return updated row fragment
  GET  /health            — Provider health detail page
  POST /health/{provider_name}/reset — Reset provider failure count and return updated row fragment

All routes except /login, /auth/callback, /auth/token, /logout use
Depends(require_admin) to enforce authentication.
"""
from __future__ import annotations

import logging
from pathlib import Path

import config
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import notifications
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


# ── API Key management routes ─────────────────────────────────────────────────

@router.get("/keys", response_class=HTMLResponse, name="admin_keys")
async def keys_page(request: Request, admin_id: int = Depends(require_admin)):
    """API key management page — lists all 18 key groups with set/not-set status."""
    try:
        key_groups = await admin_service.list_key_groups()
    except Exception:
        logger.warning("list_key_groups() failed", exc_info=True)
        key_groups = []
    return templates.TemplateResponse(
        "keys.html",
        {"request": request, "key_groups": key_groups},
    )


@router.post("/keys/{group_name}/{key_name}/save", response_class=HTMLResponse, name="admin_key_save")
async def key_save(
    request: Request,
    group_name: str,
    key_name: str,
    value: str = Form(...),
    admin_id: int = Depends(require_admin),
):
    """Save an API key value and return the updated key group card fragment."""
    if not value.strip():
        # Re-render the card with an error indicator
        group = await admin_service.get_key_group(group_name)
        return templates.TemplateResponse(
            "partials/key_group.html",
            {"request": request, "group": group, "error": "Value cannot be empty"},
            status_code=400,
        )
    await admin_service.set_api_key(key_name, value, admin_id=admin_id)
    try:
        await notifications.admin(
            f"API key `{key_name}` updated via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for key save", exc_info=True)
    group = await admin_service.get_key_group(group_name)
    return templates.TemplateResponse(
        "partials/key_group.html",
        {"request": request, "group": group},
    )


@router.post("/keys/{group_name}/{key_name}/delete", response_class=HTMLResponse, name="admin_key_delete")
async def key_delete(
    request: Request,
    group_name: str,
    key_name: str,
    admin_id: int = Depends(require_admin),
):
    """Delete an API key value and return the updated key group card fragment."""
    await admin_service.delete_api_key(key_name)
    try:
        await notifications.admin(
            f"API key `{key_name}` deleted via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for key delete", exc_info=True)
    group = await admin_service.get_key_group(group_name)
    return templates.TemplateResponse(
        "partials/key_group.html",
        {"request": request, "group": group},
    )


# ── Affiliate tag management routes ───────────────────────────────────────────

@router.get("/tags", response_class=HTMLResponse, name="admin_tags")
async def tags_page(request: Request, admin_id: int = Depends(require_admin)):
    """Affiliate tag management page — lists all tags with status and counts."""
    try:
        tags = await admin_service.list_tags()
    except Exception:
        logger.warning("list_tags() failed", exc_info=True)
        tags = []
    return templates.TemplateResponse(
        "tags.html",
        {"request": request, "tags": tags},
    )


@router.post("/tags/add", response_class=HTMLResponse, name="admin_tag_add")
async def tag_add(
    request: Request,
    tag: str = Form(...),
    admin_id: int = Depends(require_admin),
):
    """Create a new affiliate tag and return a new tag row fragment for HTMX."""
    if not tag.strip():
        return HTMLResponse("", status_code=400)
    new_tag = await admin_service.add_tag(tag.strip(), description="", admin_id=admin_id)
    try:
        await notifications.admin(
            f"Affiliate tag `{tag.strip()}` added via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for tag add", exc_info=True)
    return templates.TemplateResponse(
        "partials/tag_row.html",
        {"request": request, "tag": new_tag},
    )


@router.post("/tags/{tag_id}/activate", response_class=HTMLResponse, name="admin_tag_activate")
async def tag_activate(
    request: Request,
    tag_id: int,
    admin_id: int = Depends(require_admin),
):
    """Activate a tag and return the updated tag row fragment."""
    await admin_service.set_active_tag(tag_id)
    try:
        await notifications.admin(
            f"Tag {tag_id} activated via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for tag activate", exc_info=True)
    tags = await admin_service.list_tags()
    tag = next((t for t in tags if t.id == tag_id), None)
    return templates.TemplateResponse(
        "partials/tag_row.html",
        {"request": request, "tag": tag},
    )


@router.post("/tags/{tag_id}/deactivate", response_class=HTMLResponse, name="admin_tag_deactivate")
async def tag_deactivate(
    request: Request,
    tag_id: int,
    admin_id: int = Depends(require_admin),
):
    """Deactivate all tags and return the updated tag row fragment for the specified tag."""
    await admin_service.deactivate_all_tags()
    try:
        await notifications.admin(
            f"Tag {tag_id} deactivated via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for tag deactivate", exc_info=True)
    tags = await admin_service.list_tags()
    tag = next((t for t in tags if t.id == tag_id), None)
    return templates.TemplateResponse(
        "partials/tag_row.html",
        {"request": request, "tag": tag},
    )


@router.get("/settings", response_class=HTMLResponse, name="admin_settings")
async def settings_page(request: Request, admin_id: int = Depends(require_admin)):
    """Bot settings editor — lists all runtime settings with appropriate input controls."""
    try:
        settings = await admin_service.list_settings()
    except Exception:
        logger.warning("list_settings() failed", exc_info=True)
        settings = []
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings},
    )


@router.post("/settings/{key}/update", response_class=HTMLResponse, name="admin_setting_update")
async def setting_update(
    request: Request,
    key: str,
    value: str = Form(...),
    admin_id: int = Depends(require_admin),
):
    """Update a bot setting and return the updated setting row fragment."""
    await admin_service.set_setting(key, value, admin_id=admin_id)
    try:
        await notifications.admin(
            f"Setting `{key}` changed to `{value}` via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for setting update", exc_info=True)
    settings = await admin_service.list_settings()
    setting = next((s for s in settings if s.key == key), None)
    return templates.TemplateResponse(
        "partials/setting_row.html",
        {"request": request, "setting": setting},
    )


@router.post("/settings/{key}/reset", response_class=HTMLResponse, name="admin_setting_reset")
async def setting_reset(
    request: Request,
    key: str,
    admin_id: int = Depends(require_admin),
):
    """Reset a bot setting to its default and return the updated setting row fragment."""
    await admin_service.reset_setting(key)
    try:
        await notifications.admin(
            f"Setting `{key}` reset to default via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for setting reset", exc_info=True)
    settings = await admin_service.list_settings()
    setting = next((s for s in settings if s.key == key), None)
    return templates.TemplateResponse(
        "partials/setting_row.html",
        {"request": request, "setting": setting},
    )


@router.post("/tags/{tag_id}/remove", response_class=HTMLResponse, name="admin_tag_remove")
async def tag_remove(
    request: Request,
    tag_id: int,
    admin_id: int = Depends(require_admin),
):
    """Delete a tag and return empty HTML (HTMX removes the row from DOM)."""
    await admin_service.remove_tag(tag_id)
    try:
        await notifications.admin(
            f"Tag {tag_id} removed via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for tag remove", exc_info=True)
    return HTMLResponse("", status_code=200)


# ── Provider health management routes ─────────────────────────────────────────

@router.get("/health", response_class=HTMLResponse, name="admin_health")
async def health_page(request: Request, admin_id: int = Depends(require_admin)):
    """Provider health detail page — full table with status, latency, failure count."""
    try:
        providers = await admin_service.get_provider_health()
    except Exception:
        logger.warning("get_provider_health() failed", exc_info=True)
        providers = []
    return templates.TemplateResponse(
        "health.html",
        {"request": request, "providers": providers},
    )


@router.post("/health/{provider_name:path}/reset", response_class=HTMLResponse, name="admin_health_reset")
async def health_reset(
    request: Request,
    provider_name: str,
    admin_id: int = Depends(require_admin),
):
    """Reset provider failure count and return updated provider row fragment."""
    import providers.manager as pm
    await pm.reset_provider_health(provider_name)
    try:
        await notifications.admin(
            f"Provider `{provider_name}` health reset via web by admin {admin_id}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        logger.warning("Failed to send admin notification for health reset", exc_info=True)
    all_providers = await admin_service.get_provider_health()
    provider = next((p for p in all_providers if p.name == provider_name), None)
    return templates.TemplateResponse(
        "partials/provider_health_row.html",
        {"request": request, "provider": provider},
    )
