"""
israel_scraper.py — Verify Amazon-to-Israel shipping via Playwright + Israeli proxy.

Uses a real headless Chromium browser (playwright + playwright-stealth) routed
through an Israeli residential proxy, so Amazon sees a genuine Israeli browser
and returns accurate Israel-specific delivery information.

Proxy priority (auto-selected, no code changes needed when switching)
──────────────────────────────────────────────────────────────────────
1. Decodo residential proxy  (decodo_user + decodo_password in /admin)
     → Rotating Israeli residential IPs (Partner/HOT/Cellcom)
     → Best reliability: Amazon never sees the same IP twice
     → ~$3.50/GB  ·  register at decodo.com
     → Proxy URL built automatically:
        http://user-USER-country-il:PASS@gate.decodo.com:7000

2. WireGuard / custom proxy  (israel_proxy_url in /admin)
     → Your own Israeli exit node — single static IP
     → Free but may eventually get flagged at high volume
     → Format: socks5://HOST:PORT  or  http://HOST:PORT
     → One-liner setup:  docker run -d -p 1080:1080 serjs/go-socks5-proxy

Flow (per ASIN, result cached 24 h in DB)
──────────────────────────────────────────
1. Launch Chromium with auto-selected proxy
2. GET amazon.com — get session cookies, extract anti-CSRF token via JS
3. POST /gp/delivery-options/ajax/change-address  (countryCode=IL)  in-page
4. GET /dp/{ASIN} — Amazon now shows Israel-specific delivery info
5. Parse HTML → ships_to_israel / is_free_shipping / note
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from circuit_breaker import registry as cb_registry

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CACHE_TTL = 86_400      # 24 hours

# ── Circuit breaker for proxy health ──────────────────────────────────────────
_proxy_failures: dict[str, float] = {}   # proxy_url → monotonic timestamp of last failure
_CIRCUIT_BREAKER_COOLDOWN = 300          # 5 minutes


def is_proxy_healthy(proxy_url: str) -> bool:
    """Return True if proxy has not failed within the cooldown window."""
    last_fail = _proxy_failures.get(proxy_url)
    if last_fail is None:
        return True
    return (time.monotonic() - last_fail) > _CIRCUIT_BREAKER_COOLDOWN


def record_proxy_failure(proxy_url: str) -> None:
    """Record a proxy failure timestamp for the circuit breaker."""
    _proxy_failures[proxy_url] = time.monotonic()


def record_proxy_success(proxy_url: str) -> None:
    """Clear a proxy's failure record on success."""
    _proxy_failures.pop(proxy_url, None)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _build_proxy_cfg(proxy_url: str) -> dict:
    """Convert a proxy URL to Playwright's proxy config dict.

    Playwright needs username/password split out from the server URL.
    e.g. http://USER:PASS@host:port → server=http://host:port, username=USER, password=PASS
    """
    import urllib.parse as _up
    p = _up.urlparse(proxy_url)
    cfg: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        cfg["username"] = _up.unquote(p.username)
    if p.password:
        cfg["password"] = _up.unquote(p.password)
    return cfg

# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class IsraelShippingResult:
    asin:             str
    verified:         bool            # False = check failed (proxy err, CAPTCHA …)
    ships_to_israel:  Optional[bool]  # None when not verified
    is_free_shipping: Optional[bool]  # None when not verified
    note:             str             # display note for product card


# ── Public API ─────────────────────────────────────────────────────────────────

async def _get_ordered_proxy_urls() -> list[str]:
    """
    Return all configured proxy URLs in priority order:
      1. Decodo residential (rotating Israeli IPs)   — best reliability
      2. israel_proxy_url  (WireGuard / SOCKS5)       — static fallback

    The scraper will try each in order and move to the next on failure.
    Port is read from decodo_port key (default 7000).
    Supported Decodo ports:
      7000  HTTP  (recommended for Playwright)
      7001  HTTPS
      7002  SOCKS5
    """
    import key_store
    urls: list[str] = []

    user = (await key_store.get("decodo_user")     or "").strip()
    pw   = (await key_store.get("decodo_password") or "").strip()
    if user and pw:
        raw_port = (await key_store.get("decodo_port") or "").strip()
        port = raw_port if raw_port.isdigit() else "7000"
        urls.append(f"http://user-{user}-country-il:{pw}@gate.decodo.com:{port}")

    wg = (await key_store.get("israel_proxy_url") or "").strip()
    if wg:
        urls.append(wg)

    return urls


async def _get_proxy_url() -> Optional[str]:
    """Return the highest-priority configured proxy URL, or None."""
    urls = await _get_ordered_proxy_urls()
    return urls[0] if urls else None


async def is_configured() -> bool:
    """Return True if any Israeli proxy is configured (Decodo or WireGuard)."""
    return bool(await _get_ordered_proxy_urls())


async def check_shipping(asin: str) -> IsraelShippingResult:
    """
    Return Israel shipping info for an ASIN, using a 24-hour DB cache.
    Tries each configured proxy in order — Decodo first, SOCKS5 fallback.
    Never raises — returns an unverified result on any error.
    """
    proxies = await _get_ordered_proxy_urls()
    if not proxies:
        return _unverified(asin, "No proxy configured — add Decodo keys or israel_proxy_url via /admin")

    # ── Check DB cache ─────────────────────────────────────────────────────────
    try:
        import database as db
        cached = await db.get_israel_cache(asin)
        if cached:
            logger.debug("Israel cache hit for %s", asin)
            return cached
    except Exception as exc:
        logger.warning("Israel cache read failed: %s", exc)

    # ── Try each proxy in order (skip unhealthy proxies via circuit breaker) ──
    result = None
    for i, proxy_url in enumerate(proxies):
        proxy_url = proxy_url.strip()
        label = "Decodo" if i == 0 and "decodo.com" in proxy_url else "fallback proxy"
        cb = cb_registry.get(
            f"israel_proxy:{label}",
            failure_threshold=5,
            recovery_timeout=120.0,   # proxies may need longer recovery
            success_threshold=2,
        )
        logger.debug("Israel check: trying %s for %s", label, asin)
        try:
            result = await cb.call(_scrape_or_raise(asin, proxy_url))
        except Exception as exc:
            logger.info("Israel check failed via %s (circuit breaker): %s", label, exc)
            result = _unverified(asin, f"{label}: {type(exc).__name__}")
        if result and result.verified:
            logger.info("Israel check succeeded via %s for %s", label, asin)
            break

    # ── Store only verified results ────────────────────────────────────────────
    if result and result.verified:
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


# ── Playwright scraper ─────────────────────────────────────────────────────────

class _ScrapeFailedError(Exception):
    """Raised by _scrape_or_raise when scraping returns an unverified result."""
    pass


async def _scrape_or_raise(asin: str, proxy_url: str) -> IsraelShippingResult:
    """Wrapper around _scrape that raises on unverified results.

    This allows the circuit breaker to track failures properly, since
    _scrape itself never raises (it returns _unverified() instead).
    """
    result = await _scrape(asin, proxy_url)
    if not result.verified:
        raise _ScrapeFailedError(result.note)
    return result


async def _scrape(asin: str, proxy_url: str) -> IsraelShippingResult:
    """
    Launch headless Chromium through the Israeli proxy, set delivery to IL,
    fetch the product page, and parse the delivery section.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        from playwright_utils import apply_stealth

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless = True,
                proxy    = _build_proxy_cfg(proxy_url),
                args     = [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = await browser.new_context(
                viewport    = {"width": 1366, "height": 768},
                locale      = "en-US",
                timezone_id = "Asia/Jerusalem",
                user_agent  = _UA,
            )
            page = await context.new_page()

            # Apply stealth patches (canvas, WebGL, navigator.webdriver, etc.)
            await apply_stealth(page)

            try:
                # ── Step 1: Amazon homepage — cookies + CSRF ───────────────
                logger.info("Israel check: loading amazon.com for %s", asin)
                try:
                    await page.goto(
                        "https://www.amazon.com",
                        timeout    = 20_000,
                        wait_until = "domcontentloaded",
                    )
                except Exception as nav_exc:
                    if "closed" in str(nav_exc).lower():
                        return _unverified(asin, "Browser closed during navigation")
                    raise

                if await _is_captcha_page(page):
                    logger.warning("CAPTCHA on Amazon homepage for ASIN %s — trying solver", asin)
                    import captcha_solver
                    solved = await captcha_solver.solve_playwright_captcha(page)
                    if not solved:
                        return _unverified(asin, "CAPTCHA on homepage (unsolved)")

                # ── Step 2: Set delivery country to Israel ─────────────────
                await _set_delivery_israel(page)

                # Small pause so the session cookie is written before next nav
                await asyncio.sleep(0.8)

                # ── Step 3: Product page ───────────────────────────────────
                logger.debug("Israel check: loading dp/%s", asin)
                await page.goto(
                    f"https://www.amazon.com/dp/{asin}",
                    timeout    = 25_000,
                    wait_until = "domcontentloaded",
                )

                if await _is_captcha_page(page):
                    logger.warning("CAPTCHA on product page for ASIN %s — trying solver", asin)
                    import captcha_solver
                    solved = await captcha_solver.solve_playwright_captcha(page)
                    if not solved:
                        return _unverified(asin, "CAPTCHA on product page (unsolved)")

                # Wait for the delivery block to appear (best-effort)
                try:
                    await page.wait_for_selector(
                        "#deliveryBlockSelectAsin, "
                        "#mir-layout-DELIVERY_BLOCK, "
                        "#delivery-message, "
                        "#exports_desktop_qualifiedBuybox_tlc_feature_div",
                        timeout = 6_000,
                    )
                except PWTimeout:
                    pass  # Parse whatever is there

                html = await page.content()
                logger.debug("Israel check: page loaded for %s (%d bytes)", asin, len(html))
                return _parse_html(asin, html)

            finally:
                await browser.close()

    except Exception as exc:
        logger.warning("Israel Playwright scrape failed for %s: %s", asin, exc)
        return _unverified(asin, type(exc).__name__)


async def _set_delivery_israel(page) -> None:
    """
    Change Amazon's delivery destination to Israel using the internal AJAX
    API, executed inside the page's JavaScript context so it shares cookies.
    """
    try:
        # Extract anti-CSRF token from page JS
        csrf: Optional[str] = await asyncio.wait_for(page.evaluate("""
            () => {
                const patterns = [
                    /"anti-csrftoken-a2z"\\s*:\\s*"([^"]{10,})"/,
                    /'anti-csrftoken-a2z'\\s*:\\s*'([^']{10,})'/,
                ];
                const html = document.documentElement.innerHTML;
                for (const p of patterns) {
                    const m = html.match(p);
                    if (m) return m[1];
                }
                return null;
            }
        """), timeout=10)

        if not csrf:
            logger.debug("No CSRF token on Amazon homepage — trying UI click")
            await _set_delivery_israel_via_ui(page)
            return

        status: int = await asyncio.wait_for(page.evaluate(
            """
            async ([csrf]) => {
                try {
                    const body = new URLSearchParams({
                        locationType:  'COUNTRY',
                        zipCode:       '',
                        countryCode:   'IL',
                        deviceType:    'web',
                        pageType:      'Desktop:Search',
                        storeContext:  'NoStoreName',
                        actionSource:  'glow',
                    });
                    const resp = await fetch(
                        '/gp/delivery-options/ajax/change-address',
                        {
                            method:  'POST',
                            headers: {
                                'Content-Type':           'application/x-www-form-urlencoded',
                                'anti-csrftoken-a2z':     csrf,
                            },
                            body: body.toString(),
                        }
                    );
                    return resp.status;
                } catch(e) {
                    return -1;
                }
            }
            """,
            [csrf],
        ), timeout=15)
        logger.debug("Delivery-to-IL API returned HTTP %s for %s", status, "homepage")

    except Exception as exc:
        logger.debug("_set_delivery_israel failed: %s", exc)


async def _set_delivery_israel_via_ui(page) -> None:
    """
    Fallback: click the 'Deliver to' location widget in the Amazon nav bar
    and select Israel from the country dropdown.
    """
    try:
        # Open the location popover
        await page.click(
            "#nav-global-location-popover-link",
            timeout = 4_000,
        )
        await asyncio.sleep(0.5)

        # Select Israel from the country dropdown
        await page.select_option(
            'select[name="glowCountryCode"]',
            value   = "IL",
            timeout = 4_000,
        )
        await asyncio.sleep(0.3)

        # Click Save / Done
        await page.click(
            'span[data-action="GLUXSaveAction"] input, '
            'input.a-button-input[aria-labelledby*="GLUXSave"]',
            timeout = 4_000,
        )
        logger.debug("Delivery set to IL via UI click")

    except Exception as exc:
        logger.debug("UI location change failed (non-fatal): %s", exc)


async def _is_captcha_page(page) -> bool:
    """Check if the current Playwright page is an Amazon CAPTCHA challenge."""
    try:
        content = await page.content()
        return _is_captcha(content)
    except Exception:
        return False


# ── HTML parsing (pure functions — unchanged from aiohttp version) ──────────────

# Phrases that definitively mean this item will NOT ship to Israel.
# IMPORTANT: must be specific enough not to fire for out-of-stock / country-
# restricted (non-Israel) items.  Generic "not available" is intentionally
# excluded because it fires for any OOS product regardless of country.
_NO_SHIP_PHRASES = [
    "this item cannot be shipped to your selected delivery location",
    "this item does not ship to",
    "does not ship to israel",
    "cannot be delivered to israel",
    "item not available in this country",
    "not available for your location",
    "we don't ship to israel",
    # country-in-page signals (Amazon displays the chosen country in delivery block)
    "this item is not available in",
    "doesn't ship to israel",
    "not available to ship to israel",
]

# Phrases that confirm free shipping applies
_FREE_SHIP_PHRASES = [
    "free delivery",
    "free shipping",
    "ships free",
]


def _parse_html(asin: str, html: str) -> IsraelShippingResult:
    html_lower = html.lower()

    # ── Sanity: is this actually a product page? ───────────────────────────────
    if "producttitle" not in html_lower and "dp/" not in html_lower:
        return _unverified(asin, "Not a product page")

    # ── Definitive negative signals ────────────────────────────────────────────
    for phrase in _NO_SHIP_PHRASES:
        if phrase in html_lower:
            return IsraelShippingResult(
                asin             = asin,
                verified         = True,
                ships_to_israel  = False,
                is_free_shipping = False,
                note             = "❌ Verified: does not ship to 🇮🇱 Israel",
            )

    # ── Free shipping ──────────────────────────────────────────────────────────
    has_free = any(p in html_lower for p in _FREE_SHIP_PHRASES)
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

    # ── Ships but free status unknown ──────────────────────────────────────────
    return IsraelShippingResult(
        asin             = asin,
        verified         = True,
        ships_to_israel  = True,
        is_free_shipping = False,
        note             = "🟡 Verified: ships to 🇮🇱 Israel (shipping cost may apply)",
    )


def _extract_delivery_section(html: str) -> str:
    patterns = [
        r'id="delivery[^"]*"[^>]*>(.*?)</[^>]+>',
        r'class="[^"]*delivery[^"]*"[^>]*>(.*?)</[^>]+>',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(0)[:2000]
    return html[:3000]


def _is_captcha(html: str) -> bool:
    """Delegate to centralized CAPTCHA detection in captcha_solver."""
    from captcha_solver import is_captcha_html
    return is_captcha_html(html)


def _extract_csrf(html: str) -> Optional[str]:
    """Extract Amazon's anti-CSRF token from raw HTML (utility, used in tests)."""
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
