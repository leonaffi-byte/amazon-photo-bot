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

Progressive health degradation:
  After 1 failure in window: state = 'degraded', log warning
  After 2 failures in window: state = 'degraded', send admin notification
  After 3+ failures in window: state = 'disabled', send admin alert, auto-recover after cooldown
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from correlation import get_correlation_id
from providers.base import ProviderResult, VisionProvider
from circuit_breaker import CircuitOpenError, registry as cb_registry
from metrics import VISION_REQUESTS_TOTAL, VISION_LATENCY, API_COST_DOLLARS, ERRORS_TOTAL

logger = logging.getLogger(__name__)

# Module-level cache — reset to {} by admin.py when a key changes
_providers: dict[str, VisionProvider] = {}
_providers_lock = asyncio.Lock()


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

    # Check for models ready for auto-recovery
    try:
        recovery_models = await db.get_models_ready_for_recovery()
        for pname in recovery_models:
            await db.update_model_health_state(
                pname, state="degraded", is_disabled=False,
                last_notification_level=0,
            )
            logger.info("[%s] Auto-recovery: moving from disabled -> degraded for retry", pname)
            import notifications
            _esc_name = pname.replace("-", "\\-").replace(".", "\\.").replace("/", "\\/")
            try:
                asyncio.create_task(notifications.admin(
                    f"\\u2705 `{_esc_name}` recovered and is back online\n"
                    f"State: degraded \\(testing\\)"
                ))
            except Exception:
                pass
    except Exception:
        pass

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

    # ── Mistral (Pixtral 12B — cheap vision) ─────────────────────────────────
    mistral_key = await key_store.get("mistral_api_key")
    if mistral_key:
        from providers.mistral_provider import MistralProvider
        for model, env_flag in [
            ("pixtral-12b-2409", "ENABLE_MISTRAL_PIXTRAL_12B"),
        ]:
            if _model_enabled(env_flag):
                try:
                    p = MistralProvider(mistral_key, model)
                    providers[p.full_name] = p
                    logger.info("Loaded provider: %s", p.full_name)
                except Exception as exc:
                    logger.warning("Could not load mistral/%s: %s", model, exc)
            else:
                logger.info("Skipped provider mistral/%s (disabled by %s)", model, env_flag)

    # ── SambaNova (Llama 4 Maverick — FREE) ──────────────────────────────────
    sambanova_key = await key_store.get("sambanova_api_key")
    if sambanova_key:
        from providers.sambanova_provider import SambaNovaProvider
        for model, env_flag in [
            ("Llama-4-Maverick-17B-128E-Instruct", "ENABLE_SAMBANOVA_MAVERICK"),
        ]:
            if _model_enabled(env_flag):
                try:
                    p = SambaNovaProvider(sambanova_key, model)
                    providers[p.full_name] = p
                    logger.info("Loaded provider: %s", p.full_name)
                except Exception as exc:
                    logger.warning("Could not load sambanova/%s: %s", model, exc)
            else:
                logger.info("Skipped provider sambanova/%s (disabled by %s)", model, env_flag)

    # ── Together AI (Llama 4 Maverick — cheap) ───────────────────────────────
    together_key = await key_store.get("together_api_key")
    if together_key:
        from providers.together_provider import TogetherProvider
        for model, env_flag in [
            ("meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "ENABLE_TOGETHER_MAVERICK"),
        ]:
            if _model_enabled(env_flag):
                try:
                    p = TogetherProvider(together_key, model)
                    providers[p.full_name] = p
                    logger.info("Loaded provider: %s", p.full_name)
                except Exception as exc:
                    logger.warning("Could not load together/%s: %s", model, exc)
            else:
                logger.info("Skipped provider together/%s (disabled by %s)", model, env_flag)

    # ── Fireworks AI ── (disabled: no vision model on account) ──────────
    #     fireworks_key = await key_store.get("fireworks_api_key")
    #     if fireworks_key:
    #         from providers.fireworks_provider import FireworksProvider
    #         for model, env_flag in [
    #             ("accounts/fireworks/models/glm-4p7", "ENABLE_FIREWORKS_GLM4"),
    #         ]:
    #             if _model_enabled(env_flag):
    #                 try:
    #                     p = FireworksProvider(fireworks_key, model)
    #                     providers[p.full_name] = p
    #                     logger.info("Loaded provider: %s", p.full_name)
    #                 except Exception as exc:
    #                     logger.warning("Could not load fireworks/%s: %s", model, exc)
    #             else:
    #                 logger.info("Skipped provider fireworks/%s (disabled by %s)", model, env_flag)

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
    async with _providers_lock:
        if not _providers:
            _providers = await _build_providers()
    return _providers


async def cheapest_provider() -> VisionProvider:
    providers = await get_providers()
    # Estimate for typical call: ~800 input tokens, ~150 output tokens
    return min(
        providers.values(),
        key=lambda p: p.cost_per_image + p.cost_per_1k_input_tokens * 0.8 + getattr(p, 'cost_per_1k_output_tokens', 0) * 0.15,
    )


# -- Progressive health degradation -------------------------------------------

# Errors that strongly suggest the model is gone / unavailable
_MODEL_GONE_PATTERNS = (
    "404", "not found", "does not exist", "no such model",
    "model_not_found", "invalid model", "deprecated",
    "unauthorized", "forbidden", "invalid api key", "authentication",
)


def _escape_md2(text: str) -> str:
    """Escape MarkdownV2 special characters for admin notifications."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _failures_in_window(failure_timestamps: list[float], window_seconds: int) -> int:
    """Count how many failure timestamps fall within the recent time window."""
    cutoff = time.time() - window_seconds
    return sum(1 for ts in failure_timestamps if ts >= cutoff)


async def _handle_progressive_health(
    provider_name: str,
    error_str: str,
    model_gone: bool,
) -> None:
    """
    Progressive degradation logic:
      1 failure in window  -> state='degraded', log warning
      2 failures in window -> state='degraded', send admin notification
      3+ failures in window -> state='disabled', send admin alert, schedule auto-recovery
    """
    import config
    import database as db
    import notifications

    health = await db.get_model_health_row(provider_name)
    if not health:
        return

    failure_window = config.HEALTH_FAILURE_WINDOW
    disable_threshold = config.HEALTH_DISABLE_THRESHOLD
    recovery_cooldown = config.HEALTH_RECOVERY_COOLDOWN

    # If model is definitively gone (404, not found, etc.), disable immediately
    if model_gone:
        disabled_until = time.time() + recovery_cooldown
        await db.update_model_health_state(
            provider_name,
            state="disabled",
            is_disabled=True,
            disabled_until=disabled_until,
            last_notification_level=3,
        )
        _providers.pop(provider_name, None)
        esc_name = _escape_md2(provider_name)
        esc_err = _escape_md2(error_str[:200])
        cooldown_min = recovery_cooldown // 60
        try:
            asyncio.create_task(notifications.admin(
                f"\\u26d4 `{esc_name}` auto\\-disabled\n"
                f"Reason: model not found\n"
                f"Error: `{esc_err}`\n"
                f"Will retry in {cooldown_min} minutes\\.\n\n"
                f"Re\\-enable via /admin \\u2192 Models"
            ))
        except Exception:
            pass
        logger.warning("[%s] AUTO-DISABLED: model not found", provider_name)
        return

    # Count failures within the time window
    failures_in_window = _failures_in_window(health["failure_timestamps"], failure_window)
    prev_notification_level = health["last_notification_level"]

    if failures_in_window >= disable_threshold:
        # Level 3: auto-disable with recovery timer
        disabled_until = time.time() + recovery_cooldown
        await db.update_model_health_state(
            provider_name,
            state="disabled",
            is_disabled=True,
            disabled_until=disabled_until,
            last_notification_level=3,
        )
        _providers.pop(provider_name, None)
        logger.warning(
            "[%s] AUTO-DISABLED after %d failures in %ds window. Will retry in %ds.",
            provider_name, failures_in_window, failure_window, recovery_cooldown,
        )
        if prev_notification_level < 3:
            esc_name = _escape_md2(provider_name)
            esc_err = _escape_md2(error_str[:200])
            cooldown_min = recovery_cooldown // 60
            try:
                asyncio.create_task(notifications.admin(
                    f"\\u26d4 `{esc_name}` auto\\-disabled after "
                    f"{failures_in_window} failures in {failure_window // 60} min\\.\n"
                    f"Last error: `{esc_err}`\n"
                    f"Will retry in {cooldown_min} minutes\\.\n\n"
                    f"Re\\-enable via /admin \\u2192 Models"
                ))
            except Exception:
                pass

    elif failures_in_window == 2:
        # Level 2: degraded + admin alert
        await db.update_model_health_state(
            provider_name,
            state="degraded",
            last_notification_level=max(prev_notification_level, 2),
        )
        logger.warning(
            "[%s] DEGRADED: 2 failures in %ds window — may be experiencing issues",
            provider_name, failure_window,
        )
        if prev_notification_level < 2:
            esc_name = _escape_md2(provider_name)
            try:
                asyncio.create_task(notifications.admin(
                    f"\\ud83d\\udd34 `{esc_name}` had 2 failures in the last "
                    f"{failure_window // 60} min \\u2014 may be experiencing issues"
                ))
            except Exception:
                pass

    elif failures_in_window == 1:
        # Level 1: degraded + log warning only
        await db.update_model_health_state(
            provider_name,
            state="degraded",
            last_notification_level=max(prev_notification_level, 1),
        )
        logger.warning(
            "[%s] DEGRADED: 1 failure in %ds window",
            provider_name, failure_window,
        )
        if prev_notification_level < 1:
            esc_name = _escape_md2(provider_name)
            try:
                asyncio.create_task(notifications.admin(
                    f"\\u26a0\\ufe0f `{esc_name}` had 1 failure in the last "
                    f"{failure_window // 60} min"
                ))
            except Exception:
                pass


# -- Core analysis function ---------------------------------------------------

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

    cid = get_correlation_id()
    logger.info(
        "analyse_image mode=%s targets=[%s] cid=%s",
        mode, ", ".join(t.full_name for t in targets), cid,
    )

    async def _safe_run(provider: VisionProvider) -> Optional[ProviderResult]:
        import database as db
        cb = cb_registry.get(
            f"vision:{provider.full_name}",
            failure_threshold=5,
            recovery_timeout=60.0,
            success_threshold=2,
        )
        last_exc: Exception | None = None

        for attempt in range(2):  # 1 initial + 1 retry
            try:
                result = await cb.call(
                    provider.analyse(image_bytes, context_hint=context_hint)
                )
                logger.info(
                    "[%s] OK -- confidence=%s cost=%s latency=%dms",
                    provider.full_name, result.confidence, result.cost_str, result.latency_ms,
                )
                # Record metrics
                _labels = {"provider": provider.name, "model": provider.model_id}
                VISION_REQUESTS_TOTAL.inc(labels={**_labels, "status": "success"})
                VISION_LATENCY.observe(result.latency_ms / 1000.0, labels=_labels)
                API_COST_DOLLARS.inc(result.cost_usd, labels=_labels)
                # Log cost + record success (resets failure state)
                try:
                    await db.log_api_cost(
                        provider_name=provider.full_name,
                        cost_usd=result.cost_usd,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        user_id=user_id,
                    )
                    # Check if this model was degraded — send recovery notification
                    health = await db.get_model_health_row(provider.full_name)
                    was_degraded = health and health["state"] in ("degraded", "disabled")
                    prev_level = health["last_notification_level"] if health else 0

                    await db.record_model_success(provider.full_name)

                    if was_degraded and prev_level >= 2:
                        esc_name = _escape_md2(provider.full_name)
                        import notifications
                        try:
                            asyncio.create_task(notifications.admin(
                                f"\\u2705 `{esc_name}` recovered and is back online"
                            ))
                        except Exception:
                            pass
                except Exception as dbe:
                    logger.warning("[%s] DB log failed (non-critical): %s", provider.full_name, dbe)
                return result

            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                is_permanent = any(p in err_str for p in _MODEL_GONE_PATTERNS)

                if attempt == 0 and not is_permanent:
                    logger.warning("[%s] Transient failure, retrying in 2s: %s", provider.full_name, exc)
                    await asyncio.sleep(2)
                    continue

                # Final failure — track and apply progressive degradation
                logger.error("[%s] Failed: %s", provider.full_name, exc)
                VISION_REQUESTS_TOTAL.inc(labels={"provider": provider.name, "model": provider.model_id, "status": "error"})
                ERRORS_TOTAL.inc(labels={"module": "providers.manager", "error_type": type(exc).__name__})
                try:
                    await db.increment_model_failures(provider.full_name, str(exc))
                    model_gone = any(p in err_str for p in _MODEL_GONE_PATTERNS)
                    await _handle_progressive_health(
                        provider.full_name, str(exc), model_gone,
                    )
                except Exception as dbe:
                    logger.warning("[%s] DB health tracking failed: %s", provider.full_name, dbe)
                return None
        return None

    raw_results = await asyncio.gather(*[_safe_run(p) for p in targets])
    all_results  = [r for r in raw_results if r is not None]

    if not all_results:
        raise RuntimeError("All vision providers failed. Add or check your API keys in /admin -> API Keys.")

    winner = max(all_results, key=lambda r: r.quality_score)
    return winner, all_results
