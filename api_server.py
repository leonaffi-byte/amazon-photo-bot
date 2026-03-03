"""
api_server.py — Israel Shipping Verifier  ·  Public REST API

Checks whether Amazon products ship to Israel and whether free shipping applies.
Powered by headless Chromium through an Israeli WireGuard exit node.

Base URL:   https://api.amznl.cc
Docs:       https://api.amznl.cc/docs

Authentication
──────────────
All /v1/* endpoints require:   X-API-Key: isk_your_key_here
Admin /v1/admin/* endpoints:   X-Admin-Secret: your_admin_secret

Rate limits
───────────
free  plan  —  100 requests / 24 h
basic plan  —  1 000 requests / 24 h
pro   plan  —  10 000 requests / 24 h

Run locally:
  uvicorn api_server:app --host 0.0.0.0 --port 8001 --reload

Run in Docker:
  docker compose up amazon-api
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

import database as db

logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title        = "Israel Shipping Verifier API",
    description  = (
        "Check whether Amazon products ship to Israel and whether free shipping "
        "applies (cart ≥ $49 FBA threshold).\n\n"
        "Powered by a real Chromium browser routed through an Israeli IP — "
        "results reflect what an actual Israeli Amazon customer sees.\n\n"
        "**Authentication**: pass your key in the `X-API-Key` header.\n"
        "**Get a key**: contact the API owner."
    ),
    version      = "1.0.0",
    contact      = {"name": "API Support", "url": "https://amznl.cc"},
    license_info = {"name": "Commercial — not open source"},
    docs_url     = "/docs",
    redoc_url    = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Auth ───────────────────────────────────────────────────────────────────────

_API_KEY_HEADER    = APIKeyHeader(name="X-API-Key",      auto_error=False)
_ADMIN_KEY_HEADER  = APIKeyHeader(name="X-Admin-Secret", auto_error=False)
_ADMIN_SECRET      = os.getenv("API_ADMIN_SECRET", "")

# ── In-memory sliding-window rate limiter ──────────────────────────────────────
# { api_key: deque of UTC timestamps }
_windows: dict[str, deque] = defaultdict(lambda: deque())
_WINDOW  = 86_400   # 24 hours in seconds

PLAN_LIMITS = {
    "free":  100,
    "basic": 1_000,
    "pro":   10_000,
}

# ── Pydantic models ────────────────────────────────────────────────────────────

class ShippingResult(BaseModel):
    asin:             str             = Field(..., example="B08XYZ12AB")
    verified:         bool            = Field(..., description="True = checked via real browser")
    ships_to_israel:  Optional[bool]  = Field(None, description="None = could not determine")
    is_free_shipping: Optional[bool]  = Field(None, description="None = could not determine")
    note:             str             = Field(..., description="Human-readable delivery note")
    cached:           bool            = Field(..., description="True = served from 24h cache")
    checked_at:       Optional[str]   = Field(None, description="ISO-8601 UTC timestamp of last check")

class BatchRequest(BaseModel):
    asins: list[str] = Field(
        ...,
        min_items  = 1,
        max_items  = 10,
        example    = ["B08XYZ12AB", "B09ABC12DE"],
        description = "List of ASINs to check (1–10)",
    )
    fresh: bool = Field(False, description="If true, bypass cache and re-verify each ASIN")

class BatchResult(BaseModel):
    results: list[ShippingResult]
    errors:  dict[str, str] = Field(default_factory=dict)

class QuotaInfo(BaseModel):
    plan:          str = Field(..., example="basic")
    daily_limit:   int = Field(..., example=1000)
    used_today:    int = Field(..., example=42)
    remaining:     int = Field(..., example=958)
    window_resets: str = Field(..., description="ISO-8601 UTC — when the oldest request in window expires")

class ApiKey(BaseModel):
    key:           str
    name:          str
    plan:          str
    daily_limit:   int
    total_requests:int
    is_active:     bool
    created_at:    str
    notes:         str

class CreateKeyRequest(BaseModel):
    name:        str  = Field(..., example="My App")
    plan:        str  = Field("free", pattern="^(free|basic|pro)$")
    daily_limit: Optional[int] = Field(None, description="Override plan default")
    notes:       str  = Field("", description="Optional notes about this key")


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await db.init_db()
    logger.info("🚀 Israel Shipping API started")


# ── Dependency: validate API key ───────────────────────────────────────────────

async def get_api_key(raw_key: str = Security(_API_KEY_HEADER)) -> dict:
    """
    Validate the X-API-Key header.
    Returns the key row dict on success; raises 401/403/429 on failure.
    """
    if not raw_key:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Missing X-API-Key header. Get a key at https://amznl.cc",
        )

    key_row = await db.get_external_api_key(raw_key)
    if not key_row:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Invalid API key.",
        )
    if not key_row["is_active"]:
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail      = "This API key has been revoked.",
        )

    # ── Sliding-window rate limit ──────────────────────────────────────────────
    limit = key_row["daily_limit"]
    win   = _windows[raw_key]
    cutoff = time.time() - _WINDOW
    while win and win[0] < cutoff:
        win.popleft()
    if len(win) >= limit:
        oldest  = win[0]
        resets  = datetime.fromtimestamp(oldest + _WINDOW, tz=timezone.utc).isoformat()
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail      = f"Daily limit of {limit} requests reached. Resets at {resets}.",
            headers     = {"Retry-After": str(int(oldest + _WINDOW - time.time()))},
        )
    win.append(time.time())

    return key_row


async def get_admin_key(raw_key: str = Security(_ADMIN_KEY_HEADER)) -> None:
    """Validate the X-Admin-Secret header."""
    if not _ADMIN_SECRET:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail      = "Admin access not configured (set API_ADMIN_SECRET env var).",
        )
    if raw_key != _ADMIN_SECRET:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Invalid admin secret.",
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _validate_asin(asin: str) -> str:
    asin = asin.strip().upper()
    if len(asin) != 10 or not asin.isalnum():
        raise HTTPException(
            status_code = 422,
            detail      = f"Invalid ASIN '{asin}'. Must be exactly 10 alphanumeric characters.",
        )
    return asin


async def _check_one(asin: str, fresh: bool = False) -> ShippingResult:
    """Run or retrieve the shipping check for one ASIN."""
    import israel_scraper

    if fresh:
        # Delete cache entry so check_shipping re-scrapes
        await db.delete_israel_cache(asin)

    result = await israel_scraper.check_shipping(asin)
    cached = not fresh   # heuristic: if we didn't ask for fresh, it was probably cached

    # Record request
    await db.log_api_request(
        asin             = asin,
        cached           = cached,
        ships_to_israel  = result.ships_to_israel,
        is_free_shipping = result.is_free_shipping,
    )

    return ShippingResult(
        asin             = result.asin,
        verified         = result.verified,
        ships_to_israel  = result.ships_to_israel,
        is_free_shipping = result.is_free_shipping,
        note             = result.note,
        cached           = cached,
        checked_at       = datetime.now(timezone.utc).isoformat(),
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def _root():
    return RedirectResponse("/docs")


@app.get("/health", tags=["System"])
async def health():
    """Public health check — no auth required."""
    return {
        "status":  "ok",
        "service": "israel-shipping-api",
        "version": "1.0.0",
        "docs":    "https://api.amznl.cc/docs",
    }


@app.get(
    "/v1/check",
    response_model = ShippingResult,
    tags           = ["Shipping"],
    summary        = "Check a single ASIN",
    description    = (
        "Verify whether an Amazon product ships to Israel and whether free "
        "shipping applies (FBA items with cart ≥ $49).\n\n"
        "Results are cached for 24 hours per ASIN. Pass `?fresh=true` to "
        "bypass the cache and re-scrape."
    ),
)
async def check_single(
    asin:    str  = Query(..., description="Amazon ASIN (10 alphanumeric chars)"),
    fresh:   bool = Query(False, description="Bypass cache and re-verify"),
    _key:    dict = Depends(get_api_key),
):
    asin = _validate_asin(asin)
    return await _check_one(asin, fresh=fresh)


@app.post(
    "/v1/batch",
    response_model = BatchResult,
    tags           = ["Shipping"],
    summary        = "Check multiple ASINs (up to 10)",
    description    = (
        "Verify shipping for up to 10 ASINs in one request. "
        "Checks run in parallel. Each ASIN consumes one request from your quota.\n\n"
        "ASINs that fail (scrape error, CAPTCHA unsolved) are returned in "
        "`errors` with a reason string."
    ),
)
async def check_batch(
    body:  BatchRequest,
    _key:  dict = Depends(get_api_key),
):
    results: list[ShippingResult] = []
    errors:  dict[str, str]       = {}

    async def _safe_check(asin: str) -> None:
        try:
            result = await _check_one(asin, fresh=body.fresh)
            results.append(result)
        except HTTPException:
            raise
        except Exception as exc:
            errors[asin] = str(exc)

    asin_list = [_validate_asin(a) for a in body.asins]
    await asyncio.gather(*[_safe_check(a) for a in asin_list], return_exceptions=True)

    return BatchResult(results=results, errors=errors)


@app.get(
    "/v1/cache/{asin}",
    response_model = ShippingResult,
    tags           = ["Shipping"],
    summary        = "Read cached result (no fresh check)",
    description    = (
        "Return the cached verification result for an ASIN without triggering "
        "a new scrape. Returns 404 if the ASIN has never been checked or the "
        "cache has expired (24h TTL)."
    ),
)
async def get_cached(
    asin:  str,
    _key:  dict = Depends(get_api_key),
):
    asin = _validate_asin(asin)
    cached = await db.get_israel_cache(asin)
    if not cached:
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail      = f"No cached result for ASIN {asin}. Use /v1/check to verify.",
        )
    return ShippingResult(
        asin             = cached.asin,
        verified         = cached.verified,
        ships_to_israel  = cached.ships_to_israel,
        is_free_shipping = cached.is_free_shipping,
        note             = cached.note,
        cached           = True,
        checked_at       = datetime.now(timezone.utc).isoformat(),
    )


@app.delete(
    "/v1/cache/{asin}",
    tags    = ["Shipping"],
    summary = "Invalidate cached result",
)
async def invalidate_cache(
    asin:  str,
    _key:  dict = Depends(get_api_key),
):
    """Force the next /v1/check call to re-scrape by clearing the cache entry."""
    asin = _validate_asin(asin)
    await db.delete_israel_cache(asin)
    return {"asin": asin, "cache": "cleared"}


@app.get(
    "/v1/quota",
    response_model = QuotaInfo,
    tags           = ["Account"],
    summary        = "Check remaining quota",
)
async def quota(key_row: dict = Depends(get_api_key)):
    """Return usage stats for the authenticated API key."""
    raw_key = key_row["key"]
    win     = _windows[raw_key]
    cutoff  = time.time() - _WINDOW
    valid   = [t for t in win if t > cutoff]
    limit   = key_row["daily_limit"]
    used    = len(valid)

    resets_at = (
        datetime.fromtimestamp(valid[0] + _WINDOW, tz=timezone.utc).isoformat()
        if valid else
        datetime.now(timezone.utc).isoformat()
    )

    return QuotaInfo(
        plan          = key_row["plan"],
        daily_limit   = limit,
        used_today    = used,
        remaining     = max(0, limit - used),
        window_resets = resets_at,
    )


# ── Admin endpoints ────────────────────────────────────────────────────────────

@app.post(
    "/v1/admin/keys",
    response_model = ApiKey,
    tags           = ["Admin"],
    summary        = "Create a new API key",
    status_code    = status.HTTP_201_CREATED,
)
async def create_key(
    body:  CreateKeyRequest,
    _:     None = Depends(get_admin_key),
):
    """Create an API key. Protected by X-Admin-Secret header."""
    limit = body.daily_limit or PLAN_LIMITS.get(body.plan, 100)
    key   = "isk_" + secrets.token_hex(16)   # isk_<32 hex chars>
    row   = await db.create_external_api_key(
        key         = key,
        name        = body.name,
        plan        = body.plan,
        daily_limit = limit,
        notes       = body.notes,
    )
    return ApiKey(**row)


@app.get(
    "/v1/admin/keys",
    response_model = list[ApiKey],
    tags           = ["Admin"],
    summary        = "List all API keys",
)
async def list_keys(_: None = Depends(get_admin_key)):
    rows = await db.list_external_api_keys()
    return [ApiKey(**r) for r in rows]


@app.get(
    "/v1/admin/keys/{key}",
    response_model = ApiKey,
    tags           = ["Admin"],
    summary        = "Get API key details",
)
async def get_key(key: str, _: None = Depends(get_admin_key)):
    row = await db.get_external_api_key(key)
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    return ApiKey(**row)


@app.delete(
    "/v1/admin/keys/{key}",
    tags    = ["Admin"],
    summary = "Revoke an API key",
)
async def revoke_key(key: str, _: None = Depends(get_admin_key)):
    row = await db.get_external_api_key(key)
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.revoke_external_api_key(key)
    return {"key": key, "status": "revoked"}


@app.patch(
    "/v1/admin/keys/{key}",
    response_model = ApiKey,
    tags           = ["Admin"],
    summary        = "Update API key (plan, limit, active status)",
)
async def update_key(
    key:    str,
    plan:   Optional[str] = Query(None, pattern="^(free|basic|pro)$"),
    limit:  Optional[int] = Query(None, ge=1),
    active: Optional[bool]= Query(None),
    _:      None          = Depends(get_admin_key),
):
    row = await db.get_external_api_key(key)
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    updated = await db.update_external_api_key(
        key         = key,
        plan        = plan,
        daily_limit = limit,
        is_active   = active,
    )
    return ApiKey(**updated)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    uvicorn.run(
        "api_server:app",
        host    = "0.0.0.0",
        port    = int(os.getenv("API_PORT", "8001")),
        reload  = False,
        workers = 1,      # single worker — shares in-memory rate limiter correctly
        log_level = "info",
    )
