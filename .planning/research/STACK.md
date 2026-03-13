# Technology Stack

**Project:** Amazon Photo Bot - Multi-Platform Expansion
**Researched:** 2026-03-13
**Scope:** New libraries for web interfaces, photo annotations, multi-platform messaging, price charts, and admin dashboard. Does NOT re-cover existing stack (see `.planning/codebase/STACK.md`).

## Recommended Stack

### Web Admin Dashboard

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| FastAPI | >=0.110.0 (existing) | Backend for admin dashboard | Already in the project for the API server; extend it rather than adding another framework | HIGH |
| Jinja2 | >=3.1.4 | Server-side HTML templating | Ships with FastAPI/Starlette. No extra dependency. Partials support for HTMX fragments | HIGH |
| HTMX | 2.0.8 (CDN) | Dynamic UI without JavaScript framework | 20KB total, server-rendered HTML fragments, no build step, no Node.js toolchain. FastAPI+HTMX is the dominant Python admin pattern in 2025-2026 | HIGH |
| Alpine.js | 3.x (CDN) | Lightweight client-side interactivity | 15KB, handles dropdowns/modals/toggles that HTMX alone cannot. No build step | HIGH |
| Tailwind CSS | 3.x (CDN) | Utility-first CSS | CDN play version avoids build tooling. Good enough for admin dashboards. Upgrade to Tailwind CLI if design gets complex | MEDIUM |

**Why NOT React/Vue/Svelte:** Adding a JavaScript SPA framework means a Node.js toolchain, npm build step, API serialization layer, and state management complexity. For an admin dashboard consumed by 1-3 admins, this is massive overhead. FastAPI+HTMX+Jinja2 achieves 90% of the UX with 10% of the complexity. The server already has all the data; just render HTML.

**Why NOT Django Admin / SQLAdmin:** The project is FastAPI-based, not Django. SQLAdmin exists for FastAPI but forces you into its opinionated CRUD patterns. The admin panel needs custom dashboards (usage stats, provider health, search analytics), not generic CRUD. Custom Jinja2 templates give full control.

### Public-Facing Web App

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| FastAPI | >=0.110.0 (existing) | Web app backend | Same server, separate router. Share auth, DB, and business logic with the bot | HIGH |
| Jinja2 + HTMX | Same as above | Photo upload UI, results display | Server-rendered for SEO (important for organic discovery). HTMX for the interactive upload/results flow | HIGH |
| Dropzone.js | 6.0.0 (CDN) | Drag-and-drop photo upload | 45KB, no dependencies. Handles drag-drop, preview, progress. Well-tested library | MEDIUM |

**Why NOT a separate frontend app:** A separate React app would need CORS config, separate deployment, API versioning, and double the maintenance. The web app is a thin layer over existing bot logic. Server-render it.

### Photo Annotation Overlays

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Pillow | >=12.1.0 (upgrade from ~10.4.0) | Image annotation, overlays, text labels | Already a dependency. ImageDraw with RGBA mode supports semi-transparent rectangles, alpha compositing, text labels. No new dependency needed | HIGH |

**Approach:** Create an RGBA overlay image, draw semi-transparent filled rectangles with `ImageDraw.rectangle(xy, fill=(R, G, B, alpha))`, add product number labels with `ImageDraw.text()`, then composite onto the original with `Image.alpha_composite()`. Run in `asyncio.get_event_loop().run_in_executor(None, ...)` since Pillow is synchronous.

**Why NOT OpenCV:** OpenCV (cv2) is 50+ MB, pulls in numpy, and is designed for computer vision pipelines. For drawing colored rectangles and text on images, Pillow is sufficient and already installed. OpenCV would be warranted only if doing client-side shape detection, which is not the case here (bounding boxes come from the vision API).

**Why NOT supervision (Roboflow):** The `supervision` library is excellent for ML model output visualization but overkill for drawing 3-5 labeled rectangles. It adds roboflow ecosystem dependencies.

### Price History Charts

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| matplotlib | >=3.10.0 | Generate price history chart PNGs | Industry standard for static chart generation in Python. Renders to BytesIO buffer, no disk I/O needed. Excellent control over styling | HIGH |

**Approach:** Generate chart with matplotlib, save to `io.BytesIO()` buffer as PNG, send the bytes directly via adapter's `send_photo()`. Wrap in `run_in_executor()` since matplotlib is synchronous. Cache chart images by ASIN with a TTL.

**Why NOT Plotly + Kaleido:** Plotly generates beautiful interactive charts but we need static PNGs for Telegram/WhatsApp. Kaleido (Plotly's static export engine) adds ~90MB and spawns a Chromium subprocess -- contradicts the project's async-first, no-subprocess constraint. Matplotlib generates PNGs natively with zero subprocess overhead.

**Why NOT mplfinance:** It is designed for OHLC candlestick charts (stocks). Price history data is simple time-series (date, price) -- a basic line chart with fill. Standard matplotlib is cleaner.

### Multi-Platform Messaging

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| aiohttp | ~3.10.0 (existing) | HTTP client for Meta Graph API | Already used. WhatsApp and Instagram adapters already implement Meta Cloud API with aiohttp + shared_meta.py helpers. No new library needed | HIGH |
| Meta Graph API v21.0 | (existing) | WhatsApp + Instagram DM backend | Both adapters already use it via `shared_meta.py`. The adapters exist but are untested -- the work is testing/hardening, not new libraries | HIGH |

**Why NOT pywa:** The WhatsApp adapter already implements the Cloud API directly with aiohttp. Adding pywa would create two competing WhatsApp implementations. The existing adapter follows the project's `PlatformAdapter` interface and shares code with Instagram via `shared_meta.py`. Stick with what exists.

**Why NOT aiograpi (Instagram private API):** It reverse-engineers Instagram's private API, which violates Instagram ToS and breaks frequently. The existing Instagram adapter uses the official Instagram Messaging API (part of Meta Graph API). Official API is the only production-viable path.

**WhatsApp Business API Requirements (non-library):**
- Meta Business verification (business documents)
- WhatsApp Business Platform account
- Phone number registered with WhatsApp Business
- Webhook endpoint with HTTPS (already needed for the web app)
- Message template approval for outbound messages

### Supporting Libraries (New)

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| python-multipart | >=0.0.9 | File upload parsing for FastAPI | Required by FastAPI for `UploadFile` endpoints (photo upload in web app) | HIGH |
| itsdangerous | >=2.2.0 | Signed session tokens for admin auth | Lightweight session signing. Already a Starlette dependency, just import it | HIGH |

### Libraries Explicitly NOT Adding

| Library | Why Not |
|---------|---------|
| React / Vue / Svelte | No build toolchain for an admin panel with 1-3 users |
| SQLAlchemy / SQLModel | Project uses raw aiosqlite. Migration to ORM is a separate decision |
| Celery / Dramatiq | No background task queue needed. `asyncio.create_task()` handles everything |
| Redis | Not needed at SQLite scale. Would add infrastructure complexity |
| pywa | Existing WhatsApp adapter already implements Cloud API with aiohttp |
| opencv-python | 50MB+ for drawing rectangles. Pillow already handles this |
| plotly + kaleido | 90MB+ Chromium subprocess for static PNGs. Matplotlib does it natively |
| Bokeh | Interactive charts for browser. We need static PNGs for messaging platforms |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not Alternative |
|----------|-------------|-------------|---------------------|
| Admin UI | HTMX + Jinja2 | React SPA | Build toolchain, API layer, 10x complexity for 3 admin users |
| Admin UI | HTMX + Jinja2 | Streamlit | Opinionated layout, poor mobile support, not embeddable |
| Admin UI | HTMX + Jinja2 | Gradio | Designed for ML demos, not admin dashboards |
| Charts | matplotlib | Plotly + Kaleido | Subprocess overhead, 90MB dependency, async-hostile |
| Charts | matplotlib | Pygal | Less control over styling, smaller community |
| Photo overlay | Pillow (existing) | OpenCV | 50MB dependency for a task Pillow handles natively |
| WhatsApp | aiohttp (existing) | pywa | Duplicate implementation; existing adapter already works |
| Web framework | FastAPI (existing) | Django | Would require rewriting the entire project |
| CSS | Tailwind CDN | Bootstrap | Tailwind CDN is more flexible; Bootstrap adds opinionated components |

## Version Pinning Strategy

```
# requirements.txt additions for this milestone
# (add to existing requirements.txt)

# Web dashboard (Jinja2 ships with Starlette/FastAPI -- no separate install)
python-multipart>=0.0.9          # FastAPI file upload support

# Price history charts
matplotlib>=3.10.0               # Static chart generation (PNG to BytesIO)

# Pillow upgrade (already a dependency, bump version)
Pillow>=12.1.0                   # Was ~10.4.0; upgrade for RGBA overlay improvements
```

**CDN dependencies (no pip install):**
```html
<!-- HTMX 2.0.8 -->
<script src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js"></script>

<!-- Alpine.js 3.x -->
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>

<!-- Tailwind CSS CDN (play mode) -->
<script src="https://cdn.tailwindcss.com"></script>

<!-- Dropzone.js 6 (photo upload) -->
<link href="https://cdn.jsdelivr.net/npm/dropzone@6/dist/dropzone.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/dropzone@6/dist/dropzone-min.js"></script>
```

## Installation

```bash
# New dependencies only (add to existing requirements.txt)
pip install python-multipart>=0.0.9 matplotlib>=3.10.0

# Upgrade existing Pillow
pip install --upgrade Pillow>=12.1.0
```

No new system-level dependencies. No new Docker build changes (matplotlib installs cleanly in python:3.11-slim). HTMX/Alpine/Tailwind are CDN-loaded, zero server-side footprint.

## Architecture Impact

### What Changes
- `api_server.py` expands to serve admin dashboard and public web app (or split into `web_server.py`)
- New `templates/` directory for Jinja2 templates (admin + public)
- New `static/` directory for CSS overrides if needed
- `price_history.py` gains a `generate_chart(prices, dates) -> bytes` function using matplotlib
- `image_analyzer.py` gains annotation overlay logic using Pillow RGBA compositing
- Existing WhatsApp/Instagram adapters get tested and hardened (no new libraries)

### What Does NOT Change
- Core bot logic (`bot.py`, `image_analyzer.py`, `amazon_search.py`)
- Provider system (`providers/`)
- Search backend system (`search_backends/`)
- Database schema approach (aiosqlite)
- Telegram adapter

## Sources

- [Pillow 12.1.1 ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)
- [Pillow alpha compositing](https://pillow.readthedocs.io/en/stable/reference/Image.html)
- [matplotlib 3.10.8 documentation](https://matplotlib.org/stable/index.html)
- [matplotlib savefig to BytesIO](https://techoverflow.net/2023/08/28/how-to-matplotlib-plt-savefig-to-a-io-bytesio-buffer/)
- [HTMX 2.0 documentation](https://htmx.org/docs/)
- [FastAPI + HTMX patterns 2025](https://johal.in/htmx-fastapi-patterns-hypermedia-driven-single-page-applications-2025/)
- [FastAPI + Jinja2 + HTMX dashboard](https://www.johal.in/fastapi-templating-jinja2-server-rendered-ml-dashboards-with-htmx-2025/)
- [Using HTMX with FastAPI (TestDriven.io)](https://testdriven.io/blog/fastapi-htmx/)
- [PyWa WhatsApp library](https://pywa.readthedocs.io/en/latest/) -- evaluated but NOT recommended (existing adapter preferred)
- [Meta Graph API for Instagram Messaging](https://developers.facebook.com/docs/instagram-messaging/)
- [Plotly static export with Kaleido](https://plotly.com/python/) -- evaluated but NOT recommended (subprocess overhead)
- [HTMX GitHub releases](https://github.com/bigskysoftware/htmx/releases) -- version 2.0.8 confirmed

---

*Stack research: 2026-03-13*
