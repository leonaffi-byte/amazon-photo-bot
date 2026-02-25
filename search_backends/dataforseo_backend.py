"""
DataForSEO Amazon SERP backend.

Pay-per-use: ~$0.003 per search, no monthly subscription.
Deposit $5 at app.dataforseo.com → good for ~1,600 searches.

Sign up:   https://app.dataforseo.com/register
API docs:  https://docs.dataforseo.com/v3/serp/amazon/organic/live/advanced/

Authentication:
  DataForSEO uses HTTP Basic Auth.
  login    → your DataForSEO account email
  password → your DataForSEO API password  (Dashboard → API Access → Password)

Why this is better than RapidAPI for our use case:
  • Pay only for what you use — no monthly commitment
  • Returns is_prime correctly (RapidAPI always returns False in search)
  • Returns seller name — "Amazon.com" means sold AND shipped by Amazon
  • delivery_info array contains the same "shipped by Amazon" signal we rely on
  • Stable, well-documented API with SLAs
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Optional

import aiohttp

from search_backends.base import AmazonItem, SearchBackend

logger = logging.getLogger(__name__)

LIVE_URL    = "https://api.dataforseo.com/v3/serp/amazon/organic/live/advanced"
LOCATION_US = 2840   # United States
LANGUAGE_EN = "en"


class DataForSEOBackend(SearchBackend):

    def __init__(self, login: str, password: str) -> None:
        self._login    = login
        self._password = password
        creds          = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._headers  = {
            "Authorization": f"Basic {creds}",
            "Content-Type":  "application/json",
        }

    @property
    def name(self) -> str:
        return "DataForSEO / Amazon SERP"

    async def search(self, query: str, max_results: int = 20, page: int = 1) -> list[AmazonItem]:
        """
        Search Amazon via DataForSEO live endpoint (~$0.003/call).

        page=1 → offset 0, page=2 → offset max_results, etc.
        """
        offset = (page - 1) * max_results

        payload = [{
            "keyword":       query,
            "location_code": LOCATION_US,
            "language_code": LANGUAGE_EN,
            "device":        "desktop",
            "depth":         max_results,
            **({"offset": offset} if offset > 0 else {}),
        }]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LIVE_URL,
                headers = self._headers,
                json    = payload,
                timeout = aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"DataForSEO error {resp.status}: {text[:200]}")
                data = await resp.json()

        raw_items = _extract_items(data, query)
        logger.info("DataForSEO returned %d products for query '%s'", len(raw_items), query)

        items: list[AmazonItem] = []
        for raw in raw_items[:max_results]:
            item = self._parse_item(raw)
            if item:
                items.append(item)

        items.sort(key=lambda i: i.score, reverse=True)
        return items

    # ── Item parser ───────────────────────────────────────────────────────────

    def _parse_item(self, raw: dict) -> Optional[AmazonItem]:
        if not raw or not isinstance(raw, dict):
            return None
        try:
            # ── ASIN ──────────────────────────────────────────────────────────
            asin = raw.get("data_asin") or ""
            if not asin:
                url = raw.get("url") or ""
                m   = re.search(r"/dp/([A-Z0-9]{10})", url)
                if m:
                    asin = m.group(1)
                else:
                    return None

            title = (raw.get("title") or "").strip()
            if not title:
                return None

            # ── Price ─────────────────────────────────────────────────────────
            price_usd: Optional[float] = None
            for field in ("price_from", "price_to"):
                val = raw.get(field)
                if val is not None:
                    try:
                        price_usd = float(val)
                        break
                    except (TypeError, ValueError):
                        pass

            # ── Rating ────────────────────────────────────────────────────────
            rating: Optional[float]    = None
            review_count: Optional[int] = None
            rating_data = raw.get("rating") or {}
            if isinstance(rating_data, dict):
                try:
                    rating = float(rating_data.get("value") or 0) or None
                except (TypeError, ValueError):
                    pass
                try:
                    rc           = rating_data.get("votes_count")
                    review_count = int(rc) if rc else None
                except (TypeError, ValueError):
                    pass

            # ── Image ─────────────────────────────────────────────────────────
            image_url: Optional[str] = raw.get("image_url")

            # ── Fulfillment / Israel shipping detection ────────────────────────
            # DataForSEO returns is_prime correctly (unlike RapidAPI search).
            is_prime = bool(raw.get("is_prime", False))

            # delivery_info: list of strings, e.g.:
            #   ["FREE delivery Fri, Feb 28",
            #    "Or fastest delivery Thu, Feb 27",
            #    "Ships from and sold by Amazon.com"]
            delivery_list = raw.get("delivery_info") or []
            delivery_text = " ".join(str(d) for d in delivery_list).lower()

            is_shipped_by_amazon = (
                "shipped by amazon" in delivery_text
                or "fulfilled by amazon" in delivery_text
            )

            # seller field: "Amazon.com" = sold AND shipped by Amazon
            seller = (raw.get("seller") or "").lower().strip()
            is_sold_by_amazon = seller in ("amazon.com", "amazon") or "amazon.com" in seller

            is_amazon_fulfilled = is_shipped_by_amazon or is_sold_by_amazon

            return AmazonItem(
                asin               = asin,
                title              = title,
                image_url          = image_url,
                price_usd          = price_usd,
                currency           = "USD",
                rating             = rating,
                review_count       = review_count,
                is_amazon_fulfilled= is_amazon_fulfilled,
                is_sold_by_amazon  = is_sold_by_amazon,
                is_prime           = is_prime,
                availability       = "In Stock",
            )
        except Exception as exc:
            logger.warning("DataForSEO item parse error for %s: %s", raw.get("data_asin", "?"), exc)
            return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_items(data: dict, query: str) -> list[dict]:
    """Unpack DataForSEO's nested tasks→result→items structure."""
    items: list[dict] = []
    for task in data.get("tasks", []):
        code = task.get("status_code")
        if code != 20000:
            logger.warning(
                "DataForSEO task error %s for '%s': %s",
                code, query, task.get("status_message", ""),
            )
            continue
        for result in task.get("result", []) or []:
            for item in result.get("items", []) or []:
                if item.get("type") == "amazon_serp":
                    items.append(item)
    return items
