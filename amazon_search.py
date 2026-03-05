"""
amazon_search.py — public interface for product search.

The rest of the bot imports only from here:
  from amazon_search import search_amazon, AmazonItem

Backend is chosen automatically based on which keys are present in .env:

  SEARCH_BACKEND=rapidapi   →  RapidAPI "Real-Time Amazon Data"  (recommended for new bots)
  SEARCH_BACKEND=paapi      →  Amazon PA-API 5.0                  (if you have Associates + sales)
  SEARCH_BACKEND=auto       →  tries paapi first, falls back to rapidapi (default)

If SEARCH_BACKEND=auto and you have both keys, PA-API is used (more accurate FBA data).
If PA-API fails (e.g. account suspended), it auto-falls back to RapidAPI silently.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from search_backends.base import AmazonItem, SearchBackend
from image_analyzer import ProductInfo
import config

logger = logging.getLogger(__name__)

# Re-export AmazonItem so existing imports from amazon_search still work
__all__ = ["AmazonItem", "search_amazon", "get_backend", "backend_name", "reset_backend"]

_backend: Optional[SearchBackend] = None
_backend_lock = asyncio.Lock()


def reset_backend() -> None:
    """Clear cached backend — call when SEARCH_BACKEND setting changes."""
    global _backend
    _backend = None
    logger.info("Search backend cache cleared")


async def get_backend() -> SearchBackend:
    """Return the active backend, initialising it once on first call."""
    global _backend
    async with _backend_lock:
        if _backend is None:
            _backend = await _build_backend()
            logger.info("Search backend: %s", _backend.name)
    return _backend


def _clean_query(q: str) -> str:
    """Strip non-ASCII and normalize whitespace for Amazon search queries."""
    q = q.encode("ascii", errors="ignore").decode()
    q = " ".join(q.split())
    return q[:100]


async def backend_name() -> str:
    try:
        return (await get_backend()).name
    except Exception:
        return "not configured"


async def _get_fallback_backend(current: "SearchBackend") -> "Optional[SearchBackend]":
    """Get next available backend after the current one fails."""
    import key_store
    rapidapi_key       = await key_store.get("rapidapi_key")
    amazon_access      = await key_store.get("amazon_access_key")
    amazon_secret      = await key_store.get("amazon_secret_key")
    amazon_tag         = await key_store.get("amazon_associate_tag")
    dataforseo_login   = await key_store.get("dataforseo_login")
    dataforseo_password= await key_store.get("dataforseo_password")

    current_name = current.name.lower()

    # Try backends in order, skipping the current one
    if "paapi" not in current_name and bool(amazon_access and amazon_secret and amazon_tag):
        return _make_paapi(amazon_access, amazon_secret, amazon_tag)
    if "rapidapi" not in current_name and bool(rapidapi_key):
        return _make_rapidapi(rapidapi_key)
    if "dataforseo" not in current_name and bool(dataforseo_login and dataforseo_password):
        fb = _make_dataforseo(dataforseo_login, dataforseo_password)
        if await fb.is_available():
            return fb
    if "bright" not in current_name:
        bd_token = await key_store.get("brightdata_api_token")
        bd_zone  = await key_store.get("brightdata_zone") or "unlocker"
        if bd_token:
            return _make_brightdata(bd_token, bd_zone)
    return None


async def _build_backend() -> SearchBackend:
    """
    Build the search backend using keys from key_store (DB → .env fallback).
    Called automatically on first search — no restart needed after key changes.

    Priority order (auto mode):
      1. PA-API          — free, best data quality, needs Associates qualification
      2. RapidAPI        — free tier 100/month, most reliable default
      3. RapidAPI        — pay-per-use or subscription, 100 free/month
    """
    import key_store

    mode = config.SEARCH_BACKEND.lower()

    rapidapi_key       = await key_store.get("rapidapi_key")
    amazon_access      = await key_store.get("amazon_access_key")
    amazon_secret      = await key_store.get("amazon_secret_key")
    amazon_tag         = await key_store.get("amazon_associate_tag")
    dataforseo_login   = await key_store.get("dataforseo_login")
    dataforseo_password= await key_store.get("dataforseo_password")

    has_paapi      = bool(amazon_access and amazon_secret and amazon_tag)
    has_rapidapi   = bool(rapidapi_key)
    has_dataforseo = bool(dataforseo_login and dataforseo_password)

    if mode == "paapi":
        if not has_paapi:
            raise RuntimeError(
                "SEARCH_BACKEND=paapi but Amazon PA-API keys are not set.\n"
                "Add them via /admin → 🔑 API Keys."
            )
        return _make_paapi(amazon_access, amazon_secret, amazon_tag)

    if mode == "rapidapi":
        if not has_rapidapi:
            raise RuntimeError(
                "SEARCH_BACKEND=rapidapi but RAPIDAPI_KEY is not set.\n"
                "Add it via /admin → 🔑 API Keys."
            )
        return _make_rapidapi(rapidapi_key)

    if mode == "dataforseo":
        if not has_dataforseo:
            raise RuntimeError(
                "SEARCH_BACKEND=dataforseo but DataForSEO keys are not set.\n"
                "Add dataforseo_login + dataforseo_password via /admin → 🔑 API Keys."
            )
        backend = _make_dataforseo(dataforseo_login, dataforseo_password)
        if not await backend.is_available():
            raise RuntimeError(
                "DataForSEO Amazon SERP endpoint is not enabled on your account.\n"
                "Go to app.dataforseo.com → API → SERP → Amazon to activate it.\n"
                "Your account has credits but lacks access to this specific endpoint."
            )
        return backend

    if mode == "playwright":
        logger.info("Using Playwright / Amazon Direct Scraper backend")
        return _make_playwright()

    # auto mode: PA-API → RapidAPI → DataForSEO → Playwright (free fallback)
    # RapidAPI comes before DataForSEO: it has a free quota and is more reliable
    # for accounts that haven't enabled the DataForSEO Amazon SERP endpoint.
    if has_paapi:
        logger.info("Auto-selected PA-API backend")
        return _make_paapi(amazon_access, amazon_secret, amazon_tag)
    if has_rapidapi:
        logger.info("Auto-selected RapidAPI backend")
        return _make_rapidapi(rapidapi_key)
    if has_dataforseo:
        backend = _make_dataforseo(dataforseo_login, dataforseo_password)
        if await backend.is_available():
            logger.info("Auto-selected DataForSEO backend")
            return backend
        logger.info("DataForSEO Amazon SERP not enabled on this account — skipping")

    # Bright Data Web Unlocker
    bd_token = await key_store.get("brightdata_api_token")
    bd_zone  = await key_store.get("brightdata_zone") or "unlocker"
    if bd_token:
        logger.info("Auto-selected Bright Data Web Unlocker backend")
        return _make_brightdata(bd_token, bd_zone)

    # Last resort: raise error (Playwright disabled)
    raise RuntimeError(
        "No search API keys found and Playwright is disabled.\n"
        "Add at least one key via /admin → 🔑 API Keys:\n"
        "  • RapidAPI key\n"
        "  • DataForSEO login + password\n"
        "  • Amazon PA-API keys\n"
        "  • Bright Data token"
    )


def _make_paapi(access: str, secret: str, tag: str) -> SearchBackend:
    from search_backends.paapi_backend import PaapiBackend
    return PaapiBackend(
        access_key=access, secret_key=secret, associate_tag=tag,
        marketplace=config.AMAZON_MARKETPLACE,
    )


def _make_rapidapi(api_key: str) -> SearchBackend:
    from search_backends.rapidapi_backend import RapidAPIBackend
    return RapidAPIBackend(api_key=api_key)


def _make_dataforseo(login: str, password: str) -> SearchBackend:
    from search_backends.dataforseo_backend import DataForSEOBackend
    return DataForSEOBackend(login=login, password=password)


def _make_playwright() -> SearchBackend:
    from search_backends.playwright_backend import PlaywrightBackend
    return PlaywrightBackend()


def _make_brightdata(token: str, zone: str) -> SearchBackend:
    from search_backends.brightdata_backend import BrightDataBackend
    return BrightDataBackend(api_token=token, zone=zone)


# ── Public search function ─────────────────────────────────────────────────────

async def search_amazon(
    product: ProductInfo,
    max_results: int = config.MAX_RESULTS,
    israel_free_delivery_only: bool = False,
    page: int = 1,
) -> list[AmazonItem]:
    """
    Search Amazon for the identified product.

    Steps:
      1. Try the specific amazon_search_query from the vision model.
      2. If fewer than 3 results, retry with alternative_query.
      3. De-duplicate by ASIN.
      4. Optionally filter to Israel-free-delivery-eligible items only.
      5. Return sorted by quality score (rating × log reviews).

    Args:
        product:                  ProductInfo from image_analyzer / vision provider.
        max_results:              How many items to return.
        israel_free_delivery_only: When True, keep only FBA-eligible items.

    Returns:
        List of AmazonItem, best first.
    """
    backend = await get_backend()
    seen: dict[str, AmazonItem] = {}

    # Pre-process queries: strip non-ASCII and normalize whitespace
    primary_query = _clean_query(product.amazon_search_query)
    alt_query = _clean_query(product.alternative_query)

    # Wrap backend search with automatic fallback on quota/rate errors
    async def _search_with_fallback(query: str, max_res: int, pg: int = 1) -> list:
        nonlocal backend
        try:
            return await backend.search(query, max_res, page=pg)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "429" in exc_str or "quota" in exc_str or "rate" in exc_str or "limit" in exc_str:
                logger.warning("Backend %s quota exceeded, trying fallback...", backend.name)
                fb = await _get_fallback_backend(backend)
                if fb:
                    logger.info("Falling back to %s", fb.name)
                    backend = fb
                    global _backend
                    _backend = fb
                    return await fb.search(query, max_res, page=pg)
            raise

    if page > 1:
        # Lazy-load: fetch a specific Amazon results page directly (no fallback needed)
        try:
            items = await _search_with_fallback(primary_query, max_results, pg=page)
            for item in items:
                seen[item.asin] = item
            logger.info("[%s] Page %d '%s' → %d items", backend.name, page, primary_query, len(seen))
        except Exception as exc:
            logger.warning("Page %d search failed: %s", page, exc)
    else:
        # Primary query
        try:
            items = await _search_with_fallback(primary_query, max_results)
            for item in items:
                seen[item.asin] = item
            logger.info("[%s] Primary '%s' → %d results", backend.name, primary_query, len(seen))
        except Exception as exc:
            logger.warning("Primary search failed: %s", exc)

        # Fallback if too few results — small delay to avoid burst rate-limiting
        if len(seen) < 3 and alt_query != primary_query:
            try:
                items = await _search_with_fallback(alt_query, max_results)
                for item in items:
                    if item.asin not in seen:
                        seen[item.asin] = item
                logger.info("[%s] Fallback '%s' → %d total", backend.name, alt_query, len(seen))
            except Exception as exc:
                logger.warning("Fallback search failed: %s", exc)

    result = list(seen.values())

    # Apply Israel filter
    if israel_free_delivery_only:
        filtered = [i for i in result if i.qualifies_for_israel_free_delivery]
        if filtered:
            result = filtered
        else:
            logger.info("Israel filter would remove all results — returning unfiltered")

    result.sort(key=lambda i: i.score, reverse=True)
    return result[:max_results]
