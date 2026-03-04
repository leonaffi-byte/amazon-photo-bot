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

from PIL import Image as _PILImage
import io as _io

import config
import database as db
import style
import url_shortener
from correlation import get_correlation_id, new_correlation_id
from image_analyzer import ProductInfo
from providers.base import ProviderResult
from providers.manager import analyse_image, get_providers
from amazon_search import AmazonItem, search_amazon, backend_name
from translator import detect_language, translate_and_refine
from metrics import REQUESTS_TOTAL
from dataforseo_labs import DataForSEOLabs

logger = logging.getLogger(__name__)

# ── Callback data ──────────────────────────────────────────────────────────────
CB_FILTER_YES      = "filter:yes"
CB_FILTER_NO       = "filter:no"
CB_PREV            = "nav:prev"
CB_NEXT            = "nav:next"
CB_CHANGE_FILTER   = "nav:change"
CB_USE_RESULT      = "use:"           # + index
CB_TRY_DIFFERENTLY = "nav:try"        # re-search using next provider result
CB_SIMILAR         = "dfs:similar:"   # + asin  — show competitor products
CB_RELATED         = "dfs:related:"   # + keyword — run search for related term

# Cached DFS Labs client (None when creds absent)
_dfs_labs_client: "DataForSEOLabs | None" = None

# Placeholder image when a product has no photo URL
_PLACEHOLDER_IMG = "https://placehold.co/600x400/FF9900/FFF.png?text=Amazon"

# Maximum photo file size we'll process (bytes)
_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB

# Background task references — prevent fire-and-forget tasks from being GC'd
_background_tasks: set[asyncio.Task] = set()

# Deduplication cache: file_unique_id → (timestamp, winner, all_results)
_analysis_cache: dict[str, tuple[float, ProviderResult, list[ProviderResult]]] = {}
_ANALYSIS_CACHE_TTL = 60  # seconds

_MAX_IMAGE_DIM = 1024
_JPEG_QUALITY = 85
_SESSION_TTL = 600  # 10 minutes
_CLEANUP_INTERVAL = 300  # 5 minutes

def _compress_image(raw: bytes) -> bytes:
    img = _PILImage.open(_io.BytesIO(raw))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_IMAGE_DIM:
        ratio = _MAX_IMAGE_DIM / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), _PILImage.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


async def _periodic_cleanup() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        now = time.monotonic()
        stale_keys = [k for k, (ts, *_) in _analysis_cache.items()
                      if now - ts > _ANALYSIS_CACHE_TTL]
        for k in stale_keys:
            del _analysis_cache[k]
        stale_sessions = [uid for uid, s in _sessions.items()
                          if now - s._created_at > _SESSION_TTL]
        for uid in stale_sessions:
            del _sessions[uid]
        empty = [uid for uid, dq in _rate_buckets.items() if not dq]
        for uid in empty:
            del _rate_buckets[uid]
        if stale_keys or stale_sessions or empty:
            logger.debug("Cleanup: %d cache, %d sessions, %d buckets evicted",
                         len(stale_keys), len(stale_sessions), len(empty))



async def _get_dfs_labs() -> "DataForSEOLabs | None":
    """Return a cached DataForSEOLabs client, or None if creds not configured."""
    global _dfs_labs_client
    if _dfs_labs_client is not None:
        return _dfs_labs_client
    from settings_store import key_store
    login    = await key_store.get("dataforseo_login")
    password = await key_store.get("dataforseo_password")
    if login and password:
        _dfs_labs_client = DataForSEOLabs(login, password)
        return _dfs_labs_client
    return None


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
    _created_at: float = field(default_factory=time.monotonic)

    # Per-ASIN Israel shipping verification results (populated by background checks).
    # True = confirmed ships to Israel, False = confirmed does NOT ship.
    # Items verified as False are hidden from the israel_only filter.
    _israel_verified: dict = field(default_factory=dict)  # asin -> bool

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

    def _israel_eligible(self, item: AmazonItem) -> bool:
        """
        True when an item should appear in the Israel-only filter.

        Priority:
          1. Playwright-verified FALSE  → always exclude (confirmed non-shipper)
          2. Playwright-verified TRUE   → always include (confirmed shipper)
          3. No verification yet        → use heuristic (qualifies_for_israel_free_delivery)
             which now includes free_delivery_likely so far more items pass.
        """
        verified = self._israel_verified.get(item.asin)
        if verified is False:
            return False
        if verified is True:
            return True
        return item.qualifies_for_israel_free_delivery

    def apply_filter(self, israel_only: bool) -> None:
        self.israel_only = israel_only
        self.page = 0
        if israel_only:
            eligible = [i for i in self.all_items if self._israel_eligible(i)]
            # If heuristic yields nothing, show everything rather than an empty list
            self.filtered_items = eligible if eligible else list(self.all_items)
        else:
            self.filtered_items = list(self.all_items)

    def append_items(self, new_items: list[AmazonItem]) -> None:
        """Add more Amazon results without resetting the page position."""
        self.all_items.extend(new_items)
        if self.israel_only:
            eligible = [i for i in self.all_items if self._israel_eligible(i)]
            self.filtered_items = eligible if eligible else list(self.all_items)
        else:
            self.filtered_items = list(self.all_items)

    def record_israel_result(self, asin: str, ships: bool) -> None:
        """Called by background Israel check; re-applies active filter."""
        self._israel_verified[asin] = ships
        # Re-apply current filter so confirmed non-shippers disappear from results
        self.apply_filter(self.israel_only)


_sessions: dict[int, UserSession] = {}


# ── Rate limiter ───────────────────────────────────────────────────────────────
# Per-user rate limiting: custom limits from DB, fallback to config defaults.
# In-memory buckets keyed by user_id; limits resolved per-check.
_rate_buckets: dict[int, deque] = defaultdict(deque)
# Local cache of per-user DB limits to avoid hitting DB on every request.
# Populated on first check, refreshed when admin changes limits.
_rate_limit_cache: dict[int, tuple[int, int]] = {}  # user_id -> (max_requests, window_seconds)


async def _get_user_limits(user_id: int) -> tuple[int, int]:
    """Return (max_requests, window_seconds) for the given user.

    Checks local cache first, then DB for per-user override, then config defaults.
    """
    if user_id in _rate_limit_cache:
        return _rate_limit_cache[user_id]
    custom = await db.get_user_rate_limit(user_id)
    if custom:
        limits = (custom.max_requests, custom.window_seconds)
        _rate_limit_cache[user_id] = limits
        return limits
    return (config.DEFAULT_RATE_LIMIT, config.DEFAULT_RATE_WINDOW)


def invalidate_rate_limit_cache(user_id: int | None = None) -> None:
    """Clear cached rate limits. Called when admin changes limits."""
    if user_id is not None:
        _rate_limit_cache.pop(user_id, None)
    else:
        _rate_limit_cache.clear()


async def _is_rate_limited(user_id: int) -> tuple[bool, int, int]:
    """Check whether user_id is rate-limited.

    Returns (is_limited, max_requests, window_seconds) so the caller can
    display the correct limits in the error message.
    """
    max_req, window = await _get_user_limits(user_id)
    now    = time.monotonic()
    bucket = _rate_buckets[user_id]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= max_req:
        return True, max_req, window
    bucket.append(now)
    return False, max_req, window


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
    rows.append([InlineKeyboardButton("✨ Use best result", callback_data=f"{CB_USE_RESULT}0")])
    return InlineKeyboardMarkup(rows)


async def results_keyboard(session: UserSession, affiliate_tag: Optional[str], user_id: int = 0) -> InlineKeyboardMarkup:
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
        long_url = item.affiliate_url(affiliate_tag, subtag=f"tg_{user_id}" if user_id else None)
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
        next_idx = (session.chosen_provider_idx + 1) % len(session.all_provider_results)
        next_name = session.all_provider_results[next_idx].provider_name
        current = session.chosen_provider_idx + 1
        total_providers = len(session.all_provider_results)
        rows.append([InlineKeyboardButton(f"🔄 Try {next_name} ({current}/{total_providers})", callback_data=CB_TRY_DIFFERENTLY)])

    # ── Similar products (DFS Labs) ────────────────────────────────────────────
    if item and item.asin:
        rows.append([InlineKeyboardButton(
            "🔍  Similar products",
            callback_data=f"{CB_SIMILAR}{item.asin}",
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
    user_id = update.effective_user.id
    is_admin = user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)
    try:
        providers = await get_providers()
    except Exception:
        await update.message.reply_text(style.error_no_providers(is_admin=is_admin), parse_mode="MarkdownV2")
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
    REQUESTS_TOTAL.inc(labels={"type": "photo"})
    user_id = update.effective_user.id
    cid = new_correlation_id()
    logger.info("Photo received from user %d [cid=%s]", user_id, cid)

    limited, max_req, window = await _is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(
            style.error_rate_limited(max_req, window),
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
        is_admin = user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)
        await update.message.reply_text(style.error_no_providers(is_admin=is_admin), parse_mode="MarkdownV2")
        return

    # ── Reject oversized photos before any loading message ──────────────────
    photo = update.message.photo[-1]
    if photo.file_size and photo.file_size > _MAX_PHOTO_BYTES:
        await update.message.reply_text("Photo is too large (max 10 MB). Please send a smaller image.")
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
    photo_file = await context.bot.get_file(photo.file_id)
    raw_bytes = bytes(await photo_file.download_as_bytearray())
    image_bytes = _compress_image(raw_bytes)
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
            is_admin = user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)
            await msg.edit_text(style.error_no_providers(is_admin=is_admin), parse_mode="MarkdownV2")
            return
        except Exception as exc:
            logger.error("Vision analysis failed: %s", exc)
            is_admin = user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)
            await msg.edit_text(style.error_analysis_failed(is_admin=is_admin), parse_mode="MarkdownV2")
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
    REQUESTS_TOTAL.inc(labels={"type": "callback"})
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    cid = new_correlation_id()
    logger.info("Callback '%s' from user %d [cid=%s]", query.data, user_id, cid)
    session = get_session(user_id)
    data    = query.data

    # ── Noop page indicator tap — show page info ──────────────────────────────
    if data == "nav:noop":
        idx = session.page
        total = len(session.filtered_items)
        await query.answer(f"Page {idx + 1} of {total}")

    # ── DFS Labs: Similar products ─────────────────────────────────────────────
    if data.startswith(CB_SIMILAR):
        asin = data[len(CB_SIMILAR):]
        await _handle_similar(query, context, session, asin)
        return

    # ── DFS Labs: Related keyword search ──────────────────────────────────────
    if data.startswith(CB_RELATED):
        keyword = data[len(CB_RELATED):]
        # Build a minimal ProductInfo so search_amazon can run
        if not session.product_info:
            from image_analyzer import ProductInfo as _PI
            session.product_info = _PI(product_name=keyword)
        else:
            session.product_info.product_name = keyword

        session.all_items      = []
        session.amazon_page    = 1
        session.more_available = False
        session.page           = 0
        session.results_msg_id = None

        try:
            await query.edit_message_text(
                style.loading_search(keyword, "all items"),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

        try:
            items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
        except Exception as exc:
            logger.error("Related keyword search failed: %s", exc)
            await query.edit_message_text(
                "❌ Search failed\\. Please try again\\.", parse_mode="MarkdownV2"
            )
            return

        session.all_items = items
        session.more_available = len(items) >= config.MAX_RESULTS
        session.apply_filter(session.israel_only)

        if not session.filtered_items:
            await query.edit_message_text(
                style.error_no_results(), parse_mode="MarkdownV2"
            )
            return

        await _render_results(query, context, session)
        return

    # ── Provider chosen in compare mode ───────────────────────────────────────
    if data.startswith(CB_USE_RESULT):
        try:
            idx = int(data[len(CB_USE_RESULT):])
            chosen = session.all_provider_results[idx]
        except (ValueError, IndexError):
            await query.answer("Session expired — please send a new photo.", show_alert=True)
            return
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
                "⚠️ Session expired\\. Send a new photo or type a product name to start over\\.",
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
            correlation_id=get_correlation_id(),
        )
        if active_tag:
            await db.increment_tag_search_count(active_tag)

        if not session.filtered_items:
            await query.edit_message_text(style.error_no_results(), parse_mode="MarkdownV2")
            return

        await _render_results(query, context, session)

        # Spawn related-keyword suggestions (non-blocking, needs chat_id)
        if session.product_info and session.product_info.product_name:
            _spawn_related_keywords(
                context,
                chat_id = query.message.chat_id,
                keyword = session.product_info.product_name,
            )
        return

    # ── Toggle filter ─────────────────────────────────────────────────────────
    if data == CB_CHANGE_FILTER:
        session.apply_filter(not session.israel_only)
        if not session.filtered_items:
            # Show button to toggle back instead of a dead-end message
            toggle_back = "🌐  Show all" if session.israel_only else "✈️  Free delivery only"
            await query.edit_message_text(
                "😔 No results with that filter\\. Try the other option\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(toggle_back, callback_data=CB_CHANGE_FILTER)],
                ]),
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
                try:
                    await query.edit_message_caption(
                        caption="⏳ _Loading more results…_",
                        parse_mode="MarkdownV2",
                    )
                except Exception:
                    pass
                new_items = await search_amazon(
                    session.product_info,
                    max_results=config.MAX_RESULTS,
                    page=session.amazon_page + 1,
                )
                if new_items:
                    session.amazon_page += 1
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
            session.page = max(0, total - 1)
            await query.answer("No more results available.", show_alert=False)

        await _render_results(query, context, session)
        return


def _spawn_background_check(
    coro,
    context,
    session: UserSession,
    chat_id: int | None = None,
) -> None:
    """Fire-and-forget: run a background coroutine that updates the product card."""
    try:
        msg_id = session.results_msg_id
        if not chat_id or not msg_id:
            return
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception:
        pass


async def _verify_price_async(
    bot, chat_id: int, msg_id: int,
    item, session: UserSession, page_snap: int,
    affiliate_tag: str | None, user_id: int = 0,
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
        keyboard = await results_keyboard(session, affiliate_tag, user_id=user_id)
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


async def _verify_israel_async(
    bot, chat_id: int, msg_id: int,
    item, session: UserSession, page_snap: int,
    affiliate_tag: str | None, user_id: int = 0,
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

        # Update per-ASIN verification dict (re-applies filter — removes confirmed non-shippers)
        if result.ships_to_israel is not None:
            session.record_israel_result(item.asin, result.ships_to_israel)

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
        keyboard = await results_keyboard(session, affiliate_tag, user_id=user_id)
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


def _spawn_related_keywords(
    context, chat_id: int, keyword: str
) -> None:
    """
    Fire-and-forget: fetch DFS Labs related keywords and send them as a
    separate inline-button message so the user can tap to re-search.
    Silently does nothing when DFS Labs creds are absent.
    """
    asyncio.create_task(_send_related_keywords(context.bot, chat_id, keyword))


async def _send_related_keywords(bot, chat_id: int, keyword: str) -> None:
    try:
        labs = await _get_dfs_labs()
        if not labs:
            return
        related = await labs.related_keywords(keyword, limit=6)
        if not related:
            return
        # Build buttons (max 3 per row)
        buttons = [
            InlineKeyboardButton(
                r.label(),
                callback_data=f"{CB_RELATED}{r.keyword[:40]}",
            )
            for r in related
        ]
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        await bot.send_message(
            chat_id     = chat_id,
            text        = "🔍 *Related Amazon searches:*",
            parse_mode  = "MarkdownV2",
            reply_markup= InlineKeyboardMarkup(rows),
        )
    except Exception as exc:
        logger.debug("Related keywords fetch failed: %s", exc)


async def _handle_similar(query, context, session: UserSession, asin: str) -> None:
    """
    Load competitor products from DFS Labs Product Competitors,
    enrich them via Ranked Keywords, and display as new product results.
    """
    try:
        await query.answer("🔍 Finding similar products…")
    except Exception:
        pass

    labs = await _get_dfs_labs()
    if not labs:
        await query.answer("DataForSEO not configured.", show_alert=True)
        return

    try:
        await query.edit_message_caption(
            caption    = "🔍 *Finding similar products\\.\\.\\.*",
            parse_mode = "MarkdownV2",
        )
    except Exception:
        pass

    try:
        competitor_asins = await labs.get_competitors(asin, limit=20)
        if not competitor_asins:
            await query.edit_message_caption(
                caption    = "😔 No similar products found\\.",
                parse_mode = "MarkdownV2",
            )
            return

        enriched = await labs.enrich_many(competitor_asins[:12], concurrency=4)
        if not enriched:
            await query.edit_message_caption(
                caption    = "😔 Could not load product details\\.",
                parse_mode = "MarkdownV2",
            )
            return

        # Convert DFSProduct → AmazonItem
        new_items: list[AmazonItem] = []
        for comp_asin, prod in enriched.items():
            new_items.append(AmazonItem(
                asin         = comp_asin,
                title        = prod.title or comp_asin,
                price        = prod.price,
                currency     = prod.currency or "USD",
                image_url    = prod.image_url or "",
                rating       = prod.rating,
                free_delivery= None,
                is_prime     = None,
            ))

        # Load into session as new results
        session.all_items      = new_items
        session.amazon_page    = 1
        session.more_available = False
        session.page           = 0
        session.results_msg_id = None   # force fresh photo send
        session.apply_filter(session.israel_only)

        if not session.filtered_items:
            await query.edit_message_caption(
                caption    = style.error_no_results(),
                parse_mode = "MarkdownV2",
            )
            return

        await _render_results(query, context, session)

    except Exception as exc:
        logger.error("Similar products failed: %s", exc)
        try:
            await query.edit_message_caption(
                caption    = "❌ Failed to load similar products\\.",
                parse_mode = "MarkdownV2",
            )
        except Exception:
            pass


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
    uid = query.from_user.id
    keyboard = await results_keyboard(session, affiliate_tag, user_id=uid)
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
            _spawn_background_check(
                _verify_israel_async(context.bot, query.message.chat_id, session.results_msg_id,
                                     item, session, session.page, affiliate_tag, user_id=uid),
                context, session, chat_id=query.message.chat_id,
            )
            _spawn_background_check(
                _verify_price_async(context.bot, query.message.chat_id, session.results_msg_id,
                                    item, session, session.page, affiliate_tag, user_id=uid),
                context, session, chat_id=query.message.chat_id,
            )
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
            _spawn_background_check(
                _verify_israel_async(context.bot, query.message.chat_id, session.results_msg_id,
                                     item, session, session.page, affiliate_tag, user_id=uid),
                context, session, chat_id=query.message.chat_id,
            )
            _spawn_background_check(
                _verify_price_async(context.bot, query.message.chat_id, session.results_msg_id,
                                    item, session, session.page, affiliate_tag, user_id=uid),
                context, session, chat_id=query.message.chat_id,
            )
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
    REQUESTS_TOTAL.inc(labels={"type": "text"})
    user_id = update.effective_user.id
    text    = (update.message.text or "").strip()

    if not text:
        return

    cid = new_correlation_id()
    logger.info("Text search from user %d: '%s' [cid=%s]", user_id, text[:80], cid)

    limited, max_req, window = await _is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(
            style.error_rate_limited(max_req, window),
            parse_mode="MarkdownV2",
        )
        return

    _sessions[user_id] = UserSession()
    session = _sessions[user_id]

    msg = await update.message.reply_text(f"🔍 Searching Amazon for '{text[:60]}'…")

    # ── Detect language & translate ───────────────────────────────────────────
    lang = detect_language(text)
    try:
        english, refined_query = await translate_and_refine(text)
    except Exception as exc:
        logger.warning("translate_and_refine failed: %s", exc)
        english, refined_query = text, text
        try:
            await msg.edit_text("⚠️ Translation unavailable, searching with original text…")
        except Exception:
            pass

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
    asyncio.create_task(_periodic_cleanup())
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


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to non-photo/text messages (video, voice, sticker, etc.)."""
    await update.message.reply_text(style.not_a_photo(), parse_mode="MarkdownV2")


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
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.VOICE | filters.Sticker.ALL | filters.VIDEO_NOTE | filters.AUDIO | filters.ANIMATION,
        handle_unsupported,
    ))
    return app
