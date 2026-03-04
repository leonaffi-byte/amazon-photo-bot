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
from style import esc as e

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(
    ST_TAG_NAME, ST_TAG_DESC, ST_TAG_CONFIRM,   # add affiliate tag
    ST_KEY_VALUE,                                # set API key
    ST_SETTING_VALUE,                            # edit a bot setting
    ST_TAG_IMPORT,                               # import tags CSV file upload
    ST_RL_USER_ID,                               # rate limit: enter user ID
    ST_RL_MAX_REQ,                               # rate limit: enter max requests
    ST_RL_WINDOW,                                # rate limit: enter window seconds
) = range(9)

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
CB_TAG_DEF    = f"{P}tag_def:"    # + id  — set default tag
CB_TAG_CLRDEF = f"{P}tag_clrdef"  # clear default tag

# API keys
CB_KEY_SET    = f"{P}key_set:"    # + key_name
CB_KEY_DEL    = f"{P}key_del:"    # + key_name
CB_KEY_DELOK  = f"{P}key_delok:"  # + key_name  (confirmed)

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

# Export
CB_EXPORT        = f"{P}export"
CB_EXP_SEARCH    = f"{P}exp_search:"   # + date_range
CB_EXP_COST      = f"{P}exp_cost:"     # + date_range
CB_EXP_USER      = f"{P}exp_user:"     # + date_range
CB_EXP_ALL       = f"{P}exp_all:"      # + date_range

# Circuit breakers
CB_CIRCUITS       = f"{P}circuits"
CB_CB_RESET       = f"{P}cb_reset:"     # + circuit name
CB_CB_RESET_ALL   = f"{P}cb_reset_all"

# Rate limits
CB_RATELIMIT     = f"{P}ratelimit"
CB_RL_SET_USER   = f"{P}rl_setuser"   # enter conversation to set user limit
CB_RL_DEL        = f"{P}rl_del:"      # + user_id — confirm delete
CB_RL_DELOK      = f"{P}rl_delok:"    # + user_id — actually delete


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

# Use style.esc for MarkdownV2 escaping (aliased as e for brevity)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — MAIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

async def _panel_content() -> tuple[str, InlineKeyboardMarkup]:
    import asyncio as _aio
    tags, stats, admins, all_keys = await _aio.gather(
        db.get_all_tags(),
        db.get_stats(),
        db.get_all_admins(),
        key_store.get_all_keys(),
    )
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
            InlineKeyboardButton("🔌  Circuits",    callback_data=CB_CIRCUITS),
            InlineKeyboardButton("⏱  Rate Limits", callback_data=CB_RATELIMIT),
        ],
        [
            InlineKeyboardButton("👥  Admins",      callback_data=CB_ADMINS),
            InlineKeyboardButton("📤  Export",      callback_data=CB_EXPORT),
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
        badges = []
        if t.is_active:
            badges.append("✅ *ACTIVE*")
        if t.is_default:
            badges.append("⭐ *DEFAULT*")
        badge = " ".join(badges) if badges else "⬜"
        lines.append(
            f"{badge}  `{e(t.tag)}`\n"
            f"  _{e(t.description)}_   🔍 {t.search_count} searches\n"
        )
        btn_row = []
        if not t.is_active:
            btn_row.append(InlineKeyboardButton(f"✅  Activate {t.tag}", callback_data=f"{CB_TAG_ACT}{t.id}"))
        if not t.is_default:
            btn_row.append(InlineKeyboardButton(f"⭐  Default {t.tag}", callback_data=f"{CB_TAG_DEF}{t.id}"))
        btn_row.append(InlineKeyboardButton(f"🗑  Delete {t.tag}", callback_data=f"{CB_TAG_DEL}{t.id}"))
        rows.append(btn_row)

    rows += [
        [InlineKeyboardButton("➕  Add Tag",      callback_data=CB_TAG_ADD),
         InlineKeyboardButton("🚫  Disable all",  callback_data=CB_TAG_NONE)],
        [InlineKeyboardButton("📤  Export CSV",   callback_data="adm:tag_export"),
         InlineKeyboardButton("📥  Import CSV",   callback_data="adm:tag_import")],
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
    for k in ("tag_flow", "key_flow", "setting_flow", "rl_flow"):
        context.user_data.pop(k, None)
    await update.message.reply_text("❌ Cancelled\\.", parse_mode="MarkdownV2")
    return ConversationHandler.END


# ── Export / Import tags ──────────────────────────────────────────────────────

async def cmd_exporttags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a CSV file with all affiliate tags to the admin."""
    if not await guard(update, context):
        return
    csv_data = await db.export_tags_csv()
    if not csv_data.strip() or csv_data.count("\n") <= 1:
        await update.message.reply_text(
            "⚠️ No tags to export\\.", parse_mode="MarkdownV2"
        )
        return
    import io
    buf = io.BytesIO(csv_data.encode("utf-8"))
    buf.name = "affiliate_tags.csv"
    await update.message.reply_document(
        document=buf,
        caption="📤 Affiliate tags exported.",
    )


async def _tag_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline 'Export CSV' button inside the tags panel."""
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return
    csv_data = await db.export_tags_csv()
    if not csv_data.strip() or csv_data.count("\n") <= 1:
        await q.answer("No tags to export.", show_alert=True)
        return
    import io
    buf = io.BytesIO(csv_data.encode("utf-8"))
    buf.name = "affiliate_tags.csv"
    await q.message.reply_document(
        document=buf,
        caption="📤 Affiliate tags exported.",
    )


async def cmd_importtags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt the admin to upload a CSV file for bulk tag import."""
    if not await guard(update, context):
        return ConversationHandler.END
    await update.message.reply_text(
        f"📥 *IMPORT AFFILIATE TAGS*\n{st.DIV}\n\n"
        "Upload a CSV file with columns:\n"
        "`tag_name, description, is_active, is_default`\n\n"
        "Only `tag_name` is required\\. Duplicate tags will be skipped\\.\n\n"
        "_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_TAG_IMPORT


async def _tag_import_callback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the inline 'Import CSV' button — enters conversation."""
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END
    await q.edit_message_text(
        f"📥 *IMPORT AFFILIATE TAGS*\n{st.DIV}\n\n"
        "Upload a CSV file with columns:\n"
        "`tag_name, description, is_active, is_default`\n\n"
        "Only `tag_name` is required\\. Duplicate tags will be skipped\\.\n\n"
        "_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_TAG_IMPORT


async def received_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process uploaded CSV file for tag import."""
    if not await guard(update, context):
        return ConversationHandler.END

    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "⚠️ Please upload a CSV file\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_TAG_IMPORT

    if doc.file_size > 1_000_000:  # 1 MB limit
        await update.message.reply_text(
            "⚠️ File too large \\(max 1 MB\\)\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_TAG_IMPORT

    try:
        file = await doc.get_file()
        data = await file.download_as_bytearray()
        csv_text = data.decode("utf-8")
    except Exception as exc:
        logger.error("Failed to download import file: %s", exc)
        await update.message.reply_text(
            "⚠️ Failed to read file\\. Ensure it is a valid UTF\\-8 CSV\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_TAG_IMPORT

    result = await db.import_tags_csv(csv_text, update.effective_user.id)

    text, kb = await _tags_content()
    await update.message.reply_text(
        f"📥 *Import complete\\!*\n\n"
        f"  ✅ Imported: *{result['imported']}*\n"
        f"  ⏭ Skipped \\(duplicates\\): *{result['skipped']}*\n"
        f"  ❌ Errors: *{result['errors']}*\n\n" + text,
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
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
    "azure_openai_key":     ("☁️ Azure OpenAI Key",    "Azure portal → resource → Keys and Endpoint → KEY 1"),
    "azure_openai_endpoint":("☁️ Azure Endpoint",      "https://YOUR-RESOURCE.openai.azure.com/"),
    "azure_openai_deployment":("☁️ Azure Deployment",  "Name you gave the deployment in Azure AI Studio"),
    "decodo_user":          ("🔄 Decodo Username",      "decodo.com residential proxy — rotating Israeli IPs (best). Format: your_username (suffix -country-IL added automatically)"),
    "decodo_password":      ("🔄 Decodo Password",      "decodo.com residential proxy password. Set both decodo_user + decodo_password to enable rotating IPs"),
    "decodo_port":          ("🔌 Decodo Port",           "Port from your Decodo dashboard (default: 7000). Options: 7000=HTTP, 7001=HTTPS, 7002=SOCKS5. Leave empty for 7000"),
    "israel_proxy_url":     ("🇮🇱 Israel Proxy URL",   "Fallback proxy — used automatically if Decodo fails. Format: socks5://user:pass@host:port or http://host:port"),
    "capsolver_api_key":    ("🤖 CapSolver API Key",   "capsolver.com — auto-solves Amazon CAPTCHAs (~$0.80/1000). Used by Israel verifier + Playwright search"),
    "dataforseo_login":     ("🛒 DataForSEO Login",    "Email used at app.dataforseo.com (~$0.003/search, pay-per-use)"),
    "dataforseo_password":  ("🛒 DataForSEO Password", "API password — Dashboard → API Access → Password"),
    "rapidapi_key":         ("🛒 RapidAPI",            "Amazon product search fallback (100 free/month)"),
    "amazon_access_key":    ("🛒 Amazon Access Key",   "PA-API (free, needs Associates qualification)"),
    "amazon_secret_key":    ("🛒 Amazon Secret Key",   "PA-API (optional)"),
    "amazon_associate_tag": ("🛒 Associate Tag",       "PA-API affiliate tag (optional)"),
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

    # Validate the key before saving
    import key_validator
    validating_msg = await update.effective_chat.send_message(
        f"🔄 Validating *{e(label)}*\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    is_valid, error_msg = await key_validator.validate_key_pair(key_name, value)

    if not is_valid:
        # Key is invalid — do NOT save, show error
        try:
            await validating_msg.delete()
        except Exception:
            pass
        await update.effective_chat.send_message(
            f"❌ *{e(label)}* validation failed\\!\n\n"
            f"Error: `{e(error_msg[:300])}`\n\n"
            f"{st.SDIV}\n"
            f"Key was *not saved*\\. Please check the value and try again\\.\n\n"
            f"_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_KEY_VALUE

    # Key is valid (or validation was skipped) — save it
    await key_store.set(key_name, value, update.effective_user.id)

    # Reload providers / search backend so new key takes effect immediately
    _reload_backends(key_name)

    try:
        await validating_msg.delete()
    except Exception:
        pass

    info_suffix = ""
    if error_msg:
        # error_msg contains info (e.g. CapSolver balance) when is_valid=True
        info_suffix = f"\n_{e(error_msg)}_"

    text, kb = await _keys_content()
    await update.effective_chat.send_message(
        f"✅ *{e(label)}* validated and saved\\! \\(bot reloaded\\){info_suffix}\n\n" + text,
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
                       "groq_api_key", "openrouter_api_key",
                       "azure_openai_key", "azure_openai_endpoint", "azure_openai_deployment"):
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
# SECTION 6 — CIRCUIT BREAKERS
# ══════════════════════════════════════════════════════════════════════════════

async def _circuits_content() -> tuple[str, InlineKeyboardMarkup]:
    from circuit_breaker import registry as cb_registry
    import time as _time

    all_stats = cb_registry.get_all_stats()
    if not all_stats:
        text = (
            f"🔌 *CIRCUIT BREAKERS*\n{st.DIV}\n\n"
            f"_No circuits registered yet\\._\n"
            f"_Circuits are created when external services are first called\\._"
        )
        return text, InlineKeyboardMarkup([
            [InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)],
        ])

    state_emoji = {"CLOSED": "🟢", "OPEN": "🔴", "HALF_OPEN": "🟡"}

    lines = [f"🔌 *CIRCUIT BREAKERS*\n{st.DIV}\n"]
    rows = []
    for s in all_stats:
        emoji = state_emoji.get(s["state"], "⚪")
        name = e(s["name"])

        last_fail = ""
        if s["last_failure_time"] is not None:
            ago = _time.monotonic() - s["last_failure_time"]
            if ago < 60:
                last_fail = f"{ago:.0f}s ago"
            elif ago < 3600:
                last_fail = f"{ago / 60:.0f}m ago"
            else:
                last_fail = f"{ago / 3600:.1f}h ago"

        lines.append(
            f"{emoji} `{name}`\n"
            f"  State: *{e(s['state'])}*  "
            f"Failures: {s['failure_count']}/{s['failure_threshold']}  "
            f"Calls: {s['total_calls']}\n"
        )
        if s["last_failure_error"]:
            err_short = e(s["last_failure_error"][:80])
            lines.append(f"  Last error \\({e(last_fail)}\\): _{err_short}_\n")

        if s["state"] != "CLOSED":
            rows.append([InlineKeyboardButton(
                f"↩️  Reset {s['name']}",
                callback_data=f"{CB_CB_RESET}{s['name']}",
            )])

    rows.append([InlineKeyboardButton("↩️  Reset All Open", callback_data=CB_CB_RESET_ALL)])
    rows.append([InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def cmd_circuits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /circuits command — show circuit breaker status."""
    if not await guard(update, context):
        return
    text, kb = await _circuits_content()
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — BOT SETTINGS
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
# SECTION 8 — PER-USER RATE LIMITS
# ══════════════════════════════════════════════════════════════════════════════

async def _ratelimit_content() -> tuple[str, InlineKeyboardMarkup]:
    """Build the rate-limit admin panel page."""
    import config as _cfg
    custom_limits = await db.list_user_rate_limits()

    default_req    = _cfg.DEFAULT_RATE_LIMIT
    default_window = _cfg.DEFAULT_RATE_WINDOW

    lines = [
        f"⏱ *RATE LIMITS*\n{st.DIV}\n",
        f"_Per\\-user request throttling\\._\n",
        f"{st.SDIV}\n",
        f"*Default:* `{default_req}` requests / `{default_window}` seconds\n",
        f"_Change defaults in ⚙️ Settings_\n",
    ]

    if custom_limits:
        lines.append(f"\n{st.SDIV}\n*Custom overrides:*\n")
        for rl in custom_limits:
            lines.append(
                f"  ▸ User `{rl.user_id}`:  "
                f"`{rl.max_requests}` req / `{rl.window_seconds}`s"
            )
    else:
        lines.append(f"\n{st.SDIV}\n_No custom overrides\\. All users use the default\\._")

    rows = []
    for rl in custom_limits:
        rows.append([InlineKeyboardButton(
            f"🗑  Remove {rl.user_id}",
            callback_data=f"{CB_RL_DEL}{rl.user_id}",
        )])
    rows += [
        [InlineKeyboardButton("➕  Set User Limit", callback_data=CB_RL_SET_USER)],
        [InlineKeyboardButton("◀  Back",            callback_data=CB_PANEL)],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def cmd_ratelimit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry via /ratelimit command."""
    if not await guard(update, context):
        return
    text, kb = await _ratelimit_content()
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)


# ── Set user rate limit conversation ──────────────────────────────────────────

async def _rl_set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via callback button — prompt for user ID."""
    q = update.callback_query
    await q.answer()
    if not await is_admin(q.from_user.id):
        await q.answer("⛔", show_alert=True)
        return ConversationHandler.END
    context.user_data["rl_flow"] = {}
    await q.edit_message_text(
        f"⏱ *SET USER RATE LIMIT*\n{st.DIV}\n\n"
        f"*Step 1 / 3* — Enter the Telegram user ID:\n\n"
        f"`123456789`\n\n"
        f"{st.SDIV}\n"
        f"_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_RL_USER_ID


async def received_rl_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "⚠️ Invalid\\. Enter a numeric Telegram user ID\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_RL_USER_ID
    context.user_data["rl_flow"]["user_id"] = int(text)
    await update.message.reply_text(
        f"✅ User ID: `{e(text)}`\n\n"
        f"*Step 2 / 3* — Max requests per window:\n"
        f"_e\\.g\\. `10`_\n\n_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_RL_MAX_REQ


async def received_rl_max_req(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "⚠️ Enter a positive integer\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_RL_MAX_REQ
    context.user_data["rl_flow"]["max_requests"] = int(text)
    await update.message.reply_text(
        f"✅ Max requests: `{e(text)}`\n\n"
        f"*Step 3 / 3* — Window size in seconds:\n"
        f"_e\\.g\\. `60` \\= 1 minute, `3600` \\= 1 hour_\n\n_/cancel to abort_",
        parse_mode="MarkdownV2",
    )
    return ST_RL_WINDOW


async def received_rl_window(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard(update, context):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "⚠️ Enter a positive integer \\(seconds\\)\\.\n_/cancel to abort_",
            parse_mode="MarkdownV2",
        )
        return ST_RL_WINDOW

    flow = context.user_data.get("rl_flow", {})
    user_id      = flow.get("user_id", 0)
    max_requests = flow.get("max_requests", 5)
    window_secs  = int(text)

    await db.set_user_rate_limit(
        user_id=user_id,
        max_requests=max_requests,
        window_seconds=window_secs,
        updated_by=update.effective_user.id,
    )

    # Invalidate the in-memory cache in bot.py so the new limit takes effect
    try:
        from bot import invalidate_rate_limit_cache
        invalidate_rate_limit_cache(user_id)
    except Exception:
        pass

    text_msg, kb = await _ratelimit_content()
    await update.message.reply_text(
        f"✅ Rate limit set for user `{user_id}`: "
        f"`{max_requests}` req / `{window_secs}`s\\!\n\n" + text_msg,
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )
    context.user_data.pop("rl_flow", None)
    return ConversationHandler.END



# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — ANALYTICS EXPORT
# ══════════════════════════════════════════════════════════════════════════════

import io
import json
from datetime import datetime, timedelta, timezone

# Date-range constants used in callback data
_DATE_RANGES = {
    "7d":  "Last 7 days",
    "30d": "Last 30 days",
    "all": "All time",
}


def _date_range_to_iso(range_key: str) -> tuple[str | None, str | None]:
    """Convert a range key like '7d' to (start_date, end_date) ISO strings."""
    if range_key == "7d":
        start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return start, None
    if range_key == "30d":
        start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        return start, None
    return None, None   # all time


async def _export_content() -> tuple[str, InlineKeyboardMarkup]:
    """Show the export panel with type options, each with date-range sub-buttons."""
    text = (
        f"📤 *EXPORT ANALYTICS*\n{st.DIV}\n\n"
        "Export search stats, cost breakdowns, and user activity\n"
        "as CSV or JSON files\\.\n\n"
        f"{st.SDIV}\n"
        "*Search Logs* \u2014 every search event \\(CSV\\)\n"
        "*API Costs* \u2014 per\\-request AI cost tracking \\(CSV\\)\n"
        "*User Activity* \u2014 aggregated per\\-user stats \\(CSV\\)\n"
        "*All Data* \u2014 everything combined \\(JSON\\)\n\n"
        "_Select an export and time range:_"
    )

    rows = [
        # Search Logs row with date ranges
        [InlineKeyboardButton(f"🔍 Logs 7d",  callback_data=f"{CB_EXP_SEARCH}7d"),
         InlineKeyboardButton(f"🔍 Logs 30d", callback_data=f"{CB_EXP_SEARCH}30d"),
         InlineKeyboardButton(f"🔍 Logs All", callback_data=f"{CB_EXP_SEARCH}all")],
        # API Costs row with date ranges
        [InlineKeyboardButton(f"💰 Costs 7d",  callback_data=f"{CB_EXP_COST}7d"),
         InlineKeyboardButton(f"💰 Costs 30d", callback_data=f"{CB_EXP_COST}30d"),
         InlineKeyboardButton(f"💰 Costs All", callback_data=f"{CB_EXP_COST}all")],
        # User Activity row with date ranges
        [InlineKeyboardButton(f"👤 Users 7d",  callback_data=f"{CB_EXP_USER}7d"),
         InlineKeyboardButton(f"👤 Users 30d", callback_data=f"{CB_EXP_USER}30d"),
         InlineKeyboardButton(f"👤 Users All", callback_data=f"{CB_EXP_USER}all")],
        # All Data (JSON) row with date ranges
        [InlineKeyboardButton(f"📦 All 7d",  callback_data=f"{CB_EXP_ALL}7d"),
         InlineKeyboardButton(f"📦 All 30d", callback_data=f"{CB_EXP_ALL}30d"),
         InlineKeyboardButton(f"📦 All",     callback_data=f"{CB_EXP_ALL}all")],
        # Back button
        [InlineKeyboardButton("◀  Back", callback_data=CB_PANEL)],
    ]
    return text, InlineKeyboardMarkup(rows)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /export command — show export panel."""
    if not await guard(update, context):
        return
    text, kb = await _export_content()
    await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=kb)


async def _send_export_file(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    data: str | bytes,
    filename: str,
    caption: str,
) -> None:
    """Send export data as a Telegram document."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    buf = io.BytesIO(data)
    buf.name = filename
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=buf,
        caption=caption,
    )


async def _handle_export_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> bool:
    """
    Handle export-related callback data.
    Returns True if the callback was handled, False otherwise.
    """
    q = update.callback_query

    if data == CB_EXPORT:
        text, kb = await _export_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        return True

    if data.startswith(CB_EXP_SEARCH):
        rk = data[len(CB_EXP_SEARCH):]
        start, end = _date_range_to_iso(rk)
        label = _DATE_RANGES.get(rk, rk)
        # Check JSON first to detect empty data (CSV always has headers)
        check = await db.export_search_logs(fmt="json", start_date=start, end_date=end)
        if not check:
            await q.answer("No search log data found.", show_alert=True)
            return True
        result = await db.export_search_logs(fmt="csv", start_date=start, end_date=end)
        filename = f"search_logs_{rk}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        await _send_export_file(q, context, result, filename, f"Search Logs — {label}")
        await q.answer("Export sent!")
        return True

    if data.startswith(CB_EXP_COST):
        rk = data[len(CB_EXP_COST):]
        start, end = _date_range_to_iso(rk)
        label = _DATE_RANGES.get(rk, rk)
        check = await db.export_api_costs(fmt="json", start_date=start, end_date=end)
        if not check:
            await q.answer("No API cost data found.", show_alert=True)
            return True
        result = await db.export_api_costs(fmt="csv", start_date=start, end_date=end)
        filename = f"api_costs_{rk}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        await _send_export_file(q, context, result, filename, f"API Costs — {label}")
        await q.answer("Export sent!")
        return True

    if data.startswith(CB_EXP_USER):
        rk = data[len(CB_EXP_USER):]
        start, end = _date_range_to_iso(rk)
        label = _DATE_RANGES.get(rk, rk)
        check = await db.export_user_activity(fmt="json", start_date=start, end_date=end)
        if not check:
            await q.answer("No user activity data found.", show_alert=True)
            return True
        result = await db.export_user_activity(fmt="csv", start_date=start, end_date=end)
        filename = f"user_activity_{rk}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        await _send_export_file(q, context, result, filename, f"User Activity — {label}")
        await q.answer("Export sent!")
        return True

    if data.startswith(CB_EXP_ALL):
        rk = data[len(CB_EXP_ALL):]
        start, end = _date_range_to_iso(rk)
        label = _DATE_RANGES.get(rk, rk)
        search_logs = await db.export_search_logs(fmt="json", start_date=start, end_date=end)
        api_costs = await db.export_api_costs(fmt="json", start_date=start, end_date=end)
        user_activity = await db.export_user_activity(fmt="json", start_date=start, end_date=end)
        combined = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "date_range": label,
            "search_logs": search_logs,
            "api_costs": api_costs,
            "user_activity": user_activity,
        }
        json_str = json.dumps(combined, indent=2, ensure_ascii=False, default=str)
        filename = f"analytics_export_{rk}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
        await _send_export_file(q, context, json_str, filename, f"Full Analytics Export — {label}")
        await q.answer("Export sent!")
        return True

    return False


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

    elif data.startswith(CB_TAG_DEF):
        tag_id = int(data[len(CB_TAG_DEF):])
        await db.set_default_tag(tag_id)
        text, kb = await _tags_content()
        await q.edit_message_text("⭐ Default tag set\\!\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    elif data == CB_TAG_CLRDEF:
        await db.clear_default_tag()
        text, kb = await _tags_content()
        await q.edit_message_text("⭐ Default tag cleared\\.\n\n" + text,
                                  parse_mode="MarkdownV2", reply_markup=kb)

    elif data == "adm:tag_export":
        await _tag_export_callback(update, context)

    # ── API keys ───────────────────────────────────────────────────────────────
    elif data == CB_KEYS:
        text, kb = await _keys_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_KEY_DEL) and not data.startswith(CB_KEY_DELOK):
        key_name = data[len(CB_KEY_DEL):]
        label = _KEY_LABELS.get(key_name, (key_name, ""))[0]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Yes, clear key", callback_data=f"{CB_KEY_DELOK}{key_name}"),
            InlineKeyboardButton("❌  Cancel",         callback_data=CB_KEYS),
        ]])
        await q.edit_message_text(
            f"🗑 Clear *{e(label)}* API key?\n\n_Bot will fall back to \\.env value\\._",
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_KEY_DELOK):
        key_name = data[len(CB_KEY_DELOK):]
        await key_store.delete(key_name)
        _reload_backends(key_name)
        text, kb = await _keys_content()
        await q.edit_message_text("🗑 Key cleared\\.\n\n" + text,
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
            "⏰ *Expires in 30 minutes* — single\\-use only\n"
            "▸ Recipient taps link → bot opens → instant admin access\n\n"
            "_Send this link to the person you want to invite\\._",
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

    # ── Circuit Breakers ──────────────────────────────────────────────────────
    elif data == CB_CIRCUITS:
        text, kb = await _circuits_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data == CB_CB_RESET_ALL:
        from circuit_breaker import registry as cb_registry
        count = cb_registry.reset_all()
        text, kb = await _circuits_content()
        await q.edit_message_text(
            f"↩️ Reset *{count}* circuit\\(s\\)\\.\n\n" + text,
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_CB_RESET):
        from circuit_breaker import registry as cb_registry
        name = data[len(CB_CB_RESET):]
        found = cb_registry.reset(name)
        text, kb = await _circuits_content()
        if found:
            await q.edit_message_text(
                f"↩️ Circuit `{e(name)}` reset to CLOSED\\.\n\n" + text,
                parse_mode="MarkdownV2", reply_markup=kb,
            )
        else:
            await q.edit_message_text(
                f"⚠️ Circuit `{e(name)}` not found\\.\n\n" + text,
                parse_mode="MarkdownV2", reply_markup=kb,
            )

    # ── Export ────────────────────────────────────────────────────────────────────────
    elif data == CB_EXPORT or data.startswith(f"{P}exp_"):
        await _handle_export_callback(update, context, data)

    # ── Rate Limits ───────────────────────────────────────────────────────────
    elif data == CB_RATELIMIT:
        text, kb = await _ratelimit_content()
        await q.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=kb)

    elif data.startswith(CB_RL_DEL) and not data.startswith(CB_RL_DELOK):
        target_id = int(data[len(CB_RL_DEL):])
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Remove", callback_data=f"{CB_RL_DELOK}{target_id}"),
            InlineKeyboardButton("❌  Cancel", callback_data=CB_RATELIMIT),
        ]])
        await q.edit_message_text(
            f"🗑 Remove custom rate limit for user `{target_id}`?\n\n"
            f"_They will revert to the default limit\\._",
            parse_mode="MarkdownV2", reply_markup=kb,
        )

    elif data.startswith(CB_RL_DELOK):
        target_id = int(data[len(CB_RL_DELOK):])
        await db.remove_user_rate_limit(target_id)
        try:
            from bot import invalidate_rate_limit_cache
            invalidate_rate_limit_cache(target_id)
        except Exception:
            pass
        text, kb = await _ratelimit_content()
        await q.edit_message_text(
            f"✅ Custom limit removed for user `{target_id}`\\.\n\n" + text,
            parse_mode="MarkdownV2", reply_markup=kb,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — DATABASE BACKUPS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /backup — trigger an immediate manual backup and send the file to the admin.
    """
    if not await guard(update, context):
        return

    await update.message.reply_text(
        f"💾 *Starting backup\\.\\.\\.*\n{st.SDIV}\n_This may take a moment\\._",
        parse_mode="MarkdownV2",
    )

    try:
        from db_backup import backup_database
        path = await backup_database()

        import os
        size = os.path.getsize(path)
        size_str = f"{size / 1024:.1f} KB" if size < 1_048_576 else f"{size / 1_048_576:.1f} MB"

        await update.message.reply_document(
            document=open(path, "rb"),
            filename=os.path.basename(path),
            caption=(
                f"Backup complete\\.\n"
                f"Size: `{e(size_str)}`"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.error("Manual backup failed: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"Backup failed: `{e(str(exc)[:200])}`",
            parse_mode="MarkdownV2",
        )


async def cmd_backups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /backups — list available backups with sizes and dates.
    """
    if not await guard(update, context):
        return

    try:
        from db_backup import list_backups
        backups = await list_backups()

        if not backups:
            await update.message.reply_text(
                f"💾 *BACKUPS*\n{st.DIV}\n\n_No backups found\\._\n\nUse /backup to create one\\.",
                parse_mode="MarkdownV2",
            )
            return

        lines = [f"💾 *BACKUPS*\n{st.DIV}\n"]
        for b in backups[:20]:   # show at most 20
            size = b["size"]
            size_str = f"{size / 1024:.1f} KB" if size < 1_048_576 else f"{size / 1_048_576:.1f} MB"
            # Parse ISO date for display
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(b["date"])
                date_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                date_str = b["date"][:19]
            lines.append(f"▸ `{e(b['filename'])}`\n  {e(size_str)}  ·  {e(date_str)}\n")

        import config as _cfg
        lines.append(f"{st.SDIV}")
        lines.append(f"_Retention: {_cfg.BACKUP_KEEP_DAYS} days  ·  Auto: {'on' if _cfg.BACKUP_ENABLED else 'off'}  ·  Hour: {_cfg.BACKUP_HOUR:02d}:00_")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.error("List backups failed: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"Failed to list backups: `{e(str(exc)[:200])}`",
            parse_mode="MarkdownV2",
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — /health COMMAND (progressive model health overview)
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current health status of all providers/models with progressive state."""
    if not await guard(update, context):
        return

    import time as _time
    health_rows = await db.get_all_model_health()

    if not health_rows:
        await update.message.reply_text(
            f"\\U0001f3e5 *MODEL HEALTH*\n{st.DIV}\n\n_No models tracked yet\\._",
            parse_mode="MarkdownV2",
        )
        return

    lines = [
        f"\\U0001f3e5 *MODEL HEALTH*",
        f"{st.DIV}",
        "",
    ]

    now = _time.time()

    for r in health_rows:
        state = r.get("state", "healthy")
        name = e(r["provider_name"])

        if state == "disabled":
            status_icon = "\\U0001f534"   # red circle
            status_text = "DISABLED"
        elif state == "degraded":
            status_icon = "\\U0001f7e1"   # yellow circle
            status_text = "DEGRADED"
        else:
            status_icon = "\\U0001f7e2"   # green circle
            status_text = "HEALTHY"

        lines.append(f"  {status_icon} `{name}`  *{e(status_text)}*")

        # Failure count within window
        import config as _cfg
        fail_ts = r.get("failure_timestamps", [])
        cutoff = now - _cfg.HEALTH_FAILURE_WINDOW
        recent_failures = sum(1 for ts in fail_ts if ts >= cutoff)
        if recent_failures:
            window_min = _cfg.HEALTH_FAILURE_WINDOW // 60
            lines.append(f"    Failures \\(last {window_min}m\\): *{recent_failures}*")

        if r["total_failures"]:
            lines.append(f"    Total failures: {r['total_failures']}")

        if r["consecutive_failures"]:
            lines.append(f"    Consecutive: {r['consecutive_failures']}")

        # Auto-recovery time
        disabled_until = r.get("disabled_until")
        if state == "disabled" and disabled_until:
            remaining = max(0, disabled_until - now)
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                lines.append(f"    Auto\\-retry in: *{mins}m {secs}s*")
            else:
                lines.append(f"    _Ready for auto\\-recovery_")

        if r["last_failure_reason"]:
            reason = e(r["last_failure_reason"][:80])
            lines.append(f"    Last error: _{reason}_")

        lines.append("")

    # Config summary
    import config as _cfg
    lines.append(f"{st.SDIV}")
    lines.append(f"\\u2699\\ufe0f *Config*")
    lines.append(f"  Failure window: {_cfg.HEALTH_FAILURE_WINDOW // 60}m")
    lines.append(f"  Disable threshold: {_cfg.HEALTH_DISABLE_THRESHOLD} failures")
    lines.append(f"  Recovery cooldown: {_cfg.HEALTH_RECOVERY_COOLDOWN // 60}m")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
    )


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
# VALIDATE KEYS COMMAND
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_validatekeys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /validatekeys — validate all stored API keys and report status.
    Makes a lightweight health-check call for each configured key.
    """
    if not await guard(update, context):
        return

    msg = await update.message.reply_text(
        f"🔄 *Validating all API keys\\.\\.\\.*\n{st.DIV}\n\n"
        "_This may take a moment\\._",
        parse_mode="MarkdownV2",
    )

    import key_validator
    results = await key_validator.validate_all_stored_keys()

    if not results:
        await msg.edit_text(
            f"🔑 *KEY VALIDATION*\n{st.DIV}\n\n"
            "_No API keys are currently set\\._\n\n"
            "Use /admin → 🔑 API Keys to add keys\\.",
            parse_mode="MarkdownV2",
        )
        return

    lines = [f"🔑 *KEY VALIDATION RESULTS*\n{st.DIV}\n"]
    valid_count = 0
    invalid_count = 0
    skipped_count = 0

    for key_name, (is_valid, info) in sorted(results.items()):
        label_info = _KEY_LABELS.get(key_name)
        label = label_info[0] if label_info else key_name

        if "skipped" in info.lower() if info else False:
            skipped_count += 1
            lines.append(f"⏭  *{e(label)}*: _skipped_")
        elif is_valid:
            valid_count += 1
            detail = f"  _{e(info)}_" if info else ""
            lines.append(f"✅  *{e(label)}*: valid{detail}")
        else:
            invalid_count += 1
            lines.append(f"❌  *{e(label)}*: `{e(info[:150])}`")

    lines.append(f"\n{st.SDIV}")
    lines.append(
        f"✅ Valid: *{valid_count}*  ❌ Invalid: *{invalid_count}*  "
        f"⏭ Skipped: *{skipped_count}*"
    )

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
    )


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

    # Conversation: import affiliate tags from CSV file
    import_conv = ConversationHandler(
        entry_points=[
            CommandHandler("importtags", cmd_importtags),
            CallbackQueryHandler(_tag_import_callback_entry, pattern=r"^adm:tag_import$"),
        ],
        states={
            ST_TAG_IMPORT: [
                MessageHandler(filters.Document.ALL, received_import_file),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
        allow_reentry=True,
        per_message=False,
    )

    # Conversation: set per-user rate limit
    ratelimit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(_rl_set_entry, pattern=f"^{CB_RL_SET_USER}$"),
        ],
        states={
            ST_RL_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_rl_user_id)],
            ST_RL_MAX_REQ: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_rl_max_req)],
            ST_RL_WINDOW:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_rl_window)],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
        allow_reentry=True,
        per_message=False,
    )

    return [
        CommandHandler("admin", cmd_admin),
        CommandHandler("export", cmd_export),
        CommandHandler("ratelimit", cmd_ratelimit),
        CommandHandler("backup", cmd_backup),
        CommandHandler("backups", cmd_backups),
        CommandHandler("circuits", cmd_circuits),
        CommandHandler("validatekeys", cmd_validatekeys),
        CommandHandler("health", cmd_health),
        CommandHandler("exporttags", cmd_exporttags),
        tag_conv,
        key_conv,
        setting_conv,
        import_conv,
        ratelimit_conv,
        # All other adm:* callbacks not handled by conversations
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^adm:(?!tag_add(ok|cancel)$)",
        ),
    ]
