"""
testbot.py — Private AI testing / evaluation bot.

Runs every photo through ALL enabled AI models simultaneously.
Shows per-model breakdown: product name, confidence, latency, cost.
Tracks cumulative session statistics.

Access is restricted to ADMIN_IDS only (set in .env → config.py).

Commands:
  /start  — welcome + quick guide
  /stats  — cumulative cost & model breakdown for this session
  /reset  — clear session stats
  /models — list currently loaded AI providers

Usage:  python testbot.py        (reads TEST_BOT_TOKEN from env/DB)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import database as db
from amazon_search import search_amazon, AmazonItem
from providers.base import ProviderResult
from providers.manager import analyse_image, get_providers

logger = logging.getLogger(__name__)

# ── Access control ─────────────────────────────────────────────────────────────

_ALLOWED: set[int] = set()   # populated from config.ADMIN_IDS in _post_init


def _allowed(user_id: int) -> bool:
    return user_id in _ALLOWED


# ── MarkdownV2 helpers ─────────────────────────────────────────────────────────

def esc(text: str) -> str:
    for c in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(c, "\\" + c)
    return text


def _conf(c: str) -> str:
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(c, "⚪")


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class TestSession:
    photo_count: int = 0
    total_cost_usd: float = 0.0

    # Per-model accumulators
    provider_costs:       dict[str, float]      = field(default_factory=dict)
    provider_counts:      dict[str, int]        = field(default_factory=dict)
    provider_high:        dict[str, int]        = field(default_factory=dict)
    provider_medium:      dict[str, int]        = field(default_factory=dict)
    provider_low:         dict[str, int]        = field(default_factory=dict)
    provider_errors:      dict[str, int]        = field(default_factory=dict)
    provider_accepted:    dict[str, int]        = field(default_factory=dict)   # user clicked Search
    provider_latencies:   dict[str, list[int]]  = field(default_factory=dict)

    # Current photo results — keyed by index for callback dispatch
    current_results: list[ProviderResult] = field(default_factory=list)


_sessions: dict[int, TestSession] = {}


def _session(uid: int) -> TestSession:
    if uid not in _sessions:
        _sessions[uid] = TestSession()
    return _sessions[uid]


# ── Formatters ─────────────────────────────────────────────────────────────────

def _provider_card(r: ProviderResult) -> str:
    tokens = r.input_tokens + r.output_tokens
    features = ""
    if r.key_features:
        feats = [esc(f) for f in r.key_features[:3]]
        features = "\n" + "  ".join(f"▸ {f}" for f in feats)
    notes = f"\n📝 _{esc(r.notes[:80])}_" if r.notes else ""

    return (
        f"🤖 *{esc(r.provider_name)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *{esc(r.product_name)}*\n"
        f"🏷️  Brand: {esc(r.brand or 'Unknown')}\n"
        f"🎯 Confidence: {_conf(r.confidence)} {esc(r.confidence)}{features}{notes}\n"
        f"🔍 `{esc(r.amazon_search_query)}`\n"
        f"⏱️  {esc(f'{r.latency_ms:,}ms')}  •  🔢 {esc(str(tokens))} tokens  •  💰 {esc(f'${r.cost_usd:.5f}')}"
    )


def _summary_card(session: TestSession, results: list[ProviderResult]) -> str:
    photo_cost = sum(r.cost_usd for r in results)
    avg = session.total_cost_usd / session.photo_count if session.photo_count else 0.0

    header = (
        f"📊 *PHOTO \\#{esc(str(session.photo_count))} SUMMARY*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    rows = []
    for r in sorted(results, key=lambda x: x.cost_usd):
        short = esc(r.provider_name.split("/")[-1][:22])
        cost  = esc(f"${r.cost_usd:.5f}")
        lat   = esc(f"{r.latency_ms}ms")
        conf  = _conf(r.confidence)
        rows.append(f"  {conf} `{short}` — {cost}  {lat}")

    footer = (
        f"\n💸 This photo: *{esc(f'${photo_cost:.5f}')}*\n"
        f"📈 Session total: *{esc(f'${session.total_cost_usd:.4f}')}*\n"
        f"📉 Avg/photo: {esc(f'${avg:.5f}')}\n"
        f"📸 Photos analyzed: {session.photo_count}"
    )

    return header + "\n".join(rows) + footer


def _amazon_card(items: list[AmazonItem], provider_name: str, query: str) -> str:
    short = esc(provider_name.split("/")[-1][:22])
    q     = esc(query[:60])

    if not items:
        return f"😔 *No results*\nQuery: `{q}`"

    lines = [
        f"🛒 *Results via* `{short}`",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Query: `{q}`\n",
    ]

    for i, item in enumerate(items[:5], 1):
        price = esc(f"${item.price_usd:.2f}") if item.price_usd else esc("N/A")
        stars = esc(f"⭐{item.rating:.1f}") if item.rating else ""
        fba   = "✈️" if item.qualifies_for_israel_free_delivery else ""
        title = esc(item.title[:50])
        lines.append(f"{i}\\. *{title}*\n    {price}  {stars}  {fba}")

    return "\n".join(lines)


def _bar(ratio: float, width: int = 10) -> str:
    """Render a simple ASCII progress bar like ████████░░"""
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def _stats_card(session: TestSession) -> str:
    if session.photo_count == 0:
        return "📊 No photos analyzed yet\\."

    n     = session.photo_count
    avg   = session.total_cost_usd / n
    lines = [
        "📊 *SESSION REPORT*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📸 Photos analyzed: *{n}*",
        f"💸 Total cost: *{esc(f'${session.total_cost_usd:.4f}')}*",
        f"📉 Avg cost / photo: {esc(f'${avg:.5f}')}",
        "",
        "*🎯 Success rate \\(high confidence\\):*",
    ]

    # Success rates — sorted by success ratio descending
    all_providers = sorted(
        session.provider_counts.keys(),
        key=lambda p: session.provider_high.get(p, 0) / max(session.provider_counts.get(p, 1), 1),
        reverse=True,
    )
    for p in all_providers:
        total   = session.provider_counts.get(p, 0)
        high    = session.provider_high.get(p, 0)
        medium  = session.provider_medium.get(p, 0)
        low     = session.provider_low.get(p, 0)
        errors  = session.provider_errors.get(p, 0)
        success = high / total if total else 0.0
        short   = esc(p.split("/")[-1][:20])
        bar     = esc(_bar(success))
        pct     = esc(f"{success*100:.0f}%")
        lines.append(
            f"  `{short}` {bar} {pct}\n"
            f"    🟢{high} 🟡{medium} 🔴{low} ❌{errors} / {total} calls"
        )

    # Acceptance rates
    any_accepted = any(session.provider_accepted.get(p, 0) for p in all_providers)
    if any_accepted:
        lines += ["", "*✅ Acceptance rate \\(user searched with\\):*"]
        for p in all_providers:
            total    = session.provider_counts.get(p, 0)
            accepted = session.provider_accepted.get(p, 0)
            ratio    = accepted / total if total else 0.0
            short    = esc(p.split("/")[-1][:20])
            bar      = esc(_bar(ratio))
            pct      = esc(f"{ratio*100:.0f}%")
            lines.append(f"  `{short}` {bar} {pct} \\({accepted}/{total}\\)")

    # Speed + cost table
    lines += ["", "*⚡ Speed \\& cost:*"]
    for p in all_providers:
        lats  = session.provider_latencies.get(p, [])
        avg_l = int(sum(lats) / len(lats)) if lats else 0
        cost  = session.provider_costs.get(p, 0.0)
        count = session.provider_counts.get(p, 1)
        short = esc(p.split("/")[-1][:20])
        lines.append(
            f"  `{short}` — avg {esc(f'{avg_l}ms')}  •  total {esc(f'${cost:.4f}')}"
        )

    return "\n".join(lines)


# ── Handlers ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied\\.", parse_mode="MarkdownV2")
        return
    await update.message.reply_text(
        "🧪 *AMAZON BOT TESTING MODE*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send a product photo and I'll run it through *all enabled AI models* simultaneously\\.\n\n"
        "For each model you'll see:\n"
        "▸ Product name \\+ brand\n"
        "▸ Confidence level\n"
        "▸ Amazon search query generated\n"
        "▸ Latency, token count, and cost\n"
        "▸ \\[Search Amazon\\] button to test the query\n\n"
        "📊 /stats — cumulative cost breakdown\n"
        "🔄 /reset — clear session stats\n"
        "🤖 /models — list loaded providers",
        parse_mode="MarkdownV2",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        _stats_card(_session(update.effective_user.id)),
        parse_mode="MarkdownV2",
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_user.id):
        return
    _sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("🔄 Session stats cleared\\.", parse_mode="MarkdownV2")


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_user.id):
        return
    try:
        providers = await get_providers()
    except Exception as exc:
        await update.message.reply_text(f"❌ {esc(str(exc))}", parse_mode="MarkdownV2")
        return

    lines = ["🤖 *LOADED PROVIDERS*", "━━━━━━━━━━━━━━━━━━━━"]
    for name in providers:
        lines.append(f"  ▸ `{esc(name)}`")
    lines.append(f"\n_{len(providers)} provider{'s' if len(providers) != 1 else ''} ready_")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not _allowed(uid):
        await update.message.reply_text("⛔ Access denied\\.", parse_mode="MarkdownV2")
        return

    session = _session(uid)

    try:
        providers = await get_providers()
        n = len(providers)
    except Exception as exc:
        await update.message.reply_text(f"❌ {esc(str(exc))}", parse_mode="MarkdownV2")
        return

    if n == 0:
        await update.message.reply_text("❌ No AI providers configured\\.", parse_mode="MarkdownV2")
        return

    status = await update.message.reply_text(
        f"⠙ Running through {n} model{'s' if n != 1 else ''}…"
    )

    # Download image
    photo      = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await photo_file.download_as_bytearray())

    # Always use compare mode — run ALL providers in parallel
    try:
        _, all_results = await analyse_image(image_bytes, mode="compare")
    except Exception as exc:
        logger.error("Analysis error: %s", exc)
        await status.edit_text(f"❌ Analysis failed\\: {esc(str(exc)[:120])}", parse_mode="MarkdownV2")
        return

    session.photo_count += 1
    session.current_results = all_results
    photo_cost = 0.0

    await status.delete()

    # ── Track which providers ran but produced no result (errors) ──────────────
    # all_results only contains successes; detect errors by comparing with
    # the expected provider list
    try:
        all_providers = await get_providers()
        expected_names = set(all_providers.keys())
    except Exception:
        expected_names = set()
    got_names = {r.provider_name for r in all_results}
    for missing in expected_names - got_names:
        session.provider_errors[missing] = session.provider_errors.get(missing, 0) + 1
        session.provider_counts[missing] = session.provider_counts.get(missing, 0) + 1

    # Send one card per provider
    for idx, result in enumerate(all_results):
        session.provider_costs[result.provider_name] = (
            session.provider_costs.get(result.provider_name, 0.0) + result.cost_usd
        )
        session.provider_counts[result.provider_name] = (
            session.provider_counts.get(result.provider_name, 0) + 1
        )
        # Confidence breakdown
        conf_key = f"provider_{result.confidence}"  # provider_high / provider_medium / provider_low
        bucket = getattr(session, conf_key, None)
        if bucket is not None:
            bucket[result.provider_name] = bucket.get(result.provider_name, 0) + 1
        # Latency tracking
        lats = session.provider_latencies.setdefault(result.provider_name, [])
        lats.append(result.latency_ms)

        photo_cost += result.cost_usd

        await update.message.reply_text(
            _provider_card(result),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔍 Search Amazon", callback_data=f"srch:{idx}"),
            ]]),
        )

    session.total_cost_usd += photo_cost

    # Summary card
    await update.message.reply_text(
        _summary_card(session, all_results),
        parse_mode="MarkdownV2",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not _allowed(uid):
        return

    data    = query.data
    session = _session(uid)

    if data.startswith("srch:"):
        idx = int(data[5:])
        if idx >= len(session.current_results):
            await query.answer("⚠️ Result expired — send a new photo.", show_alert=True)
            return

        result = session.current_results[idx]

        # Record acceptance for this provider
        session.provider_accepted[result.provider_name] = (
            session.provider_accepted.get(result.provider_name, 0) + 1
        )

        # Remove the Search button so it can't be double-clicked
        await query.edit_message_reply_markup(None)

        status = await query.message.reply_text(
            f"⠙ Searching: `{esc(result.amazon_search_query)}`…",
            parse_mode="MarkdownV2",
        )

        try:
            product_info = result.to_product_info()
            items        = await search_amazon(product_info, max_results=5)
        except Exception as exc:
            await status.edit_text(
                f"❌ Search failed\\: {esc(str(exc)[:120])}", parse_mode="MarkdownV2"
            )
            return

        affiliate_tag = await db.get_active_tag()
        card          = _amazon_card(items, result.provider_name, result.amazon_search_query)
        buttons       = [
            [InlineKeyboardButton(
                f"🛒 {item.title[:35]}  {'$'+f'{item.price_usd:.2f}' if item.price_usd else ''}",
                url=item.affiliate_url(affiliate_tag),
            )]
            for item in items[:5]
        ]

        await status.edit_text(
            card,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=True,
        )


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _allowed(update.effective_user.id):
        await update.message.reply_text("📸 Send a product photo to analyze it\\.", parse_mode="MarkdownV2")


# ── App factory ────────────────────────────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await db.init_db()
    await config.apply_db_settings()
    _ALLOWED.update(config.ADMIN_IDS)
    logger.info("Test bot ready. Allowed users: %s", _ALLOWED)


def build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other))
    return app


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = os.environ.get("TEST_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TEST_BOT_TOKEN env var not set.", file=sys.stderr)
        sys.exit(1)

    logger.info("Starting test bot…")
    app = build_application(token)
    app.run_polling()
