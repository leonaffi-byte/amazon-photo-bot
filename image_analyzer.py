"""
image_analyzer.py — kept as the canonical home of ProductInfo.
Actual analysis is delegated to providers/manager.py.

amazon_search.py imports ProductInfo from here — do not move it.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProductInfo:
    """Structured product identification result passed to Amazon search."""
    product_name: str
    brand: Optional[str]
    category: str
    key_features: list[str]
    amazon_search_query: str
    alternative_query: str
    confidence: str       # high | medium | low
    notes: str            # includes provider name in compare/best mode

    def __post_init__(self):
        if not self.product_name:
            self.product_name = "Unknown Product"
        if self.confidence not in ("high", "medium", "low"):
            self.confidence = "medium"
        if len(self.key_features) > 10:
            self.key_features = self.key_features[:10]
