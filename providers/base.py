"""
Shared types and base class for all vision providers.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Prompt (shared across all providers) ──────────────────────────────────────

SYSTEM_PROMPT = """You are an expert product identification assistant.
Analyse the product photo and return ONLY a valid JSON object — no markdown, no prose.

JSON schema (all fields required unless marked optional):
{
  "product_name":          "concise name — brand + model if visible",
  "brand":                 "brand name or null",
  "category":              "Amazon browse category (e.g. Electronics, Kitchen)",
  "key_features":          ["up to 5 most distinctive features"],
  "amazon_search_query":   "≤100-char optimised Amazon keyword search string",
  "alternative_query":     "broader fallback search if main query fails",
  "confidence":            "high | medium | low",
  "notes":                 "brief note on identification quality"
}

Rules:
- amazon_search_query: most specific terms first, include model# if visible
- If brand unknown, omit it from search query to avoid zero results
- key_features: focus on what distinguishes this from similar products
"""

USER_PROMPT = (
    "Analyse this product photo and return the JSON. "
    "Focus on identifying exactly what this is so a shopper can find it on Amazon."
)


# ── Shared result type ─────────────────────────────────────────────────────────

@dataclass
class ProviderResult:
    """Result from a single vision provider."""
    provider_name: str          # e.g. "openai/gpt-4o"
    model_id: str               # full model id
    product_name: str
    brand: Optional[str]
    category: str
    key_features: list[str]
    amazon_search_query: str
    alternative_query: str
    confidence: str             # high | medium | low
    notes: str
    latency_ms: int             # wall-clock time for this call
    input_tokens: int
    output_tokens: int
    cost_usd: float             # estimated cost

    # internal quality score for ranking (higher = better)
    quality_score: float = field(init=False)

    def __post_init__(self) -> None:
        # Score = confidence weight × completeness
        conf_weight = {"high": 1.0, "medium": 0.6, "low": 0.2}.get(self.confidence, 0.3)
        completeness = (
            (1 if self.product_name else 0)
            + (1 if self.brand else 0)
            + (0.5 * min(len(self.key_features), 5) / 5)
            + (1 if len(self.amazon_search_query) > 5 else 0)
        )
        self.quality_score = conf_weight * completeness

    @property
    def cost_str(self) -> str:
        if self.cost_usd < 0.001:
            return f"${self.cost_usd * 1000:.3f}m"   # show in milli-dollars
        return f"${self.cost_usd:.4f}"

    def to_product_info(self):
        """Convert to the ProductInfo used by amazon_search."""
        from image_analyzer import ProductInfo
        return ProductInfo(
            product_name=self.product_name,
            brand=self.brand,
            category=self.category,
            key_features=self.key_features,
            amazon_search_query=self.amazon_search_query,
            alternative_query=self.alternative_query,
            confidence=self.confidence,
            notes=f"[{self.provider_name}] {self.notes}",
        )


def parse_json_response(raw: str, provider_name: str) -> dict:
    """
    Parse JSON from a model response, handling markdown fences gracefully.
    Raises ValueError on parse failure.
    """
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("[%s] Non-JSON response: %s", provider_name, raw[:300])
        raise ValueError(f"[{provider_name}] JSON parse error: {exc}") from exc


# ── Abstract base ──────────────────────────────────────────────────────────────

class VisionProvider(ABC):
    """Base class all vision providers must implement."""

    name: str           # e.g. "openai"
    model_id: str       # e.g. "gpt-4o"
    cost_per_1k_input_tokens: float
    cost_per_1k_output_tokens: float
    # Extra per-image cost for vision (input image processing flat fee or per-tile)
    cost_per_image: float = 0.0

    @abstractmethod
    async def analyse(self, image_bytes: bytes) -> ProviderResult:
        """Run vision inference on image_bytes. Must return ProviderResult."""
        ...

    @property
    def full_name(self) -> str:
        return f"{self.name}/{self.model_id}"

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            self.cost_per_image
            + input_tokens / 1000 * self.cost_per_1k_input_tokens
            + output_tokens / 1000 * self.cost_per_1k_output_tokens
        )
