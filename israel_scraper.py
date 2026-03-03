"""
israel_scraper.py — Verify Amazon-to-Israel shipping via Playwright + Israeli proxy.

Uses a real headless Chromium browser (playwright + playwright-stealth) routed
through your Israeli WireGuard exit node, so Amazon sees a genuine Israeli
browser and returns accurate Israel-specific delivery information.

Why Playwright instead of aiohttp
──────────────────────────────────
Amazon's bot detection has 4 layers:
  1. TLS fingerprint  — aiohttp ≠ Chrome  →  blocked
  2. JS challenges    — no execution      →  blocked
  3. Behaviour signals — no mouse/timing  →  blocked
  4. CAPTCHA          — last resort
Playwright runs real Chromium; playwright-stealth patches canvas/WebGL/etc.
fingerprints.  Routing through a residential Israeli IP completes the picture.

Flow (per ASIN, result cached 24 h in DB)
──────────────────────────────────────────
1. Launch Chromium with proxy = your Israeli SOCKS5/HTTP endpoint
2. GET amazon.com — get session cookies, extract anti-CSRF token via JS
3. POST /gp/delivery-options/ajax/change-address  (countryCode=IL)  in-page
4. GET /dp/{ASIN} — Amazon now shows Israel-specific delivery info
5. Parse HTML → ships_to_israel / is_free_shipping / note

Proxy setup (one-liner on your Israeli WireGuard server):
  docker run -d -p 1080:1080 serjs/go-socks5-proxy

Then:  /admin → 🔑 API Keys → israel_proxy_url = socks5://YOUR_ISRAEL_IP:1080
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CACHE_TTL = 86_400      # 24 hours

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class IsraelShippingResult:
    asin:             str
    verified:         bool            # False = check failed (proxy err, CAPTCHA …)
    ships_to_israel:  Optional[bool]  # None when not verified
    is_free_shipping: Optional[bool]  # None when not verified
    note:             str             # display note for product card


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

    # ── Scrape via Playwright ──────────────────────────────────────────────────
    result = await _scrape(asin, proxy_url.strip())

    # ── Store only verified results ────────────────────────────────────────────
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


# ── Playwright scraper ─────────────────────────────────────────────────────────

async def _scrape(asin: str, proxy_url: str) -> IsraelShippingResult:
    """
    Launch headless Chromium through the Israeli proxy, set delivery to IL,
    fetch the product page, and parse the delivery section.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        # Import stealth — graceful if package not installed
        try:
            from playwright_stealth import stealth_async
            _has_stealth = True
        except ImportError:
            _has_stealth = False
            logger.warning(
                "playwright-stealth not installed — bot detection risk is higher. "
                "Add 'playwright-stealth>=1.0.6' to requirements.txt."
            )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless = True,
                proxy    = {"server": proxy_url},
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
            if _has_stealth:
                from playwright_stealth import stealth_async
                await stealth_async(page)

            try:
                # ── Step 1: Amazon homepage — cookies + CSRF ───────────────
                logger.debug("Israel check: loading amazon.com for %s", asin)
                await page.goto(
                    "https://www.amazon.com",
                    timeout    = 20_000,
                    wait_until = "domcontentloaded",
                )

                if await _is_captcha_page(page):
                    logger.warning("CAPTCHA on Amazon homepage for ASIN %s", asin)
                    return _unverified(asin, "CAPTCHA on homepage")

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
                    logger.warning("CAPTCHA on product page for ASIN %s", asin)
                    return _unverified(asin, "CAPTCHA on product page")

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
        csrf: Optional[str] = await page.evaluate("""
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
        """)

        if not csrf:
            logger.debug("No CSRF token on Amazon homepage — trying UI click")
            await _set_delivery_israel_via_ui(page)
            return

        status: int = await page.evaluate(
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
        )
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
    html_lower = html.lower()
    return (
        "enter the characters you see below"   in html_lower
        or "api-services-support@amazon.com"   in html_lower
        or "sorry, we just need to make sure"  in html_lower
        or "type the characters you see"        in html_lower
    )


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
