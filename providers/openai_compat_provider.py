"""
Generic OpenAI-compatible vision provider.

Replaces the five near-identical provider files (groq, mistral, sambanova,
together, fireworks) that only differed in base_url, pricing, and display names.
Any service that exposes an OpenAI-compatible /v1/chat/completions endpoint
with vision support can use this class.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Optional

import httpx
import openai

from providers.base import (
    SYSTEM_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider,
    PROVIDER_TIMEOUT_SECONDS, detect_media_type,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(VisionProvider):
    """Vision provider for any OpenAI-compatible API endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        name: str,
        model: str,
        input_cost_per_1k: float = 0.0,
        output_cost_per_1k: float = 0.0,
        image_cost: float = 0.0,
        display_name: Optional[str] = None,
        default_headers: Optional[dict[str, str]] = None,
        max_tokens: int = 512,
    ):
        self.name = name
        self.model_id = model
        self._display_name = display_name or model.split("/")[-1]
        self._max_tokens = max_tokens

        client_kwargs: dict = dict(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS),
        )
        if default_headers:
            client_kwargs["default_headers"] = default_headers
        self._client = openai.AsyncOpenAI(**client_kwargs)

        self.cost_per_1k_input_tokens = input_cost_per_1k
        self.cost_per_1k_output_tokens = output_cost_per_1k
        self.cost_per_image = image_cost

    @property
    def full_name(self) -> str:
        return f"{self.name}/{self._display_name}"

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()
        media_type = detect_media_type(image_bytes)
        t0 = time.monotonic()

        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model_id,
                max_tokens=self._max_tokens,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{b64}"},
                            },
                            {"type": "text", "text": build_user_prompt(context_hint)},
                        ],
                    },
                ],
            ),
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 800
        output_tokens = usage.completion_tokens if usage else 150

        return self._build_result(raw, latency_ms, input_tokens, output_tokens)
