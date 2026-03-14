"""
bot_core.py -- Platform-agnostic bot logic.

Extracts ALL business logic from bot.py into a BotCore class that uses
PlatformAdapter instead of direct Telegram calls, and Formatter instead
of the old style.py module.

Session state is kept in-memory per user_key (platform:user_id).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image as _PILImage
import io as _io

import config
import database as db
import url_shortener
from adapters.base import Button, CarouselItem, MessageRef, PlatformAdapter
from correlation import get_correlation_id, new_correlation_id
from formatter import Formatter
from i18n import available_languages, t
from image_analyzer import ProductInfo
from providers.base import ProviderResult
from providers.manager import analyse_image, get_providers
from amazon_search import AmazonItem, search_amazon, backend_name
from translator import detect_language, translate_and_refine
from metrics import REQUESTS_TOTAL
from dataforseo_labs import DataForSEOLabs
import log_group

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
CB_LANG_PREFIX     = "lang:"          # + language code
CB_PICK_PRODUCT    = "pick:"           # + product index

# Placeholder image when a product has no photo URL
_PLACEHOLDER_IMG = "https://placehold.co/600x400/FF9900/FFF.png?text=Amazon"

# Maximum photo file size we'll process (bytes)
_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB

_MAX_IMAGE_DIM = 1024
_JPEG_QUALITY = 85
_SESSION_TTL = 600          # 10 minutes
_CLEANUP_INTERVAL = 300     # 5 minutes
_ANALYSIS_CACHE_TTL = 60    # seconds


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


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class UserSession:
    all_provider_results: list[ProviderResult] = field(default_factory=list)
    chosen_result: Optional[ProviderResult]    = None
    product_info: Optional[ProductInfo]        = None
    chosen_provider_idx: int = 0               # index into all_provider_results
    all_detected_products: list = field(default_factory=list)  # list[ProductInfo] when multi-product

    all_items: list[AmazonItem]      = field(default_factory=list)
    filtered_items: list[AmazonItem] = field(default_factory=list)
    israel_only: bool = False

    # page = current ITEM index (0-based) in filtered_items
    page: int = 0

    # Lazy loading: track which Amazon results page we last fetched
    amazon_page: int = 1      # next Amazon page to fetch (1 = first batch already done)
    more_available: bool = True

    # Photo carousel state — MessageRef of the current product photo card
    results_msg_ref: Optional[MessageRef] = None

    # Store raw image bytes so "Try differently" can re-analyse without re-upload
    image_bytes: Optional[bytes] = None

    # Cached admin flag — resolved once per session
    is_admin: Optional[bool] = None
    _created_at: float = field(default_factory=time.monotonic)

    # Per-ASIN Israel shipping verification results (populated by background checks).
    _israel_verified: dict = field(default_factory=dict)  # asin -> bool

    # Background enrichment data cached on the session
    _last_price_history: Any = None
    _last_israel_result: Any = None

    @property
    def total_items(self) -> int:
        return max(1, len(self.filtered_items))

    def current_item(self) -> Optional[AmazonItem]:
        if not self.filtered_items:
            return None
        idx = max(0, min(self.page, len(self.filtered_items) - 1))
        return self.filtered_items[idx]

    def current_page_items(self) -> list[AmazonItem]:
        item = self.current_item()
        return [item] if item else []

    def _israel_eligible(self, item: AmazonItem) -> bool:
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
            self.filtered_items = eligible if eligible else list(self.all_items)
        else:
            self.filtered_items = list(self.all_items)

    def append_items(self, new_items: list[AmazonItem]) -> None:
        self.all_items.extend(new_items)
        if self.israel_only:
            eligible = [i for i in self.all_items if self._israel_eligible(i)]
            self.filtered_items = eligible if eligible else list(self.all_items)
        else:
            self.filtered_items = list(self.all_items)

    def record_israel_result(self, asin: str, ships: bool) -> None:
        self._israel_verified[asin] = ships
        self.apply_filter(self.israel_only)


# ── BotCore ────────────────────────────────────────────────────────────────────

class BotCore:
    """Platform-agnostic bot logic.

    All I/O is delegated to the PlatformAdapter.  All text formatting is
    delegated to the Formatter (which uses i18n translations).
    """

    def __init__(self, adapter: PlatformAdapter) -> None:
        self.adapter = adapter
        self._sessions: dict[str, UserSession] = {}
        self._rate_buckets: dict[str, deque] = defaultdict(deque)
        self._rate_limit_cache: dict[int, tuple[int, int]] = {}
        self._analysis_cache: dict[str, tuple[float, ProviderResult, list[ProviderResult]]] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._dfs_labs_client: DataForSEOLabs | None = None

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_session(self, user_key: str) -> UserSession:
        if user_key not in self._sessions:
            self._sessions[user_key] = UserSession()
        return self._sessions[user_key]

    def _new_session(self, user_key: str) -> UserSession:
        self._sessions[user_key] = UserSession()
        return self._sessions[user_key]

    async def _get_lang(self, user_key: str) -> str:
        """Get user language, defaulting to 'en'."""
        lang = await db.get_user_lang(user_key)
        return lang or "en"

    def _fmt(self, lang: str) -> Formatter:
        return Formatter(self.adapter.platform_name, lang)

    async def _is_admin(self, user_id: int) -> bool:
        return user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id)

    async def _resolve_admin(self, session: UserSession, user_id: int) -> bool:
        if session.is_admin is None:
            session.is_admin = await self._is_admin(user_id)
        return session.is_admin

    async def _get_dfs_labs(self) -> DataForSEOLabs | None:
        if self._dfs_labs_client is not None:
            return self._dfs_labs_client
        from settings_store import key_store
        login = await key_store.get("dataforseo_login")
        password = await key_store.get("dataforseo_password")
        if login and password:
            self._dfs_labs_client = DataForSEOLabs(login, password)
            return self._dfs_labs_client
        return None

    # ── Rate limiter ───────────────────────────────────────────────────────

    async def _get_user_limits(self, user_id: int) -> tuple[int, int]:
        if user_id in self._rate_limit_cache:
            return self._rate_limit_cache[user_id]
        custom = await db.get_user_rate_limit(user_id)
        if custom:
            limits = (custom.max_requests, custom.window_seconds)
            self._rate_limit_cache[user_id] = limits
            return limits
        return (config.DEFAULT_RATE_LIMIT, config.DEFAULT_RATE_WINDOW)

    def invalidate_rate_limit_cache(self, user_id: int | None = None) -> None:
        if user_id is not None:
            self._rate_limit_cache.pop(user_id, None)
        else:
            self._rate_limit_cache.clear()

    async def _is_rate_limited(self, user_id: int) -> tuple[bool, int, int]:
        max_req, window = await self._get_user_limits(user_id)
        now = time.monotonic()
        bucket = self._rate_buckets[str(user_id)]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= max_req:
            return True, max_req, window
        bucket.append(now)
        return False, max_req, window

    # ── Periodic cleanup ───────────────────────────────────────────────────

    async def periodic_cleanup(self) -> None:
        """Background task: evict stale sessions, cache entries, rate buckets."""
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            now = time.monotonic()
            stale_cache = [k for k, (ts, *_) in self._analysis_cache.items()
                          if now - ts > _ANALYSIS_CACHE_TTL]
            for k in stale_cache:
                del self._analysis_cache[k]
            stale_sessions = [uk for uk, s in self._sessions.items()
                              if now - s._created_at > _SESSION_TTL]
            for uk in stale_sessions:
                del self._sessions[uk]
            empty = [uk for uk, dq in self._rate_buckets.items() if not dq]
            for uk in empty:
                del self._rate_buckets[uk]
            if stale_cache or stale_sessions or empty:
                logger.debug("Cleanup: %d cache, %d sessions, %d buckets evicted",
                             len(stale_cache), len(stale_sessions), len(empty))

    # ── Spawn background tasks ─────────────────────────────────────────────

    def _spawn_task(self, coro) -> None:
        """Fire-and-forget a coroutine, preventing GC."""
        try:
            task = asyncio.create_task(coro)
            self._background_tasks.add(task)
            def _on_done(t):
                self._background_tasks.discard(t)
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.debug("Background task failed: %s", exc)
            task.add_done_callback(_on_done)
        except Exception:
            pass

    # ── Language picker ────────────────────────────────────────────────────

    async def _ask_language(self, chat_id: str, user_key: str) -> None:
        """Send a language picker with buttons for each available language."""
        langs = available_languages()
        buttons: list[list[Button]] = []
        for code, name in langs:
            buttons.append([Button(label=name, callback_data=f"{CB_LANG_PREFIX}{code}")])
        fmt = self._fmt("en")
        await self.adapter.send_text(chat_id, fmt.language_picker(), buttons=buttons)

    # ── Navigation buttons ─────────────────────────────────────────────────

    async def _build_nav_buttons(
        self, session: UserSession, lang: str, user_id: int = 0,
    ) -> list[list[Button]]:
        """Build the navigation button rows for the product carousel."""
        item = session.current_item()
        total = len(session.filtered_items)
        idx = session.page
        rows: list[list[Button]] = []

        # ── Shop button ───────────────────────────────────────────────────
        if item:
            affiliate_tag = await db.get_active_tag()
            long_url = item.affiliate_url(
                affiliate_tag,
                subtag=f"{self.adapter.platform_name}_{user_id}" if user_id else None,
            )
            url_map = await url_shortener.shorten_many([long_url])
            shop_url = url_map.get(long_url, long_url)
            rows.append([Button(label=t("btn_shop", lang=lang), url=shop_url)])

        # ── Navigation row ────────────────────────────────────────────────
        nav: list[Button] = []
        if idx > 0:
            nav.append(Button(label="\u25c0", callback_data=CB_PREV))

        page_label = f"{idx + 1} / {total}"
        if idx == total - 1 and session.more_available:
            page_label += " +"
        nav.append(Button(label=page_label, callback_data="nav:noop"))

        if idx < total - 1 or session.more_available:
            nav.append(Button(label="\u25b6", callback_data=CB_NEXT))
        if nav:
            rows.append(nav)

        # ── Filter toggle ─────────────────────────────────────────────────
        if session.israel_only:
            toggle_label = t("filter_all", lang=lang)
        else:
            toggle_label = t("filter_israel", lang=lang)
        rows.append([Button(label=toggle_label, callback_data=CB_CHANGE_FILTER)])

        # ── Try differently ───────────────────────────────────────────────
        if len(session.all_provider_results) > 1:
            current = session.chosen_provider_idx + 1
            total_providers = len(session.all_provider_results)
            if session.is_admin:
                next_idx = (session.chosen_provider_idx + 1) % len(session.all_provider_results)
                next_name = session.all_provider_results[next_idx].provider_name
                label = f"\U0001f504 Try {next_name} ({current}/{total_providers})"
            else:
                label = t("btn_try_different", lang=lang)
            rows.append([Button(label=label, callback_data=CB_TRY_DIFFERENTLY)])

        # ── Similar products (DFS Labs) ───────────────────────────────────
        if item and item.asin:
            rows.append([Button(
                label=t("btn_similar", lang=lang),
                callback_data=f"{CB_SIMILAR}{item.asin}",
            )])

        return rows

    # ── Render product ─────────────────────────────────────────────────────

    async def _render_product(
        self,
        user_key: str,
        chat_id: str,
        session: UserSession,
        lang: str,
        user_id: int = 0,
        old_msg_ref: Optional[MessageRef] = None,
    ) -> None:
        """Render the current product as a photo card.

        Handles 3 modes based on adapter capabilities:
        1. If adapter.supports_photo_edit and we have an existing ref -> edit_photo
        2. If adapter.supports_carousels -> send_carousel (first render only)
        3. Else -> send new photo message each time
        """
        fmt = self._fmt(lang)
        affiliate_tag = await db.get_active_tag()
        is_admin = await self._resolve_admin(session, user_id)

        item = session.current_item()
        total = len(session.filtered_items)

        if not item:
            await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
            return

        # Build short URL
        short_url = None
        long_url = item.affiliate_url(
            affiliate_tag,
            subtag=f"{self.adapter.platform_name}_{user_id}" if user_id else None,
        )
        url_map = await url_shortener.shorten_many([long_url])
        short_url = url_map.get(long_url, long_url)

        caption = fmt.product_caption(
            item,
            index=session.page + 1,
            total=total,
            short_url=short_url,
            is_admin=is_admin,
        )
        buttons = await self._build_nav_buttons(session, lang, user_id=user_id)
        image_url = item.image_url or _PLACEHOLDER_IMG

        # Mode 1: Edit existing photo message in-place
        if session.results_msg_ref and self.adapter.supports_photo_edit:
            try:
                await self.adapter.edit_photo(
                    session.results_msg_ref,
                    image=image_url,
                    caption=caption,
                    buttons=buttons,
                )
                self._spawn_background_checks(session, chat_id, item, lang, user_id)
                return
            except Exception as exc:
                logger.warning("edit_photo failed (%s), falling back to new send", exc)

        # Mode 2 / Mode 3: Send a new photo message
        try:
            # Delete old message if we have one (best-effort)
            if session.results_msg_ref:
                try:
                    await self.adapter.delete_message(session.results_msg_ref)
                except Exception:
                    pass
            elif old_msg_ref:
                try:
                    await self.adapter.delete_message(old_msg_ref)
                except Exception:
                    pass

            ref = await self.adapter.send_photo(
                chat_id=chat_id,
                image=image_url,
                caption=caption,
                buttons=buttons,
            )
            session.results_msg_ref = ref
            self._spawn_background_checks(session, chat_id, item, lang, user_id)
        except Exception as exc:
            logger.error("send_photo failed: %s, falling back to text", exc)
            # Text fallback
            try:
                ref = await self.adapter.send_text(
                    chat_id=chat_id,
                    text=caption,
                    buttons=buttons,
                )
                session.results_msg_ref = ref
            except Exception as exc2:
                logger.error("Text fallback also failed: %s", exc2)

    # ── Background enrichment ──────────────────────────────────────────────

    def _spawn_background_checks(
        self,
        session: UserSession,
        chat_id: str,
        item: AmazonItem,
        lang: str,
        user_id: int = 0,
    ) -> None:
        """Kick off Israel verification and price history checks."""
        if not session.results_msg_ref:
            logger.info("Skipping background checks: no results_msg_ref")
            return
        logger.info("Starting background enrichment for ASIN %s", item.asin)
        page_snap = session.page
        self._spawn_task(
            self._verify_israel_async(
                chat_id, session, item, page_snap, lang, user_id,
            )
        )
        self._spawn_task(
            self._verify_price_async(
                chat_id, session, item, page_snap, lang, user_id,
            )
        )

    async def _verify_israel_async(
        self,
        chat_id: str,
        session: UserSession,
        item: AmazonItem,
        page_snap: int,
        lang: str,
        user_id: int = 0,
    ) -> None:
        """Background: check Israel shipping via scraper, then edit caption."""
        try:
            import israel_scraper
            configured = await israel_scraper.is_configured()
            logger.info("Israel scraper configured: %s", configured)
            if not configured:
                return

            logger.info("Starting Israel check for ASIN %s", item.asin)
            result = await asyncio.wait_for(
                israel_scraper.check_shipping(item.asin),
                timeout=14.0,
            )
            logger.info("Israel check result: verified=%s, ships=%s, note=%s", result.verified, result.ships_to_israel, result.note)
            if not result.verified:
                return

            if session.page != page_snap:
                return

            session._last_israel_result = result
            await log_group.log("🇮🇱", f"Israel check: {result.note}")

            if result.ships_to_israel is not None:
                session.record_israel_result(item.asin, result.ships_to_israel)

            await self._update_caption_after_enrichment(
                chat_id, session, item, page_snap, lang, user_id,
            )
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            logger.info("Israel async verify failed: %s", exc)

    async def _verify_price_async(
        self,
        chat_id: str,
        session: UserSession,
        item: AmazonItem,
        page_snap: int,
        lang: str,
        user_id: int = 0,
    ) -> None:
        """Background: fetch price history then edit the caption."""
        try:
            import price_history as ph_mod
            logger.info("Starting price history check for ASIN %s", item.asin)
            ph = await ph_mod.get_price_history(item.asin)
            logger.info("Price history result: %s", ph)
            if not ph:
                return

            if session.page != page_snap:
                return

            session._last_price_history = ph
            await log_group.log("📉", f"Price history: current=${ph.current}, avg90=${ph.avg_90d}, ATL=${ph.low_all_time}\n{ph.deal_label}")

            await self._update_caption_after_enrichment(
                chat_id, session, item, page_snap, lang, user_id,
            )
        except Exception as exc:
            logger.info("Price history async fetch failed: %s", exc)

    async def _update_caption_after_enrichment(
        self,
        chat_id: str,
        session: UserSession,
        item: AmazonItem,
        page_snap: int,
        lang: str,
        user_id: int = 0,
    ) -> None:
        """Re-render caption with enrichment data and edit the existing message."""
        if session.page != page_snap:
            return
        if not session.results_msg_ref:
            return

        fmt = self._fmt(lang)
        affiliate_tag = await db.get_active_tag()
        total = len(session.filtered_items)

        short_url = None
        long_url = item.affiliate_url(
            affiliate_tag,
            subtag=f"{self.adapter.platform_name}_{user_id}" if user_id else None,
        )
        url_map = await url_shortener.shorten_many([long_url])
        short_url = url_map.get(long_url, long_url)

        # Determine israel status string for formatter
        israel_status = None
        israel_result = session._last_israel_result
        if israel_result:
            if getattr(israel_result, "ships_to_israel", None) is True:
                if getattr(israel_result, "free_shipping", False):
                    israel_status = "free"
                else:
                    israel_status = "yes"
            elif getattr(israel_result, "ships_to_israel", None) is False:
                israel_status = "no"
        elif session.israel_only:
            israel_status = "checking"

        caption = fmt.product_caption(
            item,
            index=page_snap + 1,
            total=total,
            short_url=short_url,
            israel_status=israel_status,
            price_history=session._last_price_history,
            is_admin=session.is_admin,
        )
        buttons = await self._build_nav_buttons(session, lang, user_id=user_id)

        try:
            if self.adapter.supports_photo_edit:
                await self.adapter.edit_photo(
                    session.results_msg_ref,
                    image=item.image_url or _PLACEHOLDER_IMG,
                    caption=caption,
                    buttons=buttons,
                )
            else:
                await self.adapter.edit_text(
                    session.results_msg_ref,
                    text=caption,
                    buttons=buttons,
                )
        except Exception:
            pass

    # ── Search & render flow ───────────────────────────────────────────────

    async def _search_and_render(
        self,
        user_key: str,
        chat_id: str,
        israel_only: bool,
        session: UserSession,
        lang: str,
        user_id: int = 0,
        loading_msg_ref: Optional[MessageRef] = None,
    ) -> None:
        """Run Amazon search, apply filter, render first product."""
        fmt = self._fmt(lang)

        if loading_msg_ref:
            try:
                await self.adapter.edit_text(
                    loading_msg_ref,
                    text=fmt.loading_search(),
                )
            except Exception:
                pass

        try:
            all_items = await search_amazon(
                session.product_info, max_results=config.MAX_RESULTS,
            )
        except RuntimeError:
            err_text = fmt.error("err_search_failed")
            if loading_msg_ref:
                await self.adapter.edit_text(loading_msg_ref, text=err_text)
            else:
                await self.adapter.send_text(chat_id, err_text)
            return
        except Exception as exc:
            logger.error("Amazon search failed: %s", exc)
            err_text = fmt.error("err_search_failed")
            if loading_msg_ref:
                await self.adapter.edit_text(loading_msg_ref, text=err_text)
            else:
                await self.adapter.send_text(chat_id, err_text)
            return

        session.all_items = all_items
        await log_group.log("🔍", f"Search: '{session.product_info.amazon_search_query}'\nResults: {len(session.all_items)} items")
        session.amazon_page = 1
        session.more_available = len(all_items) >= config.MAX_RESULTS
        session.results_msg_ref = None
        session.apply_filter(israel_only)

        # Log the search
        active_tag = await db.get_active_tag()
        await db.log_search(
            user_id=user_id,
            product_name=session.product_info.product_name if session.product_info else "",
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
            err_text = fmt.error("err_no_results")
            if loading_msg_ref:
                await self.adapter.edit_text(loading_msg_ref, text=err_text)
            else:
                await self.adapter.send_text(chat_id, err_text)
            return

        await self._render_product(
            user_key, chat_id, session, lang, user_id=user_id,
            old_msg_ref=loading_msg_ref,
        )

        # Spawn related keyword suggestions
        if session.product_info and session.product_info.product_name:
            self._spawn_task(
                self._send_related_keywords(chat_id, session.product_info.product_name, lang)
            )

    # ── Related keywords (DFS Labs) ────────────────────────────────────────

    async def _send_related_keywords(
        self, chat_id: str, keyword: str, lang: str,
    ) -> None:
        """Fetch DFS Labs related keywords and send as inline buttons."""
        try:
            labs = await self._get_dfs_labs()
            if not labs:
                return
            related = await labs.related_keywords(keyword, limit=6)
            if not related:
                return
            buttons_flat = [
                Button(
                    label=r.label(),
                    callback_data=f"{CB_RELATED}{r.keyword[:40]}",
                )
                for r in related
            ]
            rows = [buttons_flat[i:i + 2] for i in range(0, len(buttons_flat), 2)]
            await self.adapter.send_text(
                chat_id,
                t("btn_similar", lang=lang),
                buttons=rows,
            )
        except Exception as exc:
            logger.debug("Related keywords fetch failed: %s", exc)

    # ── Similar products (DFS Labs) ────────────────────────────────────────

    async def _handle_similar(
        self,
        user_key: str,
        chat_id: str,
        session: UserSession,
        asin: str,
        lang: str,
        user_id: int = 0,
        msg_ref: Optional[MessageRef] = None,
    ) -> None:
        """Load competitor products from DFS Labs and display as new results."""
        fmt = self._fmt(lang)
        labs = await self._get_dfs_labs()
        if not labs:
            await self.adapter.send_text(chat_id, fmt.error("err_generic"))
            return

        if msg_ref:
            try:
                await self.adapter.edit_text(msg_ref, text=fmt.loading_search())
            except Exception:
                pass

        try:
            competitor_asins = await labs.get_competitors(asin, limit=20)
            if not competitor_asins:
                await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
                return

            enriched = await labs.enrich_many(competitor_asins[:12], concurrency=4)
            if not enriched:
                await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
                return

            new_items: list[AmazonItem] = []
            for comp_asin, prod in enriched.items():
                new_items.append(AmazonItem(
                    asin=comp_asin,
                    title=prod.title or comp_asin,
                    price=prod.price,
                    currency=prod.currency or "USD",
                    image_url=prod.image_url or "",
                    rating=prod.rating,
                    free_delivery=None,
                    is_prime=None,
                ))

            session.all_items = new_items
            session.amazon_page = 1
            session.more_available = False
            session.page = 0
            session.results_msg_ref = None
            session.apply_filter(session.israel_only)

            if not session.filtered_items:
                await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
                return

            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
                old_msg_ref=msg_ref,
            )
        except Exception as exc:
            logger.error("Similar products failed: %s", exc)
            await self.adapter.send_text(chat_id, fmt.error("err_search_failed"))

    # ═══════════════════════════════════════════════════════════════════════
    #  PUBLIC ENTRY POINTS — called by platform adapters
    # ═══════════════════════════════════════════════════════════════════════

    async def handle_command(
        self,
        user_id: int,
        chat_id: str,
        command: str,
        args: list[str],
        event: Any = None,
    ) -> None:
        """Handle slash commands: /start, /help, /language, /providers."""
        platform = self.adapter.platform_name
        user_key = f"{platform}:{user_id}"
        await db.ensure_user(user_key, platform)
        lang = await self._get_lang(user_key)
        fmt = self._fmt(lang)

        if command == "start":
            # Check if user has language set; if not, ask first
            stored_lang = await db.get_user_lang(user_key)
            if stored_lang is None:
                await self._ask_language(chat_id, user_key)
                return
            await self.adapter.send_text(chat_id, fmt.welcome())

        elif command == "help":
            await self.adapter.send_text(chat_id, fmt.help_text())

        elif command == "language":
            await self._ask_language(chat_id, user_key)

        elif command == "providers":
            try:
                providers = await get_providers()
            except Exception:
                await self.adapter.send_text(chat_id, fmt.error("err_generic"))
                return
            try:
                sb = await backend_name()
            except Exception:
                sb = "not configured"
            lines = [f"Providers: {len(providers)}"]
            for p in providers:
                lines.append(f"  - {p.name}")
            lines.append(f"Search backend: {sb}")
            await self.adapter.send_text(chat_id, "\n".join(lines))


        elif command == "setloggroup":
            if not await self._is_admin(user_id):
                return
            import log_group
            log_group.start_listening(int(user_id))
            await self.adapter.send_text(
                chat_id,
                "OK, now add me to a group and send any message there\. "
                "I will capture that group as the log group\."
            )
    async def handle_photo(
        self,
        event: Any,
        cache_key: str | None = None,
        context_hint: str | None = None,
    ) -> None:
        """Handle an incoming photo message.

        Args:
            event: The raw platform event.
            cache_key: Optional dedup cache key (e.g. file_unique_id).
            context_hint: Optional user-provided text hint (caption).
        """
        REQUESTS_TOTAL.inc(labels={"type": "photo"})
        platform = self.adapter.platform_name
        uid = self.adapter.get_user_id(event)
        user_id = int(uid) if uid.isdigit() else 0
        user_key = f"{platform}:{uid}"
        # get_chat_id may be provided by adapters; fall back to user id
        if hasattr(self.adapter, "get_chat_id"):
            chat_id = str(self.adapter.get_chat_id(event))
        else:
            chat_id = str(uid)
        cid = new_correlation_id()
        logger.info("Photo received from %s [cid=%s]", user_key, cid)
        await log_group.log("📸", f"Photo from user {user_id}\nCaption: {context_hint or 'none'}")

        await db.ensure_user(user_key, platform)

        # Check language
        lang = await db.get_user_lang(user_key)
        if lang is None:
            await self._ask_language(chat_id, user_key)
            return
        fmt = self._fmt(lang)

        # Rate limit
        limited, max_req, window = await self._is_rate_limited(user_id)
        if limited:
            await self.adapter.send_text(chat_id, fmt.error("err_rate_limit"))
            return

        session = self._new_session(user_key)

        # Check providers
        try:
            providers = await get_providers()
            n_providers = len(providers)
        except Exception:
            n_providers = 0

        if n_providers == 0:
            await self.adapter.send_text(chat_id, fmt.error("err_generic"))
            return

        # Download and compress photo
        try:
            raw_bytes = await self.adapter.download_photo(event)
        except Exception as exc:
            logger.error("Photo download failed: %s", exc)
            await self.adapter.send_text(chat_id, fmt.error("err_analysis_failed"))
            return

        if len(raw_bytes) > _MAX_PHOTO_BYTES:
            await self.adapter.send_text(chat_id, fmt.error("err_generic"))
            return

        # Handle optional caption / context hint
        if context_hint:
            hint_lang = detect_language(context_hint)
            if hint_lang != "en":
                try:
                    en_caption, _ = await translate_and_refine(context_hint)
                except Exception:
                    en_caption = context_hint
                context_hint = en_caption

        # Send loading message
        loading_ref = await self.adapter.send_text(chat_id, fmt.loading_vision())

        image_bytes = _compress_image(raw_bytes)
        session.image_bytes = image_bytes

        # Check dedup cache
        cached = None
        if cache_key:
            cached = self._analysis_cache.get(cache_key)
            if cached and (time.monotonic() - cached[0]) < _ANALYSIS_CACHE_TTL:
                logger.info("Dedup cache hit for %s (%s)", cache_key, user_key)
                winner, all_results = cached[1], cached[2]
            else:
                cached = None

        if not cached:
            try:
                winner, all_results = await analyse_image(
                    image_bytes, mode=config.VISION_MODE,
                    context_hint=context_hint, user_id=user_id,
                )
                if cache_key:
                    self._analysis_cache[cache_key] = (time.monotonic(), winner, all_results)
            except RuntimeError:
                await self.adapter.edit_text(loading_ref, text=fmt.error("err_generic"))
                return
            except Exception as exc:
                logger.error("Vision analysis failed: %s", exc)
                await self.adapter.edit_text(loading_ref, text=fmt.error("err_analysis_failed"))
                return

        session.all_provider_results = all_results
        provider_info = ", ".join(f"{r.provider_name} ({r.confidence}, {r.cost_str})" for r in all_results)
        product_names = ", ".join(r.product_name for r in all_results[:3])
        await log_group.log("🤖", f"Analysis done: {len(all_results)} providers\n{provider_info}\nTop product: {product_names}")
        session.chosen_provider_idx = 0

        is_admin = await self._is_admin(user_id)
        session.is_admin = is_admin

        # Compare mode
        if config.VISION_MODE == "compare" and len(all_results) > 1:
            compare_buttons: list[list[Button]] = []
            for i, r in enumerate(all_results):
                compare_buttons.append([Button(
                    label=f"{r.provider_name} ({r.confidence})",
                    callback_data=f"{CB_USE_RESULT}{i}",
                )])
            compare_buttons.append([Button(
                label="⭐ Best",
                callback_data=f"{CB_USE_RESULT}0",
            )])
            card_text = fmt.identification_card(winner, is_admin=is_admin)
            await self.adapter.edit_text(loading_ref, text=card_text, buttons=compare_buttons)
            return

        # Check if multiple products detected
        detected_products = winner.to_product_info_list()
        logger.info("Detected %d product(s) from %s", len(detected_products), winner.provider_name)

        if len(detected_products) > 1 and not context_hint:
            # Multi-product: annotate image and show picker
            session.all_detected_products = detected_products
            names = ", ".join(p.product_name for p in detected_products)
            await log_group.log("🔢", f"Multi-product: {len(detected_products)} items detected\n{names}")
            from image_annotator import annotate_products
            annotated_bytes = annotate_products(image_bytes, detected_products)

            # Delete loading message
            try:
                await self.adapter.delete_message(loading_ref)
            except Exception:
                pass

            # Send annotated photo first (works on all platforms including WhatsApp)
            await self.adapter.send_photo(
                chat_id,
                image=annotated_bytes,
                caption=fmt.product_picker(detected_products),
                buttons=[],  # No buttons on the photo for WhatsApp
            )

            # Platform-specific product selection UI
            if self.adapter.platform_name == "whatsapp" and hasattr(self.adapter, "send_list_message"):
                # WhatsApp: use list message for product selection (up to 10 items)
                rows = []
                for i, p in enumerate(detected_products[:10]):
                    rows.append({
                        "id": f"{CB_PICK_PRODUCT}{i}",
                        "title": p.product_name[:24],
                        "description": (p.category or "")[:72],
                    })
                sections = [{"title": "Detected Products", "rows": rows}]
                await self.adapter.send_list_message(
                    chat_id,
                    body="I found multiple products in your photo. Tap below to select one:",
                    button_label="View Products",
                    sections=sections,
                )
            else:
                # Other platforms: inline buttons on the photo caption (existing behavior)
                picker_buttons: list[list[Button]] = []
                for i, p in enumerate(detected_products):
                    short_name = p.product_name[:30]
                    picker_buttons.append([Button(
                        label=f"{i + 1}: {short_name}",
                        callback_data=f"{CB_PICK_PRODUCT}{i}",
                    )])
                await self.adapter.send_text(
                    chat_id,
                    text=fmt.product_picker(detected_products),
                    buttons=picker_buttons,
                )
            return

        # Single product (or user provided hint) -- normal flow
        session.chosen_result = winner
        session.product_info = winner.to_product_info()

        card_text = fmt.identification_card(winner, is_admin=is_admin)
        filter_buttons = self._filter_buttons(lang)
        await self.adapter.edit_text(loading_ref, text=card_text, buttons=filter_buttons)

    def _filter_buttons(self, lang: str) -> list[list[Button]]:
        """Build the Israel filter / show-all keyboard."""
        return [
            [Button(
                label=t("filter_israel", lang=lang),
                callback_data=CB_FILTER_YES,
            )],
            [Button(
                label=t("filter_all", lang=lang),
                callback_data=CB_FILTER_NO,
            )],
        ]

    async def handle_callback(
        self,
        user_id: int,
        chat_id: str,
        data: str,
        event: Any = None,
        msg_ref: Optional[MessageRef] = None,
    ) -> None:
        """Handle an inline button callback.

        Args:
            user_id: Numeric user ID.
            chat_id: Chat/conversation ID.
            data: The callback_data string from the button.
            event: Raw platform event (for platform-specific operations).
            msg_ref: Reference to the message containing the button.
        """
        REQUESTS_TOTAL.inc(labels={"type": "callback"})
        platform = self.adapter.platform_name
        user_key = f"{platform}:{user_id}"
        cid = new_correlation_id()
        logger.info("Callback '%s' from %s [cid=%s]", data, user_key, cid)
        session = self._get_session(user_key)
        lang = await self._get_lang(user_key)
        fmt = self._fmt(lang)

        # ── Language selection ────────────────────────────────────────────
        if data.startswith(CB_LANG_PREFIX):
            chosen_lang = data[len(CB_LANG_PREFIX):]
            await db.set_user_lang(user_key, chosen_lang, platform=platform)
            lang = chosen_lang
            fmt = self._fmt(lang)
            await self.adapter.send_text(chat_id, fmt.welcome())
            return

        # ── Product picker (multi-product) ───────────────────────────────
        if data.startswith(CB_PICK_PRODUCT):
            try:
                idx = int(data[len(CB_PICK_PRODUCT):])
                chosen = session.all_detected_products[idx]
                await log_group.log("👆", f"User {user_id} picked: {chosen.product_name}")
            except (ValueError, IndexError):
                await self.adapter.send_text(chat_id, fmt.error("err_generic"))
                return

            session.product_info = chosen
            # Build a ProviderResult for the identification card
            if session.all_provider_results:
                winner = session.all_provider_results[session.chosen_provider_idx] if session.chosen_provider_idx < len(session.all_provider_results) else session.all_provider_results[0]
                winner_copy = ProviderResult(
                    provider_name=winner.provider_name,
                    model_id=winner.model_id,
                    product_name=chosen.product_name,
                    brand=chosen.brand,
                    category=chosen.category,
                    key_features=chosen.key_features,
                    amazon_search_query=chosen.amazon_search_query,
                    alternative_query=chosen.alternative_query,
                    confidence=chosen.confidence,
                    notes=chosen.notes,
                    latency_ms=winner.latency_ms,
                    input_tokens=winner.input_tokens,
                    output_tokens=winner.output_tokens,
                    cost_usd=winner.cost_usd,
                )
                session.chosen_result = winner_copy
            else:
                session.chosen_result = None

            is_admin = await self._is_admin(user_id)
            if session.chosen_result:
                card_text = fmt.identification_card(session.chosen_result, is_admin=is_admin)
            else:
                card_text = chosen.product_name

            filter_buttons = self._filter_buttons(lang)
            await self.adapter.send_text(chat_id, card_text, buttons=filter_buttons)
            return

        # ── Noop page indicator tap ───────────────────────────────────────
        if data == "nav:noop":
            return

        # ── DFS Labs: Similar products ────────────────────────────────────
        if data.startswith(CB_SIMILAR):
            asin = data[len(CB_SIMILAR):]
            await self._handle_similar(
                user_key, chat_id, session, asin, lang,
                user_id=user_id, msg_ref=msg_ref,
            )
            return

        # ── DFS Labs: Related keyword search ──────────────────────────────
        if data.startswith(CB_RELATED):
            keyword = data[len(CB_RELATED):]
            if not session.product_info:
                session.product_info = ProductInfo(product_name=keyword)
            else:
                session.product_info.product_name = keyword

            session.all_items = []
            session.amazon_page = 1
            session.more_available = False
            session.page = 0
            session.results_msg_ref = None

            if msg_ref:
                try:
                    await self.adapter.edit_text(msg_ref, text=fmt.loading_search())
                except Exception:
                    pass

            try:
                items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
            except Exception as exc:
                logger.error("Related keyword search failed: %s", exc)
                await self.adapter.send_text(chat_id, fmt.error("err_search_failed"))
                return

            session.all_items = items
            session.more_available = len(items) >= config.MAX_RESULTS
            session.apply_filter(session.israel_only)

            if not session.filtered_items:
                await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
                return

            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
                old_msg_ref=msg_ref,
            )
            return

        # ── Provider chosen in compare mode ───────────────────────────────
        if data.startswith(CB_USE_RESULT):
            try:
                idx = int(data[len(CB_USE_RESULT):])
                chosen = session.all_provider_results[idx]
            except (ValueError, IndexError):
                await self.adapter.send_text(chat_id, fmt.error("err_generic"))
                return
            session.chosen_result = chosen
            session.chosen_provider_idx = idx
            session.product_info = chosen.to_product_info()
            is_admin = await self._resolve_admin(session, user_id)
            card_text = fmt.identification_card(chosen, is_admin=is_admin)
            filter_buttons = self._filter_buttons(lang)
            if msg_ref:
                await self.adapter.edit_text(msg_ref, text=card_text, buttons=filter_buttons)
            else:
                await self.adapter.send_text(chat_id, card_text, buttons=filter_buttons)
            return

        # ── Filter chosen -> search Amazon ────────────────────────────────
        if data in (CB_FILTER_YES, CB_FILTER_NO):
            if not session.product_info:
                await self.adapter.send_text(chat_id, fmt.error("err_generic"))
                return
            israel_only = data == CB_FILTER_YES
            await self._search_and_render(
                user_key, chat_id, israel_only, session, lang,
                user_id=user_id, loading_msg_ref=msg_ref,
            )
            return

        # ── Toggle filter ─────────────────────────────────────────────────
        if data == CB_CHANGE_FILTER:
            session.apply_filter(not session.israel_only)
            if not session.filtered_items:
                toggle_label = (
                    t("filter_all", lang=lang)
                    if session.israel_only
                    else t("filter_israel", lang=lang)
                )
                buttons = [[Button(label=toggle_label, callback_data=CB_CHANGE_FILTER)]]
                await self.adapter.send_text(
                    chat_id, fmt.error("err_no_results"), buttons=buttons,
                )
                return
            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
                old_msg_ref=msg_ref,
            )
            return

        # ── Try differently ───────────────────────────────────────────────
        if data == CB_TRY_DIFFERENTLY:
            if len(session.all_provider_results) < 2:
                return
            next_idx = (session.chosen_provider_idx + 1) % len(session.all_provider_results)
            session.chosen_provider_idx = next_idx
            session.chosen_result = session.all_provider_results[next_idx]
            session.product_info = session.chosen_result.to_product_info()

            # Show loading
            if msg_ref:
                try:
                    await self.adapter.edit_text(msg_ref, text=fmt.loading_search())
                except Exception:
                    pass

            try:
                new_items = await search_amazon(session.product_info, max_results=config.MAX_RESULTS)
            except Exception as exc:
                logger.error("Try-differently search failed: %s", exc)
                await self.adapter.send_text(chat_id, fmt.error("err_search_failed"))
                return

            session.all_items = new_items
            session.amazon_page = 1
            session.more_available = len(new_items) >= config.MAX_RESULTS
            session.apply_filter(session.israel_only)

            if not session.filtered_items:
                await self.adapter.send_text(chat_id, fmt.error("err_no_results"))
                return

            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
                old_msg_ref=msg_ref,
            )
            return

        # ── Pagination ────────────────────────────────────────────────────
        if data == CB_PREV:
            session.page = max(0, session.page - 1)
            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
            )
            return

        if data == CB_NEXT:
            total = len(session.filtered_items)
            next_page = session.page + 1

            if next_page < total:
                session.page = next_page
            elif session.more_available:
                # Lazy-load next batch
                try:
                    if msg_ref:
                        try:
                            await self.adapter.edit_text(
                                msg_ref,
                                text=fmt.loading_search(),
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

            await self._render_product(
                user_key, chat_id, session, lang, user_id=user_id,
            )
            return

    async def handle_text_search(
        self,
        user_id: int,
        chat_id: str,
        text: str,
        event: Any = None,
    ) -> None:
        """Handle text messages as product search queries."""
        REQUESTS_TOTAL.inc(labels={"type": "text"})
        platform = self.adapter.platform_name
        user_key = f"{platform}:{user_id}"
        cid = new_correlation_id()
        logger.info("Text search from %s: '%s' [cid=%s]", user_key, text[:80], cid)

        await db.ensure_user(user_key, platform)

        # Check language
        lang = await db.get_user_lang(user_key)
        if lang is None:
            await self._ask_language(chat_id, user_key)
            return
        fmt = self._fmt(lang)

        # Rate limit
        limited, max_req, window = await self._is_rate_limited(user_id)
        if limited:
            await self.adapter.send_text(chat_id, fmt.error("err_rate_limit"))
            return

        session = self._new_session(user_key)

        loading_ref = await self.adapter.send_text(
            chat_id, fmt.text_search_loading(text[:60]),
        )

        # Detect language & translate
        text_lang = detect_language(text)
        try:
            english, refined_query = await translate_and_refine(text)
        except Exception as exc:
            logger.warning("translate_and_refine failed: %s", exc)
            english, refined_query = text, text
            try:
                await self.adapter.edit_text(
                    loading_ref, text=fmt.error("err_generic"),
                )
            except Exception:
                pass

        # Build a mock ProductInfo from the refined query
        session.product_info = ProductInfo(
            product_name=english[:100],
            brand=None,
            category="All",
            key_features=[],
            amazon_search_query=refined_query,
            alternative_query=refined_query,
            confidence="high",
            notes="",
        )

        # Show filter keyboard
        filter_buttons = self._filter_buttons(lang)
        await self.adapter.send_text(
            chat_id,
            t("filter_ask", lang=lang),
            buttons=filter_buttons,
        )
