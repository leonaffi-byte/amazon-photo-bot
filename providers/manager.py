"""
Provider Manager — initialises, runs, and compares all enabled vision providers.
Keys are read from key_store (DB → .env fallback) on every cold-start so that
changing a key in the admin panel takes effect without restarting the bot.

Modes:
  best      — run all enabled providers in parallel, return highest quality_score winner
  cheapest  — run only the cheapest available provider
  compare   — run all in parallel, return ALL results (for side-by-side display)
  single:X  — run only provider named X (e.g. "single:openai/gpt-4o")

Per-model enable/disable via environment variables (all default to true):
  ENABLE_GPT_4O_MINI=true/false
  ENABLE_GPT_4O=true/false
  ENABLE_CLAUDE_3_HAIKU_20240307=true/false
  ENABLE_CLAUDE_3_5_SONNET_20241022=true/false
  ENABLE_GEMINI_1_5_FLASH=true/false
  ENABLE_GEMINI_2_0_FLASH=true/false
  ENABLE_GEMINI_1_5_PRO=true/false
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from providers.base import ProviderResult, VisionProvider

logger = logging.getLogger(__name__)

# Module-level cache — reset to {} by admin.py when a key changes
_providers: dict[str, VisionProvider] = {}


def _model_enabled(env_key: str, default: bool = True) -> bool:
    """
    Check whether a specific model is enabled via an environment variable.
    Default is True for most models; pass default=False to require explicit opt-in.
    """
    raw = os.getenv(env_key, "true" if default else "false")
    return raw.strip().lower() not in ("false", "0", "no")


async def _build_providers() -> dict[str, VisionProvider]:
    """
    Instantiate every provider whose API key is available (DB or .env)
    AND whose per-model toggle is enabled AND not auto-disabled.
    Returns dict keyed by full_name, ordered cheapest-first.
    """
    import key_store
    import database as db
    try:
        disabled = await db.get_disabled_models()
    except Exception:
        disabled = set()   # DB unavailable (e.g. during tests) — treat all as enabled

    providers: dict[str, VisionProvider] = {}

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_key = await key_store.get("openai_api_key")
    if openai_key:
        from providers.openai_provider import OpenAIProvider
        for model, env_flag in [
            ("gpt-4o-mini", "ENABLE_GPT_4O_MINI"),
            ("gpt-4o",      "ENABLE_GPT_4O"),
        ]:
            if _model_enabled(env_flag):
                p = OpenAIProvider(openai_key, model)
                providers[p.full_name] = p
                logger.info("Loaded provider: %s", p.full_name)
            else:
                logger.info("Skipped provider openai/%s (disabled by %s)", model, env_flag)

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_key = await key_store.get("anthropic_api_key")
    if anthropic_key:
        from providers.anthropic_provider import AnthropicProvider
        for model, env_flag, default_on in [
            ("claude-3-haiku-20240307",    "ENABLE_CLAUDE_3_HAIKU_20240307",    True),
            # Sonnet requires a paid Anthropic tier; opt-in only (set =true to enable).
            ("claude-3-5-sonnet-20241022", "ENABLE_CLAUDE_3_5_SONNET_20241022", False),
        ]:
            if _model_enabled(env_flag, default=default_on):
                p = AnthropicProvider(anthropic_key, model)
                providers[p.full_name] = p
                logger.info("Loaded provider: %s", p.full_name)
            else:
                logger.info("Skipped provider anthropic/%s (disabled by %s)", model, env_flag)

    # ── Google ────────────────────────────────────────────────────────────────
    google_key = await key_store.get("google_api_key")
    if google_key:
        from providers.gemini_provider import GeminiProvider
        for model, env_flag in [
            ("gemini-1.5-flash",   "ENABLE_GEMINI_1_5_FLASH"),
            ("gemini-2.0-flash-001", "ENABLE_GEMINI_2_0_FLASH"),
            ("gemini-1.5-pro",     "ENABLE_GEMINI_1_5_PRO"),
        ]:
            if _model_enabled(env_flag):
                p = GeminiProvider(google_key, model)
                providers[p.full_name] = p
                logger.info("Loaded provider: %s", p.full_name)
            else:
                logger.info("Skipped provider google/%s (disabled by %s)", model, env_flag)

    # ── Groq (Llama 4 Scout vision — very fast & cheap) ──────────────────────
    groq_key = await key_store.get("groq_api_key")
    if groq_key:
        from providers.groq_provider import GroqProvider
        for model, env_flag in [
            # Llama 4 Scout is the only multimodal model currently on Groq
            ("meta-llama/llama-4-scout-17b-16e-instruct", "ENABLE_GROQ_LLAMA4_SCOUT"),
        ]:
            if _model_enabled(env_flag):
                try:
                    p = GroqProvider(groq_key, model)
                    providers[p.full_name] = p
                    logger.info("Loaded provider: %s", p.full_name)
                except Exception as exc:
                    logger.warning("Could not load groq/%s: %s", model, exc)
            else:
                logger.info("Skipped provider groq/%s (disabled by %s)", model, env_flag)

    # ── Azure OpenAI (GPT-4o on Azure infrastructure) ────────────────────────
    azure_key        = await key_store.get("azure_openai_key")
    azure_endpoint   = await key_store.get("azure_openai_endpoint")
    azure_deployment = await key_store.get("azure_openai_deployment")
    if azure_key and azure_endpoint and azure_deployment:
        from providers.azure_openai_provider import AzureOpenAIProvider
        env_flag = "ENABLE_AZURE_OPENAI"
        if _model_enabled(env_flag):
            try:
                p = AzureOpenAIProvider(
                    api_key=azure_key,
                    endpoint=azure_endpoint,
                    deployment=azure_deployment,
                )
                providers[p.full_name] = p
                logger.info("Loaded provider: %s", p.full_name)
            except Exception as exc:
                logger.warning("Could not load Azure OpenAI provider: %s", exc)
        else:
            logger.info("Skipped Azure OpenAI provider (disabled by %s)", env_flag)

    # ── OpenRouter (unified gateway — models chosen by admin in /admin → Models) ─
    openrouter_key = await key_store.get("openrouter_api_key")
    if openrouter_key:
        from providers.openrouter_provider import OpenRouterProvider
        import database as _db
        import json as _json
        # Load the list of admin-enabled OR models from DB
        _or_models_raw = await _db.get_setting("openrouter_enabled_models")
        _or_models: list[dict] = []
        if _or_models_raw:
            try:
                _or_models = _json.loads(_or_models_raw)
            except Exception:
                pass
        for m in _or_models:
            model_id = m.get("id", "")
            if not model_id:
                continue
            if not _model_enabled(f"ENABLE_OR_{model_id.replace('/', '_').upper()}", default=True):
                continue
            try:
                p = OpenRouterProvider(
                    api_key=openrouter_key,
                    model=model_id,
                    input_cost_per_1k=m.get("input_1k", 0.005),
                    output_cost_per_1k=m.get("output_1k", 0.015),
                )
                providers[p.full_name] = p
                logger.info("Loaded provider: %s", p.full_name)
            except Exception as exc:
                logger.warning("Could not load openrouter/%s: %s", model_id, exc)

    # Filter out any auto-disabled models
    if disabled:
        before = len(providers)
        providers = {k: v for k, v in providers.items() if k not in disabled}
        skipped = before - len(providers)
        if skipped:
            logger.info("Skipped %d auto-disabled model(s): %s", skipped, disabled & set(providers))

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

_AUTO_DISABLE_THRESHOLD = 3   # consecutive failures before auto-disabling a model

# Errors that strongly suggest the model is gone / unavailable
_MODEL_GONE_PATTERNS = (
    "404", "not found", "does not exist", "no such model",
    "model_not_found", "invalid model", "deprecated",
)


async def analyse_image(
    image_bytes: bytes,
    mode: str = "best",
    context_hint: Optional[str] = None,
    user_id: int = 0,
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
        import database as db
        try:
            result = await provider.analyse(image_bytes, context_hint=context_hint)
            logger.info(
                "[%s] OK — confidence=%s cost=%s latency=%dms",
                provider.full_name, result.confidence, result.cost_str, result.latency_ms,
            )
            # Log cost + reset failure counter (fast SQLite writes — await directly)
            try:
                await db.log_api_cost(
                    provider_name=provider.full_name,
                    cost_usd=result.cost_usd,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    user_id=user_id,
                )
                await db.reset_model_failures(provider.full_name)
            except Exception as dbe:
                logger.warning("[%s] DB log failed (non-critical): %s", provider.full_name, dbe)
            return result

        except Exception as exc:
            err_str = str(exc).lower()
            logger.error("[%s] Failed: %s", provider.full_name, exc)

            # Track failure and potentially auto-disable
            try:
                consec = await db.increment_model_failures(provider.full_name, str(exc))
                model_gone = any(p in err_str for p in _MODEL_GONE_PATTERNS)
                if model_gone or consec >= _AUTO_DISABLE_THRESHOLD:
                    await db.mark_model_disabled(provider.full_name, str(exc))
                    _providers.pop(provider.full_name, None)
                    import notifications
                    reason = "model not found" if model_gone else f"{consec} consecutive failures"
                    await notifications.admin(
                        f"⚠️ *Auto\\-disabled model*\n"
                        f"`{provider.full_name}`\n"
                        f"Reason: {reason}\n"
                        f"Last error: `{str(exc)[:200]}`\n\n"
                        f"Re\\-enable via /admin → 🤖 Models"
                    )
                    logger.warning("[%s] AUTO-DISABLED after %d failures", provider.full_name, consec)
            except Exception as dbe:
                logger.warning("[%s] DB health tracking failed: %s", provider.full_name, dbe)
            return None

    raw_results = await asyncio.gather(*[_safe_run(p) for p in targets])
    all_results  = [r for r in raw_results if r is not None]

    if not all_results:
        raise RuntimeError("All vision providers failed. Add or check your API keys in /admin → 🔑 API Keys.")

    winner = max(all_results, key=lambda r: r.quality_score)
    return winner, all_results
