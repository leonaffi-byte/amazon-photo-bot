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
        True when this item is reliably known to be FBA/sold-by-Amazon,
        meaning it ships internationally to Israel via Amazon Global.

        Rules (conservative — prefer false negatives over false positives):
          1. is_sold_by_amazon → 100% qualifies (Amazon Retail ships globally)
          2. is_amazon_fulfilled (FBA) → qualifies (FBA items use Amazon's export program)
          3. is_prime → high-confidence proxy for FBA (~97% of Prime items are FBA)

        Deliberately NOT included:
          • "FREE delivery …" text from US search results — this is US domestic
            delivery, completely unrelated to whether the item ships to Israel.
          • Price-only signals — a cheap 3P item still doesn't ship to Israel.
        """
        return self.is_sold_by_amazon or self.is_amazon_fulfilled or self.is_prime

    @property
    def delivery_badge(self) -> str:
        """Short one-line shipping signal shown on every product card."""
        if self.is_sold_by_amazon:
            return "🟢 Sold & shipped by Amazon.com"
        if self.is_amazon_fulfilled:
            return "📦 Fulfilled by Amazon (FBA)"
        if self.is_prime:
            return "⭐ Prime eligible — FBA"
        return "🏪 Third-party seller"

    @property
    def israel_delivery_note(self) -> str:
        """
        Delivery note tailored for Israeli users.
        Shows confidence level so users know how sure we are.
        """
        threshold = config.FREE_DELIVERY_THRESHOLD
        if self.is_sold_by_amazon:
            return f"✈️ Ships to 🇮🇱 Israel — free when cart ≥ ${threshold:.0f}"
        if self.is_amazon_fulfilled:
            return f"✈️ Likely ships to 🇮🇱 Israel (FBA) — free when cart ≥ ${threshold:.0f}"
        if self.is_prime:
            return f"✈️ Probably ships to 🇮🇱 Israel (Prime/FBA) — free when cart ≥ ${threshold:.0f}"
        return "⚠️ Third-party seller — verify Israel shipping on Amazon"


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
