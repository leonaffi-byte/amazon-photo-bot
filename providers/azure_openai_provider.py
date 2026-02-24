"""
Azure OpenAI vision provider — GPT-4o via Microsoft Azure.

Use this instead of (or alongside) the direct OpenAI provider when:
  • You have Azure credits or an enterprise Azure agreement
  • You need data residency (Azure processes data in your selected region)
  • Your organisation requires Azure-based AI services for compliance

The model quality is identical to direct OpenAI — it runs the same GPT-4o
weights, just deployed on Azure's infrastructure.

Setup in Azure Portal:
  1. Create an "Azure OpenAI" resource at portal.azure.com
       (search "Azure OpenAI" in the marketplace)
  2. Open the resource → "Go to Azure OpenAI Studio"
  3. Deployments → + New deployment → pick "gpt-4o" or "gpt-4o-mini"
       Give your deployment any name, e.g. "gpt-4o-prod"
  4. Back in the resource → Keys and Endpoint:
       Copy KEY 1  (32-char hex string)
       Copy Endpoint  (https://YOUR-NAME.openai.azure.com/)
  5. In the bot: /admin → 🔑 API Keys → set:
       azure_openai_key        ← KEY 1 value
       azure_openai_endpoint   ← https://YOUR-NAME.openai.azure.com/
       azure_openai_deployment ← gpt-4o-prod  (the name you chose in step 3)

Pricing: same per-token rate as direct OpenAI, billed to your Azure subscription.
  gpt-4o:      $5.00 / 1M input tokens + $15.00 / 1M output tokens
  gpt-4o-mini: $0.15 / 1M input tokens + $0.60 / 1M output tokens
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import openai

from providers.base import (
    SYSTEM_PROMPT, build_user_prompt,
    ProviderResult, VisionProvider, parse_json_response,
)

logger = logging.getLogger(__name__)

# Matches deployment names containing these substrings to cheaper pricing
_MINI_PATTERNS = ("mini", "gpt4o-mini", "gpt-4o-mini")


class AzureOpenAIProvider(VisionProvider):
    """
    Vision provider backed by Azure OpenAI Service.

    One instance = one Azure deployment (model + region).
    You can deploy multiple models (e.g. gpt-4o + gpt-4o-mini) and add
    multiple provider instances by appending extra credentials in the admin.
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-12-01-preview",
    ):
        self.name        = "azure"
        self.model_id    = deployment
        self._deployment = deployment
        self._client     = openai.AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint.rstrip("/"),
            api_version=api_version,
        )

        # Cost depends on the underlying model — infer from deployment name
        is_mini = any(p in deployment.lower() for p in _MINI_PATTERNS)
        if is_mini:
            self.cost_per_1k_input_tokens  = 0.00015
            self.cost_per_1k_output_tokens = 0.0006
        else:
            self.cost_per_1k_input_tokens  = 0.005
            self.cost_per_1k_output_tokens = 0.015
        # Vision image: ~765 tokens for a typical high-detail product photo
        self.cost_per_image = 765 / 1000 * self.cost_per_1k_input_tokens

    @property
    def full_name(self) -> str:
        # Show region-hinted name so admins know which Azure resource it is
        return f"azure/{self._deployment}"

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

        response = await self._client.chat.completions.create(
            model=self._deployment,   # Azure uses deployment name, not model name
            max_tokens=512,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url":    f"data:{media_type};base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": build_user_prompt(context_hint)},
                    ],
                },
            ],
        )

        latency_ms    = int((time.monotonic() - t0) * 1000)
        raw           = response.choices[0].message.content or ""
        usage         = response.usage
        input_tokens  = usage.prompt_tokens     if usage else 800
        output_tokens = usage.completion_tokens if usage else 150

        data = parse_json_response(raw, self.full_name)
        cost = self.estimate_cost(input_tokens, output_tokens)

        return ProviderResult(
            provider_name       = self.full_name,
            model_id            = self._deployment,
            product_name        = data.get("product_name", "Unknown"),
            brand               = data.get("brand"),
            category            = data.get("category", "All"),
            key_features        = data.get("key_features", []),
            amazon_search_query = data.get("amazon_search_query", ""),
            alternative_query   = data.get("alternative_query",
                                           data.get("amazon_search_query", "")),
            confidence          = data.get("confidence", "medium"),
            notes               = data.get("notes", ""),
            latency_ms          = latency_ms,
            input_tokens        = input_tokens,
            output_tokens       = output_tokens,
            cost_usd            = cost,
        )
