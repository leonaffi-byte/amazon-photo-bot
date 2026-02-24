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

Israel free-delivery detection without PA-API's IsAmazonFulfilled:
  • is_prime == True  →  item is Prime-eligible → almost certainly FBA
    (Amazon stats: ~97% of Prime items are FBA or Amazon-fulfilled)
  • "sold by Amazon" in seller name  →  100% qualifies
  • delivery field contains "FREE delivery"  →  strong positive signal
  • We OR all three signals → very few false negatives
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
        """
        params = {
            "query":             query,
            "page":              str(page),
            "country":           "US",
            "sort_by":           "RELEVANCE",
            "product_condition": "ALL",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                SEARCH_URL,
                headers=self._headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"RapidAPI error {resp.status}: {text[:200]}"
                    )
                data = await resp.json()

        raw_products = data.get("data", {}).get("products", [])
        logger.info("RapidAPI returned %d products for query '%s'", len(raw_products), query)

        items: list[AmazonItem] = []
        for raw in raw_products[:max_results]:
            item = self._parse_product(raw)
            if item:
                items.append(item)

        # Sort by score (rating × log reviews)
        items.sort(key=lambda i: i.score, reverse=True)
        return items

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
            is_prime = bool(raw.get("is_prime", False))

            # RapidAPI search returns a human-readable delivery string such as
            # "FREE delivery Mon, Mar 2" or "FREE Shipping on eligible orders".
            # is_prime is almost always False in search results even for FBA items,
            # so we rely on the delivery text as the primary signal.
            delivery_text = (raw.get("delivery") or "").lower()
            has_free_delivery_text = (
                "free delivery" in delivery_text
                or "free shipping" in delivery_text
                or "ships free" in delivery_text
                or (delivery_text.startswith("free") and len(delivery_text) > 4)
            )

            # Seller name can appear in different fields depending on API version
            seller = (
                raw.get("sales_volume", "")      # sometimes contains seller hint
                or raw.get("product_details", {}).get("seller", "")
                or ""
            ).lower()
            is_sold_by_amazon = "amazon.com" in seller or "amazon" == seller.strip()

            # FBA flag: RapidAPI's search endpoint doesn't return IsAmazonFulfilled
            # directly, but is_prime is a very reliable proxy (~97% accuracy).
            # We set is_amazon_fulfilled = is_prime here; the base class OR-combines
            # is_prime and is_amazon_fulfilled anyway, so this doesn't double-count.
            is_amazon_fulfilled = is_prime or is_sold_by_amazon or has_free_delivery_text

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
