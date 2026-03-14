---
phase: 01-stability-and-infrastructure
plan: 01
subsystem: providers,search-backends,style
tags: [timeouts,error-messages,health-tracking,tdd]
dependency_graph:
  requires: []
  provides: [STAB-01, STAB-02, STAB-07]
  affects: [providers/manager.py, providers/base.py, search_backends/base.py, style.py]
tech_stack:
  added: []
  patterns: [deadline-aware-timeout, unified-constant, admin-vs-user-error-differentiation]
key_files:
  created:
    - tests/test_providers_manager.py (extended)
    - tests/test_search_timeout.py
  modified:
    - providers/base.py
    - providers/manager.py
    - providers/openai_provider.py
    - providers/anthropic_provider.py
    - providers/gemini_provider.py
    - providers/openai_compat_provider.py
    - providers/azure_openai_provider.py
    - providers/openrouter_provider.py
    - search_backends/base.py
    - search_backends/paapi_backend.py
    - search_backends/rapidapi_backend.py
    - search_backends/dataforseo_backend.py
    - search_backends/playwright_backend.py
    - search_backends/brightdata_backend.py
    - style.py
    - bot.py
    - tests/test_style.py
decisions:
  - "PROVIDER_TIMEOUT_SECONDS reduced from 60 to 30 to eliminate hung providers faster"
  - "60s total deadline in analyse_image prevents cascading slow providers from blocking indefinitely"
  - "Manager is single timeout enforcement point; provider files removed inner asyncio.wait_for calls"
  - "SEARCH_TIMEOUT_SECONDS = 15 unified across all 5 backends (some were 10, some 30)"
  - "error_no_results user message deliberately omits 'Amazon' to avoid brand confusion"
  - "error_no_backend and error_no_results gained is_admin parameter to match existing pattern"
metrics:
  duration_minutes: 14
  completed_date: "2026-03-14"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 18
---

# Phase 1 Plan 1: Timeout Unification and Error Differentiation Summary

Unified vision provider timeouts (30s per call, 60s total), unified search backend timeouts (15s), and differentiated error messages between admin and regular users.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Unify provider timeouts and add total deadline cap | 0dbb9ea |
| 2 | Unify search backend timeouts to 15 seconds | 6594c1a |
| 3 | Differentiate error messages for admin vs regular users | 8f27542 |

## What Was Built

### Task 1: Provider Timeout Unification

- `providers/base.py`: `PROVIDER_TIMEOUT_SECONDS` reduced from 60 to 30. Added documentation comment explaining the two-layer timeout strategy.
- `providers/manager.py`: Added `deadline = asyncio.get_event_loop().time() + 60` at the start of `analyse_image()`. Modified `_safe_run` to compute `remaining = max(1.0, deadline - loop.time())` and use `asyncio.wait_for(..., timeout=min(PROVIDER_TIMEOUT_SECONDS, remaining))` as the single timeout enforcement point.
- All 7 provider files (`openai_provider.py`, `anthropic_provider.py`, `gemini_provider.py`, `openai_compat_provider.py`, `azure_openai_provider.py`, `openrouter_provider.py`): Removed inner `asyncio.wait_for(call, timeout=45)` patterns. The HTTP client timeout (set to `PROVIDER_TIMEOUT_SECONDS = 30`) remains as a network-level fallback. Removed `import asyncio` from files that no longer needed it.
- `tests/test_providers_manager.py`: Added 5 new timeout tests including structural source-code assertion tests and a behavioral test that mocks a hanging provider and verifies it gets cancelled quickly.

### Task 2: Search Backend Timeout Unification

- `search_backends/base.py`: Added `SEARCH_TIMEOUT_SECONDS = 15` constant with documentation comment.
- `paapi_backend.py`, `rapidapi_backend.py`: Replaced `aiohttp.ClientTimeout(total=15)` with constant.
- `dataforseo_backend.py`: Replaced inconsistent `total=10` (probe) and `total=30` (search) with `SEARCH_TIMEOUT_SECONDS` for both.
- `playwright_backend.py`: Replaced `timeout=30_000` (page.goto), `timeout=10_000` (wait_for_selector), and `timeout=10` (asyncio.wait_for JS evaluate) with `SEARCH_TIMEOUT_SECONDS * 1000` or `SEARCH_TIMEOUT_SECONDS`.
- `brightdata_backend.py`: Replaced `aiohttp.ClientTimeout(total=30)` with constant.
- `tests/test_search_timeout.py` (new file): 6 tests verifying the constant value and that no backend has hardcoded timeout values via `inspect.getsource()` + regex checks.

### Task 3: Admin vs User Error Message Differentiation

- `style.py`:
  - `error_analysis_failed(is_admin)`: User now gets "We couldn't analyze your photo right now. Please try again in a few minutes." Admin keeps detailed message with `/admin → Model Health` link.
  - `error_no_results(is_admin)`: User gets "We couldn't find matching products. Try a clearer photo or a different angle." (no Amazon mention). Admin gets the old Try: list with additional context.
  - `error_no_backend(is_admin)`: Added `is_admin` parameter. User gets generic "Search Temporarily Unavailable" message. Admin gets the API key configuration instructions with `/admin` link.
  - `error_no_providers(is_admin)`: Already had differentiation — verified correct, no change needed.
- `bot.py`: Updated all 6 call sites of `error_no_results()` and 1 call site of `error_no_backend()` to pass `is_admin=bool(session.is_admin)`.
- `tests/test_style.py`: Added 16 new tests covering all 4 error functions with both `is_admin=True` and `is_admin=False` variants.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed dual-timeout pattern in all provider files**
- **Found during:** Task 1
- **Issue:** Provider files had both `httpx.Timeout(PROVIDER_TIMEOUT_SECONDS)` (client-level) AND `asyncio.wait_for(..., timeout=45)` (coroutine-level). The asyncio timeout was hardcoded to 45s while `PROVIDER_TIMEOUT_SECONDS` was 60s, meaning the asyncio timeout would fire before the client timeout — inconsistent and confusing.
- **Fix:** Removed all inner `asyncio.wait_for` calls from provider files. Manager's `_safe_run` is now the single coroutine-level enforcement point. HTTP client timeout (30s) provides network-level protection.
- **Files modified:** openai_provider.py, anthropic_provider.py, gemini_provider.py, openai_compat_provider.py, azure_openai_provider.py, openrouter_provider.py
- **Commits:** 0dbb9ea

**2. [Rule 2 - Missing] Added is_admin parameter to error_no_backend in bot.py**
- **Found during:** Task 3
- **Issue:** When updating `error_no_backend()` to accept `is_admin`, the bot.py call site needed to resolve admin status before calling the function, since it wasn't always available from `session.is_admin` at that point.
- **Fix:** Added inline admin check `_is_admin = session.is_admin if session.is_admin is not None else (user_id in config.ADMIN_IDS or await db.is_admin_in_db(user_id))` before the call.
- **Files modified:** bot.py
- **Commit:** 8f27542

## Test Results

All 77 tests in the targeted test files pass:
- `tests/test_providers_manager.py`: 24 passed (19 existing + 5 new)
- `tests/test_style.py`: 47 passed (29 existing + 18 new)
- `tests/test_search_timeout.py`: 6 passed (all new)
- `tests/test_progressive_health.py`: 20 passed (pre-existing, unchanged)

## Self-Check: PASSED

- FOUND: providers/base.py (PROVIDER_TIMEOUT_SECONDS = 30)
- FOUND: providers/manager.py (deadline + asyncio.wait_for)
- FOUND: search_backends/base.py (SEARCH_TIMEOUT_SECONDS = 15)
- FOUND: style.py (all error functions with is_admin)
- FOUND: tests/test_search_timeout.py (6 tests)
- FOUND: tests/test_providers_manager.py (24 tests)
- FOUND commit 0dbb9ea: feat(01-01): unify provider timeouts
- FOUND commit 6594c1a: feat(01-01): unify search backend timeouts
- FOUND commit 8f27542: feat(01-01): differentiate error messages
- Requirements STAB-01, STAB-02, STAB-07 marked complete in REQUIREMENTS.md
