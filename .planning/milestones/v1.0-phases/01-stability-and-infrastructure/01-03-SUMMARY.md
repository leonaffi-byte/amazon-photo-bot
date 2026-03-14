---
phase: 01-stability-and-infrastructure
plan: "03"
subsystem: infra
tags: [fastapi, uvicorn, aiohttp, gateway, shortener, webhook, graceful-shutdown]

# Dependency graph
requires:
  - phase: 01-stability-and-infrastructure
    provides: timeout unification and error differentiation (01-01, 01-02)
provides:
  - Consolidated FastAPI gateway on port 8080 replacing three separate HTTP servers
  - gateway.py create_app() factory with ordered router mounting
  - shortener_routes.py FastAPI router for /{code} redirect and /stats/{code}
  - webhook_routes.py FastAPI router for /webhook/{platform} with set_adapters()
  - api_server.py APIRouter export at /api/v1/* for gateway mounting
  - Graceful shutdown with 10s in-flight task drain and cancellation
  - track_task() helper for registering tasks in _active_tasks set
affects: [all future phases using HTTP endpoints, Docker deployment, Phase 4 WhatsApp/webhook integration]

# Tech tracking
tech-stack:
  added: [uvicorn (gateway server), starlette.testclient]
  patterns: [FastAPI router composition, catch-all route ordering, in-flight task tracking]

key-files:
  created:
    - gateway.py
    - shortener_routes.py
    - webhook_routes.py
    - tests/test_gateway.py
    - tests/test_shutdown.py
  modified:
    - api_server.py
    - main.py
    - docker-compose.yml
    - Dockerfile

key-decisions:
  - "Shortener /{code} catch-all router is mounted LAST in gateway.py to avoid intercepting /health, /docs, /api/v1/*, /stats/*"
  - "api_server.py retains standalone FastAPI app for direct uvicorn invocation; new APIRouter export at prefix /api/v1 for gateway mounting"
  - "webhook_routes.py uses module-level set_adapters() to register adapters before app startup, avoiding circular imports"
  - "main.py _active_tasks set + track_task() helper tracks in-flight tasks; shutdown drains with 10s grace period before cancel"
  - "docker-compose.yml amazon-api service removed; all endpoints consolidated into amazon-bot on port 8080"
  - "Dockerfile healthcheck updated from Python no-op to HTTP GET /health for real liveness detection"

patterns-established:
  - "Router ordering: specific prefix routes (/api/v1, /webhook, /stats) always before catch-all (/{code})"
  - "Security middleware: headers applied globally at gateway level, not per-router"
  - "Task lifecycle: create_task() -> track_task() -> done_callback removes from set -> drain on shutdown"

requirements-completed: [INFR-01, STAB-04]

# Metrics
duration: 15min
completed: 2026-03-14
---

# Phase 1 Plan 3: Consolidated HTTP Gateway Summary

**Three HTTP servers (aiohttp shortener :8080, aiohttp webhooks :8081, FastAPI API :8001) unified into a single FastAPI/Uvicorn gateway on port 8080 with graceful in-flight task draining**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-14T00:24:00Z
- **Completed:** 2026-03-14T00:39:00Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Created `gateway.py` with `create_app()` factory that mounts all routers in correct order (API, webhooks, shortener catch-all last)
- Converted aiohttp shortener and webhook servers to FastAPI routers with identical behavior
- Extracted `APIRouter` from `api_server.py` enabling it to be embedded in the gateway while retaining standalone invocation
- Replaced dual aiohttp runners in `main.py` with single Uvicorn server; shutdown drains in-flight tasks with 10s grace period
- Removed `amazon-api` Docker service; gateway exposes only port 8080

## Task Commits

Each task was committed atomically:

1. **Task 1: Create FastAPI gateway with converted routes** - `158b64d` (feat)
2. **Task 2: Wire gateway into main.py with graceful shutdown** - `81771be` (feat)

**Plan metadata:** _(pending docs commit)_

## Files Created/Modified

- `gateway.py` - FastAPI app factory with ordered router mounting and security headers middleware
- `shortener_routes.py` - FastAPI router for `/{code}` redirect and `/stats/{code}`
- `webhook_routes.py` - FastAPI router for `/webhook/{platform}` with `set_adapters()`
- `api_server.py` - Added `APIRouter` export at `/api/v1` prefix alongside existing standalone app
- `main.py` - Replaced aiohttp runners with Uvicorn gateway; added `_active_tasks` + `track_task()`
- `docker-compose.yml` - Removed `amazon-api` service; single `amazon-bot` service on port 8080 with HTTP healthcheck
- `Dockerfile` - Updated healthcheck to `GET /health` instead of Python no-op
- `tests/test_gateway.py` - 20 integration tests covering routing, auth, security headers, webhook dispatch
- `tests/test_shutdown.py` - 8 tests covering task drain logic and `track_task()` helper

## Decisions Made

- The `/{code}` catch-all route must be the last router registered in `gateway.py` — this ensures `/health`, `/docs`, `/api/v1/*`, and `/stats/*` are never shadowed by the shortener.
- `api_server.py` retains its standalone `FastAPI()` app for `python api_server.py` backward compatibility. The new `router` export is used only when embedded in the gateway.
- Webhook adapters are registered via `set_adapters()` (module-level) rather than constructor injection, keeping the router importable without adapter instances.
- `track_task()` is a thin helper wrapping `create_task()` + done-callback removal, making task lifecycle explicit at call sites.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all tests passed on first run.

## User Setup Required

None - no external service configuration required. The consolidated gateway runs automatically when `python main.py` is started.

## Next Phase Readiness

- Single gateway port (8080) simplifies nginx proxy config and Docker networking
- Webhook routes are ready for Phase 4 WhatsApp/Instagram adapter implementation
- `/api/v1/*` routes are accessible at the consolidated endpoint
- Graceful shutdown ensures no request is orphaned during deployments

---
*Phase: 01-stability-and-infrastructure*
*Completed: 2026-03-14*
