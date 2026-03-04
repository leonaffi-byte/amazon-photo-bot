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
    r = int(rating)  # floor instead of round to avoid 4.5 → 5 stars
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

def welcome() -> str:
    return (
        f"🛍️ *AMAZON PHOTO FINDER*\n"
        f"{DIV}\n\n"
        f"Drop a product photo and I'll identify it with AI\n"
        f"and find it on Amazon for you\\.\n\n"
        f"✨  *What I can do*\n"
        f"▸ Recognise any product from a photo\n"
        f"▸ Search Amazon in real\\-time\n"
        f"▸ Filter by free delivery to 🇮🇱 Israel\n"
        f"▸ Browse results with direct Amazon links\n\n"
        f"{DIV}\n"
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

def loading_vision(
    n_providers: int,
    mode: str,
    context_hint: Optional[str] = None,
) -> str:
    hint_line = f"\n💬 Hint: _{esc(context_hint[:80])}_" if context_hint else ""
    if mode in ("best", "compare") and n_providers > 1:
        return (
            f"🔍 *Analysing your photo*\n"
            f"{SDIV}\n"
            f"Running *{n_providers} AI providers* in parallel…{hint_line}\n\n"
            f"⠋ Identifying product…"
        )
    return (
        f"🔍 *Analysing your photo*\n"
        f"{SDIV}\n"
        f"⠋ Reading product details…{hint_line}"
    )


def text_search_ready(
    original: str,
    english: str,
    refined: str,
    lang_label: Optional[str] = None,
) -> str:
    """Shown after translating/refining the user's text query."""
    lines = [
        "🔍 *Text Search*",
        f"{SDIV}",
    ]
    if lang_label and original != english:
        lines += [
            f"{lang_label}: _{esc(original[:80])}_",
            f"🇺🇸 English: _{esc(english[:80])}_",
        ]
    lines += [
        f"🛒 Amazon query: `{esc(refined[:100])}`",
    ]
    return "\n".join(lines)


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

def identification_card(result, show_cost: bool = True, is_admin: bool = False) -> str:
    conf_icon = CONF.get(result.confidence, "⚪")
    features  = "\n".join(f"  ▸ {esc(f)}" for f in result.key_features) or "  ▸ _none detected_"

    # Provider/cost info — admin only
    admin_line = ""
    if is_admin:
        admin_line = f"\n_🤖 {esc(result.provider_name)}"
        if show_cost:
            admin_line += f"  💸 {esc(result.cost_str)}  ⚡ {result.latency_ms}ms"
        admin_line += "_"

    return (
        f"✨ *{esc(result.product_name)}*\n\n"
        f"🏢 {esc(result.brand or 'Unknown brand')}  ·  📦 {esc(result.category)}\n"
        f"{conf_icon} Confidence: *{result.confidence}*\n\n"
        f"*Key Features*\n{features}\n\n"
        f"🔎 `{esc(result.amazon_search_query)}`\n"
        f"{SDIV}\n"
        f"✈️ *Free delivery to 🇮🇱 Israel?*\n"
        f"_FBA items ship free when cart ≥ \\${config.FREE_DELIVERY_THRESHOLD:.0f}_"
        f"{admin_line}"
    )


def compare_card(results: list, show_cost: bool = True, is_admin: bool = False) -> str:
    lines = [
        f"🔬 *PROVIDER COMPARISON*\n{DIV}\n"
    ]
    for i, r in enumerate(results, 1):
        conf_icon = CONF.get(r.confidence, "⚪")
        cost_note = ""
        if is_admin and show_cost:
            cost_note = f"  💸 `{esc(r.cost_str)}` ⚡ `{r.latency_ms}ms`"
        feats = " ·  ".join(esc(f) for f in r.key_features[:2])
        provider_label = esc(r.provider_name) if is_admin else f"Option {i}"
        lines.append(
            f"*{i}\\. {provider_label}*\n"
            f"   {conf_icon} {r.confidence}   🏷️ _{esc(r.product_name)}_\n"
            f"   🔎 `{esc(r.amazon_search_query)}`\n"
            f"   {feats}{cost_note}\n"
        )
    lines.append(f"{DIV}\n_Tap an option to use its result:_")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT CARDS
# ══════════════════════════════════════════════════════════════════════════════

def product_card(item, index: int) -> str:
    """Format a single Amazon product as a compact card."""
    title = esc(item.title[:100])

    price = f"*{esc(f'${item.price_usd:.2f}')}*" if item.price_usd else "_Price not listed_"

    if item.rating and item.review_count:
        stars = star_bar(item.rating)
        rating_line = f"{esc(stars)} {item.rating} \\({esc(fmt_reviews(item.review_count))}\\)"
    elif item.rating:
        rating_line = f"{esc(star_bar(item.rating))} {item.rating}"
    else:
        rating_line = "_No ratings_"

    return (
        f"*{index}\\.* {title}\n"
        f"💰 {price}  ⭐ {rating_line}\n"
        f"{esc(item.israel_delivery_note)}"
    )


def product_caption(
    item,
    index: int = 1,
    total: int = 1,
    is_admin: bool = False,
    provider_name: Optional[str] = None,
    affiliate_tag: Optional[str] = None,
    israel_verified=None,   # Optional[israel_scraper.IsraelShippingResult]
    price_history=None,     # Optional[price_history.PriceHistory]
) -> str:
    """
    Single-product caption for the photo carousel (max 1024 chars).
    Clean, scannable layout: title → price+rating → shipping → price history.
    Admin users see the AI provider + affiliate tag in a footer line.
    """
    title = esc(item.title[:100])

    # Price
    price = f"*{esc(f'${item.price_usd:.2f}')}*" if item.price_usd else "_Price not listed_"

    # Rating — compact single line
    if item.rating and item.review_count:
        stars  = star_bar(item.rating)
        rating = f"{esc(stars)} {item.rating} \\({esc(fmt_reviews(item.review_count))}\\)"
    elif item.rating:
        rating = f"{esc(star_bar(item.rating))} {item.rating}"
    else:
        rating = "_No ratings_"

    # Israel shipping — single line combining badge + Israel info
    # Use verified result when available, otherwise heuristic
    if israel_verified and israel_verified.verified:
        israel = esc(israel_verified.note)
    else:
        israel = esc(item.israel_delivery_note)

    # Price history line (e.g. "📊 ATL $24.99 · 90d avg $38.50 · ✅ Below avg")
    price_line = _price_history_line(price_history)

    # Position counter e.g. "1/20"
    counter  = f"*{index}*/{total}" if total > 1 else ""

    admin_line = ""
    if is_admin and provider_name:
        tag_note   = f" · `{esc(affiliate_tag)}`" if affiliate_tag else ""
        admin_line = f"\n\n_🤖 {esc(provider_name)}{tag_note}_"

    caption = (
        f"*{title}*\n\n"
        f"💰 {price}  ⭐ {rating}\n"
        f"{israel}"
        f"{price_line}\n\n"
        f"{counter}"
        f"{admin_line}"
    )

    # Telegram caption hard-limit is 1024 chars
    if len(caption) > 1020:
        caption = caption[:1010]
        last_nl = caption.rfind("\n")
        if last_nl > 800:
            caption = caption[:last_nl]
        caption += "\n_…\\(truncated\\)_"
    return caption


def _price_history_line(ph) -> str:
    """
    Format a compact price history line for the product caption.
    Returns empty string if ph is None or has no useful data.
    Example: "\n📊 ATL $24\\.99 · 90d avg $38\\.50 · ✅ Below avg"
    """
    if not ph:
        return ""
    parts: list[str] = []
    if ph.low_all_time:
        parts.append(f"ATL {esc(f'${ph.low_all_time:.2f}')}")
    if ph.avg_90d:
        parts.append(f"90d avg {esc(f'${ph.avg_90d:.2f}')}")
    elif ph.avg_30d:
        parts.append(f"30d avg {esc(f'${ph.avg_30d:.2f}')}")
    if not parts:
        return ""
    deal = ph.deal_label   # already MD-escaped inside the property
    summary = " · ".join(parts)
    deal_suffix = f" · {deal}" if deal else ""
    return f"\n📊 _{summary}{deal_suffix}_"


def results_page(session, affiliate_tag: Optional[str] = None, is_admin: bool = False) -> str:
    """Full results page with header, cards, and footer."""
    p = session.page + 1
    t = session.total_pages
    n = len(session.filtered_items)
    n_all = len(session.all_items)
    n_eligible = sum(1 for i in session.all_items if i.qualifies_for_israel_free_delivery)

    filter_badge = "✈️  Free delivery to 🇮🇱" if session.israel_only else "🌐  All items"

    # Admin-only: show which AI model + affiliate tag were used
    admin_info = ""
    if is_admin:
        provider = esc(session.chosen_result.provider_name) if session.chosen_result else ""
        tag_note  = f"   🏷️ `{esc(affiliate_tag)}`" if affiliate_tag else ""
        if provider:
            admin_info = f"   🤖 {provider}{tag_note}"

    header = (
        f"🛍️ *{esc(session.product_info.product_name)}*\n"
        f"{filter_badge}  ·  📄 {p}/{t}{admin_info}\n"
        f"{SDIV}\n"
    )

    cards = []
    for i, item in enumerate(session.current_page_items()):
        global_idx = (session.page * config.RESULTS_PER_PAGE) + i + 1
        cards.append(product_card(item, global_idx))

    footer_parts = [f"{n} results"]
    if not session.israel_only and n_eligible < n_all and n_all > 0:
        footer_parts.append(f"✈️ {n_eligible} ship to Israel")
    footer = f"\n{SDIV}\n_" + "  ·  ".join(footer_parts) + "_"

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

def error_no_providers(is_admin: bool = False) -> str:
    base = (
        f"⚠️ *Service Temporarily Unavailable*\n"
        f"{DIV}\n\n"
    )
    if is_admin:
        base += (
            "No AI vision providers are configured\\.\n\n"
            "▸ /admin → 🔑 *API Keys*\n"
            "▸ Add OpenAI, Anthropic, or Google key\n\n"
            "_Free keys available at openai\\.com, anthropic\\.com, aistudio\\.google\\.com_"
        )
    else:
        base += (
            "The bot is temporarily unable to process photos\\.\n\n"
            "_Please try again later or contact the bot administrator\\._"
        )
    return base


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


def error_analysis_failed(is_admin: bool = False) -> str:
    base = (
        f"❌ *Analysis Failed*\n"
        f"{DIV}\n\n"
    )
    if is_admin:
        base += (
            "Couldn't identify this product\\. Try:\n"
            "▸ Better lighting\n"
            "▸ Less angle / closer shot\n"
            "▸ Include the product label\n\n"
            "_Check /admin → 🏥 Model Health for provider errors\\._"
        )
    else:
        base += (
            "Couldn't identify this product\\. Try:\n"
            "▸ Better lighting\n"
            "▸ Less angle / closer shot\n"
            "▸ Include the product label\n"
        )
    return base


def not_a_photo() -> str:
    return (
        f"📸 *Send a Photo*\n"
        f"{SDIV}\n"
        f"I need a product photo to search Amazon\\.\n"
        f"_Just take a pic and send it here\\!_"
    )


def error_rate_limited(max_requests: int, window_secs: int) -> str:
    if window_secs >= 3600:
        window_str = f"{window_secs // 3600} hour{'s' if window_secs >= 7200 else ''}"
    elif window_secs >= 60:
        window_str = f"{window_secs // 60} minute{'s' if window_secs >= 120 else ''}"
    else:
        window_str = f"{window_secs} second{'s' if window_secs != 1 else ''}"
    return (
        f"⏱ *Please wait*\n"
        f"{SDIV}\n"
        f"You can make up to *{max_requests} requests* every *{esc(window_str)}*\\.\n\n"
        f"_Please wait before sending another request\\._"
    )
