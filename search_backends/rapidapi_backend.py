"""
RapidAPI "Real-Time Amazon Data" backend.

Sign up at: https://rapidapi.com/search/amazon
Recommended API: "Real-Time Amazon Data" by Axesso (or any equivalent)
  https://rapidapi.com/letscrape-6bRBa3QguO5/api/real-time-amazon-data

Why this one:
  • Free tier: 100 searches/month (enough to test and start)
  • Paid: ~$9/month for 1,000 searches / pay-as-you-go ~$0.005/req
  • No Amazon relationship required — just a RapidAPI account
  • Returns: ASIN, title, price, rating, review count, image, Prime flag,
    delivery text, seller name — everything we need

Israel free-delivery detection (empirically verified):
  • delivery text contains "shipped by Amazon"
      → definitive FBA signal. Amazon writes:
        "FREE delivery Mon, Mar 2 on $35 of items shipped by Amazon"
        for FBA items and simply "FREE delivery Mon, Mar 2" for 3P items.
      → FBA items are eligible for Amazon's international shipping to 🇮🇱 Israel.
  • "sold by Amazon" in seller name  → Amazon Retail, 100% qualifies
  • is_prime from API / "Prime members" in delivery text → fallback proxy
  • Plain "FREE delivery …" WITHOUT "shipped by Amazon" → 3P domestic only
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp

from search_backends.base import AmazonItem, SearchBackend

logger = logging.getLogger(__name__)

# ── API constants ──────────────────────────────────────────────────────────────
# This is the "Real-Time Amazon Data" host — update if you use a different API
RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
SEARCH_URL    = f"https://{RAPIDAPI_HOST}/search"
PRODUCT_URL   = f"https://{RAPIDAPI_HOST}/product-details"


class RapidAPIBackend(SearchBackend):

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._headers = {
            "X-RapidAPI-Key":  api_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    @property
    def name(self) -> str:
        return "RapidAPI / Real-Time Amazon Data"

    async def search(self, query: str, max_results: int = 20, page: int = 1) -> list[AmazonItem]:
        """
        Fetch search results from a specific Amazon results page.
        page=1 is the default first page, page=2 fetches items 21-40, etc.

        Notes:
        - `product_condition` is intentionally omitted — passing "ALL" is not a valid
          value for this API and causes it to silently return 0 results.
        - Retries once with a short delay if the first call returns 0 products,
          since RapidAPI occasionally rate-limits bursts silently.
        """
        import asyncio

        params = {
            "query":   query,
            "page":    str(page),
            "country": "US",
            "sort_by": "RELEVANCE",
        }

        raw_products = await self._fetch(params)

        # Retry once on empty — RapidAPI sometimes silently rate-limits burst calls
        if not raw_products:
            logger.warning("RapidAPI returned 0 for '%s' — retrying in 1.5s", query)
            await asyncio.sleep(1.5)
            raw_products = await self._fetch(params)

        logger.info("RapidAPI returned %d products for query '%s'", len(raw_products), query)

        items: list[AmazonItem] = []
        for raw in raw_products[:max_results]:
            item = self._parse_product(raw)
            if item:
                items.append(item)

        # Sort by score (rating × log reviews)
        items.sort(key=lambda i: i.score, reverse=True)
        return items

    # ── HTTP helper ───────────────────────────────────────────────────────────

    async def _fetch(self, params: dict) -> list:
        """Single HTTP call to the search endpoint. Returns raw product list (may be empty)."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SEARCH_URL,
                headers=self._headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"RapidAPI error {resp.status}: {text[:200]}")
                data = await resp.json()
        return data.get("data", {}).get("products", [])

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parse_product(self, raw: dict) -> Optional[AmazonItem]:
        try:
            if not raw or not isinstance(raw, dict):
                return None
            asin = raw.get("asin", "")
            if not asin:
                return None

            title = raw.get("product_title", "").strip()

            # ── Price ──────────────────────────────────────────────────────────
            price_usd: Optional[float] = None
            raw_price = raw.get("product_price") or raw.get("product_minimum_offer_price")
            if raw_price:
                price_usd = _parse_price(raw_price)

            # ── Ratings ────────────────────────────────────────────────────────
            rating: Optional[float] = None
            review_count: Optional[int] = None
            try:
                rating = float(raw.get("product_star_rating") or 0) or None
            except (ValueError, TypeError):
                pass
            try:
                rc = raw.get("product_num_ratings") or raw.get("product_num_offers")
                review_count = int(rc) if rc else None
            except (ValueError, TypeError):
                pass

            # ── Image ──────────────────────────────────────────────────────────
            image_url: Optional[str] = raw.get("product_photo") or raw.get("thumbnail")

            # ── Fulfillment / Israel delivery detection ────────────────────────
            #
            # RapidAPI search results almost never set is_prime=True, but the
            # delivery text contains the real fulfilment signal:
            #
            #   FBA item → "FREE delivery Mon, Mar 2 on $35 of items shipped by Amazon"
            #   3P item  → "FREE delivery Mon, Mar 2"  (no "shipped by Amazon")
            #
            # "shipped by Amazon" = Amazon warehouses this item = FBA
            # = ships to 🇮🇱 Israel via Amazon's international shipping programme.
            # This phrase is the most reliable, API-accessible FBA indicator we have.
            #
            delivery_text = (raw.get("delivery") or "").lower()

            # Definitive FBA signal: Amazon literally says "shipped by Amazon"
            is_shipped_by_amazon = "shipped by amazon" in delivery_text

            # Secondary FBA signal: "fulfilled by amazon" (less common phrasing)
            is_fulfilled_by_amazon_text = "fulfilled by amazon" in delivery_text

            # Prime signal from delivery text (appears less often in search)
            is_prime_in_delivery = "prime members" in delivery_text

            # is_prime: API field (usually False in search) OR delivery-text signal
            is_prime = bool(raw.get("is_prime", False)) or is_prime_in_delivery

            # Seller field — check multiple locations depending on API version
            seller = (
                raw.get("sales_volume", "")
                or (raw.get("product_details") or {}).get("seller", "")
                or ""
            ).lower()
            is_sold_by_amazon = "amazon.com" in seller or seller.strip() == "amazon"

            # is_amazon_fulfilled = we have explicit evidence this is an FBA item
            is_amazon_fulfilled = (
                is_shipped_by_amazon
                or is_fulfilled_by_amazon_text
                or is_sold_by_amazon
            )

            return AmazonItem(
                asin=asin,
                title=title,
                image_url=image_url,
                price_usd=price_usd,
                currency="USD",
                rating=rating,
                review_count=review_count,
                is_amazon_fulfilled=is_amazon_fulfilled,
                is_sold_by_amazon=is_sold_by_amazon,
                is_prime=is_prime,
                availability="In Stock" if raw.get("product_url") else "Unknown",
            )
        except Exception as exc:
            logger.warning("Failed to parse RapidAPI product %s: %s", raw.get("asin", "?"), exc)
            return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_price(price_str: str) -> Optional[float]:
    """Extract numeric value from strings like '$29.99', '29.99', '$1,299.00'."""
    try:
        cleaned = re.sub(r"[^\d.]", "", str(price_str).replace(",", ""))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None
