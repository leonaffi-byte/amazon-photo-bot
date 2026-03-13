# Phase 1: Stability and Infrastructure - Research

**Researched:** 2026-03-14
**Domain:** Python async reliability, FastAPI consolidation, service extraction
**Confidence:** HIGH

## Summary

Phase 1 addresses 10 requirements across two categories: stability fixes (STAB-01 through STAB-07) and infrastructure consolidation (INFR-01 through INFR-03). The existing codebase is well-structured with clear patterns -- the work is primarily surgical fixes to existing code rather than new feature development. All changes stay within the established async-first Python stack.

The most significant piece of work is INFR-01 (server consolidation), which merges three separate HTTP servers (aiohttp shortener on port 8080, aiohttp webhooks on port 8081, FastAPI API on port 8001) into a single FastAPI application on port 8080. The remaining requirements are targeted bug fixes and refactors that follow existing codebase patterns.

**Primary recommendation:** Work bottom-up -- fix the atomic stability bugs first (timeouts, transactions, cache invalidation, photo validation, error messages), then do the structural changes (server consolidation, admin service extraction).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Merge ALL three HTTP servers into a single FastAPI application on port 8080
- Shortener server (aiohttp) -> FastAPI routes at `/{code}` redirect, `/stats/{code}`
- Webhook server (aiohttp) -> FastAPI routes at `/webhook/{platform}`
- API server (FastAPI, port 8001) -> FastAPI routes under `/api/v1/` with existing X-API-Key auth as route-specific middleware
- Single Uvicorn process, single port (8080), single middleware stack
- Docker config updated to expose only port 8080
- Silent fallback: when a vision provider fails and another succeeds, user sees nothing
- Total failure message: "We couldn't analyze your photo right now. Please try again in a few minutes."
- Search failure: don't mention Amazon by name
- Admin errors: keep at current level (provider name + admin panel link)
- Unified vision provider timeout: 30 seconds per individual provider call
- Total cap across all fallback attempts: 60 seconds
- Auto-fallback: on provider timeout, silently try next available provider within 60s total cap
- Unified search backend timeout: 15 seconds for all backends
- Remove conflicting dual-timeout pattern (60s client + 45s asyncio.wait_for -> single 30s)
- Full service layer extraction for ALL admin features into single `admin_service.py`
- Async native: all service functions are `async def`
- Telegram handlers become thin wrappers calling service layer
- Service functions return plain data (dicts, dataclasses) -- no Telegram types

### Claude's Discretion
- Specific Pillow offloading strategy for `_compress_image()` (asyncio.to_thread vs acceptable as-is)
- Database transaction consistency fixes (CSV import alignment with add_tag pattern)
- Settings cache invalidation improvements (disabled_models_cache explicit invalidation)
- Graceful shutdown edge case handling (in-flight vision API calls during SIGINT)
- Health tracking tuning (failure window, recovery cooldown values)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STAB-01 | All vision provider API calls enforce timeout (max 60s per provider) | Timeout unification: change `PROVIDER_TIMEOUT_SECONDS` from 60->30, remove conflicting `asyncio.wait_for(..., timeout=45)` in 7 provider files. Add 60s total cap in `analyse_image()`. |
| STAB-02 | Model health tracking resets failure counter after configurable time window | Already implemented via progressive health system in `providers/manager.py`. Needs tuning: verify `HEALTH_RECOVERY_COOLDOWN` default (currently 600s=10min), verify auto-recovery path in `_build_providers()`. |
| STAB-03 | Multi-step database operations wrapped in atomic transactions | CSV import (`import_tags_csv`) opens a separate connection instead of using persistent conn, and lacks `BEGIN IMMEDIATE`. Align with `add_tag` pattern. |
| STAB-04 | Graceful shutdown properly awaits all background tasks before exit | Current shutdown in `main.py` cancels cleanup tasks and adapters but does not await in-flight vision API calls. Need to track active analysis tasks. |
| STAB-05 | Photo size validated before sending to vision API | Already partially implemented in `bot.py:434` and `bot_core.py:934`. Needs: friendly MarkdownV2 message via `style.py`, consistent handling in both `bot.py` and `bot_core.py`. |
| STAB-06 | Settings/tag/model caches invalidated on admin changes | Caches exist (`_active_tag_cache`, `_disabled_models_cache`) with TTL. Cache is cleared on write in most places. Need explicit invalidation in `settings_store.set()` and verify all admin write paths clear relevant caches. |
| STAB-07 | Error messages specify which provider/backend failed (admin view) | Current error messages are generic. Per CONTEXT.md: admin gets provider name + admin panel link, regular users get friendly generic message. Changes in `style.py` and error handling in `bot.py`/`bot_core.py`. |
| INFR-01 | Consolidate 3 HTTP servers into single FastAPI gateway | Convert `shortener_server.py` (aiohttp) and `webhook_server.py` (aiohttp) to FastAPI routers, merge with `api_server.py`. Single Uvicorn on port 8080. |
| INFR-02 | Pillow CPU-bound operations offloaded to executor | `_compress_image()` in `bot.py:78` and `bot_core.py:67` uses synchronous Pillow. Wrap in `asyncio.to_thread()`. |
| INFR-03 | Admin service layer extracted from admin.py | `admin.py` has 30+ Telegram-coupled handler functions. Extract business logic into `admin_service.py` returning plain data. |
</phase_requirements>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| FastAPI | >=0.110.0 | HTTP gateway | Already used in `api_server.py`. Becomes the single server. |
| uvicorn | >=0.27.0 | ASGI server | Already a dependency. Will serve consolidated app. |
| aiosqlite | ~0.20.0 | Async SQLite | All DB operations. No changes needed. |
| python-telegram-bot | 20.7 | Telegram bot | PTB v20 async API. No changes needed. |
| Pillow | ~10.4.0 | Image processing | `_compress_image()`. Will be wrapped in `asyncio.to_thread()`. |
| aiohttp | ~3.10.0 | HTTP client (retained for outgoing requests) | Still needed for provider API calls, search backends. Removed as server. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | existing | Testing | Test infrastructure already in place |
| pytest-asyncio | existing | Async test support | `asyncio_mode = auto` configured in pytest.ini |

### No New Dependencies
This phase requires zero new packages. All work uses existing libraries.

## Architecture Patterns

### Current Architecture (3 servers)
```
main.py
  +-- PTB polling (Telegram)
  +-- aiohttp :8080 (shortener)
  +-- aiohttp :8081 (webhooks)
api_server.py
  +-- FastAPI/Uvicorn :8001 (Israel shipping API)
```

### Target Architecture (1 server)
```
main.py
  +-- PTB polling (Telegram)
  +-- FastAPI/Uvicorn :8080
       +-- /{code}              (shortener redirect)
       +-- /stats/{code}        (shortener stats)
       +-- /health              (unified health check)
       +-- /webhook/{platform}  (WhatsApp, Instagram, etc.)
       +-- /api/v1/...          (Israel shipping API, with X-API-Key middleware)
       +-- /docs                (FastAPI auto-docs, dev only)
```

### Pattern 1: FastAPI Router Extraction
**What:** Convert each aiohttp app into a FastAPI APIRouter
**When to use:** For shortener and webhook server consolidation

```python
# shortener_routes.py (converted from shortener_server.py)
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, PlainTextResponse

router = APIRouter()

@router.get("/health")
async def health():
    total = await db.get_short_link_count()
    return PlainTextResponse(f"OK -- {total} links stored")

@router.get("/{code}")
async def redirect(code: str, request: Request):
    # Validate
    code = code.split(".")[0][:16]
    if not code or not code.isalnum():
        raise HTTPException(404, "Link not found.")
    long_url = await db.get_long_url_by_code(code)
    if not long_url:
        raise HTTPException(404, "Link not found or expired.")
    # Log click async (fire-and-forget via background task)
    asyncio.create_task(db.log_click(...))
    return RedirectResponse(long_url, status_code=302)
```

### Pattern 2: Admin Service Layer Extraction
**What:** Separate business logic from Telegram UI handling
**When to use:** For every admin.py handler function

```python
# admin_service.py
from dataclasses import dataclass

@dataclass
class TagResult:
    tag_id: int
    tag_name: str
    is_active: bool

async def add_tag(tag: str, description: str, admin_id: int, make_active: bool = False) -> TagResult:
    """Pure business logic -- no Telegram types."""
    await db.add_tag(tag, description, admin_id, "Admin", make_active=make_active)
    ...
    return TagResult(...)

# admin.py (thin wrapper)
async def _handle_add_tag(update, context):
    result = await admin_service.add_tag(tag, desc, user_id)
    # Format for Telegram
    await update.message.reply_text(f"Tag {result.tag_name} added.")
```

### Pattern 3: Timeout Unification
**What:** Single timeout constant, single enforcement point
**When to use:** All provider API calls

```python
# providers/base.py
PROVIDER_TIMEOUT_SECONDS = 30  # Changed from 60

# In each provider's analyse() method:
# BEFORE (conflicting dual timeout):
#   client = AsyncOpenAI(timeout=httpx.Timeout(60))
#   response = await asyncio.wait_for(client.create(...), timeout=45)
#
# AFTER (single timeout):
#   client = AsyncOpenAI(timeout=httpx.Timeout(30))
#   response = await asyncio.wait_for(client.create(...), timeout=30)

# providers/manager.py -- total cap
async def analyse_image(...):
    deadline = asyncio.get_event_loop().time() + 60  # 60s total cap
    # Pass deadline to _safe_run so it can calculate remaining time
```

### Pattern 4: Pillow Offloading
**What:** Move synchronous Pillow operations off the async event loop
**When to use:** `_compress_image()` in bot.py and bot_core.py

```python
# Recommendation: use asyncio.to_thread()
async def _compress_image_async(raw: bytes) -> bytes:
    return await asyncio.to_thread(_compress_image, raw)
```

This is the simplest approach. `asyncio.to_thread()` runs the function in the default ThreadPoolExecutor. Pillow image operations on a single photo (resize + JPEG compress) typically take 10-50ms, so blocking is marginal but worth fixing for correctness.

### Anti-Patterns to Avoid
- **Dual timeouts:** Setting both client-level timeout (60s) AND asyncio.wait_for timeout (45s) creates unpredictable behavior. Use a single timeout value.
- **Separate database connections for related operations:** `import_tags_csv` opens its own `aiosqlite.connect()` instead of using the persistent connection via `_get_conn()`. This bypasses WAL mode and busy timeout settings.
- **Cache invalidation by TTL only:** Relying on 30-60s TTL means admin changes can take up to 60s to take effect. Explicit invalidation on write is needed.
- **Swallowing errors silently:** `bot_core.py:935` returns a generic error on oversized photos instead of the specific "photo too large" message.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP server consolidation | Custom request routing/dispatch | FastAPI APIRouter | Battle-tested, auto-docs, middleware support |
| Async timeout enforcement | Custom timer logic | `asyncio.wait_for()` | Standard library, handles cancellation correctly |
| CPU-bound offloading | Thread management | `asyncio.to_thread()` | Standard library (Python 3.9+), handles executor pool |
| Background task fire-and-forget | Untracked `asyncio.create_task()` | FastAPI `BackgroundTasks` or tracked task set | Prevents tasks from being garbage collected |

## Common Pitfalls

### Pitfall 1: FastAPI Route Ordering for Shortener
**What goes wrong:** The shortener catch-all `/{code}` route intercepts `/health`, `/docs`, `/webhook/...` etc.
**Why it happens:** FastAPI matches routes in declaration order but `/{code}` is a path parameter that matches everything.
**How to avoid:** Register the shortener router LAST, or use a regex constraint on the code parameter: `@router.get("/{code:path}")` with validation inside, or better: register all specific routes first, then the catch-all.
**Warning signs:** `/health` or `/docs` returning 404 or redirecting.

### Pitfall 2: aiohttp-to-FastAPI Middleware Translation
**What goes wrong:** aiohttp middleware signature (`async def middleware(request, handler)`) is different from FastAPI/Starlette middleware.
**Why it happens:** Different ASGI vs aiohttp middleware patterns.
**How to avoid:** Use FastAPI's `@app.middleware("http")` decorator or Starlette `BaseHTTPMiddleware`. The security headers middleware from shortener_server.py needs rewriting.
**Warning signs:** Missing security headers after migration.

### Pitfall 3: Graceful Shutdown with In-Flight Vision Calls
**What goes wrong:** SIGINT/SIGTERM arrives while `analyse_image()` is running. The asyncio.gather inside it gets cancelled, leaving HTTP connections half-open.
**Why it happens:** `main.py` shutdown cancels cleanup tasks but doesn't track/await active analysis tasks.
**How to avoid:** Maintain a set of active analysis tasks. On shutdown, give them a grace period (e.g. 10s) before cancellation. Use `asyncio.shield()` sparingly.
**Warning signs:** "Connection reset" errors in logs during shutdown.

### Pitfall 4: Database Connection Inconsistency
**What goes wrong:** `import_tags_csv` opens a fresh `aiosqlite.connect(DB_PATH)` instead of using `_get_conn()`. This creates a second connection that doesn't have WAL mode or busy_timeout configured.
**Why it happens:** Function was likely added later without following the established pattern.
**How to avoid:** Always use `_get_conn()` context manager. Use `BEGIN IMMEDIATE` for multi-step operations.
**Warning signs:** "database is locked" errors during concurrent CSV import + bot operations.

### Pitfall 5: PTB Application Lifecycle vs Uvicorn
**What goes wrong:** PTB's `Application.run_polling()` wants to own the event loop. Running it alongside Uvicorn requires careful lifecycle management.
**Why it happens:** Both PTB and Uvicorn want to be the "main" loop owner.
**How to avoid:** Current approach (PTB polling + separate aiohttp server) already handles this. For Uvicorn: use `uvicorn.Server` with `config.setup_event_loop = False` and start it manually in the existing asyncio loop, OR use the lifespan pattern.
**Warning signs:** "Event loop is already running" errors.

### Pitfall 6: Cache Invalidation Race Conditions
**What goes wrong:** Setting `_active_tag_cache = None` before the DB write completes means a concurrent read could re-populate the cache with stale data.
**Why it happens:** The invalidation happens at the start of the write operation, not after it commits.
**How to avoid:** Invalidate AFTER the commit, not before. Or better: set the cache to the new value after successful commit.
**Warning signs:** Admin changes sometimes don't take effect until TTL expires.

## Code Examples

### Consolidated FastAPI App Factory

```python
# gateway.py -- single FastAPI application
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

def create_app() -> FastAPI:
    app = FastAPI(title="Amazon Photo Bot", docs_url="/docs")

    # Security headers middleware (replaces aiohttp middleware)
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # Mount routers in order (specific before catch-all)
    from api_routes import router as api_router
    from webhook_routes import router as webhook_router
    from shortener_routes import router as shortener_router

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(webhook_router)
    app.include_router(shortener_router)  # LAST -- has /{code} catch-all

    return app
```

### Starting Uvicorn in Existing Event Loop

```python
# In main.py run()
import uvicorn

gateway_app = create_app()
uvi_config = uvicorn.Config(
    gateway_app,
    host="0.0.0.0",
    port=8080,
    log_level="info",
    # Critical: don't let uvicorn create its own loop
    loop="none",
)
server = uvicorn.Server(uvi_config)
# Start server as a background task (does not block)
server_task = asyncio.create_task(server.serve())
```

### Timeout Unification in Provider

```python
# Example: openai_provider.py AFTER fix
async def analyse(self, image_bytes, context_hint=None):
    t0 = time.monotonic()
    # Single timeout -- matches PROVIDER_TIMEOUT_SECONDS
    response = await asyncio.wait_for(
        self._client.chat.completions.create(...),
        timeout=PROVIDER_TIMEOUT_SECONDS,  # 30s, same as client timeout
    )
    ...
```

### CSV Import Transaction Fix

```python
# database.py -- import_tags_csv AFTER fix
async def import_tags_csv(csv_data: str, imported_by: int) -> dict[str, int]:
    global _active_tag_cache
    _active_tag_cache = None  # Invalidate before (will be re-set after commit)

    reader = csv.DictReader(io.StringIO(csv_data))
    imported = skipped = errors = 0
    now = datetime.now(timezone.utc).isoformat()

    async with _get_conn() as db:  # Use persistent connection
        await db.execute("BEGIN IMMEDIATE")  # Atomic transaction
        try:
            for row in reader:
                # ... same logic but inside transaction ...
                pass
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    _active_tag_cache = None  # Invalidate after commit too
    return {"imported": imported, "skipped": skipped, "errors": errors}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `asyncio.wait_for` + separate client timeout | Single unified timeout | Best practice | Prevents confusing dual-timeout behavior |
| Separate aiohttp + FastAPI servers | Single FastAPI with routers | FastAPI matured ~2023 | Simpler deployment, single port |
| `asyncio.get_event_loop().run_in_executor()` | `asyncio.to_thread()` | Python 3.9 | Cleaner API for CPU offloading |

**Deprecated/outdated:**
- aiohttp as web server when FastAPI is already in the stack -- unnecessary complexity
- Separate Docker services for bot and API when they share the same database file

## Open Questions

1. **Uvicorn startup in existing asyncio loop**
   - What we know: Uvicorn's `Server.serve()` can run as a coroutine in an existing loop
   - What's unclear: Interaction with PTB's polling loop and signal handling on Windows
   - Recommendation: Test `uvicorn.Server` with `loop="none"` config. Fall back to `hypercorn` if issues arise. The current code already handles Windows signal handler limitations (line 240 of main.py).

2. **Admin service extraction scope**
   - What we know: admin.py has 30+ handler functions tightly coupled to Telegram
   - What's unclear: Exact grouping of service functions
   - Recommendation: Group by domain: keys, tags, settings, health, stats. Each group is a set of async functions in `admin_service.py`. Keep it as one module (not a package) since the admin panel is not that large.

3. **Pillow offloading necessity**
   - What we know: `_compress_image()` does PIL.Image.open, resize, JPEG save -- typically 10-50ms for a phone photo
   - What's unclear: Whether this actually causes user-visible latency
   - Recommendation: Use `asyncio.to_thread()` -- it's a one-line change and eliminates any risk of blocking the event loop during concurrent requests. Low effort, no downside.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `pytest.ini` (asyncio_mode = auto) |
| Quick run command | `pytest tests/ -x --timeout=30` |
| Full suite command | `pytest tests/` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAB-01 | Vision provider timeout at 30s, total cap 60s | unit | `pytest tests/test_providers_manager.py -x -k timeout` | Partially (test_providers_manager.py exists, timeout tests may need adding) |
| STAB-02 | Health tracking resets after time window | unit | `pytest tests/test_progressive_health.py -x` | Yes |
| STAB-03 | CSV import uses atomic transactions | unit | `pytest tests/test_database.py -x -k import` | Partially (test_database.py exists) |
| STAB-04 | Graceful shutdown awaits in-flight tasks | integration | `pytest tests/test_shutdown.py -x` | No -- Wave 0 |
| STAB-05 | Oversized photo returns friendly message | unit | `pytest tests/test_bot.py -x -k "photo_size or oversized"` | Partially (test_bot.py exists, specific test may need adding) |
| STAB-06 | Cache invalidated on admin write | unit | `pytest tests/test_settings_store.py -x` | Yes |
| STAB-07 | Error messages differ for admin vs user | unit | `pytest tests/test_style.py -x -k error` | Partially (test_style.py exists) |
| INFR-01 | Single FastAPI gateway serves all routes | integration | `pytest tests/test_gateway.py -x` | No -- Wave 0 |
| INFR-02 | Pillow runs in thread executor | unit | `pytest tests/test_bot.py -x -k compress` | No -- Wave 0 |
| INFR-03 | Admin service returns plain data | unit | `pytest tests/test_admin_service.py -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x --timeout=30`
- **Per wave merge:** `pytest tests/`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_gateway.py` -- covers INFR-01 (consolidated FastAPI server routes)
- [ ] `tests/test_shutdown.py` -- covers STAB-04 (graceful shutdown with in-flight tasks)
- [ ] `tests/test_admin_service.py` -- covers INFR-03 (service layer returns plain data)
- [ ] Add timeout-specific tests to `tests/test_providers_manager.py` -- covers STAB-01
- [ ] Add oversized photo test to `tests/test_bot.py` -- covers STAB-05
- [ ] Add error message admin/user differentiation tests to `tests/test_style.py` -- covers STAB-07

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis: `providers/base.py`, `providers/manager.py`, `main.py`, `database.py`, `bot.py`, `bot_core.py`, `admin.py`, `shortener_server.py`, `webhook_server.py`, `api_server.py`, `config.py`, `settings_store.py`, `style.py`
- `requirements.txt` -- verified all dependency versions
- `docker-compose.yml` -- verified current multi-service architecture
- `pytest.ini` and `tests/conftest.py` -- verified test infrastructure

### Secondary (MEDIUM confidence)
- FastAPI APIRouter pattern -- well-documented in FastAPI docs, standard pattern
- `asyncio.to_thread()` -- Python standard library since 3.9, project uses 3.11+
- Uvicorn programmatic startup -- documented in uvicorn docs

### Tertiary (LOW confidence)
- None -- all findings verified against codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- patterns verified against existing codebase
- Pitfalls: HIGH -- identified from direct code analysis of actual bugs
- Validation: MEDIUM -- test file existence verified, specific test coverage needs verification

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable codebase, no fast-moving dependencies)
