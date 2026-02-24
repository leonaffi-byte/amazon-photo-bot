"""
Amazon Product Advertising API 5.0 backend — the official option.

Requirements:
  • Amazon Associates account (free to join)
  • PA-API access key + secret (instant after joining Associates)
  • ⚠️  Must generate 3 qualifying affiliate sales within 180 days or access is revoked

Advantages over RapidAPI:
  • IsAmazonFulfilled field is EXACT (not inferred from is_prime)
  • No per-request cost beyond the Associates requirement
  • Higher rate limits after the account matures

Free delivery to Israel detection:
  Uses Offers.Listings[0].DeliveryInfo.IsAmazonFulfilled (exact PA-API field).
  See amazon_search.py header comment for full explanation.

Pagination:
  PA-API returns max 10 items per call but supports ItemPage 1–10.
  We make as many page calls as needed (sequentially, to respect 1 req/s limit)
  until we have max_results unique items or exhaust all pages.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from search_backends.base import AmazonItem, SearchBackend
import config

logger = logging.getLogger(__name__)

_PAAPI_PAGE_SIZE  = 10    # PA-API hard maximum per call
_PAAPI_MAX_PAGES  = 10    # PA-API supports pages 1-10
_PAAPI_RATE_SLEEP = 1.05  # seconds between calls (PA-API rate limit: 1 req/s)

_SEARCH_RESOURCES = [
    "Images.Primary.Medium",
    "ItemInfo.Title",
    "Offers.Listings.Price",
    "Offers.Listings.DeliveryInfo.IsAmazonFulfilled",
    "Offers.Listings.DeliveryInfo.IsFreeShippingEligible",
    "Offers.Listings.MerchantInfo",
    "Offers.Listings.Availability.Message",
    "CustomerReviews.Count",
    "CustomerReviews.StarRating",
]


class PaapiBackend(SearchBackend):

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        associate_tag: str,
        marketplace: str = "www.amazon.com",
    ) -> None:
        self._access_key    = access_key
        self._secret_key    = secret_key
        self._associate_tag = associate_tag
        self._marketplace   = marketplace
        self._host   = "webservices.amazon.com"
        self._region = "us-east-1"

    @property
    def name(self) -> str:
        return "Amazon PA-API 5.0"

    async def search(self, query: str, max_results: int = 20) -> list[AmazonItem]:
        """
        Search PA-API using multiple pages (up to _PAAPI_MAX_PAGES × 10 items).
        Pages are fetched sequentially to honour the 1 req/s rate limit.
        """
        items: dict[str, AmazonItem] = {}

        pages_needed = math.ceil(max_results / _PAAPI_PAGE_SIZE)
        pages_needed = min(pages_needed, _PAAPI_MAX_PAGES)

        for page in range(1, pages_needed + 1):
            if len(items) >= max_results:
                break
            try:
                raw_data = await self._call(query, item_count=_PAAPI_PAGE_SIZE, item_page=page)
            except RuntimeError as exc:
                # If page > 1 fails (e.g. no more results), stop cleanly
                logger.warning("PA-API page %d for '%s' failed: %s", page, query, exc)
                break
            except Exception as exc:
                logger.warning("PA-API page %d error: %s", page, exc)
                break

            page_items = raw_data.get("SearchResult", {}).get("Items", [])
            if not page_items:
                logger.info("PA-API: no more items on page %d — stopping", page)
                break

            for raw in page_items:
                parsed = self._parse_item(raw)
                if parsed and parsed.asin not in items:
                    items[parsed.asin] = parsed

            logger.info(
                "PA-API page %d/%d → %d new items, %d total",
                page, pages_needed, len(page_items), len(items),
            )

            # Respect the 1 req/s rate limit between pages
            if page < pages_needed and len(items) < max_results:
                await asyncio.sleep(_PAAPI_RATE_SLEEP)

        result = list(items.values())
        result.sort(key=lambda i: i.score, reverse=True)
        return result[:max_results]

    # ── PA-API HTTP call with AWS SigV4 ───────────────────────────────────────

    async def _call(self, keyword: str, item_count: int, item_page: int = 1) -> dict:
        payload = {
            "Keywords":    keyword,
            "PartnerTag":  self._associate_tag,
            "PartnerType": "Associates",
            "Marketplace": self._marketplace,
            "ItemCount":   item_count,
            "ItemPage":    item_page,
            "Resources":   _SEARCH_RESOURCES,
        }
        url     = f"https://{self._host}/paapi5/searchitems"
        headers = self._signed_headers(payload)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    err = data.get("Errors", [{}])[0].get("Message", str(data))
                    raise RuntimeError(f"PA-API {resp.status}: {err}")
                return data

    def _signed_headers(self, payload: dict) -> dict:
        service       = "ProductAdvertisingAPI"
        content_type  = "application/json; charset=utf-8"
        amz_target    = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
        endpoint_path = "/paapi5/searchitems"

        body_bytes    = json.dumps(payload).encode()
        now           = datetime.now(timezone.utc)
        amz_date      = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp    = now.strftime("%Y%m%d")

        canonical_headers = (
            f"content-encoding:amz-1.0\n"
            f"content-type:{content_type}\n"
            f"host:{self._host}\n"
            f"x-amz-date:{amz_date}\n"
            f"x-amz-target:{amz_target}\n"
        )
        signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
        payload_hash   = hashlib.sha256(body_bytes).hexdigest()

        canonical_request = "\n".join([
            "POST", endpoint_path, "", canonical_headers, signed_headers, payload_hash
        ])
        credential_scope = f"{date_stamp}/{self._region}/{service}/aws4_request"
        string_to_sign   = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        signing_key = self._get_signing_key(date_stamp)
        signature   = _hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        return {
            "Content-Encoding": "amz-1.0",
            "Content-Type":     content_type,
            "Host":             self._host,
            "X-Amz-Date":       amz_date,
            "X-Amz-Target":     amz_target,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self._access_key}/{credential_scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }

    def _get_signing_key(self, date_stamp: str) -> bytes:
        def sign(key: bytes, msg: str) -> bytes:
            return _hmac.new(key, msg.encode(), hashlib.sha256).digest()
        k = sign(f"AWS4{self._secret_key}".encode(), date_stamp)
        k = sign(k, self._region)
        k = sign(k, "ProductAdvertisingAPI")
        return sign(k, "aws4_request")

    # ── Item parser ────────────────────────────────────────────────────────────

    def _parse_item(self, raw: dict) -> Optional[AmazonItem]:
        try:
            asin  = raw["ASIN"]
            title = raw["ItemInfo"]["Title"]["DisplayValue"]

            try:
                image_url = raw["Images"]["Primary"]["Medium"]["URL"]
            except (KeyError, TypeError):
                image_url = None

            price_usd: Optional[float] = None
            currency = "USD"
            try:
                listing   = raw["Offers"]["Listings"][0]
                price_usd = listing["Price"]["Amount"]
                currency  = listing["Price"].get("Currency", "USD")
            except (KeyError, IndexError, TypeError):
                pass

            is_amazon_fulfilled = False
            is_sold_by_amazon   = False
            availability        = "Unknown"
            is_prime            = False
            try:
                listing  = raw["Offers"]["Listings"][0]
                delivery = listing.get("DeliveryInfo", {})
                is_amazon_fulfilled = bool(delivery.get("IsAmazonFulfilled", False))
                is_prime            = bool(delivery.get("IsFreeShippingEligible", False))
                merchant            = listing.get("MerchantInfo", {})
                is_sold_by_amazon   = merchant.get("Name", "").lower() in ("amazon.com", "amazon")
                availability        = listing.get("Availability", {}).get("Message", "Unknown")
            except (KeyError, IndexError, TypeError):
                pass

            rating: Optional[float] = None
            review_count: Optional[int] = None
            try:
                cr           = raw["CustomerReviews"]
                rating       = float(cr["StarRating"]["Value"])
                review_count = int(cr["Count"]["Value"])
            except (KeyError, TypeError, ValueError):
                pass

            return AmazonItem(
                asin=asin, title=title, image_url=image_url,
                price_usd=price_usd, currency=currency,
                rating=rating, review_count=review_count,
                is_amazon_fulfilled=is_amazon_fulfilled,
                is_sold_by_amazon=is_sold_by_amazon,
                is_prime=is_prime,
                availability=availability,
            )
        except Exception as exc:
            logger.warning("Failed to parse PA-API item %s: %s", raw.get("ASIN", "?"), exc)
            return None
