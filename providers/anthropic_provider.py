"""
Anthropic vision provider — supports claude-3-5-sonnet and claude-3-haiku.

Pricing (as of early 2025):
  claude-3-5-sonnet-20241022: $3.00 / 1M input,  $15.00 / 1M output
                               Images: ~1600 tokens per standard image
  claude-3-haiku-20240307:    $0.25 / 1M input,  $1.25  / 1M output
                               Images: ~1600 tokens per standard image

Why Claude is a useful second opinion:
  - Different training data -> catches items GPT-4o misses
  - Excellent at reading fine print, small text, and nutritional labels
  - Sometimes more verbose on features (good for obscure items)
"""
from __future__ import annotations

import asyncio
import base64
import time
import logging

import anthropic

from typing import Optional
from providers.base import (
    SYSTEM_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider,
    PROVIDER_TIMEOUT_SECONDS, detect_media_type,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_IMAGE_TOKENS = 1600  # approximate tokens per image for Claude


class AnthropicProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.name = "anthropic"
        self.model_id = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )

        _pricing = {
            "claude-3-5-sonnet-20241022": (0.003,  0.015),
            "claude-3-haiku-20240307":    (0.00025, 0.00125),
        }
        self.cost_per_1k_input_tokens, self.cost_per_1k_output_tokens = _pricing.get(
            model, (0.003, 0.015)
        )
        self.cost_per_image = _ANTHROPIC_IMAGE_TOKENS / 1000 * self.cost_per_1k_input_tokens

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()
        t0 = time.monotonic()

        media_type = detect_media_type(image_bytes)

        message = await asyncio.wait_for(self._client.messages.create(
            model=self.model_id,
            max_tokens=768,
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
                        {"type": "text", "text": build_user_prompt(context_hint)},
                    ],
                }
            ],
        ), timeout=45)

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = message.content[0].text
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens

        return self._build_result(raw, latency_ms, input_tokens, output_tokens)
