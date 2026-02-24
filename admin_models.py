"""
admin_models.py — /admin → 🤖 Vision Models panel.

Features:
  • Show all currently loaded providers + model health
  • Re-enable auto-disabled models
  • Discover OpenRouter vision models with live pricing
  • Enable / disable specific OpenRouter models
  • Cross-provider comparison: if an OR model matches a direct provider model,
    show the cost difference

Callback data:
  adm:models          — open the main models panel
  adm:models:health   — show model health table
  adm:models:or       — open OpenRouter browser (first page)
  adm:models:or:{pg}  — paginated OR model list
  adm:models:ort:{h8} — toggle an OR model (h8 = first 8 chars of md5)
  adm:models:ren:{h8} — re-enable an auto-disabled model
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

# ── Constants ──────────────────────────────────────────────────────────────────
CB_MODELS       = "adm:models"
CB_MODELS_HEALTH = "adm:models:health"
CB_MODELS_OR    = "adm:models:or"
CB_OR_PAGE      = "adm:models:or:"   # + page number
CB_OR_TOGGLE    = "adm:models:ort:"  # + h8
CB_MODEL_REENABLE = "adm:models:ren:" # + h8

_OR_PAGE_SIZE = 8

# In-memory cache of discovered OR models (cleared on re-discovery)
_or_cache: list[dict] = []
# Hash → model dict mapping (for toggle callbacks)
_hash_to_model: dict[str, dict] = {}


def _h8(model_id: str) -> str:
    return hashlib.md5(model_id.encode()).hexdigest()[:8]


def _esc(text: str) -> str:
    for c in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(c, "\\" + c)
    return text


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
    # Force provider rebuild on next request
    from providers import manager as pm
    pm._providers = {}


# ── Main models panel ─────────────────────────────────────────────────────────

async def models_content() -> tuple[str, InlineKeyboardMarkup]:
    """Top-level models panel: loaded providers + health summary."""
    from providers.manager import get_providers
    try:
        providers = await get_providers()
    except Exception:
        providers = {}

    health_rows = await db.get_all_model_health()
    disabled    = {r["provider_name"] for r in health_rows if r["is_disabled"]}
    failed      = {r["provider_name"]: r["consecutive_failures"]
                   for r in health_rows if not r["is_disabled"] and r["consecutive_failures"] > 0}

    lines = [
        "🤖 *VISION MODELS*",
        f"{style.DIV}",
        f"Active: *{len(providers)}* loaded",
    ]

    if disabled:
        lines.append(f"⚠️  *{len(disabled)} auto\\-disabled* \\(tap health for details\\)")

    lines += ["", "*Loaded models:*"]
    for name in providers:
        conf_mark = "⚠️" if name in failed else "✅"
        lines.append(f"  {conf_mark} `{_esc(name)}`")

    if not providers:
        lines.append("  _None — add API keys in 🔑 API Keys_")

    import key_store
    or_key = await key_store.get("openrouter_api_key")

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🏥 Model Health", callback_data=CB_MODELS_HEALTH)],
    ]
    if or_key:
        rows.append([InlineKeyboardButton("🔍 OpenRouter Models", callback_data=CB_MODELS_OR)])
    else:
        lines += ["", "_Add openrouter\\_api\\_key to browse 100\\+ vision models_"]

    rows.append([InlineKeyboardButton("🔙 Back", callback_data="adm:panel")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ── Health panel ──────────────────────────────────────────────────────────────

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
            short = _esc(r["provider_name"].split("/")[-1][:22])
            lines.append(f"  {status} `{short}`")
            if r["consecutive_failures"]:
                lines.append(f"    Failures: {r['consecutive_failures']}×")
            if r["last_failure_reason"]:
                lines.append(f"    Last: _{_esc(r['last_failure_reason'][:60])}_")

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


# ── OpenRouter browser ────────────────────────────────────────────────────────

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
            return f"❌ Discovery failed: {_esc(str(exc)[:100])}", InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=CB_MODELS)
            ]])

    enabled_ids = {m["id"] for m in await _get_or_enabled()}

    # Get direct-provider models for cross-provider comparison
    from providers.manager import get_providers
    try:
        direct_providers = await get_providers()
    except Exception:
        direct_providers = {}

    # Build a mapping: base model name → direct provider cost
    direct_costs: dict[str, tuple[str, float]] = {}
    for p_name, p_obj in direct_providers.items():
        if "openrouter" in p_name:
            continue
        base = p_obj.model_id.split("/")[-1]
        direct_costs[base] = (p_name, p_obj.cost_per_1k_input_tokens)

    total  = len(_or_cache)
    start  = page * _OR_PAGE_SIZE
    chunk  = _or_cache[start : start + _OR_PAGE_SIZE]
    pages  = (total + _OR_PAGE_SIZE - 1) // _OR_PAGE_SIZE

    lines = [
        f"🌐 *OPENROUTER MODELS* \\({total} vision\\)",
        f"{style.SDIV}",
        f"Page {page+1}/{pages}  •  ✅ = enabled\n",
    ]

    buttons: list[list[InlineKeyboardButton]] = []
    for m in chunk:
        h    = _h8(m["id"])
        _hash_to_model[h] = m

        enabled = m["id"] in enabled_ids
        mark    = "✅" if enabled else "☐"
        base    = m["id"].split("/")[-1]

        # Cross-provider comparison
        cross = ""
        if base in direct_costs:
            d_name, d_cost = direct_costs[base]
            d_short = d_name.split("/")[0]
            savings = (m["input_1k"] - d_cost) / max(d_cost, 0.000001) * 100
            sign    = f"+{savings:.0f}%" if savings > 0 else f"{savings:.0f}%"
            cross   = f" vs {d_short} \\({sign}\\)"

        cost_str = f"\\${m['input_1k']:.4f}/1k"
        name_str = _esc(m["name"][:35])
        lines.append(f"  {mark} *{name_str}*")
        lines.append(f"     {_esc(m['id'][:40])}  {_esc(cost_str)}{cross}")

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


# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_models_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle all model-related callbacks.
    Returns True if the callback was handled, False otherwise.
    """
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
            await query.answer("Expired — please re-open the panel.", show_alert=True)
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

        # Refresh the page
        # Figure out which page this model is on
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
            await query.answer("Expired — please re-open the panel.", show_alert=True)
            return True
        pname = m.get("provider_name", "")
        await db.re_enable_model(pname)
        from providers import manager as pm
        pm._providers = {}
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
