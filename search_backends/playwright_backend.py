"""
search_backends/playwright_backend.py — Scrape Amazon search results directly.

No API key required.  Uses headless Chromium (Playwright + playwright-stealth)
routed through the Israeli WireGuard proxy so Amazon shows Israeli-relevant
delivery info.  CAPTCHAs are solved automatically via CapSolver if configured.

Advantages over API backends:
  ✅ Free — no per-search cost, no monthly quota
  ✅ Israel-aware — proxy gives real Israeli delivery text
  ✅ Fresh data — directly from Amazon, not cached by a middleman
  ✅ No rate limits (within reason)

Trade-off:
  ~3–5 s per search (Chromium launch overhead); slower than API backends
  but runs in background — acceptable for a Telegram bot.

Activation:
  SEARCH_BACKEND=playwright in .env
  or auto mode when no API keys are set (lowest priority fallback)

Optional but recommended:
  israel_proxy_url  →  socks5://YOUR_ISRAEL_IP:1080
  capsolver_api_key →  from capsolver.com  (~$0.80/1000 CAPTCHAs)
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

from search_backends.base import AmazonItem, SearchBackend

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# JS that extracts all search result data in one evaluate() call (fast)
_EXTRACT_JS = """
() => {
    const results = [];
    const containers = document.querySelectorAll(
        '[data-component-type="s-search-result"]'
    );

    for (const c of containers) {
        const asin = c.getAttribute('data-asin');
        if (!asin || asin.length < 10) continue;

        // Title
        const titleEl = c.querySelector('h2 a span, h2 span');
        const title = titleEl ? titleEl.textContent.trim() : '';
        if (!title) continue;

        // Price (screen-reader offscreen text is most reliable: "$29.99")
        const priceEl = c.querySelector('.a-price .a-offscreen');
        const priceText = priceEl ? priceEl.textContent.trim() : '';

        // Rating text: "4.5 out of 5 stars"
        const ratingEl = c.querySelector('.a-icon-alt');
        const ratingText = ratingEl ? ratingEl.textContent.trim() : '';

        // Review count
        const reviewEl = c.querySelector(
            '[aria-label*="stars"] ~ * .a-size-base,' +
            '.s-link-style .a-size-base,' +
            'span[aria-label*="ratings"]'
        );
        const reviewText = reviewEl
            ? (reviewEl.getAttribute('aria-label') || reviewEl.textContent).trim()
            : '';

        // Product image
        const imgEl = c.querySelector('.s-image, img.s-image');
        const imageUrl = imgEl ? (imgEl.getAttribute('src') || '') : '';

        // Prime badge
        const isPrime = !!c.querySelector('.a-icon-prime, .s-prime');

        // Delivery text (multiple possible containers)
        const deliveryEl = c.querySelector(
            '[data-cy="delivery-recipe-container"],' +
            '.s-align-children-center,' +
            '[class*="s-delivery"]'
        );
        const deliveryText = deliveryEl ? deliveryEl.textContent.trim() : '';

        // Seller info
        const sellerEl = c.querySelector('.s-merchant-info, [class*="merchant"]');
        const sellerText = sellerEl ? sellerEl.textContent.trim() : '';

        // Link
        const linkEl = c.querySelector('h2 a[href]');
        const href = linkEl ? linkEl.getAttribute('href') : '';

        results.push({
            asin, title, priceText, ratingText, reviewText,
            imageUrl, isPrime, deliveryText, sellerText, href
        });
    }
    return results;
}
"""


class PlaywrightBackend(SearchBackend):
    """
    Scrape Amazon.com search results via headless Chromium.
    Optionally routes through an Israeli SOCKS5/HTTP proxy.
    """

    def __init__(self) -> None:
        # proxy_url resolved at search time from key_store
        pass

    @property
    def name(self) -> str:
        return "Playwright / Amazon Direct Scraper"

    async def search(
        self,
        query: str,
        max_results: int = 20,
        page: int = 1,
    ) -> list[AmazonItem]:
        from israel_scraper import _get_proxy_url
        proxy_url = await _get_proxy_url()
        # proxy_url may be None — Playwright still works without a proxy,
        # just without Israel-specific delivery context

        try:
            from playwright.async_api import async_playwright, TimeoutError as PWTimeout
            from playwright_utils import apply_stealth

            async with async_playwright() as pw:
                launch_args = {
                    "headless": True,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                }
                if proxy_url:
                    launch_args["proxy"] = {"server": proxy_url}

                browser = await pw.chromium.launch(**launch_args)
                context = await browser.new_context(
                    viewport    = {"width": 1366, "height": 768},
                    locale      = "en-US",
                    timezone_id = "Asia/Jerusalem",
                    user_agent  = _UA,
                )
                page_obj = await context.new_page()

                await apply_stealth(page_obj)

                try:
                    items = await self._run_search(page_obj, query, page, max_results)
                finally:
                    await browser.close()

                return items

        except Exception as exc:
            logger.error("PlaywrightBackend search failed: %s", exc)
            raise RuntimeError(f"Playwright search failed: {exc}") from exc

    async def _run_search(self, page, query: str, page_num: int, max_results: int) -> list[AmazonItem]:
        """Navigate to Amazon search results and parse them."""
        from playwright.async_api import TimeoutError as PWTimeout
        import captcha_solver as cs

        url = f"https://www.amazon.com/s?k={quote_plus(query)}&page={page_num}"
        logger.info("[Playwright] Searching: %s (page %d)", query, page_num)

        # ── Load search results page ───────────────────────────────────────────
        try:
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        except PWTimeout:
            raise RuntimeError("Amazon search page timed out")

        # ── Solve CAPTCHA if present ───────────────────────────────────────────
        captcha_form = await page.query_selector('form[action="/errors/validateCaptcha"]')
        if captcha_form:
            logger.info("[Playwright] CAPTCHA detected on search page")
            solved = await cs.solve_playwright_captcha(page)
            if not solved:
                raise RuntimeError("CAPTCHA on Amazon search page could not be solved")

        # ── Wait for result cards ──────────────────────────────────────────────
        try:
            await page.wait_for_selector(
                '[data-component-type="s-search-result"]',
                timeout=10_000,
            )
        except PWTimeout:
            # Check if "no results" page
            content = await page.content()
            if "no results for" in content.lower() or "did not match" in content.lower():
                logger.info("[Playwright] No results found for '%s'", query)
                return []
            raise RuntimeError("Search result cards did not appear")

        # ── Extract data via JS ────────────────────────────────────────────────
        raw_items: list[dict] = await page.evaluate(_EXTRACT_JS)
        logger.info("[Playwright] Found %d raw results for '%s'", len(raw_items), query)

        # ── Parse into AmazonItem objects ──────────────────────────────────────
        items: list[AmazonItem] = []
        for raw in raw_items:
            item = _parse_result(raw)
            if item:
                items.append(item)
            if len(items) >= max_results:
                break

        logger.info("[Playwright] Parsed %d valid items", len(items))
        return items


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_result(raw: dict) -> Optional[AmazonItem]:
    """Convert raw JS-extracted dict into an AmazonItem."""
    asin  = raw.get("asin", "").strip()
    title = raw.get("title", "").strip()

    if not asin or len(asin) < 10 or not title:
        return None

    # ── Price ──────────────────────────────────────────────────────────────────
    price_usd: Optional[float] = None
    price_text = raw.get("priceText", "").replace(",", "")
    m = re.search(r"(\d+\.?\d*)", price_text)
    if m:
        try:
            price_usd = float(m.group(1))
        except ValueError:
            pass

    # ── Rating ─────────────────────────────────────────────────────────────────
    rating: Optional[float] = None
    m = re.search(r"([\d.]+)\s+out\s+of", raw.get("ratingText", ""))
    if m:
        try:
            rating = float(m.group(1))
        except ValueError:
            pass

    # ── Review count ───────────────────────────────────────────────────────────
    review_count: Optional[int] = None
    review_src = raw.get("reviewText", "")
    m = re.search(r"([\d,]+)", review_src)
    if m:
        try:
            review_count = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # ── Delivery / fulfillment ─────────────────────────────────────────────────
    delivery_text    = raw.get("deliveryText", "").lower()
    is_prime         = bool(raw.get("isPrime", False))
    seller_text      = raw.get("sellerText", "").lower()

    is_amazon_fulfilled = (
        "shipped by amazon" in delivery_text
        or "fulfilled by amazon" in delivery_text
    )
    is_sold_by_amazon = (
        "amazon.com" in seller_text
        or "sold by amazon" in seller_text
    )

    # Prime from delivery text (e.g. "prime members get free delivery")
    if "prime members" in delivery_text:
        is_prime = True

    # ── Image ──────────────────────────────────────────────────────────────────
    image_url = raw.get("imageUrl") or None
    if image_url and not image_url.startswith("http"):
        image_url = None

    # ── Clean URL ──────────────────────────────────────────────────────────────
    href = raw.get("href", "")
    if href and not href.startswith("http"):
        href = "https://www.amazon.com" + href

    free_delivery_likely = (
        "free delivery" in delivery_text
        or "free shipping" in delivery_text
        or is_amazon_fulfilled
        or is_prime
    )

    return AmazonItem(
        asin                = asin,
        title               = title,
        image_url           = image_url,
        price_usd           = price_usd,
        currency            = "USD",
        rating              = rating,
        review_count        = review_count,
        is_amazon_fulfilled = is_amazon_fulfilled,
        is_sold_by_amazon   = is_sold_by_amazon,
        is_prime            = is_prime,
        availability        = "In Stock",
        free_delivery_likely= free_delivery_likely,
    )
