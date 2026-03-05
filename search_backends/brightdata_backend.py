"""
Bright Data Web Unlocker search backend.

Uses Bright Data's Web Unlocker API to fetch Amazon search result pages
and parses the HTML with BeautifulSoup to extract product data.

Pricing: ~$0.0015/request  ·  Synchronous (~2-3s)
Docs: https://docs.brightdata.com/scraping-automation/web-unlocker/

This backend is used as a fallback when RapidAPI and DataForSEO quotas
are exhausted.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import aiohttp
from bs4 import BeautifulSoup

from search_backends.base import AmazonItem, SearchBackend

logger = logging.getLogger(__name__)

_API_URL = "https://api.brightdata.com/request"


class BrightDataBackend(SearchBackend):

    def __init__(self, api_token: str, zone: str = "unlocker") -> None:
        self._token = api_token
        self._zone = zone

    @property
    def name(self) -> str:
        return "Bright Data Web Unlocker"

    async def search(
        self, query: str, max_results: int = 20, page: int = 1
    ) -> list[AmazonItem]:
        url = f"https://www.amazon.com/s?k={quote_plus(query)}&page={page}"
        html = await self._fetch(url)
        if not html:
            raise RuntimeError("Bright Data returned empty response")

        items = _parse_search_html(html)
        logger.info("BrightData returned %d items for '%s' page %d", len(items), query, page)
        items.sort(key=lambda i: i.score, reverse=True)
        return items[:max_results]

    async def _fetch(self, url: str) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {
            "zone": self._zone,
            "url": url,
            "format": "raw",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Bright Data error {resp.status}: {text[:200]}")
                return await resp.text()


def _parse_search_html(html: str) -> list[AmazonItem]:
    """Parse Amazon search results page HTML into AmazonItem list."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[AmazonItem] = []

    # Amazon search results are in divs with data-asin attribute
    for card in soup.select('[data-component-type="s-search-result"]'):
        asin = card.get("data-asin", "").strip()
        if not asin:
            continue

        item = _parse_card(card, asin)
        if item:
            items.append(item)

    return items


def _parse_card(card, asin: str) -> Optional[AmazonItem]:
    """Parse a single Amazon search result card."""
    try:
        # Title
        title_el = card.select_one("h2 a span, h2 span")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # Image
        img_el = card.select_one("img.s-image")
        image_url = img_el.get("src") if img_el else None

        # Price
        price_usd = None
        price_whole = card.select_one(".a-price .a-price-whole")
        price_frac = card.select_one(".a-price .a-price-fraction")
        if price_whole:
            whole = price_whole.get_text(strip=True).replace(",", "").rstrip(".")
            frac = price_frac.get_text(strip=True) if price_frac else "00"
            try:
                price_usd = float(f"{whole}.{frac}")
            except ValueError:
                pass

        # Rating
        rating = None
        rating_el = card.select_one('[aria-label*="out of 5 stars"]')
        if rating_el:
            m = re.search(r"([\d.]+)\s+out of", rating_el.get("aria-label", ""))
            if m:
                rating = float(m.group(1))

        # Review count
        review_count = None
        review_el = card.select_one('[aria-label*="out of 5 stars"] + span, .a-size-base.s-underline-text')
        if review_el:
            text = review_el.get_text(strip=True).replace(",", "")
            m = re.search(r"(\d+)", text)
            if m:
                review_count = int(m.group(1))

        # Fulfillment signals
        card_text = card.get_text(" ", strip=True).lower()
        is_prime = bool(card.select_one('[aria-label="Amazon Prime"]') or
                        card.select_one('.a-icon-prime') or
                        "prime" in card_text[:500])
        is_shipped_by_amazon = "shipped by amazon" in card_text
        is_sold_by_amazon = "sold by amazon" in card_text or "ships from amazon" in card_text
        is_fulfilled = is_shipped_by_amazon or is_sold_by_amazon

        # Free delivery signal
        free_delivery = "free delivery" in card_text or "free shipping" in card_text

        return AmazonItem(
            asin=asin,
            title=title,
            image_url=image_url,
            price_usd=price_usd,
            currency="USD",
            rating=rating,
            review_count=review_count,
            is_amazon_fulfilled=is_fulfilled,
            is_sold_by_amazon=is_sold_by_amazon,
            is_prime=is_prime,
            availability="In Stock",
            free_delivery_likely=free_delivery or is_fulfilled or is_prime,
        )
    except Exception as exc:
        logger.warning("Failed to parse BrightData card ASIN %s: %s", asin, exc)
        return None
