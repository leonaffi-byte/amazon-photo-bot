---
phase: 05-public-web-application
plan: "01"
subsystem: web_app
tags: [fastapi, htmx, sse, jinja2, slowapi, sqlite]
dependency_graph:
  requires:
    - gateway.py (route mounting)
    - database.py (_get_conn, init_db)
    - providers/manager.py (analyse_image)
    - amazon_search.py (search_amazon)
    - image_annotator.py (annotate_products)
    - scheduler.py (_scheduler_loop)
  provides:
    - "GET / — homepage with upload zone"
    - "POST /upload — photo upload endpoint with rate limiting"
    - "GET /stream/{session_id} — SSE 4-stage progress stream"
    - "GET /search/{short_id} — result page stub"
    - "GET /search/{short_id}/image — annotated photo BLOB"
    - "web_searches SQLite table with 30-day TTL"
    - "purge_expired() daily job in scheduler"
  affects:
    - gateway.py (new route registered before shortener catch-all)
    - scheduler.py (daily purge_expired call added)
    - database.py (web_searches table + indexes added)
tech_stack:
  added:
    - slowapi (rate limiting, keyed by remote IP)
  patterns:
    - "SSE via FastAPI StreamingResponse + async generator"
    - "HTMX hx-post + sse-connect for progressive upload UX"
    - "Lazy imports inside SSE generator to avoid circular imports"
    - "TemplateResponse(request, name, context) — Starlette 2.x API"
key_files:
  created:
    - web_app/__init__.py
    - web_app/deps.py
    - web_app/router.py
    - web_app/search_store.py
    - web_app/templates/web_base.html
    - web_app/templates/home.html
    - web_app/templates/partials/progress.html
    - tests/test_web_app.py
  modified:
    - database.py (web_searches table + 2 indexes)
    - gateway.py (web_app router + SlowAPI exception handler)
    - scheduler.py (purge_expired daily job)
    - requirements.txt (slowapi added)
decisions:
  - "Lazy imports inside SSE generator (from providers.manager import analyse_image) to avoid circular import between web_app and providers at module load time"
  - "TemplateResponse uses new Starlette 2.x API: request as first arg, no request in context dict"
  - "TestResultImage and TestOGTags tests initialize DB directly then create app with mocked init_db to avoid table-not-found errors from conftest fixture interaction"
metrics:
  duration_min: 27
  completed_date: "2026-03-14"
  tasks_completed: 3
  files_created: 8
  files_modified: 4
---

# Phase 5 Plan 1: Web App Foundation Summary

**One-liner:** FastAPI web_app module with HTMX+SSE upload-to-stream-to-persist pipeline, SlowAPI rate limiting, and Jinja2 templates for the public product search UI.

## What Was Built

### web_app module (8 new files)

- `web_app/__init__.py` — Module init exporting `router` (mirrors admin_dashboard pattern)
- `web_app/deps.py` — SlowAPI `Limiter(key_func=get_remote_address)` for IP-based rate limiting
- `web_app/router.py` — 5 routes: homepage, upload, SSE stream, result stub, result image
- `web_app/search_store.py` — 3 async DB functions: `save_web_search`, `get_web_search`, `purge_expired`
- `web_app/templates/web_base.html` — Public base with Tailwind CDN, HTMX 2.0.8, htmx-ext-sse 2.2.4, RTL support, affiliate disclosure
- `web_app/templates/home.html` — Upload hero with drag-drop, "How it works" 3-step section
- `web_app/templates/partials/progress.html` — SSE listener with thumbnail, progress `<ol>`, done redirect
- `tests/test_web_app.py` — 20 tests covering all routes and DB functions

### Modified files (4)

- `database.py` — Added `web_searches` table with `short_id`, `photo_hash`, `annotated_photo` BLOB, `results_json`, `products_json`, `expires_at`; plus 2 indexes
- `gateway.py` — Route step 4: web_app router + SlowAPI state/exception handler inserted before shortener catch-all (step 5)
- `scheduler.py` — Added `purge_expired()` call in `_scheduler_loop` at REPORT_HOUR alongside daily reports (lazy import guards against ImportError)
- `requirements.txt` — Added `slowapi`

## SSE Pipeline (4-stage flow)

```
POST /upload → validates → stores in _pending[session_id] → returns progress.html
GET /stream/{session_id} → AsyncGenerator:
  1. yield progress: "Analyzing photo..."
  2. analyse_image(bytes) → winner.to_product_info_list() → products
  3. yield progress: "Found N products..."
  4. search_amazon(product) × N (asyncio.gather) → all_results
  5. yield progress: "Searching Amazon..."
  6. annotate_products(bytes, products) in executor → annotated_bytes
  7. save_web_search(...) → short_id
  8. yield done: "/search/{short_id}" → browser redirects
```

## Test Results

20/20 tests pass. Test classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| TestHomePage | 6 | Viewport, RTL/LTR, affiliate disclosure, HTMX CDN |
| TestUpload | 4 | Valid PNG, 413 oversized, 400 non-image, session in HTML |
| TestSSE | 2 | 4-stage stream stages, invalid session error |
| TestSearchStore | 4 | save+get, get_nonexistent, purge_expired, purge leaves fresh |
| TestResultImage | 2 | 200 with image/jpeg, 404 for missing |
| TestOGTags | 2 | Result page 200, expired result 410 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TemplateResponse deprecated API**
- **Found during:** Task 3 (test warnings)
- **Issue:** Starlette 2.x requires `TemplateResponse(request, name, context)` not `TemplateResponse(name, {"request": request, ...})`
- **Fix:** Updated both TemplateResponse calls in router.py to new API
- **Files modified:** web_app/router.py
- **Commit:** 23e3e36

**2. [Rule 1 - Bug] Test `_pending` access via wrong import path**
- **Found during:** Task 3 (test failure)
- **Issue:** `from web_app import router` returns APIRouter object, not the module; `importlib.import_module("web_app.router")` needed to access module-level `_pending` dict
- **Fix:** Used `importlib.import_module` in test `_seed_pending` helper
- **Commit:** 23e3e36

**3. [Rule 1 - Bug] SSE mock patch path**
- **Found during:** Task 3 (test failure)
- **Issue:** Lazy imports inside SSE generator (`from providers.manager import analyse_image`) create local bindings; patching `web_app.router.analyse_image` doesn't intercept them
- **Fix:** Patch at source module level: `providers.manager.analyse_image`, `amazon_search.search_amazon`, `image_annotator.annotate_products`
- **Commit:** 23e3e36

**4. [Rule 2 - Missing] DB init in TestResultImage/TestOGTags**
- **Found during:** Task 3 (sqlite3.OperationalError: no such table)
- **Issue:** Tests combining `client` fixture (mocked init_db) with real DB operations failed because table was never created
- **Fix:** Tests call `await database.init_db()` directly, then create a fresh client with mocked init_db
- **Commit:** 23e3e36

## Self-Check: PASSED

All 8 created files verified on disk. Both commits (4e449a4, 23e3e36) confirmed in git log. 20/20 tests pass.
