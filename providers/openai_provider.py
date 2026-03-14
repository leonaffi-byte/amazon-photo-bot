"""
OpenAI vision provider — supports gpt-4o and gpt-4o-mini.

Pricing (as of early 2025):
  gpt-4o:       $5.00 / 1M input tokens,  $15.00 / 1M output tokens
                + image tiles: each 512x512 tile = 170 tokens (~$0.00085/tile)
                A typical 1024x1024 product photo ~ 765 input tokens for vision
  gpt-4o-mini:  $0.15 / 1M input tokens,  $0.60 / 1M output tokens
                Image tiles same count but much cheaper per token
"""
from __future__ import annotations

import base64
import time
import logging

import httpx
from openai import AsyncOpenAI

from typing import Optional
from providers.base import (
    SYSTEM_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider,
    PROVIDER_TIMEOUT_SECONDS, detect_media_type,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.name = "openai"
        self.model_id = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS),
        )

        # Pricing per 1k tokens
        _pricing = {
            "gpt-4o":      (0.005,  0.015),
            "gpt-4o-mini": (0.00015, 0.0006),
        }
        self.cost_per_1k_input_tokens, self.cost_per_1k_output_tokens = _pricing.get(
            model, (0.005, 0.015)
        )
        # High-detail image processing: ~765 tokens for a typical product photo
        self.cost_per_image = 765 / 1000 * self.cost_per_1k_input_tokens

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()
        t0 = time.monotonic()

        # Timeout is enforced by the manager's _safe_run via asyncio.wait_for.
        # The HTTP client timeout (PROVIDER_TIMEOUT_SECONDS) provides a second
        # layer of protection at the network level.
        response = await self._client.chat.completions.create(
            model=self.model_id,
            max_tokens=768,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{detect_media_type(image_bytes)};base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": build_user_prompt(context_hint)},
                    ],
                },
            ],
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 800
        output_tokens = usage.completion_tokens if usage else 150

        return self._build_result(raw, latency_ms, input_tokens, output_tokens)
