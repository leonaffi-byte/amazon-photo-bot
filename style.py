"""
style.py — Complete visual style system for the bot.

Design language:
  • Structured cards with consistent emoji icons
  • Unicode box-drawing dividers
  • Animated loading sequences (multi-step edit)
  • Clear visual hierarchy: header → body → footer
  • MarkdownV2 throughout

All text that goes into Telegram messages should be formatted through this module.
"""
from __future__ import annotations
from typing import Optional
import config

# ── Escape ────────────────────────────────────────────────────────────────────

def esc(text: str) -> str:
    """Escape all MarkdownV2 special characters."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Visual constants ──────────────────────────────────────────────────────────

DIV   = "━━━━━━━━━━━━━━━━━━━━━━━━━━"    # thick divider
SDIV  = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"    # subtle divider

CONF  = {"high": "🟢", "medium": "🟡", "low": "🔴"}
STARS = {5: "★★★★★", 4: "★★★★☆", 3: "★★★☆☆", 2: "★★☆☆☆", 1: "★☆☆☆☆", 0: "☆☆☆☆☆"}


def star_bar(rating: Optional[float]) -> str:
    if rating is None:
        return "☆☆☆☆☆"
    r = round(rating)
    return STARS.get(max(0, min(5, r)), "☆☆☆☆☆")


def fmt_reviews(count: Optional[int]) -> str:
    if count is None:
        return ""
    if count >= 1_000_000:
        return f"{count/1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count/1_000:.1f}K"
    return str(count)


# ── Loading states (send first, then edit through sequence) ───────────────────

LOADING = [
    "⠋ Analysing your photo…",
    "⠙ Reading product details…",
    "⠸ Identifying brand & model…",
    "⠴ Preparing search query…",
]

SEARCH_LOADING = [
    "⠋ Searching Amazon…",
    "⠙ Fetching product data…",
    "⠸ Ranking results…",
    "⠴ Almost done…",
]


# ══════════════════════════════════════════════════════════════════════════════
# START / WELCOME
# ══════════════════════════════════════════════════════════════════════════════

def welcome(provider_list: str, vision_mode: str, search_backend: str) -> str:
    return (
        f"🛍️ *AMAZON PHOTO FINDER*\n"
        f"{DIV}\n\n"
        f"Drop a product photo — I'll identify it with AI\n"
        f"and hunt it down on Amazon for you\\.\n\n"
        f"✨  *What I can do*\n"
        f"▸ Recognise any product from a photo\n"
        f"▸ Search Amazon in real\\-time\n"
        f"▸ Filter by free delivery to 🇮🇱 Israel\n"
        f"▸ Send you direct affiliate links\n\n"
        f"{DIV}\n"
        f"🤖  *Vision:* {esc(provider_list)}  `{esc(vision_mode)}`\n"
        f"🛒  *Search:* {esc(search_backend)}\n"
        f"{DIV}\n\n"
        f"_📸 Just send a photo to get started_"
    )


def help_text(threshold: float) -> str:
    return (
        f"📖 *HOW TO USE*\n"
        f"{DIV}\n\n"
        f"*1️⃣  Send a photo*\n"
        f"_Clear, well\\-lit, brand text visible_\n\n"
        f"*2️⃣  AI identifies the product*\n"
        f"_Brand, model, features extracted_\n\n"
        f"*3️⃣  Choose your filter*\n"
        f"_Free delivery to 🇮🇱 Israel, or show all_\n\n"
        f"*4️⃣  Browse results*\n"
        f"_◀ ▶ to paginate, tap to open on Amazon_\n\n"
        f"{DIV}\n"
        f"✈️  *Free delivery to 🇮🇱 Israel*\n"
        f"Items Fulfilled by Amazon \\(FBA\\) ship free\n"
        f"when your cart reaches \\${threshold:.0f} USD\\.\n\n"
        f"💡  *Tips for best results*\n"
        f"▸ Include brand/model text in frame\n"
        f"▸ Avoid extreme angles or blur\n"
        f"▸ One product per photo\n\n"
        f"{DIV}\n"
        f"_Commands: /start · /help · /providers_"
    )


# ══════════════════════════════════════════════════════════════════════════════
# LOADING MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

def loading_vision(n_providers: int, mode: str) -> str:
    if mode in ("best", "compare") and n_providers > 1:
        return (
            f"🔍 *Analysing your photo*\n"
            f"{SDIV}\n"
            f"Running *{n_providers} AI providers* in parallel…\n\n"
            f"⠋ Identifying product…"
        )
    return (
        f"🔍 *Analysing your photo*\n"
        f"{SDIV}\n"
        f"⠋ Reading product details…"
    )


def loading_search(product_name: str, filter_label: str) -> str:
    return (
        f"🛒 *Searching Amazon*\n"
        f"{SDIV}\n"
        f"🏷️ _{esc(product_name)}_\n"
        f"🔎 {esc(filter_label)}\n\n"
        f"⠙ Fetching results…"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IDENTIFICATION RESULT
# ══════════════════════════════════════════════════════════════════════════════

def identification_card(result, show_cost: bool = True) -> str:
    conf_icon = CONF.get(result.confidence, "⚪")
    features  = "\n".join(f"  ▸ {esc(f)}" for f in result.key_features) or "  ▸ _none detected_"
    cost_line = (
        f"\n💸 `{esc(result.cost_str)}`  ⚡ `{result.latency_ms}ms`"
        if show_cost else ""
    )
    return (
        f"✨ *PRODUCT IDENTIFIED*\n"
        f"{DIV}\n\n"
        f"🏷️ *{esc(result.product_name)}*\n"
        f"🏢 {esc(result.brand or 'Unknown brand')}\n"
        f"📦 {esc(result.category)}\n\n"
        f"{conf_icon} *Confidence:* {result.confidence}   "
        f"🤖 {esc(result.provider_name)}{cost_line}\n\n"
        f"✦ *Key Features*\n{features}\n\n"
        f"{SDIV}\n"
        f"🔎 `{esc(result.amazon_search_query)}`\n"
        f"{DIV}\n\n"
        f"✈️ *Limit to free delivery to 🇮🇱 Israel?*\n"
        f"_FBA items ship free when cart ≥ \\$49_"
    )


def compare_card(results: list, show_cost: bool = True) -> str:
    lines = [
        f"🔬 *PROVIDER COMPARISON*\n{DIV}\n"
    ]
    for i, r in enumerate(results, 1):
        conf_icon = CONF.get(r.confidence, "⚪")
        cost_note = f"  💸 `{esc(r.cost_str)}` ⚡ `{r.latency_ms}ms`" if show_cost else ""
        feats = " ·  ".join(esc(f) for f in r.key_features[:2])
        lines.append(
            f"*{i}\\. {esc(r.provider_name)}*\n"
            f"   {conf_icon} {r.confidence}   🏷️ _{esc(r.product_name)}_\n"
            f"   🔎 `{esc(r.amazon_search_query)}`\n"
            f"   {feats}{cost_note}\n"
        )
    lines.append(f"{DIV}\n_Tap a provider to use its result:_")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT CARDS
# ══════════════════════════════════════════════════════════════════════════════

def product_card(item, index: int) -> str:
    """Format a single Amazon product as a rich card."""
    title = esc(item.title[:100])

    price = f"💰 *\\${item.price_usd:.2f}*" if item.price_usd else "💰 _Price not listed_"

    if item.rating and item.review_count:
        stars = star_bar(item.rating)
        rating_line = f"⭐ `{item.rating}` {esc(stars)}  _{esc(fmt_reviews(item.review_count))} reviews_"
    elif item.rating:
        rating_line = f"⭐ `{item.rating}` {esc(star_bar(item.rating))}"
    else:
        rating_line = "⭐ _No ratings yet_"

    return (
        f"*{index}\\.*  {title}\n"
        f"{price}   {rating_line}\n"
        f"{esc(item.delivery_badge)}\n"
        f"{esc(item.israel_delivery_note)}"
    )


def results_page(session, affiliate_tag: Optional[str] = None) -> str:
    """Full results page with header, cards, and footer."""
    p = session.page + 1
    t = session.total_pages
    n = len(session.filtered_items)
    n_all = len(session.all_items)
    n_eligible = sum(1 for i in session.all_items if i.qualifies_for_israel_free_delivery)

    filter_badge = "✈️  Free delivery to 🇮🇱" if session.israel_only else "🌐  All items"
    provider = esc(session.chosen_result.provider_name) if session.chosen_result else ""
    tag_note  = f"   🏷️ `{esc(affiliate_tag)}`" if affiliate_tag else ""

    header = (
        f"🛍️ *{esc(session.product_info.product_name)}*\n"
        f"{DIV}\n"
        f"{filter_badge}   📄 {p}/{t}   🤖 {provider}{tag_note}\n"
        f"{SDIV}\n"
    )

    cards = []
    for i, item in enumerate(session.current_page_items()):
        global_idx = (session.page * config.RESULTS_PER_PAGE) + i + 1
        cards.append(product_card(item, global_idx))

    footer_parts = [f"🔍 {n} results"]
    if not session.israel_only and n_eligible < n_all and n_all > 0:
        footer_parts.append(f"✈️ {n_eligible} with free Israel delivery")
    footer = f"\n{SDIV}\n_" + "   ·   ".join(footer_parts) + "_"

    full = header + f"\n\n{SDIV}\n\n".join(cards) + footer
    return full[:4050] + "\\.\\.\\." if len(full) > 4050 else full


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDERS INFO
# ══════════════════════════════════════════════════════════════════════════════

def providers_info(providers: dict, vision_mode: str, search_backend_name: str) -> str:
    lines = [f"🤖 *AI PROVIDERS*\n{DIV}\n"]
    for name, p in providers.items():
        cost = p.cost_per_image + p.cost_per_1k_input_tokens * 0.8
        cost_str = f"\\~\\${cost*1000:.3f}m/img"
        lines.append(f"▸ *{esc(name)}*  {esc(cost_str)}")
    lines += [
        f"\n{SDIV}",
        f"Mode: `{esc(vision_mode)}`",
        f"\n🛒 *SEARCH BACKEND*\n{SDIV}",
        esc(search_backend_name),
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ERROR MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

def error_no_providers() -> str:
    return (
        f"⚠️ *No AI Providers Configured*\n"
        f"{DIV}\n\n"
        f"An admin needs to add at least one vision API key\\.\n\n"
        f"▸ /admin → 🔑 *API Keys*\n"
        f"▸ Add OpenAI, Anthropic, or Google key\n\n"
        f"_Free keys available at openai\\.com, anthropic\\.com, aistudio\\.google\\.com_"
    )


def error_no_backend() -> str:
    return (
        f"⚠️ *No Search Backend Configured*\n"
        f"{DIV}\n\n"
        f"An admin needs to add a search API key\\.\n\n"
        f"▸ /admin → 🔑 *API Keys*\n"
        f"▸ Add RapidAPI key \\(free tier available\\)\n\n"
        f"_Sign up free at rapidapi\\.com_"
    )


def error_no_results() -> str:
    return (
        f"😔 *No Results Found*\n"
        f"{DIV}\n\n"
        f"Try:\n"
        f"▸ A clearer, better\\-lit photo\n"
        f"▸ Including brand text in frame\n"
        f"▸ Disabling the Israel delivery filter\n"
    )


def error_analysis_failed() -> str:
    return (
        f"❌ *Analysis Failed*\n"
        f"{DIV}\n\n"
        f"Couldn't identify this product\\. Try:\n"
        f"▸ Better lighting\n"
        f"▸ Less angle / closer shot\n"
        f"▸ Include the product label\n"
    )


def not_a_photo() -> str:
    return (
        f"📸 *Send a Photo*\n"
        f"{SDIV}\n"
        f"I need a product photo to search Amazon\\.\n"
        f"_Just take a pic and send it here\\!_"
    )


def error_rate_limited(max_requests: int, window_secs: int) -> str:
    return (
        f"⏱ *Slow Down\\!*\n"
        f"{SDIV}\n"
        f"You can analyse up to *{max_requests} photos* every *{window_secs} seconds*\\.\n\n"
        f"_Please wait a moment before sending another photo\\._"
    )
