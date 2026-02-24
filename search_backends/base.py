"""
Abstract base for all Amazon search backends.
Every backend must return the same AmazonItem list — the rest of the bot
doesn't care which backend is active.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import math

import config


@dataclass
class AmazonItem:
    asin: str
    title: str
    image_url: Optional[str]
    price_usd: Optional[float]
    currency: str
    rating: Optional[float]         # 0–5
    review_count: Optional[int]
    is_amazon_fulfilled: bool       # True = FBA / sold by Amazon → qualifies for Israel free delivery
    is_sold_by_amazon: bool         # True = sold directly by Amazon Retail (highest confidence)
    is_prime: bool                  # Prime-eligible (strong FBA proxy when FBA flag unavailable)
    availability: str

    # Computed at post-init
    score: float = field(init=False)

    def __post_init__(self) -> None:
        if self.rating and self.review_count and self.review_count > 0:
            self.score = self.rating * math.log10(self.review_count + 1)
        else:
            self.score = 0.0

    # ── Affiliate URL ──────────────────────────────────────────────────────────

    def affiliate_url(self, tag: Optional[str]) -> str:
        """
        Build an Amazon product URL.
        If an affiliate tag is provided, it's embedded as ?tag=... so every click
        is tracked under that Associates account.
        The URL is built fresh from the ASIN at display time — changing the active
        tag in the admin panel affects all subsequent button renders instantly.
        """
        base = f"https://www.amazon.com/dp/{self.asin}"
        if tag:
            return f"{base}?tag={tag}&linkCode=ogi&th=1&psc=1"
        return base

    # ── Israel delivery helpers ────────────────────────────────────────────────

    @property
    def qualifies_for_israel_free_delivery(self) -> bool:
        """
        True when this item is likely eligible for Amazon's free-shipping-to-Israel
        programme (order must still reach $49 USD total).

        Detection strategy (works across all backends):
          1. is_amazon_fulfilled (exact — available from PA-API)
          2. is_prime as fallback (Prime items are ~97% FBA)
          3. is_sold_by_amazon (always qualifies)

        We use OR so that even backends that only provide is_prime still filter correctly.
        """
        return self.is_amazon_fulfilled or self.is_prime or self.is_sold_by_amazon

    @property
    def delivery_badge(self) -> str:
        if self.is_sold_by_amazon:
            return "🟢 Ships from Amazon.com"
        if self.is_amazon_fulfilled:
            return "🔵 Fulfilled by Amazon (FBA)"
        if self.is_prime:
            return "🔵 Prime eligible (likely FBA)"
        return "🟡 Third-party seller"

    @property
    def israel_delivery_note(self) -> str:
        if self.qualifies_for_israel_free_delivery:
            threshold = config.FREE_DELIVERY_THRESHOLD
            return f"✈️ Free delivery to 🇮🇱 Israel (cart ≥ ${threshold:.0f})"
        return "⚠️ May not qualify for free delivery to Israel"


class SearchBackend(ABC):
    """All backends must implement this interface."""

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int,
        page: int = 1,
    ) -> list[AmazonItem]:
        """
        Search Amazon for products matching `query`.
        Returns up to max_results AmazonItem objects, best-first.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name for logs/display."""
        ...
