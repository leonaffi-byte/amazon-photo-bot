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
