# Phase 3: Web Admin Dashboard - Research

**Researched:** 2026-03-14
**Domain:** FastAPI + Jinja2 + HTMX server-rendered admin dashboard with Telegram Login Widget auth
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Authentication**
- Primary: Telegram Login Widget — admins click "Log in with Telegram", verified against existing ADMIN_IDS list
- Fallback: Auto-generated login token shown in bot startup logs and sendable via `/webtoken` Telegram command (for environments without domain configuration)
- Session duration: 24 hours before requiring re-login
- Single admin role — everyone in ADMIN_IDS gets full access (no viewer/admin split)
- Public internet access with auth as only protection layer

**Dashboard Home & Statistics**
- Landing page shows stats overview with key metrics: total users, searches today/week/month, photos analyzed, products found
- Sidebar navigation to detail pages (Tags, Keys, Settings, Health)
- Stats include: usage metrics, provider performance (success rate, latency, cost), search backend stats (hit rate, fallback frequency)
- No revenue estimates on dashboard
- Per-tag performance shown on tag management page (click count, search count from DB)
- Totals with simple CSS/SVG sparklines showing 7-day trends (no charting library)

**Live Updates & Interaction**
- Provider health auto-refreshes via HTMX `hx-trigger="every 30s"`
- Home page stat cards auto-refresh via HTMX polling every 60s
- Admin action feedback via inline HTMX swap (no page reload, no toast component)
- Admin actions on web dashboard trigger Telegram notifications to other admins via existing `notifications.py`

**Mobile & Styling**
- Fully responsive: sidebar collapses to hamburger menu on mobile, stat cards stack vertically, tables scroll horizontally
- Tailwind CSS via CDN (Play CDN script tag — no build step, acceptable for admin-only pages)
- HTMX via CDN for interactivity

### Claude's Discretion
- Exact Jinja2 template structure and inheritance hierarchy
- HTMX partial endpoint naming conventions
- Sparkline implementation approach (inline SVG vs CSS-only)
- Login rate limiting details
- Exact sidebar navigation items and ordering
- Table pagination for logs/history views

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMN-01 | Web-based admin dashboard accessible at `/admin` with authentication | FastAPI APIRouter mounted at `/admin` in `gateway.py`; Telegram Login Widget for auth; fallback token via `/webtoken` command |
| ADMN-02 | Dashboard shows bot statistics (users, searches, clicks) | `admin_service.get_stats()` + `db.get_stats_since()` exist; `BotStats` dataclass ready; sparklines generated server-side as inline SVG |
| ADMN-03 | Admin can manage API keys (add, remove, view status) via web UI | `admin_service.list_key_groups()`, `set_api_key()`, `delete_api_key()` all exist; 18 key groups defined in `_API_GROUPS` |
| ADMN-04 | Admin can manage affiliate tags (activate, deactivate, add) via web UI | `admin_service.list_tags()`, `add_tag()`, `remove_tag()`, `set_active_tag()` all exist |
| ADMN-05 | Admin can edit bot settings (vision mode, search backend, thresholds) via web UI | `admin_service.list_settings()`, `set_setting()`, `reset_setting()` exist; `SettingInfo` has type/choices metadata for form rendering |
| ADMN-06 | Admin can view and manage provider health status via web UI | `admin_service.get_provider_health()` returns `ProviderHealth` list; HTMX polling every 30s refreshes the panel |
</phase_requirements>

## Summary

Phase 3 builds a browser-based admin dashboard that exposes all Telegram admin commands through a web UI. The service layer (`admin_service.py`) was specifically extracted in Phase 1 to enable this — all CRUD operations for tags, keys, settings, stats, and provider health are already implemented as async functions returning plain dataclasses. This phase is primarily a thin HTTP + HTML layer on top of existing business logic.

The chosen stack is FastAPI (already the project gateway) + Jinja2 templates (server-side rendering) + HTMX (dynamic partial updates without JavaScript) + Tailwind CSS Play CDN (responsive styling without a build step). This is the project's established web stack and aligns with the locked decisions. Authentication uses the Telegram Login Widget (HMAC-SHA256 verification against the bot token) with a one-time fallback token for environments without a domain.

The key engineering decisions are: (1) mount the admin router at `/admin` in `gateway.py` using `include_router` with an auth dependency, (2) use `Starlette SessionMiddleware` with `itsdangerous` for signed, HttpOnly session cookies, (3) generate sparklines as inline SVG polygons computed in Python from the existing `get_stats_since()` database function, and (4) use HTMX `hx-target` + `hx-swap` for all form submissions and polling updates to avoid page reloads.

**Primary recommendation:** Add `admin_dashboard/` as a new module directory with its own router, templates subfolder, and auth dependency — mounted as the second-to-last router in `gateway.py` (before the catch-all shortener route).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | >=0.110.0 | HTTP framework and router | Already in requirements.txt; async-native |
| Jinja2 | bundled with FastAPI `jinja2` dep | Server-side HTML templating | Official FastAPI template engine; no JS build step |
| HTMX | 2.x (CDN) | Dynamic partial HTML updates | Project decision; eliminates JS state management |
| Tailwind CSS Play CDN | 3.x (CDN) | Utility CSS; responsive layout | Project decision; no build step for admin-only pages |
| python-multipart | latest | Form POST body parsing | Required for FastAPI form handling |
| itsdangerous | 2.x | Signed session cookie tokens | Used by Starlette SessionMiddleware |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| starlette SessionMiddleware | bundled with FastAPI | HttpOnly signed session cookies | Auth session persistence |
| aiofiles | 23.x | Async static file serving | StaticFiles mount for admin CSS/JS assets if needed |
| hashlib + hmac (stdlib) | stdlib | Telegram Login Widget verification | HMAC-SHA256 of auth data with bot token hash |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Jinja2 SSR | React/Vue SPA | React requires build tooling; defeats admin-only simplicity |
| HTMX CDN | Alpine.js | Alpine better for complex client state; HTMX sufficient for polls + swaps |
| SessionMiddleware | JWT in cookie | JWT adds complexity; session middleware simpler for single-server admin panel |
| Inline SVG sparklines | Chart.js | Chart.js is a JS dependency; inline SVG is pure server-side, zero JS |

**Installation:**
```bash
pip install python-multipart itsdangerous
```
(FastAPI, Jinja2, aiofiles, and starlette are already in `requirements.txt`)

## Architecture Patterns

### Recommended Project Structure
```
admin_dashboard/
├── __init__.py          # exports router
├── router.py            # FastAPI APIRouter with all /admin/* routes
├── auth.py              # Telegram Login Widget verification + session management
├── deps.py              # require_admin() FastAPI dependency
├── sparklines.py        # Server-side inline SVG sparkline generator
└── templates/
    ├── base.html        # Full HTML layout: sidebar, nav, head with CDN links
    ├── partials/
    │   ├── stat_cards.html      # Home page metric cards (HTMX refresh target)
    │   ├── provider_health.html # Provider health table (HTMX refresh target)
    │   ├── tag_row.html         # Single tag table row (HTMX swap after action)
    │   ├── key_group.html       # Single key group card (HTMX swap after save)
    │   └── setting_row.html     # Single setting row (HTMX swap after save)
    ├── login.html               # Login page: Telegram widget + fallback token form
    ├── home.html                # Dashboard home: stat cards + health summary
    ├── tags.html                # Affiliate tag management
    ├── keys.html                # API key management (18 groups)
    ├── settings.html            # Bot settings editor
    └── health.html              # Provider health detail page
```

The `admin_dashboard/` directory lives at project root alongside `providers/` and `search_backends/`.

### Pattern 1: Router mounting in gateway.py
**What:** The admin router is included in `gateway.py` `create_app()` between the API router and the shortener catch-all. Order is critical — the shortener `/{code}` catch-all must remain last.
**When to use:** Any new web surface in this project
**Example:**
```python
# gateway.py — inside create_app()
# After existing API and webhook routers, BEFORE shortener catch-all:
from admin_dashboard import router as admin_router
app.include_router(admin_router, prefix="/admin")
logger.info("Mounted admin dashboard at /admin")

# Shortener catch-all still LAST:
from shortener_routes import router as shortener_router
app.include_router(shortener_router)
```

### Pattern 2: Auth dependency with session cookie
**What:** A `require_admin()` FastAPI dependency checks the session cookie for a valid admin user ID. All non-login admin routes include this dependency.
**When to use:** Every route under `/admin` except `/admin/login` and `/admin/auth/callback`
**Example:**
```python
# admin_dashboard/deps.py
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

async def require_admin(request: Request):
    """FastAPI dependency — redirects to /admin/login if no valid session."""
    user_id = request.session.get("admin_user_id")
    if not user_id:
        raise HTTPException(status_code=302,
                            headers={"Location": "/admin/login"})
    return user_id
```

```python
# admin_dashboard/router.py
from fastapi import APIRouter, Depends
from .deps import require_admin

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, admin_id: int = Depends(require_admin)):
    stats = await admin_service.get_stats()
    ...
```

### Pattern 3: Telegram Login Widget verification
**What:** The widget sends auth data as GET query params to the callback URL. Verify with HMAC-SHA256 using `SHA256(bot_token)` as the secret key.
**When to use:** Primary authentication flow
**Example:**
```python
# admin_dashboard/auth.py
import hashlib
import hmac
from time import time

def verify_telegram_login(data: dict, bot_token: str) -> bool:
    """Verify Telegram Login Widget data. Returns False if tampered or > 24h old."""
    received_hash = data.pop("hash", "")
    # Check freshness: auth_date is Unix timestamp
    auth_date = int(data.get("auth_date", 0))
    if time() - auth_date > 86400:  # 24 hours
        return False
    # Build data-check string: sorted key=value pairs, newline-separated
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hash, received_hash)
```

Telegram returns these fields: `id`, `first_name`, `last_name`, `username`, `photo_url`, `auth_date`, `hash`.
The bot's domain must be registered with `@BotFather` via `/setdomain` for the widget to work.

### Pattern 4: HTMX partial refresh (polling)
**What:** Elements with `hx-get` + `hx-trigger="every Ns"` poll a dedicated partial endpoint. The endpoint returns a partial template fragment, not a full page.
**When to use:** Provider health (30s) and stat cards (60s)
**Example:**
```html
<!-- In home.html -->
<div id="stat-cards"
     hx-get="/admin/partials/stats"
     hx-trigger="every 60s"
     hx-swap="outerHTML">
  {% include "partials/stat_cards.html" %}
</div>
```

```python
# admin_dashboard/router.py
@router.get("/partials/stats", response_class=HTMLResponse)
async def partial_stats(request: Request, admin_id: int = Depends(require_admin)):
    stats = await admin_service.get_stats()
    return templates.TemplateResponse(
        request=request,
        name="partials/stat_cards.html",
        context={"stats": stats}
    )
```

The partial returns the `<div id="stat-cards" ...>` wrapper with `hx-*` attributes so polling continues after swap (`hx-swap="outerHTML"`).

### Pattern 5: HTMX inline form action (no page reload)
**What:** Form submissions use `hx-post` targeting a specific element to swap. On success the server returns only the updated HTML fragment, not a redirect.
**When to use:** Tag activate/deactivate, key save, setting update
**Example:**
```html
<!-- In tags.html — each tag row -->
<tr id="tag-row-{{ tag.id }}"
    hx-target="this"
    hx-swap="outerHTML">
  <form hx-post="/admin/tags/{{ tag.id }}/activate"
        hx-target="#tag-row-{{ tag.id }}"
        hx-swap="outerHTML">
    <button type="submit">Activate</button>
  </form>
</tr>
```

```python
@router.post("/tags/{tag_id}/activate", response_class=HTMLResponse)
async def activate_tag(request: Request, tag_id: int,
                       admin_id: int = Depends(require_admin)):
    await admin_service.set_active_tag(tag_id)
    await notifications.send_admin_notification(f"Tag {tag_id} activated via web")
    tags = await admin_service.list_tags()
    tag = next(t for t in tags if t.id == tag_id)
    return templates.TemplateResponse(
        request=request, name="partials/tag_row.html",
        context={"tag": tag}
    )
```

### Pattern 6: Server-side sparkline generation
**What:** 7-day search count data from `db.get_stats_since()` is converted to a normalized SVG polyline polygon in Python and passed to the template as a string.
**When to use:** Stat card trend indicators
**Example:**
```python
# admin_dashboard/sparklines.py
from datetime import datetime, timedelta, timezone
import database as db

async def build_7day_sparkline(width: int = 60, height: int = 20) -> str:
    """Return an inline SVG sparkline string for 7-day search counts."""
    points = []
    for i in range(6, -1, -1):
        since = datetime.now(timezone.utc) - timedelta(days=i+1)
        until = since + timedelta(days=1)
        # Use get_stats_since to get daily count
        stats = await db.get_stats_since(since)
        points.append(stats.get("total_searches", 0))

    if not any(points):
        # Flat line if no data
        pts_str = " ".join(f"{i*(width//6)},{height//2}" for i in range(7))
        return f'<svg width="{width}" height="{height}"><polyline points="{pts_str}" fill="none" stroke="#94a3b8" stroke-width="1.5"/></svg>'

    mn, mx = min(points), max(points)
    rng = mx - mn or 1
    step = width / 6
    coords = []
    for i, v in enumerate(points):
        x = i * step
        y = height - ((v - mn) / rng) * (height - 2) - 1
        coords.append(f"{x:.1f},{y:.1f}")
    pts_str = " ".join(coords)
    return f'<svg width="{width}" height="{height}"><polyline points="{pts_str}" fill="none" stroke="#6366f1" stroke-width="1.5"/></svg>'
```

### Pattern 7: Fallback token authentication
**What:** A one-time or time-limited token is generated at startup and logged. Admins can also request it via `/webtoken` Telegram command. The login page accepts this token as an alternative to the Telegram widget.
**When to use:** Environments without a domain registered with BotFather
**Example:**
```python
# admin_dashboard/auth.py
import secrets, time

_fallback_token: str | None = None
_token_issued_at: float = 0

def generate_fallback_token() -> str:
    global _fallback_token, _token_issued_at
    _fallback_token = secrets.token_urlsafe(32)
    _token_issued_at = time.time()
    return _fallback_token

def verify_fallback_token(token: str) -> bool:
    if not _fallback_token:
        return False
    if time.time() - _token_issued_at > 86400:  # 24 hours
        return False
    return hmac.compare_digest(token, _fallback_token)
```

`generate_fallback_token()` is called once in `main.py` at startup; the token is logged. The `/webtoken` bot command calls this and DMs the token to the requesting admin.

### Anti-Patterns to Avoid
- **Storing API key values in HTML:** Key management pages must only show set/not-set status. Never render actual key values in the browser.
- **Full page redirects after HTMX actions:** HTMX form submissions must return the HTML fragment, not a redirect — or HTMX will replace the whole page.
- **Shared Jinja2 template instance across router and main app:** Pass a single `Jinja2Templates` instance via module-level variable in `admin_dashboard/router.py`, not recreated per-request.
- **Mounting admin router after shortener catch-all:** The `/{code}` shortener route will intercept `/admin` paths. Admin router must be mounted before it.
- **Checking ADMIN_IDS from config only:** `admin_service.is_admin()` checks the DB; config `ADMIN_IDS` is the bootstrap list. Session verification should check both for robustness.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Signed session cookies | Custom cookie signing | `starlette.middleware.sessions.SessionMiddleware` | Handles key rotation, HMAC signing, expiry; already in Starlette bundled with FastAPI |
| HTMX request detection | Custom header check | `request.headers.get("HX-Request")` | HTMX always sends `HX-Request: true`; single-line check, no library needed |
| Form CSRF protection | Custom token generation | `SameSite=Lax` on session cookie (default) | Admin dashboard is same-origin only; SameSite=Lax prevents cross-site form submission |
| Async static files | Custom file serving | `fastapi.staticfiles.StaticFiles` | Built-in; handles content-type, etag, streaming |
| Auth data replay prevention | Custom timestamp store | Check `auth_date` freshness (< 24h) in verify function | Telegram spec: reject data older than 24h; no storage needed |

**Key insight:** The admin service layer (`admin_service.py`) already encapsulates all business logic. Web handlers are thin: call service function, pass dataclass to template, return HTML fragment. Handlers should average < 10 lines each.

## Common Pitfalls

### Pitfall 1: Telegram widget requires registered domain
**What goes wrong:** Widget silently fails to display or throws JS error; auth callback never fires.
**Why it happens:** BotFather requires `/setdomain yourdomain.com` before the Login Widget works. `localhost` is not accepted.
**How to avoid:** Always implement and test the fallback token path alongside the widget. Document that `/setdomain` is required in bot startup logs.
**Warning signs:** Widget script loads but button never appears; no JS errors but OAuth redirect never fires.

### Pitfall 2: HTMX partial returning full page replaces entire document
**What goes wrong:** After an HTMX form action, the whole page blanks out and shows only the fragment content.
**Why it happens:** Route returns `TemplateResponse("home.html", ...)` instead of `TemplateResponse("partials/stat_cards.html", ...)`.
**How to avoid:** Partial endpoints always return partial templates. Use a naming convention: routes under `/admin/partials/*` return partial templates; all others return full-page templates.
**Warning signs:** Console shows HTMX swap but full page content disappears; only a small fragment is visible.

### Pitfall 3: SessionMiddleware must be added to the top-level app, not the router
**What goes wrong:** `request.session` raises `AssertionError: SessionMiddleware must be installed`.
**Why it happens:** `SessionMiddleware` is Starlette middleware and must be added to the root FastAPI `app` via `app.add_middleware()`, not to an `APIRouter`.
**How to avoid:** Add `app.add_middleware(SessionMiddleware, secret_key=..., https_only=False, same_site="lax")` in `gateway.py`'s `create_app()`.
**Warning signs:** `AssertionError` on first session access; auth works in unit tests but fails in integration.

### Pitfall 4: API key masking — leaking values via form default
**What goes wrong:** The key edit form pre-populates the `<input>` value with the current API key value, exposing it in HTML source.
**Why it happens:** Developer passes actual key value to template context for UX convenience.
**How to avoid:** Key forms show only a placeholder like `••••••••` or "Set" text. Form `value=""` is always empty. User must type a full new value to update.
**Warning signs:** HTML source contains `OPENAI_API_KEY` values; browser autofill caches the key.

### Pitfall 5: HTMX polling continues on error responses
**What goes wrong:** A 500 from the partial endpoint stops HTMX polling entirely because HTMX swaps the error response HTML.
**Why it happens:** HTMX swaps any 2xx response and does not retry on 4xx/5xx by default.
**How to avoid:** Partial endpoints use try/except and return a graceful degraded partial (e.g., "Health data unavailable") rather than raising 500.
**Warning signs:** Provider health card disappears after the first DB error during polling.

### Pitfall 6: HTMX swap loses polling trigger attributes
**What goes wrong:** After the first auto-refresh, the element no longer polls.
**Why it happens:** `hx-swap="innerHTML"` replaces the content but the container element (with `hx-trigger` attributes) must remain. If the partial returns just inner content, polling breaks.
**How to avoid:** Use `hx-swap="outerHTML"` and ensure the partial template re-emits the container div with all `hx-*` attributes intact.
**Warning signs:** Stats update once on first trigger but never again.

## Code Examples

Verified patterns from official sources and project codebase:

### SessionMiddleware setup in gateway.py
```python
# Source: Starlette docs + FastAPI advanced middleware
from starlette.middleware.sessions import SessionMiddleware
import config

def create_app(...) -> FastAPI:
    app = FastAPI(...)
    # Session cookie: HttpOnly, SameSite=Lax, 24h session
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.ADMIN_SESSION_SECRET,  # new env var
        session_cookie="admin_session",
        max_age=86400,       # 24 hours
        same_site="lax",
        https_only=False,    # Set True in production with HTTPS
    )
    ...
```

### Telegram Login Widget HTML
```html
<!-- Source: https://core.telegram.org/widgets/login -->
<script
  async
  src="https://telegram.org/js/telegram-widget.js?22"
  data-telegram-login="{{ bot_username }}"
  data-size="large"
  data-auth-url="{{ request.url_for('telegram_callback') }}"
  data-request-access="write">
</script>
```

### Jinja2Templates with APIRouter (correct pattern)
```python
# Source: FastAPI docs — templates in larger applications
# admin_dashboard/router.py
import os
from pathlib import Path
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["admin"])
_THIS_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_THIS_DIR / "templates"))
```

### HTMX polling partial endpoint
```python
# admin_dashboard/router.py
@router.get("/partials/health", response_class=HTMLResponse)
async def partial_health(request: Request, admin_id: int = Depends(require_admin)):
    try:
        providers = await admin_service.get_provider_health()
    except Exception:
        providers = []
    return templates.TemplateResponse(
        request=request,
        name="partials/provider_health.html",
        context={"providers": providers},
    )
```

```html
<!-- partials/provider_health.html — self-contained with polling attrs -->
<div id="provider-health"
     hx-get="/admin/partials/health"
     hx-trigger="every 30s"
     hx-swap="outerHTML">
  {% for p in providers %}
  <div class="flex items-center gap-2">
    <span class="{% if p.status == 'healthy' %}text-green-600{% elif p.status == 'degraded' %}text-yellow-600{% else %}text-red-600{% endif %}">
      {{ p.name }}
    </span>
    <span class="text-sm text-gray-500">{{ p.status }}</span>
  </div>
  {% endfor %}
</div>
```

### Inline SVG sparkline from Python
```python
# admin_dashboard/sparklines.py — minimal verified implementation
def points_to_svg(values: list[int], width: int = 60, height: int = 20) -> str:
    if not values or not any(values):
        flat_y = height // 2
        pts = " ".join(f"{i*(width//(len(values)-1))},{flat_y}" for i in range(len(values)))
        return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"><polyline points="{pts}" fill="none" stroke="#cbd5e1" stroke-width="1.5"/></svg>'
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = width / (len(values) - 1)
    coords = []
    for i, v in enumerate(values):
        x = i * step
        y = height - ((v - mn) / rng) * (height - 4) - 2
        coords.append(f"{x:.1f},{y:.1f}")
    pts = " ".join(coords)
    return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"><polyline points="{pts}" fill="none" stroke="#6366f1" stroke-width="1.5"/></svg>'
```

### Jinja2 base template skeleton (Tailwind CDN)
```html
<!-- base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Admin{% endblock %} — Amazon Photo Bot</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
  <!-- Sidebar (collapses on mobile via CSS/HTMX toggle) -->
  <nav id="sidebar" class="fixed inset-y-0 left-0 w-64 bg-white shadow-sm hidden md:block">
    <div class="p-4 font-bold text-lg">Admin Panel</div>
    <ul class="mt-4 space-y-1 px-2">
      <li><a href="/admin/" class="block px-3 py-2 rounded hover:bg-gray-100">Dashboard</a></li>
      <li><a href="/admin/tags" class="block px-3 py-2 rounded hover:bg-gray-100">Tags</a></li>
      <li><a href="/admin/keys" class="block px-3 py-2 rounded hover:bg-gray-100">API Keys</a></li>
      <li><a href="/admin/settings" class="block px-3 py-2 rounded hover:bg-gray-100">Settings</a></li>
      <li><a href="/admin/health" class="block px-3 py-2 rounded hover:bg-gray-100">Health</a></li>
    </ul>
  </nav>
  <!-- Mobile hamburger -->
  <button class="md:hidden fixed top-4 left-4 z-50"
          hx-get="/admin/partials/sidebar-toggle"
          hx-target="#sidebar"
          hx-swap="outerHTML">
    ☰
  </button>
  <!-- Main content -->
  <main class="md:ml-64 p-6">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Telegram-only admin commands | Browser-based dashboard | Phase 3 goal | Admins can manage from any browser; no Telegram app required |
| Separate aiohttp servers | Single FastAPI gateway | Phase 1 | Admin dashboard mounts as router on existing gateway |
| Custom admin business logic in admin.py | Extracted admin_service.py | Phase 1 | Service layer already Telegram-free; web handlers are thin wrappers |
| Template `TemplateResponse(name, {"request": request, ...})` | `TemplateResponse(request=request, name=name, context={...})` | FastAPI 0.111.0+ | New signature avoids `request` key collision in context dict |

**Note on Tailwind Play CDN:** The Play CDN (`cdn.tailwindcss.com`) is suitable for admin-only pages per the locked decisions. It generates CSS on-the-fly in the browser. Not recommended for public production pages — noted here as a known limitation but explicitly accepted in CONTEXT.md.

## Open Questions

1. **ADMIN_SESSION_SECRET env var**
   - What we know: `SessionMiddleware` requires a secret key for HMAC signing cookies
   - What's unclear: Whether to use `TELEGRAM_BOT_TOKEN` as the secret or a separate `ADMIN_SESSION_SECRET` env var
   - Recommendation: Use a dedicated `ADMIN_SESSION_SECRET` env var with a secure random default generated at startup and logged as a warning if not set. Using the bot token directly as session secret ties two unrelated secrets together.

2. **Token regeneration on `/webtoken`**
   - What we know: Fallback token should be sendable via Telegram command
   - What's unclear: Should `/webtoken` generate a new token each call (invalidating previous) or return the current one?
   - Recommendation: Generate new token each call — simple, predictable. The previous token becomes invalid. Document this in startup logs.

3. **Search-per-day sparkline data resolution**
   - What we know: `db.get_stats_since()` exists and returns counts since a datetime
   - What's unclear: Computing 7 daily buckets requires 7 separate DB queries; no single `get_daily_counts()` function exists yet
   - Recommendation: Add `db.get_daily_search_counts(days=7)` helper using a GROUP BY date query to return the 7 data points in one query. This is a small DB addition, not a service layer addition.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_admin_dashboard.py -x` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ADMN-01 | `/admin/` returns 200 when authenticated | integration | `pytest tests/test_admin_dashboard.py::test_home_requires_auth -x` | Wave 0 |
| ADMN-01 | `/admin/` returns 302 to login when not authenticated | integration | `pytest tests/test_admin_dashboard.py::test_unauthenticated_redirects -x` | Wave 0 |
| ADMN-01 | Telegram Login Widget HMAC verification passes valid data | unit | `pytest tests/test_admin_dashboard.py::test_verify_telegram_login_valid -x` | Wave 0 |
| ADMN-01 | HMAC verification rejects tampered data | unit | `pytest tests/test_admin_dashboard.py::test_verify_telegram_login_tampered -x` | Wave 0 |
| ADMN-01 | HMAC verification rejects stale auth_date (>24h) | unit | `pytest tests/test_admin_dashboard.py::test_verify_telegram_login_stale -x` | Wave 0 |
| ADMN-01 | Fallback token verifies correctly and expires after 24h | unit | `pytest tests/test_admin_dashboard.py::test_fallback_token -x` | Wave 0 |
| ADMN-02 | Stats page renders stat cards with correct counts | integration | `pytest tests/test_admin_dashboard.py::test_stats_page -x` | Wave 0 |
| ADMN-02 | Partial stats endpoint returns HTML fragment (not full page) | integration | `pytest tests/test_admin_dashboard.py::test_partial_stats -x` | Wave 0 |
| ADMN-02 | Sparkline SVG generation produces valid polyline for 7-day data | unit | `pytest tests/test_admin_dashboard.py::test_sparkline_generation -x` | Wave 0 |
| ADMN-03 | Keys page lists all 18 API key groups | integration | `pytest tests/test_admin_dashboard.py::test_keys_page -x` | Wave 0 |
| ADMN-03 | Save key action stores value and returns updated card fragment | integration | `pytest tests/test_admin_dashboard.py::test_save_key -x` | Wave 0 |
| ADMN-03 | Key values are NOT rendered in HTML (masked) | integration | `pytest tests/test_admin_dashboard.py::test_key_values_masked -x` | Wave 0 |
| ADMN-04 | Tags page lists all tags with counts | integration | `pytest tests/test_admin_dashboard.py::test_tags_page -x` | Wave 0 |
| ADMN-04 | Activate tag action returns updated tag row fragment | integration | `pytest tests/test_admin_dashboard.py::test_activate_tag -x` | Wave 0 |
| ADMN-04 | Add tag form creates tag and returns row fragment | integration | `pytest tests/test_admin_dashboard.py::test_add_tag -x` | Wave 0 |
| ADMN-05 | Settings page renders all settings with current values | integration | `pytest tests/test_admin_dashboard.py::test_settings_page -x` | Wave 0 |
| ADMN-05 | Update setting stores value and returns updated row fragment | integration | `pytest tests/test_admin_dashboard.py::test_update_setting -x` | Wave 0 |
| ADMN-06 | Health page renders provider health table | integration | `pytest tests/test_admin_dashboard.py::test_health_page -x` | Wave 0 |
| ADMN-06 | Partial health endpoint returns HTMX-compatible fragment | integration | `pytest tests/test_admin_dashboard.py::test_partial_health -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_admin_dashboard.py -x`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_admin_dashboard.py` — covers all ADMN-01 through ADMN-06 test cases listed above
- [ ] `admin_dashboard/__init__.py` — module stub (empty, exports router)
- [ ] `admin_dashboard/router.py` — FastAPI APIRouter with all admin routes
- [ ] `admin_dashboard/auth.py` — Telegram Login Widget HMAC verification + fallback token
- [ ] `admin_dashboard/deps.py` — `require_admin()` FastAPI dependency
- [ ] `admin_dashboard/sparklines.py` — server-side SVG sparkline generator
- [ ] `admin_dashboard/templates/` directory — all HTML templates listed in architecture section

## Sources

### Primary (HIGH confidence)
- Telegram Login Widget official docs (https://core.telegram.org/widgets/login) — widget embed, data fields, HMAC verification algorithm
- FastAPI official templates docs (https://fastapi.tiangolo.com/advanced/templates/) — Jinja2Templates setup, url_for, TemplateResponse signature
- HTMX hx-trigger docs (https://htmx.org/attributes/hx-trigger/) — `every Ns` polling syntax, response code 286 stop-polling
- Project codebase: `admin_service.py`, `gateway.py`, `settings_store.py`, `database.py` — existing service API signatures

### Secondary (MEDIUM confidence)
- TestDriven.io FastAPI + HTMX tutorial (https://testdriven.io/blog/fastapi-htmx/) — HX-Request header detection, TemplateResponse fragment pattern
- FastAPI discussions #2630 (https://github.com/fastapi/fastapi/discussions/2630) — Jinja2 with routers in subdirectories, templates directory path resolution
- alexplescan.com SVG sparklines (https://alexplescan.com/posts/2023/07/08/easy-svg-sparklines/) — server-side SVG polyline pattern

### Tertiary (LOW confidence)
- WebSearch results on fastapi-csrf-protect — SameSite=Lax is sufficient for this admin-only use case; CSRF library not needed

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in project or bundled with existing dependencies
- Architecture: HIGH — gateway pattern established in Phase 1; admin_service API confirmed from source code
- Telegram auth: HIGH — verified against official Telegram docs
- HTMX patterns: HIGH — verified against official HTMX docs + TestDriven.io
- Sparklines: MEDIUM — pattern confirmed from community source; specific Python implementation is original but follows standard SVG polyline math
- Pitfalls: HIGH — SessionMiddleware placement and HTMX swap issues are known, reproducible patterns

**Research date:** 2026-03-14
**Valid until:** 2026-06-14 (stable libraries; Telegram Widget API does not change frequently)
