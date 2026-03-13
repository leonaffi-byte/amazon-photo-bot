# Architecture Patterns

**Domain:** Multi-platform product-finding bot with web frontend, admin dashboard, and photo annotation
**Researched:** 2026-03-13

## Recommended Architecture

The system already has a clean layered architecture with `PlatformAdapter` -> `BotCore` -> providers/backends. The expansion to web app, admin dashboard, and photo annotation should treat these as **additional surfaces on the same core**, not separate applications.

### High-Level Structure

```
                    +------------------+
                    |   Web Frontend   |  (public photo upload + results)
                    |   (HTMX + Jinja) |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   Admin Dashboard |  (HTMX + Jinja, auth-gated)
                    +--------+---------+
                             |
              +--------------v--------------+
              |        FastAPI Gateway       |  (single HTTP surface)
              |  /api/v1/*  /admin/*  /app/* |
              |  /webhook/* /metrics  /go/*  |
              +--------------+--------------+
                             |
              +--------------v--------------+
              |         BotCore             |  (platform-agnostic business logic)
              +-+----------+----------+-----+
                |          |          |
     +----------v-+  +----v-----+ +--v-----------+
     | Vision Mgr |  | Search   | | Photo        |
     | (providers)|  | (backends)| | Annotator    |
     +------------+  +----------+ +--------------+
                |          |          |
              +-v----------v----------v-----+
              |   Data & Config Layer       |
              |  (SQLite/aiosqlite, config) |
              +-----------------------------+

  Messaging Adapters (Telegram, WhatsApp, etc.)
  connect to BotCore directly via PlatformAdapter interface
```

### Component Boundaries

| Component | Responsibility | Communicates With | New/Existing |
|-----------|---------------|-------------------|--------------|
| **FastAPI Gateway** | Unified HTTP entry point: REST API, web app, admin dashboard, webhooks, shortener | BotCore, Database, all web-facing surfaces | **Expand existing** `api_server.py` |
| **BotCore** | Platform-agnostic business logic (photo -> vision -> search -> results) | VisionManager, SearchFacade, PhotoAnnotator, Database | Existing |
| **Web Frontend** | Public photo upload, results browsing, price history display | FastAPI Gateway via HTMX partials | **New** |
| **Admin Dashboard** | Replace Telegram admin panel: settings, stats, API keys, reports | FastAPI Gateway via HTMX partials | **New** |
| **Photo Annotator** | Draw bounding boxes / overlays on product photos, return annotated image | Called by BotCore after vision analysis | **New** |
| **Messaging Adapters** | Telegram (polling), WhatsApp/Instagram/etc (webhooks) | BotCore via PlatformAdapter | Existing |
| **Vision Manager** | Orchestrate AI vision providers | Vision providers, health tracking | Existing |
| **Search Facade** | Orchestrate Amazon search backends | Search backends, fallback chain | Existing |
| **WebAdapter** | PlatformAdapter implementation for web sessions (WebSocket/SSE) | BotCore, FastAPI Gateway | **New** |
| **URL Shortener** | Redirect service with click tracking | Database | Existing (merge into FastAPI Gateway) |

## Data Flow

### Web App Flow (New)

```
User browser
  |
  | POST /app/upload (multipart photo)
  v
FastAPI Gateway
  |
  | Creates WebSession (like UserSession but HTTP-based)
  | Returns session_id + SSE stream URL
  v
BotCore.handle_photo(web_event)
  |
  | Progress updates sent via SSE to browser
  v
Vision Analysis -> PhotoAnnotator -> Amazon Search
  |
  | Results stored in WebSession
  v
SSE pushes HTMX partial HTML for results
  |
  v
Browser renders product cards inline (no page reload)
```

### Admin Dashboard Flow (New)

```
Admin browser
  |
  | GET /admin/dashboard (session cookie auth)
  v
FastAPI Gateway
  |
  | Jinja2 template renders with current stats
  | HTMX polls /admin/api/* for live updates
  v
Database (settings, stats, API keys, reports)
```

### Photo Annotation Flow (New)

```
BotCore receives vision analysis result
  |
  | result.products = [{name, bounding_box, confidence}, ...]
  v
PhotoAnnotator.annotate(image_bytes, products)
  |
  | Pillow: draw semi-transparent overlays + numbered labels
  | Returns: annotated_image_bytes
  v
Adapter sends annotated image to user
  |
  | User taps number / selects product
  v
BotCore proceeds with chosen product -> Amazon search
```

### Messaging Platform Flow (Existing, unchanged)

```
Platform (Telegram/WhatsApp/etc)
  |
  v
PlatformAdapter.on_photo()
  |
  v
BotCore.handle_photo(event)
  |
  v
Vision -> Annotation -> Search -> Format -> Send
```

## Patterns to Follow

### Pattern 1: Web as Another Adapter

**What:** Treat the web frontend as another `PlatformAdapter` implementation, not a separate system. Create a `WebAdapter` that maps HTTP requests/SSE streams to the same `BotCore` interface.

**Why:** The entire business logic pipeline (photo -> vision -> search -> format) already exists and is platform-agnostic. The web frontend should reuse it, not duplicate it.

**When:** Building the web photo upload and results features.

**Example:**
```python
class WebAdapter(PlatformAdapter):
    """Adapter for web browser sessions using SSE for push updates."""
    platform_name = "web"
    max_caption_length = 10000  # HTML, no real limit
    supports_photo_edit = True
    supports_inline_buttons = True
    supports_carousels = True

    async def send_text(self, chat_id: str, text: str, **kw) -> MessageRef:
        # Push HTMX partial via SSE to the browser session
        await self._sse_manager.send(chat_id, render_text_partial(text))
        return MessageRef(platform="web", chat_id=chat_id, message_id=str(uuid4()))

    async def send_photo(self, chat_id: str, photo, caption: str, **kw) -> MessageRef:
        # Push product card HTML partial via SSE
        await self._sse_manager.send(chat_id, render_photo_partial(photo, caption))
        ...
```

### Pattern 2: HTMX + Jinja2 for Web Surfaces (No SPA Framework)

**What:** Use HTMX for interactivity with server-rendered Jinja2 templates. No React/Vue/Angular.

**Why:** The project is Python-only. Adding a JavaScript build pipeline (Node.js, npm, webpack/vite) for an admin dashboard and a simple photo upload page is unnecessary complexity. HTMX gives SPA-like interactivity (partial page updates, SSE integration, progress indicators) from server-rendered HTML. The team's expertise is Python, not frontend JavaScript. HTMX is 14KB with zero build step.

**When:** All web-facing surfaces (public app and admin dashboard).

**Example:**
```html
<!-- Photo upload with progress -->
<form hx-post="/app/upload" hx-encoding="multipart/form-data"
      hx-target="#results" hx-indicator="#spinner">
  <input type="file" name="photo" accept="image/*">
  <button type="submit">Find Products</button>
  <div id="spinner" class="htmx-indicator">Analyzing...</div>
</form>

<!-- Results populated via SSE -->
<div id="results" hx-ext="sse" sse-connect="/app/stream/{session_id}"
     sse-swap="result">
</div>
```

### Pattern 3: Unified FastAPI Application

**What:** Consolidate all HTTP surfaces into a single FastAPI application instead of running separate aiohttp servers.

**Why:** Currently there are three separate HTTP servers: `api_server.py` (FastAPI, port 8001), `shortener_server.py` (aiohttp, port 8080), and `webhook_server.py` (aiohttp, port 8081). This creates deployment complexity and prevents sharing middleware (auth, CORS, metrics). A single FastAPI app can mount all routes and run on one port behind nginx.

**When:** Before adding web frontend. This is prerequisite infrastructure.

**Migration path:**
1. Port shortener routes from aiohttp to FastAPI router
2. Port webhook routes from aiohttp to FastAPI router
3. Add web app and admin dashboard as FastAPI routers
4. Run single uvicorn process alongside bot adapters in the same asyncio loop

**Example:**
```python
# gateway.py — unified FastAPI app
from fastapi import FastAPI

app = FastAPI()

# Existing
app.include_router(api_router, prefix="/api/v1")      # Israel shipping API
app.include_router(shortener_router, prefix="/go")     # URL shortener
app.include_router(webhook_router, prefix="/webhook")  # Platform webhooks

# New
app.include_router(web_router, prefix="/app")          # Public web app
app.include_router(admin_router, prefix="/admin")      # Admin dashboard
app.mount("/static", StaticFiles(directory="static"))   # CSS, JS, images
```

### Pattern 4: SSE for Real-Time Progress

**What:** Use Server-Sent Events (SSE) instead of WebSockets for pushing progress updates to the web frontend.

**Why:** The photo analysis pipeline takes 3-15 seconds. Users need progress feedback. SSE is simpler than WebSockets (unidirectional, auto-reconnect, works through proxies), and HTMX has native SSE support via `hx-ext="sse"`. The bot only needs to push updates to the browser, not receive streaming data from it.

**When:** Web photo upload flow.

### Pattern 5: Photo Annotation via Pillow

**What:** Use Pillow (already a dependency) to draw semi-transparent colored overlays and numbered labels on detected product regions.

**Why:** The project already imports PIL for image compression (`bot_core.py` line 20). No new dependency needed. Vision models can return approximate bounding box coordinates. Pillow's `ImageDraw` supports rectangles with alpha blending for semi-transparent overlays.

**When:** After vision providers are updated to return bounding box data.

**Implementation approach:**
```python
from PIL import Image, ImageDraw, ImageFont

def annotate_products(image_bytes: bytes, products: list[ProductInfo]) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    colors = [(255,107,107,80), (78,205,196,80), (69,183,209,80),
              (255,230,109,80), (150,111,214,80), (247,154,66,80)]

    for i, product in enumerate(products):
        bbox = product.bounding_box  # (x1, y1, x2, y2) normalized 0-1
        x1, y1 = int(bbox[0] * img.width), int(bbox[1] * img.height)
        x2, y2 = int(bbox[2] * img.width), int(bbox[3] * img.height)
        color = colors[i % len(colors)]
        draw.rectangle([x1, y1, x2, y2], fill=color, outline=color[:3] + (255,), width=3)
        draw.text((x1+5, y1+5), str(i+1), fill=(255,255,255,255))

    result = Image.alpha_composite(img, overlay)
    buf = io.BytesIO()
    result.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Separate SPA Frontend

**What:** Building the web app as a React/Vue SPA with its own build pipeline.

**Why bad:** Doubles the technology surface (Python + Node.js), requires frontend build tooling the project doesn't have, adds CORS complexity, and the web interface is simple enough (upload photo, see results, browse products) that server-rendered HTML with HTMX handles it well. This is not a complex interactive application like Google Docs.

**Instead:** HTMX + Jinja2 + Tailwind CSS (via CDN). Zero build step.

### Anti-Pattern 2: Duplicating Business Logic for Web

**What:** Building a separate "web handler" that re-implements the photo -> vision -> search pipeline outside of BotCore.

**Why bad:** Creates two code paths that drift apart. Bug fixes apply to one but not the other. Feature additions must be done twice.

**Instead:** Create a `WebAdapter` that implements `PlatformAdapter` and routes through `BotCore` like every other adapter.

### Anti-Pattern 3: WebSockets for Everything

**What:** Using WebSockets for the web frontend's real-time updates.

**Why bad:** WebSockets are bidirectional and stateful, adding complexity for a use case that only needs server-to-client push. They don't auto-reconnect, they're harder to proxy, and they require explicit connection management.

**Instead:** SSE for push updates (progress, results). Standard HTTP POST for user actions (upload, select product, filter).

### Anti-Pattern 4: Keeping Multiple HTTP Servers

**What:** Running shortener on port 8080, webhooks on 8081, API on 8001, and adding web app on yet another port.

**Why bad:** Each server needs its own port forwarding, SSL certificate handling, and nginx config. Cross-cutting concerns (auth, logging, CORS, rate limiting) must be duplicated.

**Instead:** Single FastAPI app with routers. One port, one nginx upstream.

### Anti-Pattern 5: Admin Dashboard as Telegram Mini App

**What:** Building the admin dashboard as a Telegram Mini App (WebApp) instead of a standalone web page.

**Why bad:** Telegram Mini Apps require the Telegram client to be open, limiting access. They have viewport constraints, limited debugging tools, and tie admin functionality to a single platform. The existing Telegram admin commands (`/admin`, `/settings`) already suffer from this -- the whole point is to replace them with a proper web dashboard.

**Instead:** Standalone web dashboard at `/admin/*`, accessible from any browser, authenticated via session cookie (separate from Telegram).

## Scalability Considerations

| Concern | Current (SQLite) | At 1K daily users | At 10K daily users |
|---------|-------------------|--------------------|--------------------|
| **Database** | SQLite WAL mode, single writer | Still fine -- SQLite handles 1K writes/sec | Migrate to PostgreSQL for concurrent writes |
| **Vision API** | Sequential per user | Parallel across users, rate limit per provider | Add request queue, provider load balancing |
| **Web sessions** | In-memory dict | In-memory dict with TTL cleanup (already exists) | Redis for session store if multi-process |
| **SSE connections** | N/A | Hold open SSE per active web session (~50 concurrent) | Reverse proxy SSE buffering off; consider Redis pub/sub for multi-worker |
| **Static assets** | Served by FastAPI | Serve via nginx directly | CDN for images/CSS/JS |
| **Photo storage** | In-memory per session | In-memory, cleaned after TTL | Temp file on disk or object storage if memory pressure |

## Suggested Build Order (Dependencies)

The components have clear dependency relationships that determine build order:

### Phase ordering by dependency:

1. **Unified FastAPI Gateway** -- Prerequisite for everything web-facing. Consolidate shortener + webhook + API into single FastAPI app. No user-visible change, but unlocks all subsequent work.

2. **Photo Annotator** -- Can be built independently from web. Uses existing Pillow dependency. Requires updating vision provider prompts to request bounding box coordinates. Benefits both messaging platforms and web.

3. **Admin Dashboard** -- Depends on FastAPI Gateway. HTMX + Jinja2 pages at `/admin/*`. Replaces Telegram-only admin. Needs: auth system (simple session cookies), dashboard views for stats/settings/keys.

4. **WebAdapter + Web Frontend** -- Depends on FastAPI Gateway. Most complex new component. Needs: SSE manager, web session management, file upload handling, HTMX templates for results display. The WebAdapter makes web sessions look like any other platform to BotCore.

5. **WhatsApp Integration** -- Depends on webhook server being part of FastAPI Gateway. Adapter code exists but is untested. Needs: Meta Business API credentials, webhook verification, production testing.

### Why this order:

- Gateway consolidation is pure infrastructure with no user-facing risk
- Photo annotation adds value to existing Telegram users immediately
- Admin dashboard reduces operational friction before scaling to more platforms
- Web frontend is the most complex new surface and benefits from lessons learned on admin dashboard
- WhatsApp integration is gated on business API approval anyway

## Sources

- Existing codebase: `adapters/base.py`, `bot_core.py`, `api_server.py`, `webhook_server.py`, `shortener_server.py`, `main.py`
- [FastAPI + Telegram WebApp skeleton (SvelteKit + MongoDB)](https://github.com/sibeardev/webapp_telegram_fastapi)
- [Building Real-Time Dashboards with FastAPI and HTMX](https://medium.com/codex/building-real-time-dashboards-with-fastapi-and-htmx-01ea458673cb)
- [HTMX Renaissance -- Rethinking Web Architecture for 2026](https://www.softwareseni.com/the-htmx-renaissance-rethinking-web-architecture-for-2026/)
- [Pillow ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)
- [Transparent Overlay Labels on Object Detection](https://machinelearningspace.com/a-simple-guide-to-making-transparent-overlay-labels-on-object-detection/)
- [FastAPI Telegram Mini-App template](https://github.com/zytfo/fastapi-telegram-mini-app)
- [Telegram Mini App Development Guide 2025](https://ejaw.net/telegram-mini-app-development-2025/)

---

*Architecture analysis: 2026-03-13*
