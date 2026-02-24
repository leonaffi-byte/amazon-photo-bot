"""
Google Gemini vision provider — uses the google-genai SDK (v1 API).

Pricing (as of early 2025):
  gemini-1.5-pro:        $3.50 / 1M input,  $10.50 / 1M output
                          Images: $0.001315 per image
  gemini-1.5-flash:      $0.075 / 1M input,  $0.30  / 1M output
                          Images: $0.00002 per image  ← extremely cheap
  gemini-2.0-flash:      $0.10  / 1M input,  $0.40  / 1M output
                          Images: $0.00004 per image
  gemini-2.0-flash-lite: $0.075 / 1M input,  $0.30  / 1M output
                          Images: $0.00002 per image
"""
from __future__ import annotations

import time
import logging

from google import genai
from google.genai import types as genai_types

from typing import Optional
from providers.base import (
    SYSTEM_PROMPT, USER_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider, parse_json_response,
)

logger = logging.getLogger(__name__)

_SAFETY_OFF = [
    genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="OFF"),
    genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="OFF"),
    genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
    genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
]

_PRICING: dict[str, tuple[float, float, float]] = {
    # model_id: ($/1k_input_tokens, $/1k_output_tokens, $/image)
    "gemini-1.5-pro":        (0.0035,   0.0105,  0.001315),
    "gemini-1.5-flash":      (0.000075, 0.0003,  0.00002),
    "gemini-2.0-flash":      (0.0001,   0.0004,  0.00004),
    "gemini-2.0-flash-lite": (0.000075, 0.0003,  0.00002),
}


class GeminiProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.name     = "google"
        self.model_id = model
        # Force v1 (stable) API — v1beta doesn't expose gemini-1.5-* by bare name
        self._client  = genai.Client(api_key=api_key, http_options={"api_version": "v1"})

        rates = _PRICING.get(model, _PRICING["gemini-2.0-flash"])
        self.cost_per_1k_input_tokens  = rates[0]
        self.cost_per_1k_output_tokens = rates[1]
        self.cost_per_image            = rates[2]

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        # Detect MIME type
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif image_bytes[:4] == b"GIF8":
            mime = "image/gif"
        elif image_bytes[:4] == b"RIFF":
            mime = "image/webp"
        else:
            mime = "image/jpeg"

        gen_config = genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0,
            max_output_tokens=512,
            safety_settings=_SAFETY_OFF,
        )

        t0 = time.monotonic()

        response = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
                build_user_prompt(context_hint),
            ],
            config=gen_config,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = response.text

        usage        = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count",     800)
        output_tokens= getattr(usage, "candidates_token_count", 150)

        data = parse_json_response(raw, self.full_name)
        cost = self.estimate_cost(input_tokens, output_tokens)

        return ProviderResult(
            provider_name       = self.full_name,
            model_id            = self.model_id,
            product_name        = data.get("product_name", "Unknown"),
            brand               = data.get("brand"),
            category            = data.get("category", "All"),
            key_features        = data.get("key_features", []),
            amazon_search_query = data.get("amazon_search_query", ""),
            alternative_query   = data.get("alternative_query", data.get("amazon_search_query", "")),
            confidence          = data.get("confidence", "medium"),
            notes               = data.get("notes", ""),
            latency_ms          = latency_ms,
            input_tokens        = input_tokens,
            output_tokens       = output_tokens,
            cost_usd            = cost,
        )
