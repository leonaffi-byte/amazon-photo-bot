---
phase: 03-web-admin-dashboard
plan: "01"
subsystem: admin-dashboard
tags: [fastapi, jinja2, htmx, tailwind, auth, session, sparklines]
dependency_graph:
  requires:
    - gateway.py
    - admin_service.py
    - database.py
    - config.py
  provides:
    - admin_dashboard/ module (router, auth, deps, sparklines)
    - /admin/* routes (login, dashboard, partials)
    - SessionMiddleware on root app
    - get_daily_search_counts() in database.py
  affects:
    - gateway.py (SessionMiddleware + admin router added)
    - config.py (ADMIN_SESSION_SECRET, TELEGRAM_BOT_USERNAME added)
    - requirements.txt (python-multipart, itsdangerous, jinja2 added)
tech_stack:
  added:
    - starlette SessionMiddleware (session cookie management)
    - Jinja2Templates (FastAPI template rendering)
    - python-multipart (form data parsing for POST /auth/token)
    - itsdangerous (Starlette session signing dependency)
    - HTMX 2.0.3 (CDN, HTMX polling for stat cards and health)
    - Tailwind CSS CDN (utility-first styling)
  patterns:
    - TDD: tests written before implementation (RED -> GREEN)
    - HMAC-SHA256 with SHA256-derived secret key (Telegram Login Widget spec)
    - Module-level state for fallback token (_fallback_token, _token_issued_at)
    - HTMX outerHTML swap pattern for self-refreshing partials
    - FastAPI Depends(require_admin) for session enforcement
key_files:
  created:
    - admin_dashboard/__init__.py
    - admin_dashboard/auth.py
    - admin_dashboard/deps.py
    - admin_dashboard/sparklines.py
    - admin_dashboard/router.py
    - admin_dashboard/templates/base.html
    - admin_dashboard/templates/login.html
    - admin_dashboard/templates/home.html
    - admin_dashboard/templates/partials/stat_cards.html
    - admin_dashboard/templates/partials/provider_health.html
    - tests/test_admin_web.py
  modified:
    - database.py (added get_daily_search_counts)
    - gateway.py (SessionMiddleware + admin router mounting)
    - config.py (ADMIN_SESSION_SECRET, TELEGRAM_BOT_USERNAME)
    - requirements.txt (python-multipart, itsdangerous, jinja2)
decisions:
  - "Fallback token uses module-level state (not DB) for zero-dependency startup auth"
  - "require_admin raises HTTPException(307) rather than returning RedirectResponse (FastAPI dependency pattern)"
  - "outerHTML hx-swap used on partial wrappers so HTMX polling attributes survive DOM replacement"
  - "ADMIN_SESSION_SECRET defaults to random on startup with warning log (sessions won't survive restart)"
  - "TestDailySearchCounts tests call init_db() directly — consistent with existing test patterns"
metrics:
  duration_minutes: 18
  completed_date: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
  files_created: 11
  files_modified: 4
---

# Phase 3 Plan 1: Admin Dashboard Scaffold and Auth Summary

**One-liner:** FastAPI admin dashboard with Telegram Login Widget HMAC auth, fallback token login, HTMX-polled stat cards and provider health, and server-side SVG sparklines.

## What Was Built

### Task 1 — Module scaffold, auth system, database helper, and test stubs (commit: 62d0277)

Created the `admin_dashboard` Python package with all core non-template logic:

- **`auth.py`**: `verify_telegram_login` (HMAC-SHA256 per Telegram widget spec, < 24h freshness, no input mutation) + `generate_fallback_token` / `verify_fallback_token` (module-level secrets, 24h TTL, constant-time compare)
- **`deps.py`**: `require_admin()` FastAPI dependency — reads `admin_user_id` from session cookie, raises `HTTPException(307)` to redirect unauthenticated users to `/admin/login`
- **`sparklines.py`**: `points_to_svg` with flat-line fallback for empty/all-zero data; `build_7day_sparkline` async wrapper
- **`database.py`**: Added `get_daily_search_counts(days)` — GROUP BY `date(searched_at)`, fills zeros for missing days, returns oldest-to-newest `list[int]`
- **`tests/test_admin_web.py`**: 17 active tests (auth HMAC, fallback token, sparklines, DB counts) + 14 skipped integration stubs for ADMN-01 through ADMN-06

### Task 2 — Router, templates, SessionMiddleware, gateway wiring (commit: f8725bd)

- **`router.py`**: 8 routes — `/login`, `/auth/callback` (Telegram), `/auth/token` (fallback form POST), `/logout`, `/` (home), `/partials/stats`, `/partials/health`, `/partials/sidebar-toggle`
- **`base.html`**: Tailwind CDN + HTMX CDN, fixed sidebar with nav links, mobile hamburger with HTMX toggle, responsive main content area
- **`login.html`**: Telegram Login Widget (conditional on `bot_username`), OR divider, fallback token form, error message display
- **`home.html`**: Two HTMX polling divs — stats (60s) and health (30s) with `outerHTML` swap for self-refreshing partial attributes
- **`partials/stat_cards.html`**: Wraps in `id="stat-cards"` div with HTMX attrs so polling survives swap; 6-card grid (total users, total searches, today searches, today users, israel filter, total clicks)
- **`partials/provider_health.html`**: Wraps in `id="provider-health"` div with HTMX attrs; table with color-coded status (green=healthy, yellow=degraded, red=disabled)
- **`gateway.py`**: SessionMiddleware added to root app (before routes), admin router mounted at `/admin` before shortener catch-all
- **`config.py`**: `ADMIN_SESSION_SECRET` (env or random with warning), `TELEGRAM_BOT_USERNAME`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing functionality] Database tests needed init_db() call**
- **Found during:** Task 1 verification
- **Issue:** `TestDailySearchCounts` tests failed with `OperationalError: no such table: search_logs` because the DB tables weren't initialized in the test context
- **Fix:** Added `await database.init_db()` before calling `get_daily_search_counts` in each DB test, consistent with the pattern other test files use
- **Files modified:** `tests/test_admin_web.py`
- **Commit:** 62d0277 (included in Task 1 commit)

**2. [Rule 3 - Blocking issue] __init__.py import of router.py caused test failures before router existed**
- **Found during:** Task 1 RED phase
- **Issue:** `admin_dashboard/__init__.py` imported from `router.py` which didn't exist yet, blocking auth and sparkline tests from running
- **Fix:** Created a minimal `router.py` stub as part of Task 1 (implementation was then completed in Task 2)
- **Files modified:** `admin_dashboard/router.py`
- **Commit:** 62d0277

### Out of Scope (Pre-existing)

`test_malformed_responses.py` has a pre-existing timeout on Windows (verified by checking without my changes). Logged, not fixed per scope boundary rules.

## Self-Check: PASSED

### Files Exist

All 11 created files confirmed present on disk.

### Commits Verified

- `62d0277` — feat(03-01): admin_dashboard module scaffold, auth system, and database helper
- `f8725bd` — feat(03-01): router, templates, SessionMiddleware, and gateway wiring

### Test Results

17 active tests pass, 14 integration stubs skipped (as designed — Wave 1 stubs for future integration tests).
