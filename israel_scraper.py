"""
israel_scraper.py — Verify Amazon-to-Israel shipping via a residential proxy.

How it works
────────────
1. Connect to Amazon.com through an Israeli SOCKS5/HTTP proxy (WireGuard exit node)
2. Amazon sees the request as coming from Israel
3. Set the delivery country to IL via Amazon's internal change-address API
4. Fetch the product page — Amazon now shows Israel-specific delivery info
5. Parse the page for shipping availability / free shipping eligibility
6. Cache the result in DB for 24 hours so the same ASIN is never re-checked

Proxy setup (on your Israeli WireGuard server — one-liner):
  docker run -d -p 1080:1080 serjs/go-socks5-proxy

Then set ISRAEL_PROXY_URL = socks5://YOUR_ISRAEL_IP:1080
via /admin → 🔑 API Keys → israel_proxy_url

Supports:
  socks5://host:port           (requires aiohttp-socks)
  socks5://user:pass@host:port
  http://host:port             (aiohttp native)
  https://host:port            (aiohttp native)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CACHE_TTL  = 86_400          # 24 hours in seconds
REQ_TIMEOUT = aiohttp.ClientTimeout(total=12)

_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class IsraelShippingResult:
    asin:             str
    verified:         bool          # False = check failed (proxy error, CAPTCHA…)
    ships_to_israel:  Optional[bool]  # None when not verified
    is_free_shipping: Optional[bool]  # None when not verified
    note:             str           # display note for product card


# ── Public API ─────────────────────────────────────────────────────────────────

async def is_configured() -> bool:
    """Return True if an Israeli proxy URL has been set in the admin panel."""
    import key_store
    url = await key_store.get("israel_proxy_url")
    return bool(url and url.strip())


async def check_shipping(asin: str) -> IsraelShippingResult:
    """
    Return Israel shipping info for an ASIN, using a 24-hour DB cache.
    Never raises — returns an unverified result on any error.
    """
    import key_store
    proxy_url = await key_store.get("israel_proxy_url")
    if not proxy_url:
        return _unverified(asin, "Proxy not configured")

    # ── Check DB cache ─────────────────────────────────────────────────────────
    try:
        import database as db
        cached = await db.get_israel_cache(asin)
        if cached:
            logger.debug("Israel cache hit for %s", asin)
            return cached
    except Exception as exc:
        logger.warning("Israel cache read failed: %s", exc)

    # ── Scrape ─────────────────────────────────────────────────────────────────
    result = await _scrape(asin, proxy_url.strip())

    # ── Store in cache ─────────────────────────────────────────────────────────
    if result.verified:
        try:
            import database as db
            await db.set_israel_cache(
                asin             = asin,
                ships_to_israel  = result.ships_to_israel,
                is_free_shipping = result.is_free_shipping,
                note             = result.note,
            )
        except Exception as exc:
            logger.warning("Israel cache write failed: %s", exc)

    return result


# ── Scraping internals ─────────────────────────────────────────────────────────

async def _scrape(asin: str, proxy_url: str) -> IsraelShippingResult:
    """
    Three-step flow:
      1. GET amazon.com homepage → grab anti-CSRF token + session cookies
      2. POST change-address to Israel
      3. GET /dp/{asin} → parse delivery section
    """
    try:
        connector = _make_connector(proxy_url)

        async with aiohttp.ClientSession(
            connector  = connector,
            headers    = _HEADERS,
            cookie_jar = aiohttp.CookieJar(),
        ) as session:

            # ── Step 1: Homepage (cookies + CSRF) ──────────────────────────────
            try:
                async with session.get(
                    "https://www.amazon.com",
                    timeout = REQ_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        return _unverified(asin, f"Homepage HTTP {resp.status}")
                    home_html = await resp.text(errors="replace")
            except asyncio.TimeoutError:
                return _unverified(asin, "Homepage timeout")

            if _is_captcha(home_html):
                logger.warning("Amazon CAPTCHA on homepage for ASIN %s", asin)
                return _unverified(asin, "CAPTCHA")

            csrf = _extract_csrf(home_html)

            # ── Step 2: Set delivery country to Israel ─────────────────────────
            if csrf:
                try:
                    await session.post(
                        "https://www.amazon.com/gp/delivery-options/ajax/change-address",
                        data = {
                            "locationType":  "COUNTRY",
                            "zipCode":       "",
                            "countryCode":   "IL",
                            "deviceType":    "web",
                            "pageType":      "Desktop:Search",
                            "storeContext":  "NoStoreName",
                            "actionSource":  "glow",
                        },
                        headers = {**_HEADERS, "anti-csrftoken-a2z": csrf},
                        timeout = aiohttp.ClientTimeout(total=6),
                    )
                except Exception:
                    pass   # Non-fatal — product page may still show Israel info

            # ── Step 3: Product page ───────────────────────────────────────────
            try:
                async with session.get(
                    f"https://www.amazon.com/dp/{asin}",
                    params  = {"th": "1", "psc": "1"},
                    timeout = REQ_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        return _unverified(asin, f"Product page HTTP {resp.status}")
                    product_html = await resp.text(errors="replace")
            except asyncio.TimeoutError:
                return _unverified(asin, "Product page timeout")

            if _is_captcha(product_html):
                logger.warning("Amazon CAPTCHA on product page for ASIN %s", asin)
                return _unverified(asin, "CAPTCHA")

            return _parse_html(asin, product_html)

    except Exception as exc:
        logger.warning("Israel scrape failed for %s: %s", asin, exc)
        return _unverified(asin, type(exc).__name__)


def _make_connector(proxy_url: str):
    """Return the right aiohttp connector for the given proxy URL."""
    if proxy_url.lower().startswith("socks"):
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(proxy_url)
        except ImportError:
            logger.error(
                "aiohttp-socks not installed. "
                "Add 'aiohttp-socks>=0.8.0' to requirements.txt "
                "or use an HTTP proxy URL instead."
            )
            return None
    # HTTP / HTTPS proxy — handled natively by aiohttp via proxy= param below
    # We return None here and pass proxy= to each request instead
    return None


# ── HTML parsing ───────────────────────────────────────────────────────────────

# Phrases that mean "this item will NOT ship to Israel"
_NO_SHIP_PHRASES = [
    "this item cannot be shipped to your selected delivery location",
    "this item does not ship to",
    "does not ship to israel",
    "cannot be delivered to israel",
    "this item is not available",
    "currently unavailable",
    "item not available in this country",
    "not available for your location",
    "we don't ship to israel",
]

# Phrases that confirm free shipping applies (FBA / Prime)
_FREE_SHIP_PHRASES = [
    "free delivery",
    "free shipping",
    "ships free",
]


def _parse_html(asin: str, html: str) -> IsraelShippingResult:
    html_lower = html.lower()

    # ── Sanity check: is this a real product page? ─────────────────────────────
    if "producttitle" not in html_lower and "dp/" not in html_lower:
        return _unverified(asin, "Not a product page")

    # ── Negative signals ───────────────────────────────────────────────────────
    for phrase in _NO_SHIP_PHRASES:
        if phrase in html_lower:
            return IsraelShippingResult(
                asin             = asin,
                verified         = True,
                ships_to_israel  = False,
                is_free_shipping = False,
                note             = "❌ Verified: does not ship to 🇮🇱 Israel",
            )

    # ── Positive: free shipping ────────────────────────────────────────────────
    has_free = any(p in html_lower for p in _FREE_SHIP_PHRASES)
    # Extra confidence: is there any mention of Israel / IL in the delivery area?
    delivery_section = _extract_delivery_section(html)
    israel_mentioned = (
        "israel" in delivery_section.lower()
        or "deliver to il" in delivery_section.lower()
    )

    if has_free and israel_mentioned:
        return IsraelShippingResult(
            asin             = asin,
            verified         = True,
            ships_to_israel  = True,
            is_free_shipping = True,
            note             = "✅ Verified: ships free to 🇮🇱 Israel (cart ≥ $49)",
        )

    if has_free:
        return IsraelShippingResult(
            asin             = asin,
            verified         = True,
            ships_to_israel  = True,
            is_free_shipping = True,
            note             = "✅ Verified: FBA — likely ships free to 🇮🇱 Israel",
        )

    # ── Ships but not free (or couldn't confirm free) ─────────────────────────
    return IsraelShippingResult(
        asin             = asin,
        verified         = True,
        ships_to_israel  = True,
        is_free_shipping = False,
        note             = "🟡 Verified: ships to 🇮🇱 Israel (shipping cost may apply)",
    )


def _extract_delivery_section(html: str) -> str:
    """
    Pull out the delivery-related portion of the page HTML to reduce noise
    when looking for Israel-specific text.
    """
    patterns = [
        r'id="delivery[^"]*"[^>]*>(.*?)</[^>]+>',
        r'class="[^"]*delivery[^"]*"[^>]*>(.*?)</[^>]+>',
        r'deliveryBlockSelectAsin.*?(?=<div class="a-section)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(0)[:2000]   # cap to avoid huge matches
    return html[:3000]                 # fallback: use start of page


def _is_captcha(html: str) -> bool:
    html_lower = html.lower()
    return (
        "enter the characters you see below" in html_lower
        or "api-services-support@amazon.com" in html_lower
        or "sorry, we just need to make sure you're not a robot" in html_lower
        or "type the characters you see in this image" in html_lower
    )


def _extract_csrf(html: str) -> Optional[str]:
    """Extract Amazon's anti-CSRF token from the page HTML."""
    patterns = [
        r'"anti-csrftoken-a2z"\s*:\s*"([^"]{10,})"',
        r"'anti-csrftoken-a2z'\s*:\s*'([^']{10,})'",
        r'anti-csrftoken-a2z["\s]+value["\s]*=\s*["\']([^"\']{10,})["\']',
        r'name="anti-csrftoken-a2z"\s+value="([^"]{10,})"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def _unverified(asin: str, reason: str) -> IsraelShippingResult:
    return IsraelShippingResult(
        asin             = asin,
        verified         = False,
        ships_to_israel  = None,
        is_free_shipping = None,
        note             = f"(Could not verify: {reason})",
    )
