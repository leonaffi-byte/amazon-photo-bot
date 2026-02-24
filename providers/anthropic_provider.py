"""
Anthropic vision provider — supports claude-3-5-sonnet and claude-3-haiku.

Pricing (as of early 2025):
  claude-3-5-sonnet-20241022: $3.00 / 1M input,  $15.00 / 1M output
                               Images: ~1600 tokens per standard image
  claude-3-haiku-20240307:    $0.25 / 1M input,  $1.25  / 1M output
                               Images: ~1600 tokens per standard image

Why Claude is a useful second opinion:
  - Different training data → catches items GPT-4o misses
  - Excellent at reading fine print, small text, and nutritional labels
  - Sometimes more verbose on features (good for obscure items)
"""
from __future__ import annotations

import base64
import time
import logging

import anthropic

from providers.base import (
    SYSTEM_PROMPT, USER_PROMPT,
    ProviderResult, VisionProvider, parse_json_response,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_IMAGE_TOKENS = 1600  # approximate tokens per image for Claude


class AnthropicProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.name = "anthropic"
        self.model_id = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

        _pricing = {
            "claude-3-5-sonnet-20241022": (0.003,  0.015),
            "claude-3-haiku-20240307":    (0.00025, 0.00125),
        }
        self.cost_per_1k_input_tokens, self.cost_per_1k_output_tokens = _pricing.get(
            model, (0.003, 0.015)
        )
        self.cost_per_image = _ANTHROPIC_IMAGE_TOKENS / 1000 * self.cost_per_1k_input_tokens

    async def analyse(self, image_bytes: bytes) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()
        t0 = time.monotonic()

        # Detect media type (default jpeg)
        media_type = "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            media_type = "image/png"
        elif image_bytes[:4] == b"GIF8":
            media_type = "image/gif"
        elif image_bytes[:4] == b"RIFF":
            media_type = "image/webp"

        message = await self._client.messages.create(
            model=self.model_id,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = message.content[0].text
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens

        data = parse_json_response(raw, self.full_name)
        cost = self.estimate_cost(input_tokens, output_tokens)

        return ProviderResult(
            provider_name=self.full_name,
            model_id=self.model_id,
            product_name=data.get("product_name", "Unknown"),
            brand=data.get("brand"),
            category=data.get("category", "All"),
            key_features=data.get("key_features", []),
            amazon_search_query=data.get("amazon_search_query", ""),
            alternative_query=data.get("alternative_query", data.get("amazon_search_query", "")),
            confidence=data.get("confidence", "medium"),
            notes=data.get("notes", ""),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
