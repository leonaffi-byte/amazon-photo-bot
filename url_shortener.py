"""
url_shortener.py — Multi-backend URL shortener with automatic fallback.

Priority order:
  1. Custom self-hosted  (SHORTENER_BASE_URL set + SHORTENER_ENABLED=true)
     → your own domain, click tracking, zero per-request cost, forever cached
  2. bit.ly              (bitly_token set in DB or .env)
     → professional, custom domain support, click analytics from bit.ly dashboard
  3. TinyURL             (always available, no key needed)
     → reliable free fallback, no analytics
  4. Original URL        (if all shorteners fail)

Caching:
  • Custom shortener: stores code→URL permanently in SQLite (short_links table)
  • External services: caches result in url_cache table
  Both: the same long URL always returns the same short link.
"""
from __future__ import annotations

import logging
import secrets
import string
from typing import Optional

import aiohttp

import config
import database as db
import key_store

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=4)

# Base-62 alphabet for code generation
_ALPHABET = string.ascii_letters + string.digits   # a-z A-Z 0-9  (62 chars)
_CODE_LEN = 7   # 62^7 = 3.5 trillion combinations


# ── Public API ────────────────────────────────────────────────────────────────

async def shorten(long_url: str, label: str = "", user_id: Optional[int] = None) -> str:
    """
    Shorten a URL using the best available backend.
    Returns the short URL, or the original if all shorteners fail.
    """
    short = (
        await _try_custom(long_url, label, user_id)
        or await _try_bitly(long_url)
        or await _try_tinyurl(long_url)
    )
    return short or long_url


async def shorten_many(
    urls: list[str],
    label: str = "",
    user_id: Optional[int] = None,
) -> dict[str, str]:
    """
    Shorten multiple URLs concurrently.
    Returns {long_url: short_url} mapping.
    """
    import asyncio
    results = await asyncio.gather(
        *[shorten(u, label, user_id) for u in urls],
        return_exceptions=True,
    )
    return {
        long: (short if isinstance(short, str) else long)
        for long, short in zip(urls, results)
    }


# ── Backend: Custom self-hosted ───────────────────────────────────────────────

async def _try_custom(
    long_url: str,
    label: str = "",
    user_id: Optional[int] = None,
) -> Optional[str]:
    """
    Use the self-hosted shortener if SHORTENER_BASE_URL is configured.
    Reuses existing code if this long_url has already been shortened.
    """
    if not config.SHORTENER_ENABLED or not config.SHORTENER_BASE_URL:
        return None

    base = config.SHORTENER_BASE_URL.rstrip("/")

    # Check if this URL was already shortened — return existing code
    existing_code = await db.get_code_by_long_url(long_url)
    if existing_code:
        return f"{base}/{existing_code}"

    # Generate a unique code
    code = await _generate_unique_code()
    await db.create_short_link(long_url, code, label=label, created_by=user_id)
    short = f"{base}/{code}"
    logger.debug("Custom short: %s → %s", long_url[:60], short)
    return short


async def _generate_unique_code() -> str:
    """Generate a unique base-62 code not already in the DB."""
    for _ in range(10):   # retry loop in case of collision (extremely rare)
        code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
        existing = await db.get_long_url_by_code(code)
        if not existing:
            return code
    # Extremely unlikely to reach here, but use longer code as last resort
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN + 3))


# ── Backend: bit.ly ───────────────────────────────────────────────────────────

async def _try_bitly(long_url: str) -> Optional[str]:
    # Check external cache first
    cached = await db.get_short_url(long_url)
    if cached and "bit.ly" in cached:
        return cached

    token = await key_store.get("bitly_token")
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api-ssl.bitly.com/v4/shorten",
                json={"long_url": long_url},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status in (200, 201):
                    data  = await resp.json()
                    short = data.get("link")
                    if short:
                        await db.cache_short_url(long_url, short)
                        return short
                logger.warning("bit.ly returned %d", resp.status)
    except Exception as exc:
        logger.warning("bit.ly error: %s", exc)
    return None


# ── Backend: TinyURL ──────────────────────────────────────────────────────────

async def _try_tinyurl(long_url: str) -> Optional[str]:
    # Check external cache
    cached = await db.get_short_url(long_url)
    if cached:
        return cached

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://tinyurl.com/api-create.php",
                params={"url": long_url},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    result = (await resp.text()).strip()
                    if result.startswith("https://tinyurl.com/"):
                        await db.cache_short_url(long_url, result)
                        return result
    except Exception as exc:
        logger.warning("TinyURL error: %s", exc)
    return None


# ── Utility ───────────────────────────────────────────────────────────────────

def active_backend_name() -> str:
    """Return a human-readable name of the highest-priority active backend."""
    if config.SHORTENER_ENABLED and config.SHORTENER_BASE_URL:
        return f"Custom ({config.SHORTENER_BASE_URL})"
    return "TinyURL (free)"   # bit.ly check is async, skip here
