"""
DataForSEO Labs Amazon — synchronous live endpoints.

Three capabilities used in this project:
  1. related_keywords(keyword)   → suggestion buttons after search results
  2. enrich_asin(asin)           → accurate price/rating from DFS Labs data
  3. get_competitors(asin)       → "Similar products" feature
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://api.dataforseo.com/v3/dataforseo_labs/amazon"
LOC  = 2840   # United States
LANG = "en"


# ── data classes ─────────────────────────────────────────────────────────────

@dataclass
class RelatedKeyword:
    keyword: str
    search_volume: Optional[int]

    def label(self) -> str:
        if self.search_volume and self.search_volume >= 1000:
            vol = f"{self.search_volume // 1000}K"
        elif self.search_volume:
            vol = str(self.search_volume)
        else:
            vol = "?"
        return f"{self.keyword} ({vol}/mo)"


@dataclass
class DFSProduct:
    asin: str
    title: str = ""
    price: Optional[float] = None
    currency: str = "USD"
    rating: Optional[float] = None
    image_url: str = ""
    rank: Optional[int] = None


# ── client ────────────────────────────────────────────────────────────────────

class DataForSEOLabs:
    def __init__(self, login: str, password: str):
        creds = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }
        self._timeout = aiohttp.ClientTimeout(total=15)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _post(self, path: str, payload: list) -> dict:
        url = f"{BASE}/{path}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, headers=self._headers, json=payload, timeout=self._timeout
                ) as r:
                    data = await r.json()
            task = (data.get("tasks") or [{}])[0]
            if task.get("status_code") not in (20000,):
                logger.warning(
                    "DFS Labs %s → %s %s",
                    path, task.get("status_code"), task.get("status_message", "")
                )
                return {}
            result = (task.get("result") or [{}])[0]
            return result
        except Exception as exc:
            logger.warning("DFS Labs %s error: %s", path, exc)
            return {}

    # ── public API ────────────────────────────────────────────────────────────

    async def related_keywords(
        self, keyword: str, limit: int = 6
    ) -> list[RelatedKeyword]:
        """Return related Amazon search terms for a keyword."""
        result = await self._post(
            "related_keywords/live",
            [{"keyword": keyword, "location_code": LOC, "language_code": LANG,
              "limit": limit, "depth": 1}],
        )
        out: list[RelatedKeyword] = []
        for item in result.get("items") or []:
            kd  = item.get("keyword_data") or {}
            kw  = kd.get("keyword", "")
            vol = (kd.get("keyword_info") or {}).get("search_volume")
            if kw and kw.lower() != keyword.lower():
                out.append(RelatedKeyword(keyword=kw, search_volume=vol))
        return out[:limit]

    async def enrich_asin(self, asin: str) -> Optional[DFSProduct]:
        """
        Fetch product data for a known ASIN via ranked_keywords.
        Returns the first serp_item found (contains title, price, rating, image).
        """
        result = await self._post(
            "ranked_keywords/live",
            [{"asin": asin, "location_code": LOC, "language_code": LANG, "limit": 3}],
        )
        items = result.get("items") or []
        if not items:
            return None

        for item in items:
            serp = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
            title     = serp.get("title") or ""
            price     = serp.get("price_from")
            currency  = serp.get("currency") or "USD"
            rating_d  = serp.get("rating") or {}
            rating    = rating_d.get("value")
            image_url = serp.get("image_url") or ""
            rank      = serp.get("rank_absolute")
            if title or price:
                return DFSProduct(
                    asin=asin,
                    title=title,
                    price=float(price) if price is not None else None,
                    currency=currency,
                    rating=float(rating) if rating is not None else None,
                    image_url=image_url,
                    rank=rank,
                )
        return None

    async def get_competitors(
        self, asin: str, limit: int = 20
    ) -> list[str]:
        """Return competitor ASINs sorted by number of shared keyword rankings."""
        result = await self._post(
            "product_competitors/live",
            [{"asin": asin, "location_code": LOC, "language_code": LANG,
              "limit": limit}],
        )
        asins: list[str] = []
        for item in (result.get("items") or []):
            a = item.get("asin", "")
            if a and a != asin:
                asins.append(a)
        return asins

    async def enrich_many(
        self, asins: list[str], concurrency: int = 4
    ) -> dict[str, DFSProduct]:
        """Enrich multiple ASINs concurrently. Returns {asin: DFSProduct}."""
        sem = asyncio.Semaphore(concurrency)

        async def _one(asin: str) -> tuple[str, Optional[DFSProduct]]:
            async with sem:
                prod = await self.enrich_asin(asin)
                return asin, prod

        results = await asyncio.gather(*[_one(a) for a in asins])
        return {a: p for a, p in results if p is not None}
