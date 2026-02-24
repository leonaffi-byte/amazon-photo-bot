"""
Provider Manager — initialises, runs, and compares all enabled vision providers.
Keys are read from key_store (DB → .env fallback) on every cold-start so that
changing a key in the admin panel takes effect without restarting the bot.

Modes:
  best      — run all enabled providers in parallel, return highest quality_score winner
  cheapest  — run only the cheapest available provider
  compare   — run all in parallel, return ALL results (for side-by-side display)
  single:X  — run only provider named X (e.g. "single:openai/gpt-4o")
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from providers.base import ProviderResult, VisionProvider

logger = logging.getLogger(__name__)

# Module-level cache — reset to {} by admin.py when a key changes
_providers: dict[str, VisionProvider] = {}


async def _build_providers() -> dict[str, VisionProvider]:
    """
    Instantiate every provider whose API key is available (DB or .env).
    Returns dict keyed by full_name, ordered cheapest-first.
    """
    import key_store
    providers: dict[str, VisionProvider] = {}

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_key = await key_store.get("openai_api_key")
    if openai_key:
        from providers.openai_provider import OpenAIProvider
        for model in ["gpt-4o-mini", "gpt-4o"]:
            p = OpenAIProvider(openai_key, model)
            providers[p.full_name] = p
            logger.info("Loaded provider: %s", p.full_name)

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_key = await key_store.get("anthropic_api_key")
    if anthropic_key:
        from providers.anthropic_provider import AnthropicProvider
        for model in ["claude-3-haiku-20240307", "claude-3-5-sonnet-20241022"]:
            p = AnthropicProvider(anthropic_key, model)
            providers[p.full_name] = p
            logger.info("Loaded provider: %s", p.full_name)

    # ── Google ────────────────────────────────────────────────────────────────
    google_key = await key_store.get("google_api_key")
    if google_key:
        from providers.gemini_provider import GeminiProvider
        for model in ["gemini-1.5-flash", "gemini-1.5-pro"]:
            p = GeminiProvider(google_key, model)
            providers[p.full_name] = p
            logger.info("Loaded provider: %s", p.full_name)

    if not providers:
        raise RuntimeError(
            "No vision providers available.\n"
            "Set at least one key via /admin → 🔑 API Keys:\n"
            "  • OpenAI API key\n"
            "  • Anthropic API key\n"
            "  • Google API key"
        )

    return providers


async def get_providers() -> dict[str, VisionProvider]:
    global _providers
    if not _providers:
        _providers = await _build_providers()
    return _providers


async def cheapest_provider() -> VisionProvider:
    providers = await get_providers()
    return min(
        providers.values(),
        key=lambda p: p.cost_per_image + p.cost_per_1k_input_tokens * 0.8,
    )


# ── Core analysis function ────────────────────────────────────────────────────

async def analyse_image(
    image_bytes: bytes,
    mode: str = "best",
) -> tuple[ProviderResult, list[ProviderResult]]:
    """
    Run image analysis using the requested mode.

    Returns:
        (winner, all_results)
    """
    providers = await get_providers()

    if mode == "cheapest":
        targets = [await cheapest_provider()]
    elif mode.startswith("single:"):
        name = mode[len("single:"):]
        if name not in providers:
            available = ", ".join(providers)
            raise ValueError(f"Provider '{name}' not available. Available: {available}")
        targets = [providers[name]]
    else:
        targets = list(providers.values())

    async def _safe_run(provider: VisionProvider) -> Optional[ProviderResult]:
        try:
            result = await provider.analyse(image_bytes)
            logger.info(
                "[%s] OK — confidence=%s cost=%s latency=%dms",
                provider.full_name, result.confidence, result.cost_str, result.latency_ms,
            )
            return result
        except Exception as exc:
            logger.error("[%s] Failed: %s", provider.full_name, exc)
            return None

    raw_results = await asyncio.gather(*[_safe_run(p) for p in targets])
    all_results  = [r for r in raw_results if r is not None]

    if not all_results:
        raise RuntimeError("All vision providers failed. Add or check your API keys in /admin → 🔑 API Keys.")

    winner = max(all_results, key=lambda r: r.quality_score)
    return winner, all_results
