# Phase 5: Public Web Application - Research

**Researched:** 2026-03-14
**Domain:** FastAPI + HTMX + Jinja2 + SSE + RTL — server-rendered public web app with real-time progress
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Upload UX:** File picker + drag-drop zone. Mobile OS file picker handles camera/gallery — no dedicated camera button.
- **Real-time progress:** SSE (Server-Sent Events) via FastAPI StreamingResponse + HTMX `hx-ext="sse"`.
- **Progress stages:** 4-stage progress steps shown alongside uploaded photo thumbnail ("Analyzing photo..." → "Found 3 products..." → "Searching Amazon..." → "Done").
- **Anonymous usage:** No login required. Rate-limited with CAPTCHA after N searches per session/IP.
- **Results layout:** Vertical card list, one product per row (stacked). Mobile-friendly.
- **Annotated photo:** Displayed at top of results page, collapsible on scroll.
- **Product card content:** product image, title, price, star rating, Israel shipping badge (green/yellow/red), price history bar with deal quality label, "View on Amazon" affiliate link button.
- **Multi-product navigation:** Horizontal tabs above results ("Product 1", "Product 2", etc.). Clicking loads that product's Amazon results below.
- **URL structure:** `/search/<short-id>` (random short ID, reuse existing URL shortener pattern).
- **Persistence:** 30 days in SQLite, then auto-purged.
- **Open Graph:** Result pages use annotated photo as `og:image` for WhatsApp/Telegram/Facebook previews.
- **SEO:** Homepage indexable; result pages use `noindex` meta tag.
- **Landing page:** Upload-first hero (big centered upload zone), "How it works" 3-step section below.
- **Language:** Hebrew + English with toggle. Hebrew as default. RTL layout support. Reuse `translator.py` and `i18n.py`.
- **Visual tone:** Clean & minimal, white/light background, subtle shadows, rounded cards. Similar to Google Lens or Amazon search.
- **Footer:** Amazon Associates affiliate disclosure.

### Claude's Discretion
- CAPTCHA implementation details (threshold, provider)
- SSE event format and reconnection handling
- Exact Tailwind styling and spacing
- Rate limiting thresholds and session tracking approach
- Upload file size validation UX (error messages, max size)
- 404/expired result page design

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WEBA-01 | Public web page where users can upload a photo to identify products | FastAPI UploadFile + HTMX + SSE pipeline; web_app/ router registered in gateway.py |
| WEBA-02 | Web app displays product results with prices, ratings, affiliate links, and shipping badges | AmazonItem.affiliate_url(), AmazonItem.delivery_badge/israel_delivery_note — adapt HTML rendering from formatter.py patterns |
| WEBA-03 | Web app shows price history visualization for each product | price_history.py data → HTML price bar; adapt render_price_bar() from style.py as HTML/SVG instead of ASCII |
| WEBA-04 | Search results have shareable URLs for SEO and link sharing | `/search/<short-id>` routes; web_searches DB table; OG meta tags in result template |
| WEBA-05 | Web app is mobile-responsive (majority of users on phones) | Tailwind CDN + logical properties for RTL; mobile-first Tailwind classes throughout |
</phase_requirements>

---

## Summary

Phase 5 builds a public-facing web application that mirrors the Telegram bot experience in a browser: upload a photo, watch real-time progress via SSE, see annotated results with product cards, and share a permanent URL. The project already has every ingredient — FastAPI + HTMX + Jinja2 + Tailwind CDN (from Phase 3 admin dashboard), async image analysis (providers/manager.py), Amazon search (amazon_search.py), and URL shortener logic (url_shortener.py). The primary new work is: (1) a `web_app/` router module structured identically to `admin_dashboard/`, (2) an SSE streaming endpoint to push 4-stage progress to the browser, (3) a `web_searches` SQLite table for result persistence, (4) Hebrew/RTL support using Tailwind logical properties, and (5) rate limiting via SlowAPI.

The HTMX SSE extension (v2.2.4, separate CDN script) handles the browser-side SSE connection with automatic exponential-backoff reconnection. The upload flow uses FastAPI's `UploadFile` with python-multipart. All async patterns follow established project conventions (async def handlers, aiosqlite, no blocking calls).

**Primary recommendation:** Create `web_app/` as a sibling to `admin_dashboard/`, register it in `gateway.py` at `/` (before the shortener catch-all), and follow identical router/template/deps patterns from Phase 3.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | (already installed) | HTTP routing, UploadFile, StreamingResponse | Established in project for all web surfaces |
| Jinja2Templates | (already installed) | Server-rendered HTML templates | Used in admin_dashboard — exact same pattern |
| HTMX | 2.0.8 (CDN) | SSE connection, form submission, DOM swaps | Already loaded in base.html |
| htmx-ext-sse | 2.2.4 (CDN) | SSE extension separate from HTMX core in v2 | Required for `hx-ext="sse"` in HTMX 2.x |
| Tailwind CSS | (CDN Play) | Styling + RTL logical properties | Already used via CDN — no build step |
| python-multipart | (already installed) | Multipart form upload required by FastAPI UploadFile | FastAPI dependency for file uploads |
| aiosqlite | (already installed) | web_searches table persistence | Project-wide async SQLite library |
| slowapi | 0.1.9 | IP-based rate limiting on upload endpoint | Standard Flask-Limiter port for FastAPI/Starlette |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Pillow | (already installed) | Read uploaded image dimensions/format for validation | Re-use existing offload-to-executor pattern from Phase 1 |
| starlette.testclient | (already installed) | TestClient for route tests | Mirrors test_gateway.py and test_admin_web.py |
| python-magic | optional | Magic-byte MIME validation beyond Content-Type header | If stricter upload security required (optional) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| HTMX SSE extension | WebSockets | WebSockets require persistent connection management; SSE is one-way push, simpler for progress updates, no extra library |
| HTMX SSE extension | JS polling with hx-trigger="every Ns" | Admin dashboard uses polling for stats. SSE is more appropriate here: one connection per upload, closes when done, no wasted polling after completion |
| SlowAPI | Manual middleware | SlowAPI wraps limits-per-IP as a decorator; minimal boilerplate; already documented for FastAPI |
| Tailwind logical properties (ms-*, ps-*) | tailwindcss-rtl plugin | Logical properties are built into Tailwind v3.3+ — no additional plugin needed when using CDN |

**Installation (new packages only):**
```bash
pip install slowapi
```

Note: python-multipart is likely already installed (FastAPI dependency). Verify with `pip show python-multipart`.

---

## Architecture Patterns

### Recommended Project Structure
```
web_app/
├── __init__.py          # exports router
├── router.py            # FastAPI APIRouter — all public routes
├── deps.py              # rate limiter setup, session helpers
├── search_store.py      # async functions for web_searches table
└── templates/
    ├── web_base.html    # public base (no admin sidebar; has lang toggle + dir attr)
    ├── home.html        # landing page: upload hero + how-it-works
    ├── search.html      # results page: annotated photo + product tabs + cards
    └── partials/
        ├── progress.html    # SSE progress steps fragment
        ├── product_tabs.html # horizontal product selector
        └── product_cards.html # result card list for one product
```

This mirrors `admin_dashboard/` exactly. The router is registered in `gateway.py` before the shortener catch-all.

### Pattern 1: SSE Upload + Progress Flow

**What:** Browser POSTs photo via HTMX. Server returns an SSE stream ID. Separate SSE endpoint streams 4-stage progress events. On completion, SSE pushes a redirect or final HTML fragment.

**When to use:** Any long-running operation (10-30s analysis + search) where real-time feedback prevents user abandonment.

**HTMX SSE v2 CDN URLs (verified from htmx.org/extensions/sse/):**
```html
<script src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/htmx-ext-sse@2.2.4/sse.js"></script>
```

**HTMX 2.x SSE attribute migration (breaking change from v1):**
- v1: `hx-sse="connect:/stream swap:message"` — single attribute
- v2: separate attributes — `sse-connect="/stream"` + `sse-swap="message"` + `hx-ext="sse"`

**HTML structure for SSE progress:**
```html
<!-- Source: https://htmx.org/extensions/sse/ -->
<div hx-ext="sse" sse-connect="/web/stream/{session_id}" sse-close="done">
  <div id="progress-steps" sse-swap="progress" hx-swap="innerHTML">
    <!-- Server pushes HTML fragments named "progress" -->
  </div>
</div>
```

**FastAPI SSE endpoint pattern:**
```python
# Source: https://fastapi.tiangolo.com/tutorial/server-sent-events/
from fastapi.responses import StreamingResponse

async def _event_generator(session_id: str):
    # Stage 1
    yield f"event: progress\ndata: <div>Analyzing photo...</div>\n\n"
    products = await analyse_image(...)
    # Stage 2
    yield f"event: progress\ndata: <div>Found {len(products)} products...</div>\n\n"
    results = await search_amazon(...)
    # Stage 3
    yield f"event: progress\ndata: <div>Searching Amazon...</div>\n\n"
    short_id = await _save_and_get_short_id(session_id, products, results)
    # Stage 4 — push redirect
    yield f"event: done\ndata: <div hx-get='/search/{short_id}' hx-trigger='load' hx-target='body'></div>\n\n"

@router.get("/stream/{session_id}")
async def stream_progress(session_id: str):
    return StreamingResponse(
        _event_generator(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**Key header:** `X-Accel-Buffering: no` prevents nginx from buffering SSE responses (critical in production).

### Pattern 2: Upload Endpoint

**What:** HTMX form POST with file. FastAPI UploadFile reads bytes, validates, stores bytes in a temporary in-memory session store keyed by session_id, then returns the SSE listener HTML.

**Example:**
```python
# Source: https://fastapi.tiangolo.com/tutorial/request-files/
from fastapi import UploadFile, File, Form
import secrets

# In-memory session store: {session_id: bytes}
_pending: dict[str, bytes] = {}

@router.post("/upload")
async def upload_photo(photo: UploadFile = File(...)):
    data = await photo.read()
    if len(data) > 10 * 1024 * 1024:  # 10MB cap (matches existing bot limit)
        raise HTTPException(status_code=413, detail="Photo too large (max 10MB)")
    # Validate it's actually an image
    content_type = photo.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    session_id = secrets.token_urlsafe(16)
    _pending[session_id] = data
    # Return HTML fragment: SSE listener div
    return templates.TemplateResponse("partials/progress.html",
                                      {"request": request, "session_id": session_id})
```

### Pattern 3: RTL / Hebrew Tailwind

**What:** Toggle `dir="rtl"` on `<html>` tag. Use Tailwind logical properties throughout (built into Tailwind v3.3+, available via CDN Play).

**Logical property equivalents:**
| Physical | Logical (RTL-safe) |
|----------|-------------------|
| `ml-4` | `ms-4` (margin-inline-start) |
| `mr-4` | `me-4` (margin-inline-end) |
| `pl-4` | `ps-4` (padding-inline-start) |
| `pr-4` | `pe-4` (padding-inline-end) |
| `left-0` | `start-0` |
| `right-0` | `end-0` |
| `text-left` | `text-start` |

**HTML toggle pattern:**
```html
<!-- web_base.html -->
<html lang="{{ lang }}" dir="{{ 'rtl' if lang == 'he' else 'ltr' }}">
```

**Language toggle link:**
```html
<a href="?lang=he">עברית</a> | <a href="?lang=en">English</a>
```

Lang preference stored in browser cookie or session. Default: `he`.

### Pattern 4: Open Graph Meta Tags on Result Pages

**What:** Jinja2 template block in `web_base.html` that child templates override.

```html
<!-- Source: https://ogp.me/ -->
{% block og_tags %}{% endblock %}
```

```html
<!-- search.html overrides: -->
{% block og_tags %}
<meta property="og:type" content="website">
<meta property="og:title" content="{{ product_name }} — Amazon Photo Bot">
<meta property="og:description" content="{{ og_description }}">
<meta property="og:image" content="{{ annotated_photo_url }}">
<meta property="og:url" content="{{ request.url }}">
<meta name="robots" content="noindex">
{% endblock %}
```

**Image serving:** Annotated photo bytes stored in `web_searches` table. Served via `/search/<short-id>/image` endpoint that returns `Response(content=bytes, media_type="image/jpeg")`. This URL becomes `og:image`.

Recommended OG image size: 1200x630 pixels. The `annotate_products()` function in `image_annotator.py` returns JPEG bytes — serve them directly.

### Pattern 5: Web Searches Table

**What:** New SQLite table storing search results for 30-day sharing.

```sql
CREATE TABLE IF NOT EXISTS web_searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    short_id    TEXT NOT NULL UNIQUE,
    photo_hash  TEXT NOT NULL,              -- SHA256 of original photo bytes
    annotated_photo BLOB,                   -- annotated image bytes (for og:image)
    results_json TEXT NOT NULL,             -- JSON of list[AmazonItem] per product
    products_json TEXT NOT NULL,            -- JSON of list[ProductInfo]
    lang        TEXT NOT NULL DEFAULT 'he',
    created_at  REAL NOT NULL,             -- Unix timestamp
    expires_at  REAL NOT NULL              -- created_at + 30*86400
);
CREATE INDEX IF NOT EXISTS idx_web_searches_short_id ON web_searches(short_id);
CREATE INDEX IF NOT EXISTS idx_web_searches_expires  ON web_searches(expires_at);
```

**Auto-purge:** Register a cleanup task in the existing `scheduler.py` pattern (daily coroutine):
```python
# Runs daily alongside existing scheduler tasks
async def purge_expired_web_searches():
    async with db._get_conn() as conn:
        await conn.execute(
            "DELETE FROM web_searches WHERE expires_at < ?",
            (_time.time(),)
        )
        await conn.commit()
```

### Pattern 6: Rate Limiting with SlowAPI

**What:** SlowAPI wraps Flask-Limiter's API for FastAPI/Starlette. IP-based by default.

```python
# web_app/deps.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

```python
# web_app/router.py
from slowapi.errors import RateLimitExceeded
from web_app.deps import limiter

@router.post("/upload")
@limiter.limit("10/hour")   # Claude's discretion: threshold TBD
async def upload_photo(request: Request, photo: UploadFile = File(...)):
    ...
```

```python
# gateway.py — register SlowAPI exception handler
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from web_app.deps import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

### Anti-Patterns to Avoid

- **Don't use HTMX 1.x SSE syntax:** `hx-sse="connect:..."` does not work in HTMX 2.x. Use `sse-connect="..."` + `hx-ext="sse"` (separate SSE extension CDN script required).
- **Don't block the event loop during image processing:** `analyse_image()` and `annotate_products()` must be called with `asyncio.get_event_loop().run_in_executor()` for CPU-bound Pillow work (Phase 1 established this pattern).
- **Don't store photo bytes in the URL session cookie:** Store only the `session_id` key; bytes live in the in-memory `_pending` dict (or a DB staging row) until the SSE stream consumes them.
- **Don't register the web_app router after the shortener catch-all:** The shortener's `/{code}` route will swallow `/search/...` if registered first. Maintain gateway.py registration order.
- **Don't rely on `dir="rtl"` alone:** Also use Tailwind logical properties (`ms-*`, `ps-*`, `text-start`) throughout templates — physical directional classes (`ml-*`, `text-left`) break in RTL.
- **Don't buffer SSE via nginx:** Set `X-Accel-Buffering: no` header on the SSE response, or events won't reach the browser until the stream closes.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rate limiting | Custom IP counter in SQLite | SlowAPI + `@limiter.limit("N/hour")` | Thread-safe, Redis-upgradeable, 429 handler built in |
| SSE reconnection | JS retry loop | htmx-ext-sse 2.2.4 | Exponential backoff, Last-Event-ID replay, background tab pause built in |
| File upload | Raw `request.body()` parsing | FastAPI `UploadFile` | Streaming, content-type, size limits handled by Starlette |
| Short ID generation | UUID or custom hash | Reuse `url_shortener._generate_unique_code()` | Already deduplicates against DB; base-62, 7 chars |
| Affiliate URLs | String building | `AmazonItem.affiliate_url(tag)` | Already handles all edge cases and tag injection |
| Israel shipping badge | Custom HTML logic | `AmazonItem.delivery_badge` + `israel_delivery_note` | Already tested, correct signal weighting from Phase 2 |
| Price bar HTML | ASCII art reuse | Adapt `render_price_bar()` from `style.py` as an HTML/CSS width bar | ASCII blocks don't render well in HTML; use a div width % instead |

**Key insight:** The bot already computes everything the web app needs to display. The web layer is purely presentation — take `AmazonItem` fields and render HTML instead of Telegram markdown.

---

## Common Pitfalls

### Pitfall 1: HTMX 2.x SSE Extension is a Separate Script
**What goes wrong:** Developer loads only `htmx.min.js` and adds `hx-ext="sse"` — nothing happens.
**Why it happens:** In HTMX 2.x, extensions are NOT bundled. The SSE extension moved to `htmx-ext-sse` package with its own CDN URL.
**How to avoid:** Always load both CDN scripts:
```html
<script src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/htmx-ext-sse@2.2.4/sse.js"></script>
```
**Warning signs:** `hx-ext="sse"` silently does nothing; no EventSource connection in browser devtools Network tab.

### Pitfall 2: SSE Buffering by Reverse Proxy
**What goes wrong:** SSE events arrive all at once at the end (appears like polling), not in real time.
**Why it happens:** nginx/uvicorn/CDN buffers the response body until close.
**How to avoid:** Set `X-Accel-Buffering: no` in the SSE response headers. In Docker, uvicorn's direct mode should be fine; add the header defensively.
**Warning signs:** Progress steps all appear simultaneously after 30 seconds.

### Pitfall 3: Gateway Route Ordering Breaks `/search/...`
**What goes wrong:** Visiting `/search/abc123` returns a 404 from the shortener, or worse, looks up `abc123` as a shortener code.
**Why it happens:** `gateway.py` registers the shortener catch-all `/{code}` last — adding a new router AFTER the shortener breaks this invariant.
**How to avoid:** Register `web_app` router in `gateway.py` BEFORE the shortener catch-all:
```python
# 3. Admin dashboard at /admin/*
app.include_router(admin_router, prefix="/admin")
# 4. Web app at /* (public routes like /search/<id>, /upload)
app.include_router(web_router)
# 5. Shortener catch-all at /{code} — MUST BE LAST
app.include_router(shortener_router)
```
**Warning signs:** `/search/abc123` returns 302 redirect instead of HTML page.

### Pitfall 4: RTL Breaks with Physical Tailwind Classes
**What goes wrong:** Hebrew layout has text and padding on the wrong side.
**Why it happens:** Physical classes like `ml-4`, `text-left`, `pl-4` are direction-agnostic — they always mean "left" regardless of `dir`.
**How to avoid:** Use logical properties throughout: `ms-4` (margin-start), `ps-4` (padding-start), `text-start`. These automatically flip with `dir="rtl"`.
**Warning signs:** Hebrew UI has left-aligned text when it should be right-aligned; card padding appears reversed.

### Pitfall 5: OG Image URL Breaks Social Preview
**What goes wrong:** WhatsApp/Telegram don't show the annotated photo when the link is shared.
**Why it happens:** `og:image` must be an absolute URL (not relative); the image must be publicly accessible (not behind auth); size should be ≥ 200x200.
**How to avoid:** Build `og:image` as a fully qualified URL: `f"{config.SHORTENER_BASE_URL}/search/{short_id}/image"`. Serve `/search/<id>/image` as a public, unauthenticated endpoint.
**Warning signs:** Social media previews show no image; Telegram link preview is plain text.

### Pitfall 6: In-Memory `_pending` Dict Leaks on Server Restart
**What goes wrong:** After a server restart during SSE streaming, the photo bytes are lost; the SSE endpoint raises KeyError.
**Why it happens:** `_pending` is a module-level dict that dies with the process.
**How to avoid:** Two options: (a) Store pending bytes in a DB staging row with a short TTL (preferred for resilience), or (b) Accept the limitation for MVP (reconnection shows an error page). The SSE session is short-lived (< 60s), so lost-on-restart is acceptable for initial delivery if the error page is graceful.
**Warning signs:** After uvicorn reload, active SSE streams 500.

---

## Code Examples

Verified patterns from official sources and project conventions:

### Upload Endpoint with SlowAPI Rate Limiting
```python
# Source: FastAPI docs + slowapi docs
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from slowapi.errors import RateLimitExceeded
from web_app.deps import limiter

router = APIRouter()

@router.post("/upload", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def upload_photo(request: Request, photo: UploadFile = File(...)):
    data = await photo.read()
    if len(data) > 10 * 1024 * 1024:
        return HTMLResponse("<p class='text-red-500'>Photo too large (max 10MB)</p>", status_code=413)
    if not (photo.content_type or "").startswith("image/"):
        return HTMLResponse("<p class='text-red-500'>File must be an image</p>", status_code=400)
    import secrets
    session_id = secrets.token_urlsafe(16)
    _pending[session_id] = data
    return templates.TemplateResponse(
        "partials/progress.html", {"request": request, "session_id": session_id}
    )
```

### SSE StreamingResponse with 4 Stages
```python
# Source: https://fastapi.tiangolo.com/tutorial/server-sent-events/
import asyncio
from fastapi.responses import StreamingResponse

async def _analyse_and_stream(session_id: str, lang: str):
    data = _pending.pop(session_id, None)
    if data is None:
        yield "event: error\ndata: <p>Session expired</p>\n\n"
        return

    yield "event: progress\ndata: <li>Analyzing photo...</li>\n\n"
    await asyncio.sleep(0)  # yield control to event loop

    from providers.manager import analyse_image
    import asyncio
    loop = asyncio.get_event_loop()
    products = await analyse_image(data)

    yield f"event: progress\ndata: <li>Found {len(products)} products...</li>\n\n"

    from amazon_search import search_amazon
    all_results = []
    for p in products:
        items = await search_amazon(p.amazon_search_query)
        all_results.append(items)

    yield "event: progress\ndata: <li>Searching Amazon...</li>\n\n"

    short_id = await _save_results(data, products, all_results, lang)

    yield f"event: done\ndata: /search/{short_id}\n\n"


@router.get("/stream/{session_id}")
async def stream_progress(session_id: str, lang: str = "he"):
    return StreamingResponse(
        _analyse_and_stream(session_id, lang),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

### HTMX SSE HTML (HTMX 2.x syntax)
```html
<!-- partials/progress.html — Source: https://htmx.org/extensions/sse/ -->
<div id="upload-progress"
     hx-ext="sse"
     sse-connect="/web/stream/{{ session_id }}"
     sse-close="done">
  <img src="{{ thumb_url }}" class="w-24 h-24 rounded object-cover" alt="Your photo">
  <ol id="steps" class="mt-4 space-y-2">
    <li class="text-gray-400">Waiting...</li>
  </ol>
  <div id="redirect-target" sse-swap="done" hx-swap="none"
       hx-on::sse-message="window.location = event.data"></div>
</div>
```

### Web Searches Table Functions
```python
# web_app/search_store.py
import json, time, secrets
import database as db

async def save_web_search(
    photo_bytes: bytes,
    annotated_bytes: bytes | None,
    products: list,
    all_results: list[list],
    lang: str = "he",
) -> str:
    """Persist search results and return the short_id for the /search/<id> URL."""
    import hashlib
    short_id = secrets.token_urlsafe(8)
    photo_hash = hashlib.sha256(photo_bytes).hexdigest()
    now = time.time()
    async with db._get_conn() as conn:
        await conn.execute(
            """INSERT INTO web_searches
               (short_id, photo_hash, annotated_photo, results_json, products_json, lang, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                short_id, photo_hash, annotated_bytes,
                json.dumps([[item.__dict__ for item in items] for items in all_results]),
                json.dumps([p.__dict__ for p in products]),
                lang, now, now + 30 * 86400,
            ),
        )
        await conn.commit()
    return short_id
```

### Gateway Registration (updated gateway.py)
```python
# gateway.py — add BEFORE shortener catch-all
# 4. Public web app
from web_app import router as web_router
app.include_router(web_router, prefix="")
logger.info("Mounted web app router at /")

# 5. Shortener catch-all — MUST BE LAST
from shortener_routes import router as shortener_router
app.include_router(shortener_router)
```

### Open Graph Tags in Result Template
```html
<!-- search.html -->
{% block og_tags %}
<meta property="og:type" content="website">
<meta property="og:title" content="{{ first_product }} on Amazon — Amazon Photo Bot">
<meta property="og:description" content="Find this product and {{ product_count - 1 }} more on Amazon. Ships to Israel?">
<meta property="og:image" content="{{ base_url }}/search/{{ short_id }}/image">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{{ base_url }}/search/{{ short_id }}">
<meta name="robots" content="noindex, nofollow">
{% endblock %}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| HTMX 1.x: `hx-sse="connect:URL swap:event"` | HTMX 2.x: `hx-ext="sse"` + `sse-connect="URL"` + `sse-swap="event"` (separate CDN script) | HTMX 2.0 (2024) | Must load htmx-ext-sse separately; existing admin dashboard uses only HTMX core features (polling), not SSE extension |
| Tailwind directional classes (`ml-`, `text-left`) | Tailwind logical properties (`ms-`, `text-start`) for RTL support | Tailwind v3.3 (2023) | No plugin needed for RTL with CDN Play; just use logical utility classes |
| `EventSource` URL-only connection | htmx-ext-sse fetch-based implementation supports POST + headers + exponential backoff | 2025 | More robust reconnection than native EventSource |

**Deprecated/outdated:**
- `hx-sse` attribute (HTMX v1): replaced by `sse-connect`/`sse-swap` in v2. The admin dashboard does NOT use this, so there's no legacy code to worry about.

---

## Open Questions

1. **CAPTCHA provider for rate limit bypass**
   - What we know: CAPTCHA triggers after N searches per session/IP (Claude's discretion on threshold)
   - What's unclear: Which CAPTCHA provider — hCaptcha or Google reCAPTCHA v3? hCaptcha is friendlier for non-Google traffic; reCAPTCHA v3 is invisible but score-based.
   - Recommendation: Start with SlowAPI rate limiting only (10/hour per IP) for MVP; add CAPTCHA in v2 if abuse becomes a problem. CAPTCHA integration adds a JS dependency and server-side validation that adds scope.

2. **In-memory `_pending` dict vs. DB staging**
   - What we know: Photo bytes need to bridge the gap between the POST /upload response and the SSE stream start (~1-2 seconds).
   - What's unclear: Is an in-memory dict acceptable for a single-process deployment? (Yes for current Docker single-process setup.)
   - Recommendation: Use in-memory `_pending` dict for MVP with a 5-minute TTL cleanup coroutine. The project runs as a single uvicorn process, so no cross-process state sharing is needed.

3. **Annotated photo aspect ratio for OG image**
   - What we know: `annotate_products()` returns the original image dimensions + a bottom legend strip. OG images should be 1200x630 (1.91:1).
   - What's unclear: Do we crop/resize to 1.91:1 or serve as-is?
   - Recommendation: Serve as-is and set `og:image:width`/`og:image:height` to actual dimensions. Social platforms handle non-standard ratios by letterboxing; the annotated photo is more important than pixel-perfect ratio.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| Config file | `pytest.ini` (existing) |
| Quick run command | `pytest tests/test_web_app.py -x -q` |
| Full suite command | `pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WEBA-01 | POST /web/upload accepts valid image, returns progress HTML | unit | `pytest tests/test_web_app.py::TestUpload::test_valid_upload -x` | Wave 0 |
| WEBA-01 | POST /web/upload rejects oversized file with 413 | unit | `pytest tests/test_web_app.py::TestUpload::test_oversized_file -x` | Wave 0 |
| WEBA-01 | POST /web/upload rejects non-image content-type | unit | `pytest tests/test_web_app.py::TestUpload::test_non_image_rejected -x` | Wave 0 |
| WEBA-01 | GET /web/stream/{id} yields SSE progress events | unit | `pytest tests/test_web_app.py::TestSSE::test_stream_stages -x` | Wave 0 |
| WEBA-02 | GET /search/{id} renders product cards with price, rating, badge | unit | `pytest tests/test_web_app.py::TestResultPage::test_product_card_fields -x` | Wave 0 |
| WEBA-02 | Affiliate URL includes active tag | unit | `pytest tests/test_web_app.py::TestResultPage::test_affiliate_url -x` | Wave 0 |
| WEBA-03 | GET /search/{id} renders price history bar for each product | unit | `pytest tests/test_web_app.py::TestResultPage::test_price_history_bar -x` | Wave 0 |
| WEBA-04 | GET /search/{id} includes og:image and og:title meta tags | unit | `pytest tests/test_web_app.py::TestResultPage::test_og_tags -x` | Wave 0 |
| WEBA-04 | GET /search/{id} for expired result returns 410 or expired page | unit | `pytest tests/test_web_app.py::TestResultPage::test_expired_result -x` | Wave 0 |
| WEBA-04 | web_searches auto-purge deletes rows past expires_at | unit | `pytest tests/test_web_app.py::TestSearchStore::test_purge_expired -x` | Wave 0 |
| WEBA-05 | Home page HTML has viewport meta tag and Tailwind responsive classes | smoke | `pytest tests/test_web_app.py::TestHomePage::test_mobile_viewport -x` | Wave 0 |
| WEBA-05 | Result page HTML contains RTL dir attribute when lang=he | unit | `pytest tests/test_web_app.py::TestResultPage::test_rtl_dir_attr -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_web_app.py -x -q`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_web_app.py` — covers all WEBA-01 through WEBA-05 requirements above
- [ ] `web_app/__init__.py` — exports router
- [ ] `web_app/router.py` — FastAPI APIRouter (stub with placeholder routes)
- [ ] `web_app/search_store.py` — DB functions for web_searches table
- [ ] `web_app/deps.py` — SlowAPI limiter setup
- [ ] `web_app/templates/web_base.html` — public base template
- [ ] DB migration: `web_searches` table added to `database.init_db()`

---

## Sources

### Primary (HIGH confidence)
- `htmx.org/extensions/sse/` — HTMX 2.x SSE extension API, CDN URLs, attribute syntax, reconnection behavior
- `fastapi.tiangolo.com/tutorial/server-sent-events/` — FastAPI SSE with StreamingResponse
- `fastapi.tiangolo.com/tutorial/request-files/` — UploadFile multipart pattern
- `ogp.me/` — Open Graph protocol specification for og:image
- Project codebase: `admin_dashboard/router.py`, `gateway.py`, `database.py`, `url_shortener.py`, `search_backends/base.py`, `image_annotator.py`, `formatter.py`, `i18n.py`

### Secondary (MEDIUM confidence)
- `github.com/laurentS/slowapi` — SlowAPI for FastAPI rate limiting (verified as active 2025, multiple 2025-2026 articles)
- `tailwindcss.com` — Logical properties (`ms-*`, `ps-*`) built into Tailwind v3.3+ (available via CDN Play, no plugin needed)
- `slowapi.readthedocs.io/` — SlowAPI docs confirming IP-based rate limiting and FastAPI exception handler pattern

### Tertiary (LOW confidence)
- Medium articles on FastAPI SSE patterns — consistent with official docs, not independently verified
- flowbite.com/docs/customize/rtl/ — RTL Tailwind guidance; specific to Flowbite components but logical property info matches Tailwind docs

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed or single new addition (slowapi)
- Architecture: HIGH — directly mirrors admin_dashboard/ pattern confirmed in codebase
- SSE pattern: HIGH — verified against htmx.org official docs and FastAPI docs
- RTL/Tailwind: HIGH — verified Tailwind logical properties in v3.3+ docs
- Pitfalls: HIGH — HTMX 2.x extension separation is a documented breaking change; nginx buffering is a well-known SSE production issue

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (30 days — HTMX and Tailwind CDN versions should be checked if delayed)
