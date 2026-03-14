# Phase 1: Stability and Infrastructure - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix critical bugs (timeouts, health tracking, transactions, shutdown, photo validation, caching, error messages) and consolidate all HTTP services into a single FastAPI gateway. Extract admin service layer for reuse by future web dashboard. The existing Telegram bot must run reliably with no hung requests, no stale caches, and no data corruption after this phase.

</domain>

<decisions>
## Implementation Decisions

### Server Consolidation
- Merge ALL three HTTP servers into a single FastAPI application on port 8080
- Shortener server (aiohttp) → FastAPI routes at `/{code}` redirect, `/stats/{code}`
- Webhook server (aiohttp) → FastAPI routes at `/webhook/{platform}`
- API server (FastAPI, port 8001) → FastAPI routes under `/api/v1/` with existing X-API-Key auth as route-specific middleware
- Single Uvicorn process, single port (8080), single middleware stack
- Docker config updated to expose only port 8080

### Error Experience
- Silent fallback: when a vision provider fails and another succeeds, user sees nothing — just gets results
- Total failure message: "We couldn't analyze your photo right now. Please try again in a few minutes." — friendly, no technical details
- Search failure: don't mention Amazon by name — "We couldn't find matching products. Try a clearer photo or a different angle."
- Admin errors: keep at current level (provider name + admin panel link, no error codes or response snippets)

### Timeout & Retry Behavior
- Unified vision provider timeout: 30 seconds per individual provider call
- Total cap across all fallback attempts: 60 seconds (if first provider takes 30s, second only gets 30s)
- Auto-fallback: on provider timeout, silently try next available provider within the 60s total cap
- Unified search backend timeout: 15 seconds for all backends (PA-API, RapidAPI, DataForSEO, Playwright)
- Remove conflicting dual-timeout pattern (60s client + 45s asyncio.wait_for → single 30s)

### Admin Service Extraction
- Full service layer extraction — ALL admin features (keys, tags, settings, health, stats)
- Single module: `admin_service.py` with operations grouped by section
- Async native: all service functions are `async def` (consistent with aiosqlite and async-first codebase)
- Telegram handlers in `admin.py` become thin wrappers that call service layer and format results as Telegram messages
- Service functions return plain data (dicts, dataclasses) — no Telegram types (InlineKeyboardMarkup, etc.)

### Claude's Discretion
- Specific Pillow offloading strategy for `_compress_image()` (asyncio.to_thread vs acceptable as-is)
- Database transaction consistency fixes (CSV import alignment with add_tag pattern)
- Settings cache invalidation improvements (disabled_models_cache explicit invalidation)
- Graceful shutdown edge case handling (in-flight vision API calls during SIGINT)
- Health tracking tuning (failure window, recovery cooldown values)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `providers/base.py`: `PROVIDER_TIMEOUT_SECONDS` constant and `VisionProvider` abstract class — timeout unification point
- `providers/manager.py`: 3-tier health tracking (healthy/degraded/disabled) with auto-recovery — already well-implemented, needs tuning not rewrite
- `database.py`: TTL-based caches for active_tag (60s) and disabled_models (30s) — invalidation pattern exists, needs extension
- `api_server.py`: FastAPI app with auth middleware and rate limiting — can be adapted as router for consolidated gateway
- `admin_models.py`: Pydantic models for admin UI — can be reused in service layer returns

### Established Patterns
- Async-first throughout: aiohttp sessions, aiosqlite, asyncio.wait_for — new code must follow
- Module-level loggers: `logger = logging.getLogger(__name__)` everywhere
- Config priority: DB > .env > defaults via settings_store.py + config.py
- `BEGIN IMMEDIATE` for multi-step DB transactions (add_tag, set_active_tag)

### Integration Points
- `main.py` startup: currently creates separate aiohttp runners for shortener and webhooks — needs to create single FastAPI/Uvicorn server instead
- `bot.py` error handling: calls `style.py` format functions — error message changes go in style.py
- `admin.py` handlers: 30+ Telegram-specific functions — each needs thin wrapper conversion after service extraction
- `shortener_server.py` and `webhook_server.py`: aiohttp apps to be converted to FastAPI routers

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-stability-and-infrastructure*
*Context gathered: 2026-03-14*
