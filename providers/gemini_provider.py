"""
Google Gemini vision provider — uses the google-genai SDK (v1 API).

Pricing (as of early 2025):
  gemini-1.5-pro:        $3.50 / 1M input,  $10.50 / 1M output
                          Images: $0.001315 per image
  gemini-1.5-flash:      $0.075 / 1M input,  $0.30  / 1M output
                          Images: $0.00002 per image  <- extremely cheap
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
    SYSTEM_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider,
    PROVIDER_TIMEOUT_SECONDS, detect_media_type,
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
        self._client  = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1", "timeout": PROVIDER_TIMEOUT_SECONDS * 1000},
        )

        rates = _PRICING.get(model, _PRICING["gemini-2.0-flash"])
        self.cost_per_1k_input_tokens  = rates[0]
        self.cost_per_1k_output_tokens = rates[1]
        self.cost_per_image            = rates[2]

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        mime = detect_media_type(image_bytes)

        gen_config = genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0,
            max_output_tokens=768,
            safety_settings=_SAFETY_OFF,
        )

        t0 = time.monotonic()

        # Timeout is enforced by the manager's _safe_run via asyncio.wait_for.
        # The HTTP client timeout in genai.Client (PROVIDER_TIMEOUT_SECONDS * 1000 ms)
        # provides a second layer of protection at the network level.
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

        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 800)
        output_tokens = getattr(usage, "candidates_token_count", 150)

        return self._build_result(raw, latency_ms, input_tokens, output_tokens)
