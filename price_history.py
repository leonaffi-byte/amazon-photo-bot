"""
price_history.py — Historical Amazon price data for product cards.

Backends (tried in order, first success wins):
  1. CamelCamelCamel — Playwright + Decodo proxy (~5-8s, handles Cloudflare)
  2. Keepa           — Playwright + Decodo proxy, intercepts the XHR that
                       the Keepa website makes to its own API backend (~6-10s)

If both fail the caller receives None and nothing is shown in the card.
Results are cached 6 hours in the DB (price history doesn't change that fast).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

from bs4 import BeautifulSoup

import database as db

logger = logging.getLogger(__name__)

_CCC_URL     = "https://camelcamelcamel.com/product/{asin}"
_KEEPA_URL   = "https://keepa.com/#!product/1-{asin}"     # 1 = amazon.com
_CACHE_TTL   = 6 * 3600   # seconds




# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PriceHistory:
    asin:         str
    source:       str             # "camelcamelcamel" | "keepa"
    current:      Optional[float] = None   # current Amazon price
    low_all_time: Optional[float] = None
    avg_90d:      Optional[float] = None
    avg_30d:      Optional[float] = None
    low_90d:      Optional[float] = None

    @property
    def deal_label(self) -> str:
        """Return a short deal quality string, or empty string."""
        if not self.current:
            return ""
        if self.low_all_time and self.current <= self.low_all_time * 1.03:
            return "🔥 All\\-time low"
        if self.avg_90d and self.current <= self.avg_90d * 0.85:
            return "💸 Great deal"
        if self.avg_90d and self.current <= self.avg_90d * 0.95:
            return "✅ Below avg"
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

async def get_price_history(asin: str) -> Optional[PriceHistory]:
    """
    Return price history for an ASIN, or None if unavailable.
    Tries CamelCamelCamel first, then Keepa. Results cached 6h.
    Never raises.
    """
    try:
        cached = await db.get_price_cache(asin)
        if cached:
            logger.debug("Price cache hit for %s", asin)
            return cached
    except Exception as exc:
        logger.debug("Price cache read error: %s", exc)

    result = (
        await _from_camelcamelcamel(asin)
        or await _from_keepa(asin)
    )

    if result:
        try:
            await db.set_price_cache(asin, result)
        except Exception as exc:
            logger.debug("Price cache write error: %s", exc)

    return result


# ── Shared Playwright fetcher ─────────────────────────────────────────────────

def _build_proxy_cfg(proxy_url: str) -> Optional[dict]:
    """Convert a proxy URL string to Playwright's proxy config dict."""
    if not proxy_url:
        return None
    try:
        import urllib.parse as _up
        p = _up.urlparse(proxy_url)
        return {
            "server":   f"{p.scheme}://{p.hostname}:{p.port}",
            "username": p.username or "",
            "password": p.password or "",
        }
    except Exception:
        return None


async def _fetch_rendered_html(url: str, timeout_ms: int = 15_000) -> Optional[str]:
    """
    Fetch a URL using Playwright headless Chrome.
    Tries each configured proxy in order (Decodo → SOCKS5 → no proxy).
    Handles Cloudflare JS challenges that block plain aiohttp requests.
    Returns the fully rendered HTML, or None on any failure.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_utils import apply_stealth
        from israel_scraper import _get_ordered_proxy_urls, is_proxy_healthy, record_proxy_failure, record_proxy_success
        proxy_urls = await _get_ordered_proxy_urls()
        # Try each proxy; fall back to no-proxy as last resort.
        # Skip proxies marked unhealthy by the circuit breaker.
        proxy_cfgs = [
            _build_proxy_cfg(p) for p in proxy_urls if is_proxy_healthy(p.strip())
        ] + [None]

        async with async_playwright() as pw:
            for proxy_cfg in proxy_cfgs:
                label = proxy_cfg["server"] if proxy_cfg else "no-proxy"
                try:
                    browser = await pw.chromium.launch(
                        headless = True,
                        proxy    = proxy_cfg,
                        args     = ["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    ctx  = await browser.new_context(
                        locale      = "en-US",
                        timezone_id = "America/New_York",
                        viewport    = {"width": 1280, "height": 800},
                        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    )
                    page = await ctx.new_page()
                    await apply_stealth(page)

                    html = ""
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                        html = await page.content()
                    except Exception:
                        try:
                            html = await page.content()
                        except Exception:
                            pass
                    await browser.close()

                    if html:
                        logger.debug("Fetched %s via %s (%d chars)", url[:50], label, len(html))
                        return html
                    logger.debug("Empty HTML via %s — trying next proxy", label)
                except Exception as exc:
                    logger.debug("Playwright fetch via %s failed: %s — trying next", label, exc)
                    try:
                        await browser.close()
                    except Exception:
                        pass

        return None
    except Exception as exc:
        logger.debug("Playwright fetch failed for %s: %s", url, exc)
        return None


# ── Backend 1: CamelCamelCamel ────────────────────────────────────────────────

async def _from_camelcamelcamel(asin: str) -> Optional[PriceHistory]:
    """
    Scrape camelcamelcamel.com/product/ASIN using Playwright + Decodo proxy.
    CCC is behind Cloudflare JS challenge so plain aiohttp returns 403.
    Parses the Amazon-price stats section (Current, Lowest, Average).
    """
    url  = _CCC_URL.format(asin=asin)
    html = await _fetch_rendered_html(url)
    if not html:
        return None
    return _parse_ccc_html(asin, html)


def _parse_ccc_html(asin: str, html: str) -> Optional[PriceHistory]:
    """
    Parse CamelCamelCamel HTML.
    CCC shows a stats table for each seller type (Amazon / New / Used).
    We target the Amazon section first.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # ── Strategy 1: find the Amazon-specific stats section ─────────────
        # CCC wraps each chart in a div/section with id containing "amazon"
        amazon_section = (
            soup.find(id=re.compile(r"amazon", re.I))
            or soup.find(class_=re.compile(r"amazon", re.I))
        )

        prices: dict[str, float] = {}

        if amazon_section:
            prices = _extract_stats_from_section(amazon_section)

        # ── Strategy 2: scan entire page for labelled price rows ───────────
        if not prices:
            prices = _extract_stats_fullpage(soup)

        if not prices:
            logger.debug("CCC: no price data found for %s", asin)
            return None

        ph = PriceHistory(
            asin         = asin,
            source       = "camelcamelcamel",
            current      = prices.get("current"),
            low_all_time = prices.get("lowest") or prices.get("low"),
            avg_90d      = prices.get("average") or prices.get("avg"),
            avg_30d      = prices.get("avg30") or prices.get("30 day"),
            low_90d      = prices.get("low90") or prices.get("90 day low"),
        )
        logger.info("CCC price for %s: current=%s ATL=%s avg90=%s",
                    asin, ph.current, ph.low_all_time, ph.avg_90d)
        return ph

    except Exception as exc:
        logger.warning("CCC parse error for %s: %s", asin, exc)
        return None


def _extract_stats_from_section(section) -> dict[str, float]:
    """Extract labelled price rows from a BeautifulSoup element."""
    prices: dict[str, float] = {}
    # Each stat row usually has a label in a <th> or .name and a value with a $ sign
    rows = section.find_all(["tr", "li", "div"], recursive=True)
    for row in rows:
        text = row.get_text(" ", strip=True)
        label, value = _label_and_price(text)
        if label and value is not None:
            prices[label] = value
    return prices


def _extract_stats_fullpage(soup) -> dict[str, float]:
    """
    Fallback: scan the full page for lines that contain a dollar amount
    next to a recognisable label (Current, Lowest, Average…).
    """
    prices: dict[str, float] = {}
    for elem in soup.find_all(string=re.compile(r"\$\d")):
        text = elem.strip()
        label, value = _label_and_price(text)
        if label and value is not None:
            prices.setdefault(label, value)   # first match wins
    return prices


_LABEL_RE = re.compile(
    r"(current|lowest|low|highest|high|average|avg|30[\s\-]?day|90[\s\-]?day)",
    re.I,
)
_PRICE_RE = re.compile(r"\$(\d[\d,]*(?:\.\d{1,2})?)")


def _label_and_price(text: str):
    """
    From a string like "Lowest $24.99 on Jan 2024"
    return ("lowest", 24.99).  Returns (None, None) if no match.
    """
    label_m = _LABEL_RE.search(text)
    price_m = _PRICE_RE.search(text)
    if label_m and price_m:
        label = label_m.group(1).lower().replace(" ", "").replace("-", "")
        value = float(price_m.group(1).replace(",", ""))
        return label, value
    return None, None


# ── Backend 2: Keepa (Playwright XHR intercept) ───────────────────────────────

async def _from_keepa(asin: str) -> Optional[PriceHistory]:
    """
    Load keepa.com/#!product/1-ASIN in Playwright (Decodo proxy),
    intercept the XHR that Keepa's JS makes to api.keepa.com,
    and parse the JSON response — same format as Keepa's paid API.
    Falls back gracefully if Playwright or proxy is unavailable.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_utils import apply_stealth
        from israel_scraper import _get_ordered_proxy_urls, is_proxy_healthy
        proxy_urls  = await _get_ordered_proxy_urls()
        proxy_cfgs  = [
            _build_proxy_cfg(p) for p in proxy_urls if is_proxy_healthy(p.strip())
        ] + [None]

        captured: list[dict] = []

        async with async_playwright() as pw:
            for proxy_cfg in proxy_cfgs:
                label = proxy_cfg["server"] if proxy_cfg else "no-proxy"
                captured.clear()
                try:
                    browser = await pw.chromium.launch(
                        headless = True,
                        proxy    = proxy_cfg,
                        args     = ["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    ctx  = await browser.new_context(
                        locale      = "en-US",
                        timezone_id = "America/New_York",
                        viewport    = {"width": 1280, "height": 800},
                    )
                    page = await ctx.new_page()
                    await apply_stealth(page)

                    async def _on_response(response):
                        if "api.keepa.com/product" in response.url and response.status == 200:
                            try:
                                body = await response.json()
                                captured.append(body)
                            except Exception:
                                pass

                    page.on("response", _on_response)
                    url = _KEEPA_URL.format(asin=asin)
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=20_000)
                    except Exception:
                        pass
                    await browser.close()

                    if captured:
                        logger.debug("Keepa XHR captured via %s", label)
                        break
                    logger.debug("Keepa: no XHR via %s — trying next proxy", label)
                except Exception as exc:
                    logger.debug("Keepa via %s failed: %s", label, exc)
                    try:
                        await browser.close()
                    except Exception:
                        pass

        if not captured:
            logger.debug("Keepa: no XHR captured for %s", asin)
            return None

        return _parse_keepa_response(asin, captured[0])

    except Exception as exc:
        logger.debug("Keepa scrape failed for %s: %s", asin, exc)
        return None


def _parse_keepa_response(asin: str, data: dict) -> Optional[PriceHistory]:
    """
    Parse the JSON returned by Keepa's internal API.
    Prices are in US cents (divide by 100). -1 = not available / out of stock.
    Timestamps are "Keepa minutes" since 2011-01-01.
    The `stats` object (when present) pre-computes current/avg30/avg90/atl.
    """
    try:
        products = data.get("products") or []
        if not products:
            return None
        prod = products[0]
        stats = prod.get("stats") or {}

        def _cents(val) -> Optional[float]:
            """Convert Keepa cents to USD float, or None if invalid."""
            try:
                v = int(val)
                return round(v / 100, 2) if v > 0 else None
            except (TypeError, ValueError):
                return None

        # stats.current / avg30 / avg90 / atl are arrays indexed by
        # price type: 0=Amazon, 1=New, 2=Used, 3=Collectible, 9=Sales rank
        current      = _cents(_idx(stats.get("current"),      0))
        avg_30d      = _cents(_idx(stats.get("avg30"),        0))
        avg_90d      = _cents(_idx(stats.get("avg90"),        0))
        low_all_time = _cents(_idx(stats.get("atl"),          0))
        low_90d      = _cents(_idx(stats.get("min90"),        0))

        # If stats are missing, derive from raw csv[0] (Amazon price history)
        if not any([current, avg_90d, low_all_time]):
            csv0 = (prod.get("csv") or [None])[0]
            if csv0:
                current, avg_90d, low_all_time, avg_30d, low_90d = (
                    _derive_from_csv(csv0)
                )

        if not any([current, avg_90d, low_all_time]):
            return None

        ph = PriceHistory(
            asin         = asin,
            source       = "keepa",
            current      = current,
            low_all_time = low_all_time,
            avg_90d      = avg_90d,
            avg_30d      = avg_30d,
            low_90d      = low_90d,
        )
        logger.info("Keepa price for %s: current=%s ATL=%s avg90=%s",
                    asin, ph.current, ph.low_all_time, ph.avg_90d)
        return ph

    except Exception as exc:
        logger.warning("Keepa parse error for %s: %s", asin, exc)
        return None


def _idx(lst, i, default=None):
    """Safe list index — returns default if list is None or too short."""
    try:
        return lst[i]
    except (TypeError, IndexError):
        return default


def _derive_from_csv(csv: list) -> tuple:
    """
    Derive price stats from Keepa's raw csv array.
    Format: [keepa_minute, price_cents, keepa_minute, price_cents, ...]
    Returns (current, avg_90d, low_all_time, avg_30d, low_90d).
    """
    now_km   = int((time.time() - 1325376000) / 60)   # now in keepa-minutes
    km_30    = now_km - 30 * 24 * 60
    km_90    = now_km - 90 * 24 * 60

    prices_90: list[float] = []
    prices_30: list[float] = []
    all_prices: list[float] = []
    latest: Optional[float] = None

    for i in range(0, len(csv) - 1, 2):
        km  = csv[i]
        raw = csv[i + 1]
        if raw is None or raw < 0:
            continue
        price = raw / 100
        all_prices.append(price)
        if km >= km_90:
            prices_90.append(price)
        if km >= km_30:
            prices_30.append(price)
        latest = price   # last entry = most recent

    def _safe_avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    return (
        latest,
        _safe_avg(prices_90),
        min(all_prices) if all_prices else None,
        _safe_avg(prices_30),
        min(prices_90)  if prices_90  else None,
    )
