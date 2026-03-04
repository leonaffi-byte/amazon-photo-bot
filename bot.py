"""
bot.py — Telegram bot handlers.

All visual formatting is delegated to style.py.
All URL shortening is delegated to url_shortener.py.
Session state is kept in-memory per user_id.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
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
from translator import detect_language, translate_and_refine

logger = logging.getLogger(__name__)

# ── Callback data ──────────────────────────────────────────────────────────────
CB_FILTER_YES      = "filter:yes"
CB_FILTER_NO       = "filter:no"
CB_PREV            = "nav:prev"
CB_NEXT            = "nav:next"
CB_CHANGE_FILTER   = "nav:change"
CB_USE_RESULT      = "use:"           # + index
CB_TRY_DIFFERENTLY = "nav:try"        # re-search using next provider result

# Placeholder image when a product has no photo URL
_PLACEHOLDER_IMG = "https://placehold.co/600x400/FF9900/FFF.png?text=Amazon"

# Maximum photo file size we'll process (bytes)
_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB

# Deduplication cache: file_unique_id → (timestamp, winner, all_results)
_analysis_cache: dict[str, tuple[float, ProviderResult, list[ProviderResult]]] = {}
_ANALYSIS_CACHE_TTL = 60  # seconds


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

    # page = current ITEM index (0-based) in filtered_items
    page: int = 0

    # Lazy loading: track which Amazon results page we last fetched
    amazon_page: int = 1      # next Amazon page to fetch (1 = first batch already done)
    more_available: bool = True

    # Photo carousel state — message ID of the current product photo card
    results_msg_id: Optional[int] = None

    # Store raw image bytes so "Try differently" can re-analyse without re-upload
    image_bytes: Optional[bytes] = None

    # Cached admin flag — resolved once per session
    is_admin: Optional[bool] = None

    @property
    def total_items(self) -> int:
        return max(1, len(self.filtered_items))

    def current_item(self) -> Optional[AmazonItem]:
        if not self.filtered_items:
            return None
        idx = max(0, min(self.page, len(self.filtered_items) - 1))
        return self.filtered_items[idx]

    # Keep this for compatibility with any code that calls current_page_items()
    def current_page_items(self) -> list[AmazonItem]:
        item = self.current_item()
        return [item] if item else []

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
    """
    Photo-carousel keyboard: one Shop button for the current product,
    ◀ N/Total ▶ navigation, filter toggle, and optional Try differently.
    """
    item  = session.current_item()
    total = len(session.filtered_items)
    idx   = session.page

    rows: list[list[InlineKeyboardButton]] = []

    # ── Shop button ────────────────────────────────────────────────────────────
    if item:
        long_url = item.affiliate_url(affiliate_tag)
        url_map  = await url_shortener.shorten_many([long_url])
        shop_url = url_map.get(long_url, long_url)
        rows.append([InlineKeyboardButton("🛒  Shop on Amazon", url=shop_url)])

    # ── Navigation row ─────────────────────────────────────────────────────────
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=CB_PREV))

    page_label = f"{idx + 1} / {total}"
    if idx == total - 1 and session.more_available:
        page_label += " +"
    nav.append(InlineKeyboardButton(page_label, callback_data="nav:noop"))

    if idx < total - 1 or session.more_available:
        nav.append(InlineKeyboardButton("▶", callback_data=CB_NEXT))

    if nav:
        rows.append(nav)

    # ── Filter toggle ──────────────────────────────────────────────────────────
    toggle = "🌐  Show all" if session.israel_only else "✈️  Free delivery only"
    rows.append([InlineKeyboardButton(toggle, callback_data=CB_CHANGE_FILTER)])

    # ── Try differently ────────────────────────────────────────────────────────
    if len(session.all_provider_results) > 1:
        rows.append([InlineKeyboardButton("🔄  Try differently", callback_data=CB_TRY_DIFFERENTLY)])

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

    # ── Handle optional caption (user hint, possibly in Hebrew/Russian) ────────
    context_hint: Optional[str] = None
    raw_caption = (update.message.caption or "").strip()
    if raw_caption:
        lang = detect_language(raw_caption)
        if lang != "en":
            try:
                en_caption, _ = await translate_and_refine(raw_caption)
            except Exception:
                en_caption = raw_caption
        else:
            en_caption = raw_caption
        context_hint = en_caption

    msg = await update.message.reply_text(
        style.loading_vision(n_providers, config.VISION_MODE, context_hint=context_hint),
        parse_mode="MarkdownV2",
    )

    photo = update.message.photo[-1]
    if photo.file_size and photo.file_size > _MAX_PHOTO_BYTES:
        await update.message.reply_text("Photo is too large (max 10 MB). Please send a smaller image.")
        return
    photo_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await photo_file.download_as_bytearray())

    session.image_bytes = image_bytes

    # Check dedup cache to avoid re-analyzing the same photo within TTL
    cache_key = photo.file_unique_id
    cached = _analysis_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _ANALYSIS_CACHE_TTL:
        logger.info("Dedup cache hit for %s (user %d)", cache_key, user_id)
        winner, all_results = cached[1], cached[2]
    else:
        try:
            winner, all_results = await analyse_image(
                image_bytes, mode=config.VISION_MODE, context_hint=context_hint, user_id=user_id
            )
            _analysis_cache[cache_key] = (time.monotonic(), winner, all_results)
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
        session.results_msg_id = None   # reset so _render_results sends fresh photo
        session.apply_filter(israel_only)

        active_tag = await db.get_active_tag()
        await db.log_search(
            user_id=query.from_user.id,
            product_name=session.product_info.product_name,
            tag_used=active_tag or "none",
            provider_used=session.chosen_result.provider_name if session.chosen_result else "text-search",
            result_count=len(all_items),
            israel_filter=israel_only,
            search_type="text" if session.chosen_result is None else "photo",
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

        # Show loading in caption while keeping the carousel
        try:
            await query.edit_message_caption(
                caption=style.loading_search(
                    session.product_info.product_name,
                    "free delivery to 🇮🇱 Israel" if session.israel_only else "all items"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

        try:
            new_items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
        except Exception as exc:
            logger.error("Try-differently search failed: %s", exc)
            await query.edit_message_caption(
                caption="❌ Search failed\\. Please try again\\.", parse_mode="MarkdownV2"
            )
            return

        session.all_items      = new_items
        session.amazon_page    = 1
        session.more_available = len(new_items) >= config.MAX_RESULTS
        session.apply_filter(session.israel_only)

        if not session.filtered_items:
            try:
                await query.edit_message_caption(
                    caption=style.error_no_results(), parse_mode="MarkdownV2"
                )
            except Exception:
                await query.edit_message_text(
                    style.error_no_results(), parse_mode="MarkdownV2"
                )
            return

        await _render_results(query, context, session)
        return

    # ── Pagination ────────────────────────────────────────────────────────────
    if data == CB_PREV:
        session.page = max(0, session.page - 1)
        await _render_results(query, context, session)
        return

    if data == CB_NEXT:
        total = len(session.filtered_items)
        next_page = session.page + 1

        if next_page < total:
            session.page = next_page
        elif session.more_available:
            # Lazy-load: fetch next batch from Amazon
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
                    session.page = max(0, total - 1)
            except Exception as exc:
                logger.error("Lazy-load failed: %s", exc)
                session.more_available = False
                session.page = max(0, total - 1)
        else:
            session.page = max(0, total - 1)   # already at end

        await _render_results(query, context, session)
        return


def _spawn_price_check(context, session: UserSession, item, affiliate_tag: str | None,
                       chat_id: int | None = None) -> None:
    """
    Fire-and-forget: fetch price history (CamelCamelCamel → Keepa) in the
    background and silently update the product card caption when it arrives.
    """
    try:
        msg_id    = session.results_msg_id
        page_snap = session.page
        if not chat_id or not msg_id:
            return
        asyncio.create_task(
            _verify_price_async(
                context.bot, chat_id, msg_id,
                item, session, page_snap, affiliate_tag,
            )
        )
    except Exception:
        pass


async def _verify_price_async(
    bot, chat_id: int, msg_id: int,
    item, session: UserSession, page_snap: int,
    affiliate_tag: str | None,
) -> None:
    """
    Background coroutine: fetch price history then edit the caption.
    Silently aborts if user has navigated away or no data is found.
    """
    try:
        import price_history as ph_mod
        ph = await ph_mod.get_price_history(item.asin)
        if not ph:
            return

        # Guard: abort if user already moved to another product
        if session.page != page_snap:
            return

        # Store on session so Israel update can include it
        session._last_price_history = ph

        # Include any already-verified Israel result
        israel_result = getattr(session, "_last_israel_result", None)

        caption = style.product_caption(
            item,
            index         = page_snap + 1,
            total         = len(session.filtered_items),
            is_admin      = session.is_admin,
            provider_name = session.chosen_result.provider_name if session.chosen_result else None,
            affiliate_tag = affiliate_tag,
            israel_verified = israel_result,
            price_history   = ph,
        )
        keyboard = await results_keyboard(session, affiliate_tag)
        try:
            await bot.edit_message_caption(
                chat_id      = chat_id,
                message_id   = msg_id,
                caption      = caption,
                parse_mode   = "MarkdownV2",
                reply_markup = keyboard,
            )
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Price history async fetch failed: %s", exc)


def _spawn_israel_check(context, session: UserSession, item, affiliate_tag: str | None,
                        chat_id: int | None = None) -> None:
    """
    Fire-and-forget: verify Israel shipping in the background and silently
    update the product card caption when the result arrives.
    Does nothing if no Israeli proxy is configured.
    """
    try:
        msg_id    = session.results_msg_id
        page_snap = session.page
        if not chat_id or not msg_id:
            return
        asyncio.create_task(
            _verify_israel_async(
                context.bot, chat_id, msg_id,
                item, session, page_snap, affiliate_tag,
            )
        )
    except Exception:
        pass


async def _verify_israel_async(
    bot, chat_id: int, msg_id: int,
    item, session: UserSession, page_snap: int,
    affiliate_tag: str | None,
) -> None:
    """
    Background coroutine: checks Israel shipping via the proxy,
    then edits the Telegram message caption with the verified result.
    Silently aborts if the user has navigated to a different product.
    """
    try:
        import israel_scraper
        if not await israel_scraper.is_configured():
            return

        result = await asyncio.wait_for(
            israel_scraper.check_shipping(item.asin),
            timeout=14.0,
        )
        if not result.verified:
            return   # no new info — leave heuristic caption as-is

        # Guard: abort if user already moved to another product
        if session.page != page_snap:
            return

        # Store on session so price-history update can include it
        session._last_israel_result = result

        # Include any already-loaded price history if available
        ph = getattr(session, "_last_price_history", None)

        caption = style.product_caption(
            item,
            index           = page_snap + 1,
            total           = len(session.filtered_items),
            is_admin        = session.is_admin,
            provider_name   = session.chosen_result.provider_name if session.chosen_result else None,
            affiliate_tag   = affiliate_tag,
            israel_verified = result,
            price_history   = ph,
        )
        keyboard = await results_keyboard(session, affiliate_tag)
        try:
            await bot.edit_message_caption(
                chat_id      = chat_id,
                message_id   = msg_id,
                caption      = caption,
                parse_mode   = "MarkdownV2",
                reply_markup = keyboard,
            )
        except Exception:
            pass   # message may have been deleted/edited by user — that's fine
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        logger.debug("Israel async verify failed: %s", exc)


async def _render_results(query, context, session: UserSession) -> None:
    """
    Render the current product as a photo carousel card.

    First call: sends a new photo message, deletes the old text message.
    Subsequent calls: edits the existing photo message via edit_message_media.
    Falls back to text if the image URL is unavailable.
    """
    affiliate_tag = await db.get_active_tag()

    # Resolve admin status once per session
    if session.is_admin is None:
        uid = query.from_user.id
        session.is_admin = uid in config.ADMIN_IDS or await db.is_admin_in_db(uid)

    item  = session.current_item()
    total = len(session.filtered_items)

    if not item:
        await query.edit_message_text(style.error_no_results(), parse_mode="MarkdownV2")
        return

    caption  = style.product_caption(
        item,
        index        = session.page + 1,
        total        = total,
        is_admin     = session.is_admin,
        provider_name= session.chosen_result.provider_name if session.chosen_result else None,
        affiliate_tag= affiliate_tag,
    )
    keyboard = await results_keyboard(session, affiliate_tag)
    image_url = item.image_url or _PLACEHOLDER_IMG

    # ── First render: text msg → send new photo, delete old msg ───────────────
    if session.results_msg_id is None:
        try:
            sent = await context.bot.send_photo(
                chat_id       = query.message.chat_id,
                photo         = image_url,
                caption       = caption,
                parse_mode    = "MarkdownV2",
                reply_markup  = keyboard,
            )
            session.results_msg_id = sent.message_id
            # Delete the text identification card (best-effort)
            try:
                await query.message.delete()
            except Exception:
                pass
            # Kick off background checks (non-blocking)
            _spawn_israel_check(context, session, item, affiliate_tag,
                                chat_id=query.message.chat_id)
            _spawn_price_check(context, session, item, affiliate_tag,
                               chat_id=query.message.chat_id)
            return
        except Exception as exc:
            logger.error("send_photo failed: %s", exc)
            # Fall through to text fallback

    # ── Subsequent renders: edit the existing photo message ───────────────────
    else:
        try:
            await query.edit_message_media(
                media        = InputMediaPhoto(
                    media      = image_url,
                    caption    = caption,
                    parse_mode = "MarkdownV2",
                ),
                reply_markup = keyboard,
            )
            # Kick off background checks (non-blocking)
            _spawn_israel_check(context, session, item, affiliate_tag,
                                chat_id=query.message.chat_id)
            _spawn_price_check(context, session, item, affiliate_tag,
                               chat_id=query.message.chat_id)
            return
        except Exception as exc:
            logger.warning("edit_message_media failed (%s), trying caption-only", exc)
            try:
                await query.edit_message_caption(
                    caption      = caption,
                    parse_mode   = "MarkdownV2",
                    reply_markup = keyboard,
                )
                return
            except Exception as exc2:
                logger.error("edit_message_caption also failed: %s", exc2)

    # ── Text fallback ──────────────────────────────────────────────────────────
    try:
        text_body = style.results_page(session, affiliate_tag, is_admin=session.is_admin)
        await query.edit_message_text(
            text_body,
            parse_mode            = "MarkdownV2",
            reply_markup          = keyboard,
            disable_web_page_preview = True,
        )
    except Exception as exc:
        logger.error("Text fallback also failed: %s", exc)


async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages as product search queries (English / Hebrew / Russian)."""
    user_id = update.effective_user.id
    text    = (update.message.text or "").strip()

    if not text:
        return

    if _is_rate_limited(user_id):
        await update.message.reply_text(
            style.error_rate_limited(RATE_MAX_REQUESTS, RATE_WINDOW_SECS),
            parse_mode="MarkdownV2",
        )
        return

    _sessions[user_id] = UserSession()
    session = _sessions[user_id]

    msg = await update.message.reply_text("🔍 Processing…")

    # ── Detect language & translate ───────────────────────────────────────────
    lang = detect_language(text)
    try:
        english, refined_query = await translate_and_refine(text)
    except Exception as exc:
        logger.warning("translate_and_refine failed: %s", exc)
        english, refined_query = text, text

    lang_labels = {"he": "🇮🇱 Hebrew", "ru": "🇷🇺 Russian"}

    # ── Build a mock ProductInfo from the refined query ───────────────────────
    session.product_info = ProductInfo(
        product_name        = english[:100],
        brand               = None,
        category            = "All",
        key_features        = [],
        amazon_search_query = refined_query,
        alternative_query   = refined_query,
        confidence          = "high",
        notes               = "",
    )

    # ── Show what we're searching for ─────────────────────────────────────────
    await msg.edit_text(
        style.text_search_ready(
            original     = text,
            english      = english,
            refined      = refined_query,
            lang_label   = lang_labels.get(lang),
        ),
        parse_mode="MarkdownV2",
    )

    # ── Show filter keyboard (reuse existing flow) ────────────────────────────
    await update.message.reply_text(
        "Choose delivery option:",
        reply_markup=filter_keyboard(),
    )


# ── App factory ────────────────────────────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await db.init_db()
    if config.ADMIN_IDS:
        await db.seed_admins(config.ADMIN_IDS)
        logger.info("Seeded %d bootstrap admin(s)", len(config.ADMIN_IDS))
    await config.apply_db_settings()
    logger.info("DB settings applied to config.")


async def cmd_shorten(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command:  /shorten <amazon_url_or_asin>

    Extracts the ASIN from any Amazon product URL (or accepts a bare ASIN),
    injects the active affiliate tag, shortens via amznl.cc, and replies with
    the ready-to-share link.

    Accepts:
      /shorten https://www.amazon.com/dp/B08XYZ12AB
      /shorten https://www.amazon.com/Some-Title/dp/B08XYZ12AB/ref=...
      /shorten B08XYZ12AB
    """
    user_id = update.effective_user.id
    if not (user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)):
        return   # silently ignore non-admins

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "📎 *Usage:*\n"
            "`/shorten https://amazon.com/dp/ASIN`\n"
            "`/shorten B08XYZ12AB`",
            parse_mode="MarkdownV2",
        )
        return

    raw = parts[1].strip()

    # ── Resolve ASIN ──────────────────────────────────────────────────────────
    asin: str | None = None

    # Bare ASIN: exactly 10 alphanumeric characters
    if re.fullmatch(r"[A-Za-z0-9]{10}", raw):
        asin = raw.upper()
    else:
        # Extract from URL: /dp/XXXXXXXXXX or /gp/product/XXXXXXXXXX
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", raw, re.IGNORECASE)
        if m:
            asin = m.group(1).upper()

    if not asin:
        await update.message.reply_text(
            "❌ Could not find an ASIN in that input\\.\n\n"
            "Make sure the URL contains `/dp/` followed by a 10\\-char product ID\\.",
            parse_mode="MarkdownV2",
        )
        return

    # ── Build affiliate URL ───────────────────────────────────────────────────
    tag = await db.get_active_tag()   # returns tag string or None
    base = f"https://www.amazon.com/dp/{asin}"
    if tag:
        long_url = f"{base}?tag={tag}&linkCode=ogi&th=1&psc=1"
    else:
        long_url = base

    # ── Shorten ───────────────────────────────────────────────────────────────
    short_url = await url_shortener.shorten(long_url, label=asin, user_id=user_id)
    shortened  = short_url != long_url   # False if all shorteners failed

    # ── Reply ─────────────────────────────────────────────────────────────────
    tag_line = f"🏷 Tag: `{style.esc(tag)}`\n" if tag else "🏷 Tag: _none active_\n"
    short_line   = f"`{style.esc(short_url)}`" if shortened else f"_{style.esc(short_url)}_"
    full_preview = style.esc(long_url[:80] + ("…" if len(long_url) > 80 else ""))

    msg = (
        f"🔗 *Short link ready\\!*\n\n"
        f"{short_line}\n\n"
        f"📦 ASIN: `{asin}`\n"
        f"{tag_line}"
        f"\n_Full:_ `{full_preview}`"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


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
    app.add_handler(CommandHandler("shorten",   cmd_shorten))
    app.add_handler(MessageHandler(filters.PHOTO,                   handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_search))
    return app
