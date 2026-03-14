"""
shortener_routes.py — FastAPI router for the custom URL shortener.

Converted from shortener_server.py (aiohttp) to FastAPI.

Endpoints:
  GET /stats/{code}  -> JSON click stats for a code (must be before /{code})
  GET /{code}        -> 302 redirect to the long URL (logs click)

Note: The /{code} catch-all route MUST be registered LAST in gateway.py
to avoid intercepting /health, /docs, /api/v1/*, etc.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

import database as db

_STATS_SECRET = os.getenv("SHORTENER_STATS_SECRET", "")

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shortener"])


@router.get("/stats/{code}", include_in_schema=True)
async def stats(code: str, request: Request):
    """Return JSON click stats for a specific code. Protected by X-Stats-Secret header."""
    if _STATS_SECRET:
        provided = request.headers.get("X-Stats-Secret", "")
        if not hmac.compare_digest(provided, _STATS_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.get_link_stats(code)
    if not result:
        raise HTTPException(status_code=404, detail="Code not found.")
    return result


@router.get("/{code}")
async def redirect(code: str, request: Request):
    """
    Main handler: look up the code, log the click, issue a 302 redirect.
    Uses 302 (temporary) which works better in Telegram's in-app browser.
    """
    # Strip any file extension someone might have appended (e.g. .html)
    code = code.split(".")[0][:16]

    # Validate code before hitting the database
    if not code or not code.isalnum():
        raise HTTPException(status_code=404, detail="Link not found.")

    long_url = await db.get_long_url_by_code(code)
    if not long_url:
        raise HTTPException(status_code=404, detail="Link not found or expired.")

    # Hash IP address before storage for privacy
    raw_ip = request.headers.get("X-Real-IP") or request.client.host if request.client else ""
    ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest()[:16] if raw_ip else ""

    # Log click asynchronously (don't await — let redirect happen immediately)
    asyncio.create_task(
        db.log_click(
            code=code,
            user_agent=request.headers.get("user-agent", ""),
            referrer=request.headers.get("referer", ""),
            ip=ip_hash,
        )
    )

    # 302 (temporary) works better than 301 in Telegram's in-app browser
    return RedirectResponse(url=long_url, status_code=302)
