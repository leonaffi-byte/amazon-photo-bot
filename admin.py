"""
admin.py — Telegram admin panel.

Three sections:
  🏷️  Affiliate Tags   — add / activate / delete Amazon Associate tags
  🔑  API Keys         — set OpenAI, Anthropic, Google, RapidAPI, Amazon keys
  👥  Admins           — list admins, generate invite links, remove admins

Authentication
──────────────
No OAuth needed — Telegram's own identity system is used:
  • Admins are identified by their Telegram user_id (unforgeable — set by Telegram servers)
  • Bootstrap admin IDs come from ADMIN_IDS in .env (you only need to set yours once)
  • Additional admins are added via one-time invite links (30-minute expiry)
    — equivalent to "invite via email" OAuth flows, but Telegram-native

Invite flow (the Telegram-native "OAuth"):
  1. Existing admin taps [🔗 Generate Invite Link]
  2. Bot creates a unique 30-min one-time code, sends a t.me deep-link
  3. Recipient opens the link → Telegram opens the bot → /start <code>
  4. Bot verifies code, adds the user as admin, marks code as used
  5. Code is single-use and expires — cannot be reused or shared further
"""
from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database as db
import key_store
import settings_store
import style as st

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(
    ST_TAG_NAME, ST_TAG_DESC, ST_TAG_CONFIRM,   # add affiliate tag
    ST_KEY_VALUE,                                # set API key
    ST_SETTING_VALUE,                            # edit a bot setting
) = range(5)

# ── Callback prefixes ──────────────────────────────────────────────────────────
P = "adm:"   # all admin callbacks start with this

# Main nav
CB_PANEL      = f"{P}panel"
CB_TAGS       = f"{P}tags"
CB_KEYS       = f"{P}keys"
CB_ADMINS     = f"{P}admins"

# Affiliate tags
CB_TAG_ADD    = f"{P}tag_add"
CB_TAG_ACT    = f"{P}tag_act:"    # + id
CB_TAG_DEL    = f"{P}tag_del:"    # + id
CB_TAG_DELOK  = f"{P}tag_delok:"  # + id
CB_TAG_NONE   = f"{P}tag_none"

# API keys
CB_KEY_SET    = f"{P}key_set:"    # + key_name
CB_KEY_DEL    = f"{P}key_del:"    # + key_name

# Admins
CB_ADM_INV    = f"{P}adm_inv"
CB_ADM_DEL    = f"{P}adm_del:"    # + user_id
CB_ADM_DELOK  = f"{P}adm_delok:"  # + user_id

# Stats / misc
CB_STATS      = f"{P}stats"
CB_TAG_NONEFR = f"{P}tag_none"
CB_BACK_PANEL = f"{P}panel"

# Shortener
CB_SHORTENER     = f"{P}shortener"
CB_SHORT_DEL     = f"{P}short_del:"    # + code
CB_SHORT_DELOK   = f"{P}short_delok:"  # + code

# Settings
CB_SETTINGS      = f"{P}settings"
CB_SET_EDIT      = f"{P}set_edit:"     # + setting_key
CB_SET_CHOICE    = f"{P}set_choice:"   # + setting_key + ":" + value
CB_SET_RESET     = f"{P}set_reset:"    # + setting_key
CB_SET_FREETEXT  = f"{P}set_freetext:" # + setting_key  → enter free-text mode


# ── Auth ───────────────────────────────────────────────────────────────────────

async def is_admin(user_id: int) -> bool:
    """
    True if user_id is an admin.
    Checks DB first (managed list), then falls back to ADMIN_IDS in config
    so bootstrap admins always work even before DB is seeded.
    """
    if user_id in config.ADMIN_IDS:
        return True
    try:
        return await db.is_admin_in_db(user_id)
    except Exception:
        return False


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if not await is_admin(uid):
        if update.message:
            await update.message.reply_text("⛔ Admin access only.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Admin access only.", show_alert=True)
        return False
    return True


# ── Markdown helper ────────────────────────────────────────────────────────────

def e(text: str) -> str:
    """Escape MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — MAIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

async def _panel_content() -> tuple[str, InlineKeyboardMarkup]:
    tags      = await db.get_all_tags()
    stats     = await db.get_stats()
    admins    = await db.get_all_admins()
    all_keys  = await key_store.get_all_keys()
    keys_set  = sum(1 for v in all_keys.values() if v)
    active    = next((t for t in tags if t.is_active), None)
    tag_line  = f"`{e(active.tag)}`" if active else "_none_ ⚠️"

    vision_mode = await settings_store.get("vision_mode")
    search_backend = await settings_store.get("search_backend")

    text = (
        f"⚙️ *ADMIN PANEL*\n{st.DIV}\n\n"
        f"🏷️  Affiliate tag: {tag_line}\n"
        f"🔑  Keys set: *{keys_set}*/{len(all_keys)}\n"
        f"👥  Admins: *{len(admins)}*\n\n"
        f"{st.SDIV}\n"
        f"🤖  Vision mode: `{e(str(vision_mode))}`\n"
        f"🛒  Search backend: `{e(str(search_backend))}`\n"
        f"🔍  Searches: *{stats['total_searches']:,}*\n"
        f"👤  Users: *{stats['unique_users']:,}*\n"
    )
    import config as _cfg
    short_status = f"`{e(_cfg.SHORTENER_BASE_URL)}`" if _cfg.SHORTENER_ENABLED and _cfg.SHORTENER_BASE_URL else "_disabled_"

    text += f"🔗  Shortener: {short_status}\n"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏷️  Affiliate Tags", callback_data=CB_TAGS),
            InlineKeyboardButton("🔑  API Keys",        callback_data=CB_KEYS),
        ],
        [
            InlineKeyboardButton("⚙️  Settings",  callback_data=CB_SETTINGS),
            InlineKeyboardButton("🔗  Shortener", callback_data=CB_SHORTENER),
        ],
        [
            InlineKeyboardButton("🤖  Vision Models", callback_data="adm:models"),
            InlineKeyboardButton("📊  Stats",         callback_data=CB_STATS),
        ],
        [
            InlineKeyboardButton("👥  Admins",  callback_data=CB_ADMINS),
        ],
    ])
    return text, kb


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    text, kb = await _panel_content()
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — AFFILIATE TAGS
# ══════════════════════════════════════════════════════════════════════════════

async def _tags_content() -> tuple[str, InlineKeyboardMarkup]:
    tags = await db.get_all_tags()
    if not tags:
        text = f"🏷️ *AFFILIATE TAGS*\n{st.DIV}\n\n_No tags yet\\. Add one below\\._"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕  Add Tag", callback_data=CB_TAG_ADD)],
            [InlineKeyboardButton("◀  Back",    callback_data=CB_PANEL)],
        ])
        return text, kb

    lines = [f"🏷️ *AFFILIATE TAGS*\n{st.DIV}\n"]
    rows  = []
    for t in tags:
        badge = "✅ *ACTIVE*" if t.is_active else "⬜"
        lines.append(
            f"{badge}  `{e(t.tag)}`\n"
            f"  _{e(t.description)}_   🔍 {t.search_count} searches\n"
        )
        btn_row = []
        if not t.is_active:
            btn_row.append(InlineKeyboardButton(f"✅  Activate {t.tag}", callback_data=f"{CB_TAG_ACT}{t.id}"))
        btn_row.append(InlineKeyboardButton(f"🗑  Delete {t.tag}", callback_data=f"{CB_TAG_DEL}{t.id}"))
        rows.append(btn_row)

    rows += [
        [InlineKeyboardButton("➕  Add Tag",      callback_data=CB_TAG_ADD),
         InlineKeyboardButton("🚫  Disable all",  callback_data=CB_TAG_NONE)],
        [InlineKeyboardButton("◀  Back",           callback_data=CB_PANEL)],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ── Add-tag conversation ───────────────────────────────────────────────────────

_ADD_TAG_PROMPT = (
    f"🏷️ *ADD AFFILIATE TAG*\n{st.DIV}\n\n"
    f"*Step 1 / 2* — Type your Amazon Associate tag:\n\n"
    f"`yourtag-20`\n\n"
    f"{st.SDIV}\n"
    f"_/cancel to abort_"
)


async def _tag_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via callback button."""
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END
    context.user_data["tag_flow"] = {}
    await q.edit_message_text(_ADD_TAG_PROMPT, parse_mode="MarkdownV2")
    return ST_TAG_NAME


async def cmd_addtag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via /addtag command."""
    if not await guard(update, context):
        return ConversationHandler.END
    context.user_data["tag_flow"] = {}
    await update.message.reply_text(_ADD_TAG_PROMPT, parse_mode="MarkdownV2")
    return ST_TAG_NAME


async def received_tag_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    import re
    tag = update.message.text.strip()
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{2,48}\-\d{2}$", tag):
        await update.message.reply_text(
            "⚠️ Invalid format\\. Expected something like `mytag-20`\\.\n\nTry again or /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return ST_TAG_NAME
    context.user_data["tag_flow"]["tag"] = tag
    await update.message.reply_text(
        f"✅ Tag: `{e(tag)}`\n\n"
        "Step 2/2 — Short description \\(for your records\\):\n"
        "e\\.g\\. _Main US tag_, _Backup_\n\n_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_TAG_DESC


async def received_tag_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    desc = update.message.text.strip()[:200]
    flow = context.user_data.get("tag_flow", {})
    tag  = flow.get("tag", "")
    flow["desc"] = desc
    existing = await db.get_all_tags()
    flow["auto_activate"] = len(existing) == 0
    context.user_data["tag_flow"] = flow
    note = "\n_Will be auto\\-activated \\(first tag\\)\\._" if flow["auto_activate"] else ""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="adm:tag_addok"),
        InlineKeyboardButton("❌ Cancel",  callback_data="adm:tag_addcancel"),
    ]])
    await update.message.reply_text(
        f"📋 *Confirm*\nTag: `{e(tag)}`\nDesc: _{e(desc)}_{note}",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    return ST_TAG_CONFIRM


async def tag_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "adm:tag_addcancel":
        await q.edit_message_text("❌ Cancelled\\.", parse_mode="MarkdownV2")
        return ConversationHandler.END
    flow = context.user_data.get("tag_flow", {})
    admin_name = q.from_user.full_name or str(q.from_user.id)
    try:
        await db.add_tag(
            tag=flow["tag"], description=flow["desc"],
            admin_id=q.from_user.id, admin_name=admin_name,
            make_active=flow.get("auto_activate", False),
        )
    except ValueError as exc:
        await q.edit_message_text(f"⚠️ {e(str(exc))}", parse_mode="MarkdownV2")
        return ConversationHandler.END
    text, kb = await _tags_content()
    await q.edit_message_text("✅ Tag added\\!\n\n" + text, parse_mode="MarkdownV2", reply_markup=kb)
    context.user_data.pop("tag_flow", None)
    return ConversationHandler.END


async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("tag_flow", "key_flow", "setting_flow"):
        context.user_data.pop(k, None)
    await update.message.reply_text("❌ Cancelled\\.", parse_mode="MarkdownV2")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — API KEYS
# ══════════════════════════════════════════════════════════════════════════════

_KEY_LABELS = {
    "openai_api_key":       ("🤖 OpenAI",           "Used for GPT-4o vision"),
    "anthropic_api_key":    ("🤖 Anthropic",         "Used for Claude vision"),
    "google_api_key":       ("🤖 Google",            "Used for Gemini vision"),
    "groq_api_key":         ("🤖 Groq",              "Llama vision (free at console.groq.com)"),
    "openrouter_api_key":   ("🤖 OpenRouter",        "100+ vision models via one API (openrouter.ai)"),
    "rapidapi_key":         ("🛒 RapidAPI",          "Amazon product search (recommended)"),
    "amazon_access_key":    ("🛒 Amazon Access Key", "PA-API (optional)"),
    "amazon_secret_key":    ("🛒 Amazon Secret Key", "PA-API (optional)"),
    "amazon_associate_tag": ("🛒 Associate Tag",     "PA-API affiliate tag (optional)"),
    "bitly_token":          ("🔗 bit.ly Token",      "URL shortener — free at bitly.com (optional)"),
}


async def _keys_content() -> tuple[str, InlineKeyboardMarkup]:
    all_keys = await key_store.get_all_keys()
    lines = [
        f"🔑 *API KEYS*\n{st.DIV}\n",
        f"_Keys set here override your \\.env file\\._\n",
        f"{st.SDIV}\n",
    ]
    rows = []
    for key_name, (label, desc) in _KEY_LABELS.items():
        val    = all_keys.get(key_name)
        masked = e(key_store.mask(val))
        lines.append(f"*{e(label)}*\n  {masked}\n  _{e(desc)}_\n")
        btn_row = [InlineKeyboardButton(f"✏️  {label}", callback_data=f"{CB_KEY_SET}{key_name}")]
        if val:
            btn_row.append(InlineKeyboardButton("🗑", callback_data=f"{CB_KEY_DEL}{key_name}"))
        rows.append(btn_row)

    rows.append([InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# Set-key conversation
async def _key_set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END
    key_name = q.data[len(CB_KEY_SET):]
    label, desc = _KEY_LABELS.get(key_name, (key_name, ""))
    context.user_data["key_flow"] = {"key_name": key_name, "label": label}
    await q.edit_message_text(
        f"🔑 *SET API KEY*\n{st.DIV}\n\n"
        f"*{e(label)}*\n_{e(desc)}_\n\n"
        f"{st.SDIV}\n"
        "Type or paste the key value\\.\n\n"
        "🔒 _Your message will be deleted immediately after saving\\._\n\n"
        "_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_KEY_VALUE


async def received_key_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    value    = update.message.text.strip()
    flow     = context.user_data.get("key_flow", {})
    key_name = flow.get("key_name", "")
    label    = flow.get("label", key_name)

    # Delete the user's message immediately so the key doesn't sit in chat history
    try:
        await update.message.delete()
    except Exception:
        pass

    if not value:
        await update.message.reply_text("⚠️ Empty value — not saved\\.", parse_mode="MarkdownV2")
        return ST_KEY_VALUE

    await key_store.set(key_name, value, update.effective_user.id)

    # Reload providers / search backend so new key takes effect immediately
    _reload_backends(key_name)

    text, kb = await _keys_content()
    await update.message.reply_text(
        f"✅ *{e(label)}* saved\\! \\(bot reloaded\\)\n\n" + text,
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    context.user_data.pop("key_flow", None)
    return ConversationHandler.END


def _reload_backends(changed_key: str) -> None:
    """
    Reset cached backends so they pick up the new key on next use.
    Called after any API key is changed in the admin panel.
    """
    import amazon_search
    amazon_search._backend = None   # force re-init with new key

    if changed_key in ("openai_api_key", "anthropic_api_key", "google_api_key",
                       "groq_api_key", "openrouter_api_key"):
        import providers.manager as pm
        pm._providers = {}          # force re-init of vision providers


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — ADMIN MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def _admins_content(viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    admins = await db.get_all_admins()
    lines  = [f"👥 *ADMINS*\n{st.DIV}\n"]
    rows   = []
    for adm in admins:
        name = e(adm.full_name or adm.username or str(adm.user_id))
        you  = "  ✦ _you_" if adm.user_id == viewer_id else ""
        lines.append(f"▸ *{name}*{you}   `{adm.user_id}`")
        if adm.user_id != viewer_id:
            rows.append([InlineKeyboardButton(
                f"🗑  Remove {adm.full_name or adm.user_id}",
                callback_data=f"{CB_ADM_DEL}{adm.user_id}",
            )])

    rows += [
        [InlineKeyboardButton("🔗  Generate Invite Link", callback_data=CB_ADM_INV)],
        [InlineKeyboardButton("◀  Back",                   callback_data=CB_PANEL)],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════════════════════════

async def _stats_content() -> str:
    stats = await db.get_stats()
    tags  = await db.get_all_tags()

    per_tag = []
    for t in tags:
        n = stats["searches_per_tag"].get(t.tag, 0)
        mark = "  ✅" if t.is_active else ""
        per_tag.append(f"  ▸ `{e(t.tag)}`{mark}  — {n}")
    no_tag = stats["searches_per_tag"].get("none", 0)
    if no_tag:
        per_tag.append(f"  ▸ _\\(no tag\\)_ — {no_tag}")

    def pct(a, b):
        return f"{a/b*100:.1f}" if b else "0"

    tag_block = "\n".join(per_tag) if per_tag else "  _no data_"
    return (
        f"📊 *STATS*\n{st.DIV}\n\n"
        f"🔍  Searches: *{stats['total_searches']:,}*\n"
        f"👤  Users: *{stats['unique_users']:,}*\n"
        f"🇮🇱  Israel filter: *{stats['israel_filter_uses']:,}×* "
        f"\\({e(pct(stats['israel_filter_uses'], stats['total_searches']))}%\\)\n"
        f"🕐  Last: `{e(str(stats['last_search'])[:19])}`\n\n"
        f"{st.SDIV}\n"
        f"*Searches per tag:*\n{tag_block}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — URL SHORTENER
# ══════════════════════════════════════════════════════════════════════════════

async def _shortener_content() -> tuple[str, InlineKeyboardMarkup]:
    import config as _cfg
    from url_shortener import active_backend_name

    stats = await db.get_shortener_stats()

    if _cfg.SHORTENER_ENABLED and _cfg.SHORTENER_BASE_URL:
        backend_line = f"🟢 *Custom* — `{e(_cfg.SHORTENER_BASE_URL)}`"
        port_line    = f"Port: `{_cfg.SHORTENER_PORT}`"
    else:
        backend_line = f"🟡 *External* — {e(active_backend_name())}"
        port_line    = "_Self-hosted server not running_"

    # Top links table
    top_lines = []
    for link in stats["top_links"]:
        code   = e(link["code"])
        clicks = link["clicks"]
        label  = e(link["label"][:30]) if link["label"] else "_no label_"
        top_lines.append(f"  ▸ `{code}`  {clicks} clicks  _{label}_")

    top_block = "\n".join(top_lines) if top_lines else "  _no links yet_"

    text = (
        f"🔗 *URL SHORTENER*\n{st.DIV}\n\n"
        f"{backend_line}\n"
        f"{port_line}\n\n"
        f"{st.SDIV}\n"
        f"📊  *Stats*\n"
        f"  Links:    *{stats['total_links']:,}*\n"
        f"  Clicks:   *{stats['total_clicks']:,}*\n"
        f"  Last 24h: *{stats['clicks_24h']:,}*\n"
        f"  Last 7d:  *{stats['clicks_7d']:,}*\n\n"
        f"*Top 5 links:*\n{top_block}\n\n"
        f"{st.SDIV}\n"
        f"_Set SHORTENER\\_BASE\\_URL in \\.env to activate your own server_"
    )

    # Delete buttons for top links
    rows = [
        [InlineKeyboardButton(
            f"🗑  Delete /{link['code']} ({link['clicks']} clicks)",
            callback_data=f"{CB_SHORT_DEL}{link['code']}",
        )]
        for link in stats["top_links"]
    ]
    rows.append([InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)])
    return text, InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — BOT SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

async def _settings_content() -> tuple[str, InlineKeyboardMarkup]:
    all_vals = await settings_store.get_all()
    lines = [
        f"⚙️ *BOT SETTINGS*\n{st.DIV}\n",
        f"_Changes take effect immediately — no restart needed\\._\n",
        f"_Overrides your \\.env file\\._\n",
        f"{st.SDIV}\n",
    ]
    rows = []
    for key, meta in settings_store.SETTINGS_META.items():
        raw = all_vals.get(key, meta["default"])
        lines.append(f"*{e(meta['label'])}*\n  `{e(raw)}`  _{e(meta['desc'])}_\n")
        rows.append([InlineKeyboardButton(
            f"✏️  {meta['label']}", callback_data=f"{CB_SET_EDIT}{key}"
        )])

    rows.append([InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _setting_edit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via callback — show current value and prompt for new one."""
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END

    key = q.data[len(CB_SET_EDIT):]
    meta = settings_store.SETTINGS_META.get(key)
    if not meta:
        await q.answer("Unknown setting.", show_alert=True)
        return ConversationHandler.END

    current = await settings_store.get_raw(key)
    context.user_data["setting_flow"] = {"key": key, "meta": meta}

    # If this setting has a fixed choice list, show buttons instead of free text.
    # If allow_custom=True, also show a "📝 Custom…" button for free-text entry.
    if meta["choices"]:
        rows = [
            [InlineKeyboardButton(
                f"{'✅ ' if c == current else ''}{c}",
                callback_data=f"{CB_SET_CHOICE}{key}:{c}",
            )]
            for c in meta["choices"]
        ]
        if meta.get("allow_custom"):
            rows.append([InlineKeyboardButton(
                "📝  Enter custom value…",
                callback_data=f"{CB_SET_FREETEXT}{key}",
            )])
        rows.append([InlineKeyboardButton("◀  Cancel", callback_data=CB_SETTINGS)])
        await q.edit_message_text(
            f"⚙️ *{e(meta['label'])}*\n{st.DIV}\n\n"
            f"_{e(meta['desc'])}_\n\n"
            f"Current: `{e(current)}`\n\n"
            f"Choose a value:",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return ConversationHandler.END   # handled entirely via callbacks

    # Free-text setting
    await q.edit_message_text(
        f"⚙️ *{e(meta['label'])}*\n{st.DIV}\n\n"
        f"_{e(meta['desc'])}_\n\n"
        f"Current: `{e(current)}`\n\n"
        f"Type the new value and send it\\.\n\n"
        f"{st.SDIV}\n"
        f"_/cancel to abort  ·  /reset\\_setting to restore default_",
        parse_mode="MarkdownV2",
    )
    return ST_SETTING_VALUE


async def received_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END

    flow = context.user_data.get("setting_flow", {})
    key  = flow.get("key", "")
    meta = flow.get("meta", {})

    raw = update.message.text.strip()

    # Validate
    try:
        settings_store._cast(raw, meta.get("type", "str"))
    except (ValueError, TypeError) as exc:
        await update.message.reply_text(
            f"⚠️ Invalid value: {e(str(exc))}\n\nTry again or /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return ST_SETTING_VALUE

    await settings_store.set(key, raw, update.effective_user.id)

    text, kb = await _settings_content()
    await update.message.reply_text(
        f"✅ *{e(meta.get('label', key))}* set to `{e(raw)}`\\!\n\n" + text,
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    context.user_data.pop("setting_flow", None)
    return ConversationHandler.END


async def reset_setting_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Allow /reset_setting inside a setting conversation to clear the DB override."""
    if not await guard(update, context):
        return ConversationHandler.END
    flow = context.user_data.get("setting_flow", {})
    key  = flow.get("key")
    if not key:
        await update.message.reply_text("Nothing to reset\\.", parse_mode="MarkdownV2")
        return ConversationHandler.END
    await settings_store.delete(key)
    meta = settings_store.SETTINGS_META.get(key, {})
    default = meta.get("default", "")
    text, kb = await _settings_content()
    await update.message.reply_text(
        f"↩️ *{e(meta.get('label', key))}* reset to default: `{e(default)}`\n\n" + text,
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    context.user_data.pop("setting_flow", None)
    return ConversationHandler.END


async def _setting_freetext_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry via the '📝 Enter custom value…' button on a choices-based setting.
    Transitions the conversation into free-text mode (ST_SETTING_VALUE).
    """
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END

    key  = q.data[len(CB_SET_FREETEXT):]
    meta = settings_store.SETTINGS_META.get(key)
    if not meta:
        await q.answer("Unknown setting.", show_alert=True)
        return ConversationHandler.END

    current = await settings_store.get_raw(key)
    context.user_data["setting_flow"] = {"key": key, "meta": meta}

    await q.edit_message_text(
        f"⚙️ *{e(meta['label'])}*\n{st.DIV}\n\n"
        f"_{e(meta['desc'])}_\n\n"
        f"Current: `{e(current)}`\n\n"
        f"Type the new value and send it\\.\n\n"
        f"{st.SDIV}\n"
        f"_/cancel to abort  ·  /reset\\_setting to restore default_",
        parse_mode="MarkdownV2",
    )
    return ST_SETTING_VALUE


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CALLBACK ROUTER
# ══════════════════════════════════════════════════════════════════════════════

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    data = q.data

    if not await is_admin(uid):
        await q.answer("⛔ Admin access only.", show_alert=True)
        return

    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data=CB_PANEL)]])

    # ── Main panel ─────────────────────────────────────────────────────────────
    if data == CB_PANEL:
        text, kb = await _panel_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    # ── Affiliate tags ─────────────────────────────────────────────────────────
    elif data == CB_TAGS:
        text, kb = await _tags_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data == CB_TAG_NONE:
        await db.deactivate_all_tags()
        text, kb = await _tags_content()
        await q.edit_message_text("🚫 All tags deactivated\\.\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_TAG_ACT):
        await db.set_active_tag(int(data[len(CB_TAG_ACT):]))
        text, kb = await _tags_content()
        await q.edit_message_text("✅ Tag activated\\!\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_TAG_DEL) and not data.startswith(CB_TAG_DELOK):
        tag_id = int(data[len(CB_TAG_DEL):])
        tags = await db.get_all_tags()
        tag  = next((t for t in tags if t.id == tag_id), None)
        if not tag:
            await q.answer("Not found.", show_alert=True); return
        warn = " ⚠️ This is the active tag\\!" if tag.is_active else ""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Delete", callback_data=f"{CB_TAG_DELOK}{tag_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_TAGS),
        ]])
        await q.edit_message_text(
            f"🗑 Delete `{e(tag.tag)}`?{warn}\n_{e(tag.description)}_",
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_TAG_DELOK):
        await db.remove_tag(int(data[len(CB_TAG_DELOK):]))
        text, kb = await _tags_content()
        await q.edit_message_text("🗑 Deleted\\.\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    # ── API keys ───────────────────────────────────────────────────────────────
    elif data == CB_KEYS:
        text, kb = await _keys_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_KEY_DEL):
        key_name = data[len(CB_KEY_DEL):]
        await key_store.delete(key_name)
        _reload_backends(key_name)
        text, kb = await _keys_content()
        await q.edit_message_text("🗑 Key cleared \\(bot now uses \\.env fallback\\)\\.\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    # ── Admins ─────────────────────────────────────────────────────────────────
    elif data == CB_ADMINS:
        text, kb = await _admins_content(uid)
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data == CB_ADM_INV:
        label = f"Invited by {q.from_user.full_name or uid}"
        code  = await db.create_invite(created_by=uid, label=label, ttl_minutes=30)
        bot_username = (await q.get_bot().get_me()).username
        deep_link = f"https://t.me/{bot_username}?start=invite_{code}"
        await q.edit_message_text(
            f"🔗 *ADMIN INVITE LINK*\n{st.DIV}\n\n"
            f"`{e(deep_link)}`\n\n"
            f"{st.SDIV}\n"
            "▸ Single\\-use  ·  Expires in *30 minutes*\n"
            "▸ Recipient taps link → bot opens → instant admin access\n\n"
            "_Equivalent to an OAuth invite flow, but Telegram\\-native\\._",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀  Back to Admins", callback_data=CB_ADMINS)
            ]]),
        )

    elif data.startswith(CB_ADM_DEL) and not data.startswith(CB_ADM_DELOK):
        target_id = int(data[len(CB_ADM_DEL):])
        admins = await db.get_all_admins()
        adm = next((a for a in admins if a.user_id == target_id), None)
        if not adm:
            await q.answer("Not found.", show_alert=True); return
        name = e(adm.full_name or str(adm.user_id))
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Remove",  callback_data=f"{CB_ADM_DELOK}{target_id}"),
            InlineKeyboardButton("❌ Cancel",  callback_data=CB_ADMINS),
        ]])
        await q.edit_message_text(
            f"Remove admin *{name}*?",
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_ADM_DELOK):
        target_id = int(data[len(CB_ADM_DELOK):])
        await db.remove_admin(target_id)
        text, kb = await _admins_content(uid)
        await q.edit_message_text("✅ Admin removed\\.\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    # ── Vision Models (delegated to admin_models.py) ──────────────────────────
    elif data.startswith("adm:models"):
        import admin_models as am
        handled = await am.handle_models_callback(update, context)
        if not handled:
            pass   # fall through — unknown sub-command
        return

    # ── Stats ──────────────────────────────────────────────────────────────────
    elif data == CB_STATS:
        text = await _stats_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=back_kb)

    # ── Settings ───────────────────────────────────────────────────────────────
    elif data == CB_SETTINGS:
        text, kb = await _settings_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_SET_CHOICE):
        # Inline choice selection (no conversation needed)
        rest = data[len(CB_SET_CHOICE):]           # "key:value"
        key, _, value = rest.partition(":")
        meta = settings_store.SETTINGS_META.get(key)
        if meta:
            await settings_store.set(key, value, uid)
            text, kb = await _settings_content()
            await q.edit_message_text(
                f"✅ *{e(meta['label'])}* set to `{e(value)}`\\!\n\n" + text,
                parse_mode="MarkdownV2", reply_markup=kb,
            )
        else:
            await q.answer("Unknown setting.", show_alert=True)

    elif data.startswith(CB_SET_RESET):
        key = data[len(CB_SET_RESET):]
        meta = settings_store.SETTINGS_META.get(key)
        if meta:
            await settings_store.delete(key)
            default = meta.get("default", "")
            text, kb = await _settings_content()
            await q.edit_message_text(
                f"↩️ *{e(meta['label'])}* reset to default: `{e(default)}`\n\n" + text,
                parse_mode="MarkdownV2", reply_markup=kb,
            )
        else:
            await q.answer("Unknown setting.", show_alert=True)

    # ── Shortener ──────────────────────────────────────────────────────────────
    elif data == CB_SHORTENER:
        text, kb = await _shortener_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_SHORT_DEL) and not data.startswith(CB_SHORT_DELOK):
        code = data[len(CB_SHORT_DEL):]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Yes, delete", callback_data=f"{CB_SHORT_DELOK}{code}"),
            InlineKeyboardButton("❌  Cancel",       callback_data=CB_SHORTENER),
        ]])
        await q.edit_message_text(
            f"🗑 Delete short link `/{e(code)}` and all its click history?",
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_SHORT_DELOK):
        code = data[len(CB_SHORT_DELOK):]
        await db.delete_short_link(code)
        text, kb = await _shortener_content()
        await q.edit_message_text("🗑 Deleted\\.\n\n" + text, parse_mode="MarkdownV2", reply_markup=kb)


# ══════════════════════════════════════════════════════════════════════════════
# INVITE REDEMPTION  (/start invite_<code>)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_start_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Called by bot.py when /start is received with an invite_ deep-link parameter.
    Validates the one-time code and promotes the user to admin.
    """
    user = update.effective_user
    args = context.args or []
    if not args or not args[0].startswith("invite_"):
        return   # not an invite link — let normal /start handle it

    code = args[0][len("invite_"):]
    label = await db.use_invite(code, user.id)

    if label is None:
        await update.message.reply_text(
            "❌ This invite link is invalid, already used, or has expired\\.\n"
            "Ask an admin for a new one\\.",
            parse_mode="MarkdownV2",
        )
        return

    # Promote
    await db.add_admin(
        user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        added_by=None,   # we don't know who invited (stored separately in invite row)
    )

    await update.message.reply_text(
        f"✅ *Welcome, {e(user.full_name or 'Admin')}\\!*\n\n"
        "You now have admin access\\. Use /admin to open the panel\\.",
        parse_mode="MarkdownV2",
    )
    logger.info("New admin added via invite: %s (%d)", user.full_name, user.id)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

def get_admin_handlers():
    # Conversation: add affiliate tag
    tag_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addtag", cmd_addtag),
            CallbackQueryHandler(_tag_add_entry, pattern=f"^{CB_TAG_ADD}$"),
        ],
        states={
            ST_TAG_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, received_tag_name)],
            ST_TAG_DESC:    [MessageHandler(filters.TEXT & ~filters.COMMAND, received_tag_desc)],
            ST_TAG_CONFIRM: [CallbackQueryHandler(tag_confirm_callback, pattern="^adm:tag_add(ok|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
        allow_reentry=True,
        per_message=False,
    )

    # Conversation: set API key
    key_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(_key_set_entry, pattern=f"^{CB_KEY_SET}"),
        ],
        states={
            ST_KEY_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_key_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
        allow_reentry=True,
        per_message=False,
    )

    # Conversation: edit a free-text bot setting (or custom value for choice settings)
    setting_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(_setting_edit_entry,     pattern=f"^{CB_SET_EDIT}"),
            CallbackQueryHandler(_setting_freetext_entry, pattern=f"^{CB_SET_FREETEXT}"),
        ],
        states={
            ST_SETTING_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_setting_value),
                CommandHandler("reset_setting", reset_setting_cmd),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
        allow_reentry=True,
        per_message=False,
    )

    return [
        CommandHandler("admin", cmd_admin),
        tag_conv,
        key_conv,
        setting_conv,
        # All other adm:* callbacks not handled by conversations
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^adm:(?!tag_add(ok|cancel)$)",
        ),
    ]
