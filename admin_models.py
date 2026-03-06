"""
admin_models.py — /admin -> Vision Models panel.

Organized by provider category with per-model status:
  - Which models are loaded (have API key + enabled)
  - Which are available but disabled (have key, toggle off)
  - Which need an API key
  - Model health status

Callback data:
  adm:models              — main models panel
  adm:models:health       — model health table
  adm:models:or           — open OpenRouter browser (first page)
  adm:models:or:{pg}      — paginated OR model list
  adm:models:ort:{h8}     — toggle an OR model
  adm:models:ren:{h8}     — re-enable an auto-disabled model
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

import database as db
import style

logger = logging.getLogger(__name__)

# -- Constants ----------------------------------------------------------------
CB_MODELS        = "adm:models"
CB_MODELS_HEALTH = "adm:models:health"
CB_MODELS_OR     = "adm:models:or"
CB_OR_PAGE       = "adm:models:or:"   # + page number
CB_OR_TOGGLE     = "adm:models:ort:"  # + h8
CB_MODEL_REENABLE = "adm:models:ren:" # + h8

_OR_PAGE_SIZE = 8

# In-memory cache of discovered OR models
_or_cache: list[dict] = []
_hash_to_model: dict[str, dict] = {}


def _h8(model_id: str) -> str:
    return hashlib.md5(model_id.encode()).hexdigest()[:8]


async def _get_or_enabled() -> list[dict]:
    raw = await db.get_setting("openrouter_enabled_models")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


async def _set_or_enabled(models: list[dict]) -> None:
    await db.set_setting("openrouter_enabled_models", json.dumps(models), admin_id=0)
    from providers import manager as pm
    pm.reset_providers()


# -- Model catalog: all known models per provider ----------------------------

# Each entry: (model_id, display_name, env_flag, cost_hint)
_MODEL_CATALOG = {
    "OpenAI": {
        "key": "openai_api_key",
        "icon": "🟢",
        "models": [
            ("gpt-4o-mini", "GPT-4o Mini", "ENABLE_GPT_4O_MINI", "$0.15/1M in"),
            ("gpt-4o", "GPT-4o", "ENABLE_GPT_4O", "$2.50/1M in"),
        ],
    },
    "Anthropic": {
        "key": "anthropic_api_key",
        "icon": "🟠",
        "models": [
            ("claude-3-haiku-20240307", "Claude 3 Haiku", "ENABLE_CLAUDE_3_HAIKU_20240307", "$0.25/1M in"),
            ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "ENABLE_CLAUDE_3_5_SONNET_20241022", "$3.00/1M in"),
        ],
    },
    "Google": {
        "key": "google_api_key",
        "icon": "🔵",
        "models": [
            ("gemini-1.5-flash", "Gemini 1.5 Flash", "ENABLE_GEMINI_1_5_FLASH", "free tier"),
            ("gemini-2.0-flash-001", "Gemini 2.0 Flash", "ENABLE_GEMINI_2_0_FLASH", "free tier"),
            ("gemini-1.5-pro", "Gemini 1.5 Pro", "ENABLE_GEMINI_1_5_PRO", "$1.25/1M in"),
        ],
    },
    "Groq": {
        "key": "groq_api_key",
        "icon": "🟡",
        "models": [
            ("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout", "ENABLE_GROQ_LLAMA4_SCOUT", "$0.11/1M in"),
        ],
    },
    "Mistral": {
        "key": "mistral_api_key",
        "icon": "🟣",
        "models": [
            ("pixtral-12b-2409", "Pixtral 12B", "ENABLE_MISTRAL_PIXTRAL_12B", "$0.10/1M in"),
        ],
    },
    "SambaNova": {
        "key": "sambanova_api_key",
        "icon": "⚫",
        "models": [
            ("Llama-4-Maverick-17B-128E-Instruct", "Llama 4 Maverick", "ENABLE_SAMBANOVA_MAVERICK", "FREE"),
        ],
    },
    "Together AI": {
        "key": "together_api_key",
        "icon": "🔴",
        "models": [
            ("meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "Llama 4 Maverick", "ENABLE_TOGETHER_MAVERICK", "$0.27/1M in"),
        ],
    },
    "Fireworks AI": {
        "key": "fireworks_api_key",
        "icon": "🟤",
        "models": [],  # dynamic — loaded from compat config
    },
    "Azure OpenAI": {
        "key": "azure_openai_key",
        "icon": "☁️",
        "models": [
            ("gpt-4o (Azure)", "GPT-4o on Azure", "ENABLE_AZURE_OPENAI", "Azure pricing"),
        ],
    },
}


# -- Main models panel -------------------------------------------------------

async def models_content() -> tuple[str, InlineKeyboardMarkup]:
    """Top-level models panel: organized by provider with status."""
    import key_store
    import os

    from providers.manager import get_providers
    try:
        providers = await get_providers()
    except Exception:
        providers = {}

    # Get health data
    health_rows = await db.get_all_model_health()
    disabled_set = {r["provider_name"] for r in health_rows if r["is_disabled"]}
    failed_map = {r["provider_name"]: r["consecutive_failures"]
                  for r in health_rows if not r["is_disabled"] and r["consecutive_failures"] > 0}

    loaded_names = set(providers.keys())
    total_loaded = len(loaded_names)
    total_disabled = len(disabled_set)

    lines = [
        f"🤖 *VISION MODELS*",
        f"{style.DIV}",
        f"*{total_loaded}* active",
    ]
    if total_disabled:
        lines[-1] += f"  ·  *{total_disabled}* auto\\-disabled"
    lines.append("")

    # Show each provider category
    for provider_name, info in _MODEL_CATALOG.items():
        key_name = info["key"]
        icon = info["icon"]
        has_key = bool(await key_store.get(key_name))

        if not has_key and not info["models"]:
            continue  # Skip empty categories with no key

        models = info["models"]
        if not models and not has_key:
            continue

        # Count loaded models for this provider
        provider_prefix = provider_name.lower().replace(" ", "").replace("ai", "")
        loaded_here = []
        disabled_here = []

        for model_id, display, env_flag, cost in models:
            # Find matching loaded provider
            full_names = [n for n in loaded_names if model_id in n or display.lower().replace(" ", "-") in n.lower()]
            is_loaded = bool(full_names)
            is_disabled_health = any(n in disabled_set for n in full_names) if full_names else False
            has_failures = any(n in failed_map for n in full_names) if full_names else False
            env_enabled = os.getenv(env_flag, "true").strip().lower() not in ("false", "0", "no")

            if is_loaded:
                loaded_here.append((display, cost, has_failures))
            elif is_disabled_health:
                disabled_here.append((display, "auto\\-disabled"))
            elif has_key and not env_enabled:
                disabled_here.append((display, "toggle off"))

        if not has_key:
            lines.append(f"{icon} *{style.esc(provider_name)}*  —  _no API key_")
        elif loaded_here:
            lines.append(f"{icon} *{style.esc(provider_name)}*")
            for display, cost, has_fail in loaded_here:
                mark = "⚠️" if has_fail else "✅"
                lines.append(f"   {mark} {style.esc(display)}  _{style.esc(cost)}_")
            for display, reason in disabled_here:
                lines.append(f"   ⏸ {style.esc(display)}  _{reason}_")
        elif disabled_here:
            lines.append(f"{icon} *{style.esc(provider_name)}*  ⏸")
            for display, reason in disabled_here:
                lines.append(f"   ⏸ {style.esc(display)}  _{reason}_")
        else:
            lines.append(f"{icon} *{style.esc(provider_name)}*  —  _no models enabled_")

    # OpenRouter section
    or_key = await key_store.get("openrouter_api_key")
    or_enabled = await _get_or_enabled()
    or_loaded = [n for n in loaded_names if "openrouter" in n.lower()]

    lines.append("")
    if or_key:
        lines.append(f"🌐 *OpenRouter*")
        if or_loaded:
            lines.append(f"   ✅ {len(or_loaded)} model{'s' if len(or_loaded) != 1 else ''} enabled")
            for name in sorted(or_loaded)[:5]:
                short = name.split("/")[-1][:30]
                mark = "⚠️" if name in failed_map else "✅"
                lines.append(f"   {mark} {style.esc(short)}")
            if len(or_loaded) > 5:
                lines.append(f"   _\\+{len(or_loaded) - 5} more\\.\\.\\._")
        elif or_enabled:
            lines.append(f"   ⏸ {len(or_enabled)} configured but not loaded")
        else:
            lines.append(f"   _Tap Browse to add models_")
    else:
        lines.append(f"🌐 *OpenRouter*  —  _no API key_")
        lines.append(f"   _Add key to browse 100\\+ vision models_")

    # Buttons
    rows: list[list[InlineKeyboardButton]] = []

    if total_disabled:
        rows.append([InlineKeyboardButton("🏥 Model Health", callback_data=CB_MODELS_HEALTH)])

    if or_key:
        rows.append([InlineKeyboardButton("🌐 Browse OpenRouter Models", callback_data=CB_MODELS_OR)])

    rows.append([InlineKeyboardButton("🔙 Back", callback_data="adm:panel")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


# -- Health panel -------------------------------------------------------------

async def health_content() -> tuple[str, InlineKeyboardMarkup]:
    health_rows = await db.get_all_model_health()

    lines = [
        "🏥 *MODEL HEALTH*",
        f"{style.DIV}",
    ]

    if not health_rows:
        lines.append("_No failure data yet\\._")
    else:
        for r in health_rows:
            status = "🔴 DISABLED" if r["is_disabled"] else (
                "🟡 unstable" if r["consecutive_failures"] >= 2 else "🟢 ok"
            )
            short = style.esc(r["provider_name"].split("/")[-1][:22])
            lines.append(f"  {status} `{short}`")
            if r["consecutive_failures"]:
                lines.append(f"    Failures: {r['consecutive_failures']}×")
            if r["last_failure_reason"]:
                lines.append(f"    Last: _{style.esc(r['last_failure_reason'][:60])}_")

    # Re-enable buttons for disabled models
    buttons: list[list[InlineKeyboardButton]] = []
    for r in health_rows:
        if r["is_disabled"]:
            h = _h8(r["provider_name"])
            _hash_to_model[h] = {"provider_name": r["provider_name"]}
            buttons.append([InlineKeyboardButton(
                f"♻️ Re-enable {r['provider_name'].split('/')[-1][:20]}",
                callback_data=f"{CB_MODEL_REENABLE}{h}",
            )])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=CB_MODELS)])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# -- OpenRouter browser -------------------------------------------------------

async def or_page_content(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Show one page of discovered OpenRouter vision models."""
    global _or_cache

    import key_store
    or_key = await key_store.get("openrouter_api_key")
    if not or_key:
        return "❌ OpenRouter key not set\\.", InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data=CB_MODELS)
        ]])

    # Auto-discover if cache is empty
    if not _or_cache:
        try:
            from providers.openrouter_provider import discover_vision_models
            _or_cache = await discover_vision_models(or_key)
        except Exception as exc:
            return f"❌ Discovery failed: {style.esc(str(exc)[:100])}", InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=CB_MODELS)
            ]])

    enabled_ids = {m["id"] for m in await _get_or_enabled()}

    total  = len(_or_cache)
    start  = page * _OR_PAGE_SIZE
    chunk  = _or_cache[start : start + _OR_PAGE_SIZE]
    pages  = (total + _OR_PAGE_SIZE - 1) // _OR_PAGE_SIZE

    lines = [
        f"🌐 *OPENROUTER MODELS* \\({total} vision\\)",
        f"{style.SDIV}",
        f"Page {page+1}/{pages}  •  ✅ \\= enabled\n",
    ]

    buttons: list[list[InlineKeyboardButton]] = []
    for m in chunk:
        h    = _h8(m["id"])
        _hash_to_model[h] = m

        enabled = m["id"] in enabled_ids
        mark    = "✅" if enabled else "☐"

        cost_str = f"\\${m['input_1k']:.4f}/1k"
        name_str = style.esc(m["name"][:35])
        lines.append(f"  {mark} *{name_str}*")
        lines.append(f"     {style.esc(m['id'][:40])}  {style.esc(cost_str)}")

        buttons.append([InlineKeyboardButton(
            f"{'✅ Disable' if enabled else '☐ Enable'}  {m['name'][:30]}",
            callback_data=f"{CB_OR_TOGGLE}{h}",
        )])

    # Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"{CB_OR_PAGE}{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"{CB_OR_PAGE}{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data=CB_MODELS_OR)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=CB_MODELS)])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# -- Callback handler ---------------------------------------------------------

async def handle_models_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle all model-related callbacks. Returns True if handled."""
    query = update.callback_query
    data  = query.data or ""

    if data == CB_MODELS:
        text, kb = await models_content()
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        return True

    if data == CB_MODELS_HEALTH:
        text, kb = await health_content()
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        return True

    if data == CB_MODELS_OR or data == f"{CB_OR_PAGE}0":
        global _or_cache
        _or_cache = []   # force re-discovery
        text, kb = await or_page_content(0)
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb,
                                      disable_web_page_preview=True)
        return True

    if data.startswith(CB_OR_PAGE):
        pg = int(data[len(CB_OR_PAGE):])
        text, kb = await or_page_content(pg)
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb,
                                      disable_web_page_preview=True)
        return True

    if data.startswith(CB_OR_TOGGLE):
        h8 = data[len(CB_OR_TOGGLE):]
        m  = _hash_to_model.get(h8)
        if not m:
            await query.answer("Session lost — please re-open the panel.", show_alert=True)
            return True

        enabled  = await _get_or_enabled()
        is_on    = any(e["id"] == m["id"] for e in enabled)

        if is_on:
            enabled = [e for e in enabled if e["id"] != m["id"]]
            action  = "disabled"
        else:
            enabled.append({"id": m["id"], "input_1k": m["input_1k"], "output_1k": m["output_1k"]})
            action  = "enabled"

        await _set_or_enabled(enabled)
        await query.answer(f"{action.capitalize()}: {m['name'][:30]}", show_alert=False)

        try:
            idx  = next(i for i, c in enumerate(_or_cache) if c["id"] == m["id"])
            page = idx // _OR_PAGE_SIZE
        except StopIteration:
            page = 0
        text, kb = await or_page_content(page)
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb,
                                      disable_web_page_preview=True)
        return True

    if data.startswith(CB_MODEL_REENABLE):
        h8 = data[len(CB_MODEL_REENABLE):]
        m  = _hash_to_model.get(h8)
        if not m:
            await query.answer("Session lost — please re-open the panel.", show_alert=True)
            return True
        pname = m.get("provider_name", "")
        await db.re_enable_model(pname)
        from providers import manager as pm
        pm.reset_providers()
        await query.answer(f"Re-enabled: {pname.split('/')[-1]}", show_alert=False)
        text, kb = await health_content()
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        return True

    return False


def get_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(
        handle_models_callback,
        pattern=r"^adm:models",
    )
