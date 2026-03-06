"""
Shared types and base class for all vision providers.
"""
from __future__ import annotations

import json
import logging
import re as _re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Shared utilities ──────────────────────────────────────────────────────────

def detect_media_type(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"GIF8":
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and len(image_bytes) > 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if len(image_bytes) >= 3 and image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/jpeg"  # safe default


def sanitize_query(q: str, max_len: int = 100) -> str:
    """Strip markdown artifacts and cap length for search queries."""
    q = _re.sub(r"[`*_~\[\]()#]", "", q).strip()
    q = " ".join(q.split())
    return q[:max_len]


def _extract_features(data: dict) -> list[str]:
    """Type-coerce key_features from model response to a clean list of strings."""
    features = data.get("key_features", [])
    if isinstance(features, list):
        return [str(f) for f in features if f][:5]
    if isinstance(features, str):
        return [f.strip() for f in features.split(",") if f.strip()][:5]
    return []

# ── Prompt (shared across all providers) ──────────────────────────────────────

SYSTEM_PROMPT = """You are an expert product identification assistant.
Analyse the product photo and return ONLY valid JSON — no markdown, no prose.

Return a JSON object with a "products" array. Each element uses this schema:
{
  "products": [
    {
      "product_name":          "concise name — brand + model if visible",
      "brand":                 "brand name or null",
      "category":              "Amazon browse category (e.g. Electronics, Kitchen)",
      "key_features":          ["up to 5 most distinctive features"],
      "amazon_search_query":   "≤100-char optimised Amazon keyword search string",
      "alternative_query":     "broader fallback search if main query fails",
      "confidence":            "high | medium | low",
      "notes":                 "brief note on identification quality",
      "bbox":                  [x_percent, y_percent, width_percent, height_percent]
    }
  ]
}

Rules:
- If the photo contains ONE product, return exactly one element in "products"
- If the photo contains MULTIPLE distinct products, return one element per product (up to 6)
- bbox: approximate bounding box as percentages (0-100) of image width/height. [x, y] is the top-left corner.
- amazon_search_query: most specific terms first, include model# if visible. ALWAYS include the dominant color of the product (e.g. "black leather jacket", "navy blue dress", "brown plaid shirt"). Color is critical for accurate Amazon results.
- If brand unknown, omit it from search query to avoid zero results
- key_features: focus on what distinguishes this from similar products
- If a person is wearing or holding the product, describe ONLY the product — ignore the person entirely
- Never refuse: if the photo shows a person, identify the clothing/accessory/item they are wearing
- If the product is clothing, footwear, or accessories and appears gendered (e.g. worn by a woman, styled for women/men, or clearly designed for a specific gender), prefix amazon_search_query with "women's" or "men's". Infer gender from the model wearing it, the style, cut, or product design. When in doubt, include the gender prefix based on your best assessment.
- If the user has drawn a circle, arrow, highlight, or annotation on the image, identify ONLY the highlighted/circled product and return just that one element
"""

USER_PROMPT = (
    "Analyse this product photo and return the JSON with a \"products\" array. "
    "Focus ONLY on the PRODUCT or ITEM itself (clothing, accessory, gadget, object) — "
    "not on any person who may appear in the photo. "
    "Identify what each item is so a shopper can find it on Amazon."
)


def build_user_prompt(context_hint: Optional[str] = None) -> str:
    """Return the user prompt, optionally prepending a user-provided hint."""
    if context_hint and context_hint.strip():
        return (
            f"User context about this product: \"{context_hint.strip()}\"\n\n"
            + USER_PROMPT
        )
    return USER_PROMPT


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
    bbox: Optional[tuple[float, float, float, float]] = None  # (x%, y%, w%, h%)
    products_raw: list[dict] = field(default_factory=list)

    # internal quality score for ranking (higher = better)
    quality_score: float = field(init=False)

    def __post_init__(self) -> None:
        conf_weight = {"high": 1.0, "medium": 0.6, "low": 0.2}.get(self.confidence, 0.3)
        name_score = 1.0 if (self.product_name and self.product_name not in ("Unknown", "Unknown Product", "")) else 0.0
        brand_score = 1.0 if (self.brand and len(self.brand) > 1) else 0.0
        features_score = 0.5 * min(len(self.key_features), 5) / 5
        q_len = len(self.amazon_search_query)
        query_score = min(q_len / 40.0, 1.0) if q_len > 5 else 0.0
        completeness = name_score + brand_score + features_score + query_score
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
            bbox=self.bbox,
        )

    def to_product_info_list(self) -> list:
        """Convert all detected products to ProductInfo list."""
        from image_analyzer import ProductInfo
        if not self.products_raw:
            return [self.to_product_info()]
        result = []
        for p in self.products_raw:
            bbox_raw = p.get("bbox")
            bbox = tuple(bbox_raw) if bbox_raw and len(bbox_raw) == 4 else None
            result.append(ProductInfo(
                product_name=p.get("product_name", "Unknown Product"),
                brand=p.get("brand"),
                category=p.get("category", ""),
                key_features=p.get("key_features", [])[:5],
                amazon_search_query=p.get("amazon_search_query", ""),
                alternative_query=p.get("alternative_query", ""),
                confidence=p.get("confidence", "medium"),
                notes=f"[{self.provider_name}] {p.get('notes', '')}",
                bbox=bbox,
            ))
        return result


def _fix_json(text: str) -> str:
    """Fix common JSON issues from LLM outputs."""
    # Remove trailing commas before } or ]
    text = _re.sub(r",\s*([}\]])", r"\1", text)
    # Fix unquoted keys (simple cases)
    text = _re.sub(r"(\{|,)\s*(\w+)\s*:", r'\1 "\2":', text)
    return text


def parse_json_response(raw: str, provider_name: str) -> dict:
    """
    Parse JSON from a model response, handling markdown fences gracefully.
    Supports both single-object and {"products": [...]} multi-product format.
    Always returns a dict with a "products" key containing a list.
    Raises ValueError on parse failure.
    """
    text = raw.strip()
    parsed = None

    # Try 1: Direct parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try 2: Extract from markdown fence
    if parsed is None:
        fence_match = _re.search(r"```(?:json)?\s*\n?([\s\S]+?)\n?```", text)
        if fence_match:
            extracted = fence_match.group(1).strip()
            try:
                parsed = json.loads(extracted)
            except json.JSONDecodeError:
                # Try with JSON fixes (trailing commas etc)
                try:
                    parsed = json.loads(_fix_json(extracted))
                except json.JSONDecodeError:
                    pass

    # Try 3: Find first {...} block
    if parsed is None:
        brace_match = _re.search(r"\{[\s\S]+\}", text)
        if brace_match:
            candidate = brace_match.group(0)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed = json.loads(_fix_json(candidate))
                except json.JSONDecodeError:
                    pass

    # Try 4: Apply fixes to full text
    if parsed is None:
        try:
            parsed = json.loads(_fix_json(text))
        except json.JSONDecodeError:
            pass

    if parsed is None:
        logger.error("[%s] Non-JSON response: %s", provider_name, raw[:300])
        raise ValueError(f"[{provider_name}] JSON parse error: could not extract valid JSON")

    # Normalize to {"products": [...]} format
    if isinstance(parsed, list):
        return {"products": parsed}
    if isinstance(parsed, dict):
        if "products" in parsed and isinstance(parsed["products"], list):
            return parsed
        # Single product dict — wrap in products array
        return {"products": [parsed]}

    raise ValueError(f"[{provider_name}] Unexpected JSON type: {type(parsed)}")


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
    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
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
