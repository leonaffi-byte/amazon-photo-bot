"""
web_app/router.py — FastAPI router for the public web application.

Routes:
  GET  /           — Homepage with photo upload zone
  POST /upload     — Accept uploaded photo, validate, return SSE progress fragment
  GET  /stream/{session_id}  — SSE stream for 4-stage analysis progress
  GET  /search/{short_id}    — Result page (stub; full rendering in Plan 02)
  GET  /search/{short_id}/image — Serve annotated photo BLOB
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import secrets
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from web_app.deps import limiter

logger = logging.getLogger(__name__)

# ── Router and templates ───────────────────────────────────────────────────────

router = APIRouter(tags=["web"])
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── In-memory pending sessions ─────────────────────────────────────────────────
# Maps session_id -> (bytes, created_at_epoch)
_pending: dict[str, tuple[bytes, float]] = {}
_PENDING_TTL = 300  # 5 minutes


def _cleanup_pending() -> None:
    """Remove stale entries from _pending (called lazily before each new upload)."""
    now = time.time()
    stale = [sid for sid, (_, ts) in _pending.items() if now - ts > _PENDING_TTL]
    for sid in stale:
        del _pending[sid]


# ── i18n strings ───────────────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    "he": {
        "site_title": "Amazon Photo Bot",
        "upload_title": "מצא מוצרים מתמונה",
        "upload_subtitle": "העלה תמונה של מוצר ונמצא אותו באמזון",
        "upload_button": "בחר תמונה",
        "or_drag": "או גרור תמונה לכאן",
        "drop_here": "שחרר כאן",
        "uploading": "מעלה...",
        "how_it_works": "איך זה עובד?",
        "step1": "העלה תמונה",
        "step1_desc": "צלם או בחר תמונה של מוצר",
        "step2": "AI מזהה",
        "step2_desc": "הבינה המלאכותית מזהה את המוצרים",
        "step3": "תוצאות מאמזון",
        "step3_desc": "קבל קישורים ומחירים מאמזון",
        "supports": "תומך ב-JPG, PNG, WEBP — מקסימום 10 MB",
        "analyzing": "מנתח את התמונה...",
        "found_products": "נמצאו {n} מוצרים...",
        "found_one_product": "נמצא מוצר אחד...",
        "no_products": "לא זוהו מוצרים",
        "searching": "מחפש באמזון...",
        "done": "סיום!",
        "view_on_amazon": "צפה באמזון",
        "ships_to_israel": "נשלח לישראל",
        "likely_ships": "כנראה נשלח",
        "may_not_ship": "ייתכן שלא נשלח",
        "no_results": "לא נמצאו מוצרים תואמים באמזון",
        "expired": "תוצאות פגו",
        "not_found": "תוצאות לא נמצאו",
        "reviews": "ביקורות",
        "price_history_unavailable": "היסטוריית מחירים לא זמינה",
        "affiliate_disclosure": "כשותף Amazon, אנו מרוויחים מרכישות מתאימות",
        "product": "מוצר",
        "result_expired_msg": "תוצאות החיפוש פגו. אנא חפש שוב.",
        "photo_too_large": "התמונה גדולה מדי (מקסימום 10MB)",
        "not_an_image": "הקובץ חייב להיות תמונה",
        "session_expired": "הסשן פג. אנא העלה שוב.",
        "photo_detected": "תמונה עם מוצרים שזוהו",
        "search_again": "חפש שוב",
        "price_unavailable": "מחיר לא זמין",
        "in_stock": "במלאי",
    },
    "en": {
        "site_title": "Amazon Photo Bot",
        "upload_title": "Find Products from a Photo",
        "upload_subtitle": "Upload a photo of any product and we'll find it on Amazon",
        "upload_button": "Choose Photo",
        "or_drag": "or drag and drop here",
        "drop_here": "Drop here",
        "uploading": "Uploading...",
        "how_it_works": "How It Works",
        "step1": "Upload Photo",
        "step1_desc": "Take or select a photo of a product",
        "step2": "AI Identifies",
        "step2_desc": "Our AI identifies the products in your photo",
        "step3": "Amazon Results",
        "step3_desc": "Get links and prices from Amazon",
        "supports": "Supports JPG, PNG, WEBP — max 10 MB",
        "analyzing": "Analyzing photo...",
        "found_products": "Found {n} products...",
        "found_one_product": "Found 1 product...",
        "no_products": "No products detected",
        "searching": "Searching Amazon...",
        "done": "Done!",
        "view_on_amazon": "View on Amazon",
        "ships_to_israel": "Ships to Israel",
        "likely_ships": "Likely ships",
        "may_not_ship": "May not ship",
        "no_results": "No matching products found on Amazon",
        "expired": "Results Expired",
        "not_found": "Results Not Found",
        "reviews": "reviews",
        "price_history_unavailable": "Price history unavailable",
        "affiliate_disclosure": "As an Amazon Associate, we earn from qualifying purchases",
        "product": "Product",
        "result_expired_msg": "These search results have expired. Please search again.",
        "photo_too_large": "Photo too large (max 10MB)",
        "not_an_image": "File must be an image",
        "session_expired": "Session expired. Please upload again.",
        "photo_detected": "Photo with detected products",
        "search_again": "Search Again",
        "price_unavailable": "Price unavailable",
        "in_stock": "In Stock",
    },
}


def _get_lang(request: Request) -> str:
    """Determine language: ?lang= param first, then cookie, then default 'he'."""
    lang = request.query_params.get("lang", "").strip()
    if lang not in ("he", "en"):
        lang = request.cookies.get("lang", "he")
    if lang not in ("he", "en"):
        lang = "he"
    return lang


def _t(lang: str) -> dict[str, str]:
    """Return translation dict for the given language."""
    return _STRINGS.get(lang, _STRINGS["en"])


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _sse_event(event: str, data: str) -> str:
    """Format a single SSE event string."""
    # Escape newlines in data — SSE data lines cannot contain raw newlines
    safe_data = data.replace("\n", " ")
    return f"event: {event}\ndata: {safe_data}\n\n"


def _progress_html(step: str, icon: str = "⏳") -> str:
    """Return an HTML <li> fragment for the progress list."""
    return (
        f'<li class="flex items-center gap-2 text-sm text-gray-700 py-1">'
        f'<span>{icon}</span><span>{step}</span></li>'
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def homepage(request: Request, lang: str = "he"):
    """Landing page with upload hero zone."""
    # Validate lang param
    if lang not in ("he", "en"):
        lang = "he"

    response = templates.TemplateResponse(
        request,
        "home.html",
        {"lang": lang, "t": _t(lang)},
    )
    # Set lang cookie so subsequent requests preserve preference
    response.set_cookie("lang", lang, max_age=365 * 86400, samesite="lax")
    return response


@router.post("/upload", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/hour")
async def upload(request: Request, photo: UploadFile = File(...)):
    """
    Accept uploaded photo, validate, store in _pending, return SSE progress fragment.

    Rate limited to 10 uploads per hour per IP.
    """
    _cleanup_pending()

    # Determine lang from cookie or default
    lang = _get_lang(request)
    t = _t(lang)

    # Validate content type
    content_type = photo.content_type or ""
    if not content_type.startswith("image/"):
        return HTMLResponse(
            content=(
                '<div class="text-red-500 text-sm p-4 border border-red-200 rounded-lg">'
                f"{t['not_an_image']}"
                "</div>"
            ),
            status_code=400,
        )

    # Read file bytes
    data = await photo.read()

    # Validate size (10 MB limit)
    max_bytes = 10 * 1024 * 1024
    if len(data) > max_bytes:
        return HTMLResponse(
            content=(
                '<div class="text-red-500 text-sm p-4 border border-red-200 rounded-lg">'
                f"{t['photo_too_large']}"
                "</div>"
            ),
            status_code=413,
        )

    # Generate session and store bytes
    session_id = secrets.token_urlsafe(16)
    _pending[session_id] = (data, time.time())

    # Create 96px thumbnail using Pillow
    thumbnail_b64 = ""
    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(data))
        img.thumbnail((96, 96))
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        if fmt not in ("JPEG", "PNG", "WEBP"):
            fmt = "JPEG"
        img.save(buf, format=fmt)
        thumbnail_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        logger.warning("Thumbnail generation failed: %s", exc)

    return templates.TemplateResponse(
        request,
        "partials/progress.html",
        {
            "session_id": session_id,
            "thumbnail_b64": thumbnail_b64,
            "lang": lang,
            "t": t,
        },
    )


async def _sse_generator(session_id: str, lang: str = "he") -> AsyncGenerator[str, None]:
    """
    Async generator for the SSE stream.

    Stages:
      1. Analyzing photo (vision AI)
      2. Found N products
      3. Searching Amazon
      4. Done → redirect to result page
    """
    t = _t(lang)

    # Pop the pending bytes
    entry = _pending.pop(session_id, None)
    if entry is None:
        yield _sse_event(
            "progress",
            _progress_html(t["session_expired"], "❌"),
        )
        yield _sse_event("done", "/")
        return

    data, _ = entry

    try:
        # Stage 1: Analyzing
        yield _sse_event("progress", _progress_html(t["analyzing"], "🔍"))

        # Lazy import to avoid circular imports
        from providers.manager import analyse_image
        winner, _ = await analyse_image(data)
        products = winner.to_product_info_list()

        # Stage 2: Found products
        n = len(products)
        if n == 0:
            label = t["no_products"]
        elif n == 1:
            label = t["found_one_product"]
        else:
            label = t["found_products"].format(n=n)
        yield _sse_event("progress", _progress_html(label, "✅" if n else "⚠️"))

        # Stage 3: Amazon search
        yield _sse_event("progress", _progress_html(t["searching"], "🛒"))

        import amazon_search
        all_results: list = []
        if products:
            tasks = [amazon_search.search_amazon(p) for p in products]
            all_results = list(await asyncio.gather(*tasks, return_exceptions=False))

        # Stage 3b: Annotate image (CPU-bound → run in executor)
        import image_annotator
        loop = asyncio.get_event_loop()
        try:
            annotated_bytes = await loop.run_in_executor(
                None, image_annotator.annotate_products, data, products
            )
        except Exception as exc:
            logger.warning("Image annotation failed: %s", exc)
            annotated_bytes = None

        # Stage 4: Persist and redirect
        from web_app import search_store
        short_id = await search_store.save_web_search(data, annotated_bytes, products, all_results)

        yield _sse_event("done", f"/search/{short_id}")

    except Exception as exc:
        logger.error("SSE stream error for session %s: %s", session_id, exc, exc_info=True)
        yield _sse_event(
            "progress",
            _progress_html(f"Error: {str(exc)[:80]}", "❌"),
        )
        yield _sse_event("done", "/")


@router.get("/stream/{session_id}", include_in_schema=False)
async def sse_stream(request: Request, session_id: str):
    """SSE endpoint — streams 4-stage analysis progress to the browser."""
    lang = _get_lang(request)
    return StreamingResponse(
        _sse_generator(session_id, lang=lang),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/search/{short_id}/image", include_in_schema=False)
async def result_image(short_id: str):
    """Serve the annotated photo BLOB for a web search result."""
    from web_app import search_store

    row = await search_store.get_web_search(short_id)
    if row is None or row.get("annotated_photo") is None:
        return Response(status_code=404)

    return Response(
        content=bytes(row["annotated_photo"]),
        media_type="image/jpeg",
    )


@router.get("/search/{short_id}", response_class=HTMLResponse, include_in_schema=False)
async def result_page(request: Request, short_id: str):
    """
    Full result page with product cards, tabs, OG tags, price history, and shipping badges.
    Returns 200 for valid results, 404 for missing, 410 for expired.
    """
    import json as _json
    import time as _time
    import database as _db
    from price_history import get_price_history, PriceHistory
    from web_app import search_store

    # Check if the row exists (including expired)
    async with _db._get_conn() as db:
        async with db.execute(
            "SELECT short_id, expires_at FROM web_searches WHERE short_id = ?",
            (short_id,),
        ) as cursor:
            meta_row = await cursor.fetchone()

    if meta_row is None:
        # Determine lang for error page
        lang = _get_lang(request)
        t = _t(lang)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"lang": lang, "t": t, "error_title": t["not_found"], "error_message": t["result_expired_msg"]},
            status_code=404,
        )

    if meta_row["expires_at"] < _time.time():
        lang = _get_lang(request)
        t = _t(lang)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"lang": lang, "t": t, "error_title": t["expired"], "error_message": t["result_expired_msg"]},
            status_code=410,
        )

    # Load full result data
    row = await search_store.get_web_search(short_id)
    if row is None:
        # Shouldn't happen but guard anyway
        lang = _get_lang(request)
        t = _t(lang)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"lang": lang, "t": t, "error_title": t["not_found"], "error_message": t["result_expired_msg"]},
            status_code=404,
        )

    # Determine display language: ?lang= param → cookie → stored row lang
    lang = _get_lang(request)
    # If no explicit lang preference from request, fall back to stored lang
    if not request.query_params.get("lang") and not request.cookies.get("lang"):
        stored_lang = row.get("lang", "he")
        if stored_lang in ("he", "en"):
            lang = stored_lang

    t = _t(lang)

    # Parse JSON data
    all_results: list[list[dict]] = _json.loads(row.get("results_json") or "[]")
    products: list[dict] = _json.loads(row.get("products_json") or "[]")

    # Determine active product index from query param
    try:
        active_idx = int(request.query_params.get("product", 0))
    except (ValueError, TypeError):
        active_idx = 0
    if active_idx < 0 or active_idx >= max(len(products), 1):
        active_idx = 0

    active_results: list[dict] = all_results[active_idx] if active_idx < len(all_results) else []

    # Get active affiliate tag
    affiliate_tag = await _db.get_active_tag()

    # Pre-compute affiliate URLs and shipping badges for each item
    def _affiliate_url(asin: str) -> str:
        if affiliate_tag:
            return f"https://www.amazon.com/dp/{asin}?tag={affiliate_tag}&linkCode=ogi&th=1&psc=1"
        return f"https://www.amazon.com/dp/{asin}?th=1&psc=1"

    def _shipping_badge(item: dict) -> dict:
        """Return badge info dict with text, bg_class, text_class."""
        if item.get("is_sold_by_amazon") or item.get("is_amazon_fulfilled"):
            return {"text": t["ships_to_israel"], "bg": "bg-green-100", "color": "text-green-800"}
        if item.get("is_prime"):
            return {"text": t["likely_ships"], "bg": "bg-yellow-100", "color": "text-yellow-800"}
        return {"text": t["may_not_ship"], "bg": "bg-red-100", "color": "text-red-800"}

    # Enrich active_results with affiliate URL and shipping badge
    enriched_results = []
    for item in active_results:
        item = dict(item)
        item["affiliate_url"] = _affiliate_url(item.get("asin", ""))
        item["badge"] = _shipping_badge(item)
        enriched_results.append(item)

    # Fetch price history for active product items (concurrent, max 10)
    price_histories: dict[str, dict] = {}
    if enriched_results:
        ph_tasks = [get_price_history(item["asin"]) for item in enriched_results[:10]]
        ph_results = await asyncio.gather(*ph_tasks, return_exceptions=True)
        for item, ph in zip(enriched_results[:10], ph_results):
            if isinstance(ph, PriceHistory) and ph is not None:
                price_histories[item["asin"]] = {
                    "current": ph.current,
                    "low_all_time": ph.low_all_time,
                    "avg_90d": ph.avg_90d,
                    "low_90d": ph.low_90d,
                    "deal_label": ph.deal_label,
                }

    # Build OG meta data
    import config as _config
    base_url = (_config.SHORTENER_BASE_URL or "").rstrip("/")
    og_image = f"{base_url}/search/{short_id}/image" if base_url else f"/search/{short_id}/image"
    og_title = products[0].get("product_name", "Amazon Product Search") if products else "Amazon Product Search"
    product_count = len(products)
    og_description = (
        f"Found {product_count} product{'s' if product_count != 1 else ''} — "
        "click to see prices, ratings, and Amazon links."
    )

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "lang": lang,
            "t": t,
            "short_id": short_id,
            "products": products,
            "active_product_idx": active_idx,
            "active_results": enriched_results,
            "all_results": all_results,
            "affiliate_tag": affiliate_tag,
            "price_histories": price_histories,
            "og_title": og_title,
            "og_description": og_description,
            "og_image": og_image,
            "og_url": f"{base_url}/search/{short_id}" if base_url else f"/search/{short_id}",
        },
    )
