"""
OpenRouter vision provider — access hundreds of AI models through one API.

OpenRouter (https://openrouter.ai) is an OpenAI-compatible gateway that provides
access to models from OpenAI, Anthropic, Google, Meta, Mistral, and many others.

Setup:
  1. Sign up at https://openrouter.ai
  2. Create an API key
  3. Set openrouter_api_key via /admin -> API Keys
  4. Go to /admin -> Vision Models -> Discover OpenRouter to pick models

OpenRouter model IDs look like: "openai/gpt-4o", "anthropic/claude-3-haiku",
"google/gemini-pro-vision", "meta-llama/llama-3.2-90b-vision-instruct", etc.

Cross-provider note:
  OpenRouter often charges a small markup (~0-20%) over the direct provider.
  But it's useful for:
    * Models you can't access directly (e.g. some Anthropic tiers)
    * Testing many models with one API key
    * Unified billing
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

_OR_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(VisionProvider):
    """Vision provider that uses any OpenRouter-hosted multimodal model."""

    def __init__(
        self,
        api_key: str,
        model: str,
        input_cost_per_1k: float = 0.005,
        output_cost_per_1k: float = 0.015,
        image_cost: float = 0.0,
        display_name: Optional[str] = None,
    ):
        self.name     = "openrouter"
        self.model_id = model

        # Clean display name: strip the provider prefix for readability
        # "openai/gpt-4o" -> "or/gpt-4o",  "anthropic/claude-3-haiku" -> "or/claude-3-haiku"
        self._display_name = display_name or model.split("/")[-1]

        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=_OR_BASE_URL,
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS),
            default_headers={
                "HTTP-Referer": "https://amazon-photo-bot",
                "X-Title":      "Amazon Photo Bot",
            },
        )

        self.cost_per_1k_input_tokens  = input_cost_per_1k
        self.cost_per_1k_output_tokens = output_cost_per_1k
        self.cost_per_image            = image_cost

    @property
    def full_name(self) -> str:
        return f"openrouter/{self._display_name}"

    async def analyse(
        self,
        image_bytes: bytes,
        context_hint: Optional[str] = None,
    ) -> ProviderResult:
        b64 = base64.b64encode(image_bytes).decode()

        media_type = detect_media_type(image_bytes)

        t0 = time.monotonic()

        response = await asyncio.wait_for(self._client.chat.completions.create(
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
                                "url":    f"data:{media_type};base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": build_user_prompt(context_hint)},
                    ],
                },
            ],
        ), timeout=45)

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 800
        output_tokens = usage.completion_tokens if usage else 150

        return self._build_result(raw, latency_ms, input_tokens, output_tokens)


# ── Discovery helper ─────────────────────────────────────────────────────────

async def discover_vision_models(api_key: str) -> list[dict]:
    """
    Query OpenRouter's public models endpoint and return vision-capable models.

    Each dict has:
        id           — the full model ID (e.g. "openai/gpt-4o")
        name         — human-readable name
        input_1k     — cost per 1k input tokens (USD)
        output_1k    — cost per 1k output tokens (USD)
        context      — context window (tokens)
        provider     — the upstream provider ("openai", "anthropic", etc.)
    """
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_OR_BASE_URL}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"OpenRouter models API returned {resp.status}")
                data = await resp.json()
    except Exception as exc:
        logger.error("Failed to fetch OpenRouter models: %s", exc)
        raise

    models = []
    for m in data.get("data", []):
        arch     = m.get("architecture", {})
        modality = arch.get("modality", "") or arch.get("input_modalities", "")
        # Accept models that accept images as input
        if "image" not in str(modality).lower():
            continue

        pricing     = m.get("pricing", {})
        input_cost  = float(pricing.get("prompt",     "0") or "0") * 1000
        output_cost = float(pricing.get("completion", "0") or "0") * 1000

        models.append({
            "id":        m["id"],
            "name":      m.get("name", m["id"]),
            "input_1k":  input_cost,
            "output_1k": output_cost,
            "context":   m.get("context_length", 0),
            "provider":  m["id"].split("/")[0] if "/" in m["id"] else "unknown",
        })

    # Sort cheapest first
    models.sort(key=lambda x: x["input_1k"])
    return models
