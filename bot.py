"""
bot.py — Telegram bot handlers.

All visual formatting is delegated to style.py.
All URL shortening is delegated to url_shortener.py.
Session state is kept in-memory per user_id.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
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
import style
import url_shortener
from image_analyzer import ProductInfo
from providers.base import ProviderResult
from providers.manager import analyse_image, get_providers
from amazon_search import AmazonItem, search_amazon, backend_name

logger = logging.getLogger(__name__)

# ── Callback data ──────────────────────────────────────────────────────────────
CB_FILTER_YES     = "filter:yes"
CB_FILTER_NO      = "filter:no"
CB_PREV           = "nav:prev"
CB_NEXT           = "nav:next"
CB_CHANGE_FILTER  = "nav:change"
CB_USE_RESULT     = "use:"           # + index
CB_TRY_DIFFERENTLY = "nav:try"       # re-search using next provider result


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class UserSession:
    all_provider_results: list[ProviderResult] = field(default_factory=list)
    chosen_result: Optional[ProviderResult]    = None
    product_info: Optional[ProductInfo]        = None
    chosen_provider_idx: int = 0               # index into all_provider_results

    all_items: list[AmazonItem]      = field(default_factory=list)
    filtered_items: list[AmazonItem] = field(default_factory=list)
    israel_only: bool = False
    page: int = 0          # current PAGE index (0-based), RESULTS_PER_PAGE items per page

    # Lazy loading: track which Amazon results page we last fetched
    amazon_page: int = 1   # next Amazon page to fetch (1 = first batch already done)
    more_available: bool = True   # False when Amazon returns empty page

    # Store raw image bytes so "Try differently" can re-analyse without re-upload
    image_bytes: Optional[bytes] = None

    # Cached admin flag — resolved once per session
    is_admin: Optional[bool] = None

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.filtered_items) + config.RESULTS_PER_PAGE - 1) // config.RESULTS_PER_PAGE)

    def current_page_items(self) -> list[AmazonItem]:
        s = self.page * config.RESULTS_PER_PAGE
        return self.filtered_items[s : s + config.RESULTS_PER_PAGE]

    def apply_filter(self, israel_only: bool) -> None:
        self.israel_only = israel_only
        self.page = 0
        eligible = [i for i in self.all_items if i.qualifies_for_israel_free_delivery]
        self.filtered_items = eligible if (israel_only and eligible) else list(self.all_items)

    def append_items(self, new_items: list[AmazonItem]) -> None:
        """Add more Amazon results without resetting the page position."""
        self.all_items.extend(new_items)
        eligible = [i for i in self.all_items if i.qualifies_for_israel_free_delivery]
        self.filtered_items = eligible if (self.israel_only and eligible) else list(self.all_items)


_sessions: dict[int, UserSession] = {}


# ── Rate limiter ───────────────────────────────────────────────────────────────
RATE_MAX_REQUESTS = 5
RATE_WINDOW_SECS  = 60
_rate_buckets: dict[int, deque] = defaultdict(deque)


def _is_rate_limited(user_id: int) -> bool:
    now    = time.monotonic()
    bucket = _rate_buckets[user_id]
    while bucket and now - bucket[0] > RATE_WINDOW_SECS:
        bucket.popleft()
    if len(bucket) >= RATE_MAX_REQUESTS:
        return True
    bucket.append(now)
    return False


def get_session(user_id: int) -> UserSession:
    if user_id not in _sessions:
        _sessions[user_id] = UserSession()
    return _sessions[user_id]


# ── Keyboards ──────────────────────────────────────────────────────────────────

def filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"✈️  Free delivery to 🇮🇱 Israel  (cart ≥ ${config.FREE_DELIVERY_THRESHOLD:.0f})",
            callback_data=CB_FILTER_YES,
        )],
        [InlineKeyboardButton(
            "🌐  Show all items",
            callback_data=CB_FILTER_NO,
        )],
    ])


def compare_keyboard(results: list[ProviderResult]) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results):
        conf_icon = style.CONF.get(r.confidence, "⚪")
        rows.append([InlineKeyboardButton(
            f"{conf_icon}  {r.provider_name}  ({r.confidence})",
            callback_data=f"{CB_USE_RESULT}{i}",
        )])
    return InlineKeyboardMarkup(rows)


async def results_keyboard(session: UserSession, affiliate_tag: Optional[str]) -> InlineKeyboardMarkup:
    """Paginated results keyboard: numbered product links + nav + try-differently."""
    items = session.current_page_items()

    long_urls = [item.affiliate_url(affiliate_tag) for item in items]
    url_map   = await url_shortener.shorten_many(long_urls)

    item_rows = [
        [InlineKeyboardButton(
            f"🛒  #{session.page * config.RESULTS_PER_PAGE + i + 1}  Shop on Amazon",
            url=url_map.get(item.affiliate_url(affiliate_tag), item.affiliate_url(affiliate_tag)),
        )]
        for i, item in enumerate(items)
    ]

    # Navigation row — show "Loading more…" counter hint when on last page
    nav = []
    if session.page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=CB_PREV))
    page_label = f"{session.page + 1} / {session.total_pages}"
    if session.page == session.total_pages - 1 and session.more_available:
        page_label += " +"     # hint that more exist
    nav.append(InlineKeyboardButton(page_label, callback_data="nav:noop"))
    if session.page < session.total_pages - 1 or session.more_available:
        nav.append(InlineKeyboardButton("▶", callback_data=CB_NEXT))

    toggle = "🌐  Show all" if session.israel_only else "✈️  Free delivery only"

    rows = [*item_rows, nav, [InlineKeyboardButton(toggle, callback_data=CB_CHANGE_FILTER)]]

    # "Try differently" — only shown when multiple AI results are available
    if len(session.all_provider_results) > 1:
        next_idx = (session.chosen_provider_idx + 1) % len(session.all_provider_results)
        next_name = session.all_provider_results[next_idx].provider_name.split("/")[-1][:20]
        rows.append([InlineKeyboardButton(
            f"🔄  Try differently",
            callback_data=CB_TRY_DIFFERENTLY,
        )])

    return InlineKeyboardMarkup(rows)


# ── Handlers ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from admin import handle_start_invite
    args = context.args or []
    if args and args[0].startswith("invite_"):
        await handle_start_invite(update, context)
        return

    await update.message.reply_text(style.welcome(), parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        style.help_text(config.FREE_DELIVERY_THRESHOLD),
        parse_mode="MarkdownV2",
    )


async def cmd_providers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        providers = await get_providers()
    except Exception:
        await update.message.reply_text(style.error_no_providers(), parse_mode="MarkdownV2")
        return
    try:
        sb = await backend_name()
    except Exception:
        sb = "not configured"
    await update.message.reply_text(
        style.providers_info(providers, config.VISION_MODE, sb),
        parse_mode="MarkdownV2",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if _is_rate_limited(user_id):
        await update.message.reply_text(
            style.error_rate_limited(RATE_MAX_REQUESTS, RATE_WINDOW_SECS),
            parse_mode="MarkdownV2",
        )
        return

    _sessions[user_id] = UserSession()
    session = _sessions[user_id]

    try:
        providers = await get_providers()
        n_providers = len(providers)
    except Exception:
        n_providers = 0

    if n_providers == 0:
        await update.message.reply_text(style.error_no_providers(), parse_mode="MarkdownV2")
        return

    msg = await update.message.reply_text(
        style.loading_vision(n_providers, config.VISION_MODE),
        parse_mode="MarkdownV2",
    )

    photo      = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await photo_file.download_as_bytearray())

    # Store for potential "Try differently" re-analysis
    session.image_bytes = image_bytes

    try:
        winner, all_results = await analyse_image(image_bytes, mode=config.VISION_MODE)
    except RuntimeError:
        await msg.edit_text(style.error_no_providers(), parse_mode="MarkdownV2")
        return
    except Exception as exc:
        logger.error("Vision analysis failed: %s", exc)
        await msg.edit_text(style.error_analysis_failed(), parse_mode="MarkdownV2")
        return

    session.all_provider_results = all_results
    session.chosen_provider_idx  = 0

    if config.VISION_MODE == "compare" and len(all_results) > 1:
        await msg.edit_text(
            style.compare_card(all_results, show_cost=config.SHOW_COST_INFO),
            parse_mode="MarkdownV2",
            reply_markup=compare_keyboard(all_results),
        )
        return

    session.chosen_result = winner
    session.product_info  = winner.to_product_info()

    await msg.edit_text(
        style.identification_card(winner, show_cost=config.SHOW_COST_INFO),
        parse_mode="MarkdownV2",
        reply_markup=filter_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = get_session(user_id)
    data    = query.data

    # ── Provider chosen in compare mode ───────────────────────────────────────
    if data.startswith(CB_USE_RESULT):
        idx = int(data[len(CB_USE_RESULT):])
        chosen = session.all_provider_results[idx]
        session.chosen_result       = chosen
        session.chosen_provider_idx = idx
        session.product_info        = chosen.to_product_info()
        await query.edit_message_text(
            style.identification_card(chosen, show_cost=config.SHOW_COST_INFO),
            parse_mode="MarkdownV2",
            reply_markup=filter_keyboard(),
        )
        return

    # ── Filter chosen → search Amazon ─────────────────────────────────────────
    if data in (CB_FILTER_YES, CB_FILTER_NO):
        if not session.product_info:
            await query.edit_message_text(
                "⚠️ Session expired — please send a new photo\\.",
                parse_mode="MarkdownV2",
            )
            return

        israel_only  = data == CB_FILTER_YES
        filter_label = "free delivery to 🇮🇱 Israel" if israel_only else "all items"

        await query.edit_message_text(
            style.loading_search(session.product_info.product_name, filter_label),
            parse_mode="MarkdownV2",
        )

        try:
            all_items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
        except RuntimeError:
            await query.edit_message_text(style.error_no_backend(), parse_mode="MarkdownV2")
            return
        except Exception as exc:
            logger.error("Amazon search failed: %s", exc)
            await query.edit_message_text(
                "❌ Search failed\\. Please try again\\.", parse_mode="MarkdownV2"
            )
            return

        session.all_items      = all_items
        session.amazon_page    = 1
        session.more_available = len(all_items) >= config.MAX_RESULTS
        session.apply_filter(israel_only)

        active_tag = await db.get_active_tag()
        await db.log_search(
            user_id=query.from_user.id,
            product_name=session.product_info.product_name,
            tag_used=active_tag or "none",
            provider_used=session.chosen_result.provider_name if session.chosen_result else "unknown",
            result_count=len(all_items),
            israel_filter=israel_only,
        )
        if active_tag:
            await db.increment_tag_search_count(active_tag)

        if not session.filtered_items:
            await query.edit_message_text(style.error_no_results(), parse_mode="MarkdownV2")
            return

        await _render_results(query, context, session)
        return

    # ── Toggle filter ─────────────────────────────────────────────────────────
    if data == CB_CHANGE_FILTER:
        session.apply_filter(not session.israel_only)
        if not session.filtered_items:
            await query.edit_message_text(
                "😔 No results with that filter\\. Try the other option\\.",
                parse_mode="MarkdownV2",
            )
            return
        await _render_results(query, context, session)
        return

    # ── Try differently — re-search with next provider's result ───────────────
    if data == CB_TRY_DIFFERENTLY:
        if len(session.all_provider_results) < 2:
            return
        next_idx = (session.chosen_provider_idx + 1) % len(session.all_provider_results)
        session.chosen_provider_idx = next_idx
        session.chosen_result       = session.all_provider_results[next_idx]
        session.product_info        = session.chosen_result.to_product_info()

        await query.edit_message_text(
            style.loading_search(session.product_info.product_name,
                                 "free delivery to 🇮🇱 Israel" if session.israel_only else "all items"),
            parse_mode="MarkdownV2",
        )

        try:
            new_items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
        except Exception as exc:
            logger.error("Try-differently search failed: %s", exc)
            await query.edit_message_text(
                "❌ Search failed\\. Please try again\\.", parse_mode="MarkdownV2"
            )
            return

        session.all_items      = new_items
        session.amazon_page    = 1
        session.more_available = len(new_items) >= config.MAX_RESULTS
        session.apply_filter(session.israel_only)

        if not session.filtered_items:
            await query.edit_message_text(style.error_no_results(), parse_mode="MarkdownV2")
            return

        await _render_results(query, context, session)
        return

    # ── Pagination ────────────────────────────────────────────────────────────
    if data == CB_PREV:
        session.page = max(0, session.page - 1)
        await _render_results(query, context, session)
        return

    if data == CB_NEXT:
        next_page = session.page + 1
        if next_page < session.total_pages:
            session.page = next_page
        elif session.more_available:
            # Lazy-load: fetch next batch from Amazon
            await query.edit_message_text("⠙ Loading more results…", parse_mode="MarkdownV2")
            try:
                session.amazon_page += 1
                new_items = await search_amazon(
                    session.product_info,
                    max_results=config.MAX_RESULTS,
                    page=session.amazon_page,
                )
                if new_items:
                    session.append_items(new_items)
                    session.more_available = len(new_items) >= config.MAX_RESULTS
                    session.page = next_page
                else:
                    session.more_available = False
                    session.page = session.total_pages - 1
            except Exception as exc:
                logger.error("Lazy-load failed: %s", exc)
                session.more_available = False
                session.page = session.total_pages - 1
        else:
            session.page = session.total_pages - 1   # already at end
        await _render_results(query, context, session)
        return


async def _render_results(query, context, session: UserSession) -> None:
    """Render the current page as a text list of product cards."""
    affiliate_tag = await db.get_active_tag()

    # Resolve admin status once per session
    if session.is_admin is None:
        uid = query.from_user.id
        session.is_admin = uid in config.ADMIN_IDS or await db.is_admin_in_db(uid)

    text     = style.results_page(session, affiliate_tag, is_admin=session.is_admin)
    keyboard = await results_keyboard(session, affiliate_tag)

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        style.not_a_photo(), parse_mode="MarkdownV2"
    )


# ── App factory ────────────────────────────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await db.init_db()
    if config.ADMIN_IDS:
        await db.seed_admins(config.ADMIN_IDS)
        logger.info("Seeded %d bootstrap admin(s)", len(config.ADMIN_IDS))
    await config.apply_db_settings()
    logger.info("DB settings applied to config.")


def build_application() -> Application:
    from admin import get_admin_handlers

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    for handler in get_admin_handlers():
        app.add_handler(handler)

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(MessageHandler(filters.PHOTO,                   handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_non_photo))
    return app
