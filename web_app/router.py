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
        {"lang": lang},
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

    # Validate content type
    content_type = photo.content_type or ""
    if not content_type.startswith("image/"):
        return HTMLResponse(
            content=(
                '<div class="text-red-500 text-sm p-4 border border-red-200 rounded-lg">'
                "Only image files are accepted (JPG, PNG, WEBP, etc.)."
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
                f"File is too large ({len(data) // (1024*1024)} MB). Maximum size is 10 MB."
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

    # Determine lang from cookie or default
    lang = request.cookies.get("lang", "he")
    if lang not in ("he", "en"):
        lang = "he"

    return templates.TemplateResponse(
        request,
        "partials/progress.html",
        {
            "session_id": session_id,
            "thumbnail_b64": thumbnail_b64,
            "lang": lang,
        },
    )


async def _sse_generator(session_id: str) -> AsyncGenerator[str, None]:
    """
    Async generator for the SSE stream.

    Stages:
      1. Analyzing photo (vision AI)
      2. Found N products
      3. Searching Amazon
      4. Done → redirect to result page
    """
    # Pop the pending bytes
    entry = _pending.pop(session_id, None)
    if entry is None:
        yield _sse_event(
            "progress",
            _progress_html("Session expired. Please upload again.", "❌"),
        )
        yield _sse_event("done", "/")
        return

    data, _ = entry

    try:
        # Stage 1: Analyzing
        yield _sse_event("progress", _progress_html("Analyzing photo...", "🔍"))

        # Lazy import to avoid circular imports
        from providers.manager import analyse_image
        winner, _ = await analyse_image(data)
        products = winner.to_product_info_list()

        # Stage 2: Found products
        n = len(products)
        label = f"Found {n} product{'s' if n != 1 else ''}" if n else "No products detected"
        yield _sse_event("progress", _progress_html(label, "✅" if n else "⚠️"))

        # Stage 3: Amazon search
        yield _sse_event("progress", _progress_html("Searching Amazon...", "🛒"))

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
    return StreamingResponse(
        _sse_generator(session_id),
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
    Result page stub — Plan 02 will add full rendering with OG tags.
    Returns 200 for valid results, 404 for missing, 410 for expired.
    """
    from web_app import search_store
    import time as _time

    # First check if the row exists at all (including expired)
    async with __import__("database")._get_conn() as db:
        async with db.execute(
            "SELECT short_id, expires_at FROM web_searches WHERE short_id = ?",
            (short_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return HTMLResponse(
            content="<h1>Not found</h1>",
            status_code=404,
        )

    if row["expires_at"] < _time.time():
        return HTMLResponse(
            content="<h1>This result has expired</h1>",
            status_code=410,
        )

    # Stub response — Plan 02 will render full results page
    return HTMLResponse(
        content=(
            f'<html><body>'
            f'<p>Results loaded for: {short_id}</p>'
            f'<img src="/search/{short_id}/image" alt="Annotated product" />'
            f'</body></html>'
        ),
        status_code=200,
    )
