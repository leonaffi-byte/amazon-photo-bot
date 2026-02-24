"""
Google Gemini vision provider — supports gemini-1.5-pro and gemini-1.5-flash.

Pricing (as of early 2025, prompts ≤ 128k tokens):
  gemini-1.5-pro:   $3.50 / 1M input,  $10.50 / 1M output
                    Images: $0.001315 per image
  gemini-1.5-flash: $0.075 / 1M input,  $0.30  / 1M output
                    Images: $0.00002 per image  ← extremely cheap

Why Gemini is useful:
  - gemini-1.5-flash is the CHEAPEST option by a wide margin
  - Good for bulk/background processing when cost matters more than perfection
  - gemini-1.5-pro is competitive with GPT-4o for product recognition
  - Different training → diversity in results for compare mode
"""
from __future__ import annotations

import base64
import time
import logging

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from providers.base import (
    SYSTEM_PROMPT, USER_PROMPT,
    ProviderResult, VisionProvider, parse_json_response,
)

logger = logging.getLogger(__name__)

# Safety settings — relaxed so product images don't get blocked
_SAFETY = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}


class GeminiProvider(VisionProvider):

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.name = "google"
        self.model_id = model
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=SYSTEM_PROMPT,
        )

        _pricing = {
            "gemini-1.5-pro":   (0.0035,  0.0105,  0.001315),
            "gemini-1.5-flash": (0.000075, 0.0003,  0.00002),
            "gemini-2.0-flash": (0.0001,   0.0004,  0.00004),
        }
        rates = _pricing.get(model, (0.0035, 0.0105, 0.001315))
        self.cost_per_1k_input_tokens = rates[0]
        self.cost_per_1k_output_tokens = rates[1]
        self.cost_per_image = rates[2]

    async def analyse(self, image_bytes: bytes) -> ProviderResult:
        # Detect media type
        media_type = "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            media_type = "image/png"

        import PIL.Image
        import io
        pil_image = PIL.Image.open(io.BytesIO(image_bytes))

        t0 = time.monotonic()

        response = await self._model.generate_content_async(
            contents=[pil_image, USER_PROMPT],
            generation_config=genai.GenerationConfig(
                temperature=0,
                max_output_tokens=512,
            ),
            safety_settings=_SAFETY,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = response.text

        # Gemini token counts
        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 800)
        output_tokens = getattr(usage, "candidates_token_count", 150)

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
