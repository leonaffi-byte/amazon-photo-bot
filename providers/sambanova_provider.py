"""
SambaNova vision provider — Llama 4 Maverick via SambaNova's free OpenAI-compatible API.

FREE tier: 20 RPM limit.
"""
from __future__ import annotations

import asyncio
import base64
import time
import logging

import openai

from typing import Optional
from providers.base import (
    SYSTEM_PROMPT, USER_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider, parse_json_response,
    sanitize_query, _extract_features,
)

logger = logging.getLogger(__name__)

_SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"

_PRICING: dict[str, tuple[float, float]] = {
    "Llama-4-Maverick-17B-128E-Instruct": (0.0, 0.0),
}

_DISPLAY_NAMES: dict[str, str] = {
    "Llama-4-Maverick-17B-128E-Instruct": "llama-4-maverick",
}


class SambaNovaProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "Llama-4-Maverick-17B-128E-Instruct"):
        self.name     = "sambanova"
        self.model_id = model
        self._client  = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=_SAMBANOVA_BASE_URL,
        )
        self.cost_per_1k_input_tokens  = 0.0
        self.cost_per_1k_output_tokens = 0.0
        self.cost_per_image            = 0.0
        self._display_name = _DISPLAY_NAMES.get(model, model.split("-")[-1])

    @property
    def full_name(self) -> str:
        return f"sambanova/{self._display_name}"

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()

        media_type = "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            media_type = "image/png"
        elif image_bytes[:4] == b"RIFF":
            media_type = "image/webp"

        t0 = time.monotonic()

        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model_id,
                max_tokens=512,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type":      "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{b64}"},
                            },
                            {"type": "text", "text": build_user_prompt(context_hint)},
                        ],
                    },
                ],
            ),
            timeout=60,
        )

        latency_ms    = int((time.monotonic() - t0) * 1000)
        raw           = response.choices[0].message.content or ""
        usage         = response.usage
        input_tokens  = usage.prompt_tokens     if usage else 800
        output_tokens = usage.completion_tokens if usage else 150

        data = parse_json_response(raw, self.full_name)
        products = data["products"]
        first = products[0]
        bbox_raw = first.get("bbox")
        bbox = tuple(bbox_raw) if bbox_raw and len(bbox_raw) == 4 else None
        cost = self.estimate_cost(input_tokens, output_tokens)

        return ProviderResult(
            provider_name=self.full_name,
            model_id=self.model_id,
            product_name=first.get("product_name", "Unknown"),
            brand=first.get("brand"),
            category=first.get("category", "All"),
            key_features=_extract_features(first),
            amazon_search_query=sanitize_query(first.get("amazon_search_query", "")),
            alternative_query=sanitize_query(first.get("alternative_query", first.get("amazon_search_query", ""))),
            confidence=first.get("confidence", "medium"),
            notes=first.get("notes", ""),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            bbox=bbox,
            products_raw=products,
        )
