"""
webhook_routes.py — FastAPI router for webhook-based platform adapters.

Converted from webhook_server.py (aiohttp) to FastAPI.

Endpoints:
  POST /webhook/{platform}  -> dispatches to the adapter's handle_webhook()
  GET  /webhook/{platform}  -> dispatches to the adapter's handle_webhook_verify()

Usage in gateway.py:
    from webhook_routes import router as webhook_router, set_adapters
    set_adapters(webhook_adapters)
    app.include_router(webhook_router)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

# Module-level adapter registry — populated by set_adapters() before the app starts
_adapters: list[Any] = []

router = APIRouter(prefix="/webhook", tags=["webhooks"])


def set_adapters(adapters: list) -> None:
    """Register the webhook adapters. Call this before mounting the router."""
    global _adapters
    _adapters = list(adapters)
    for adapter in _adapters:
        platform = adapter.platform_name
        if hasattr(adapter, "handle_webhook"):
            logger.info("Registered POST /webhook/%s", platform)
        if hasattr(adapter, "handle_webhook_verify"):
            logger.info("Registered GET  /webhook/%s", platform)
    logger.info("Webhook router configured with %d adapter(s).", len(_adapters))


def _find_adapter(platform: str):
    """Find an adapter by platform name. Returns None if not found."""
    for adapter in _adapters:
        if adapter.platform_name == platform:
            return adapter
    return None


@router.post("/{platform}")
async def handle_webhook(platform: str, request: Request):
    """Dispatch incoming webhook to the appropriate platform adapter."""
    adapter = _find_adapter(platform)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"No adapter registered for platform '{platform}'")
    if not hasattr(adapter, "handle_webhook"):
        raise HTTPException(status_code=405, detail=f"Platform '{platform}' does not support webhooks")
    return await adapter.handle_webhook(request)


@router.get("/{platform}")
async def handle_webhook_verify(platform: str, request: Request):
    """Dispatch webhook verification request to the appropriate platform adapter."""
    adapter = _find_adapter(platform)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"No adapter registered for platform '{platform}'")
    if not hasattr(adapter, "handle_webhook_verify"):
        raise HTTPException(status_code=405, detail=f"Platform '{platform}' does not support webhook verification")
    return await adapter.handle_webhook_verify(request)
