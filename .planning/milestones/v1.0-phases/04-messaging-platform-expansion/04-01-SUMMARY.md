---
phase: 04-messaging-platform-expansion
plan: "01"
subsystem: adapters, database
tags: [fastapi, whatsapp, instagram, webhook, database-migration]
dependency_graph:
  requires: []
  provides:
    - adapters/whatsapp.py: FastAPI-compatible webhook handlers
    - adapters/instagram.py: FastAPI-compatible webhook handlers
    - database.py: wa_opted_in and wa_last_msg_at columns + helper functions
  affects:
    - webhook_routes.py: adapter.handle_webhook(request) contract now satisfied
    - Plans 02 and 03: WhatsApp opt-in helpers available for compliance gates
tech_stack:
  added: []
  patterns:
    - FastAPI Request / PlainTextResponse for webhook handler signatures
    - SQLite upsert via ON CONFLICT(user_key) DO UPDATE for opt-in state
key_files:
  modified:
    - adapters/whatsapp.py
    - adapters/instagram.py
    - database.py
decisions:
  - Used PlainTextResponse (not Response) to match webhook_routes.py contract described in plan
  - request.query_params.get() is the FastAPI equivalent of aiohttp request.query.get()
  - request.body() (coroutine) replaces aiohttp request.read() for reading raw bytes
metrics:
  duration_minutes: 8
  completed_date: "2026-03-14"
  tasks_completed: 2
  files_modified: 3
---

# Phase 4 Plan 01: Adapter FastAPI Migration Summary

**One-liner:** Swapped aiohttp.web types for FastAPI Request/PlainTextResponse in WhatsApp and Instagram webhook handlers, and added wa_opted_in/wa_last_msg_at DB columns with four opt-in helper functions.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Migrate webhook handlers from aiohttp.web to FastAPI | d617461 | adapters/whatsapp.py, adapters/instagram.py |
| 2 | Add DB migration entries for WhatsApp opt-in columns | 9fec858 | database.py |

## What Was Built

### Task 1: Adapter WebHook Handler Migration

Both `adapters/whatsapp.py` and `adapters/instagram.py` previously imported `from aiohttp import web` and typed their webhook methods as `web.Request` / `web.Response`. The gateway (`webhook_routes.py`) passes FastAPI `Request` objects, causing runtime type mismatches.

Changes made (purely mechanical — no logic changes):
- Replaced `from aiohttp import web` with `from fastapi import Request` and `from fastapi.responses import PlainTextResponse`
- `handle_webhook_verify(request: web.Request) -> web.Response` → `(request: Request) -> PlainTextResponse`
- `handle_webhook(request: web.Request) -> web.Response` → `(request: Request) -> PlainTextResponse`
- `request.query.get(...)` → `request.query_params.get(...)`
- `await request.read()` → `await request.body()`
- `web.Response(text=..., status=...)` → `PlainTextResponse(..., status_code=...)`
- Outbound HTTP (send_text, send_photo) untouched — still uses `aiohttp.ClientSession`

### Task 2: Database Migration and Helper Functions

Added two migration entries to `_MIGRATIONS` in `database.py`:
- `wa_opted_in INTEGER NOT NULL DEFAULT 0` — tracks WhatsApp opt-in consent (boolean as integer)
- `wa_last_msg_at REAL` — unix timestamp of last user-initiated message (for 24-hour window enforcement)

Added four helper functions following existing upsert patterns:
- `get_wa_opt_in(user_key) -> bool`
- `set_wa_opt_in(user_key, opted_in) -> None`
- `update_wa_last_msg_at(user_key, ts) -> None`
- `get_wa_last_msg_at(user_key) -> float | None`

## Verification

- Both adapter imports succeed with no aiohttp.web references remaining
- All four DB helpers importable
- `wa_opted_in` and `wa_last_msg_at` present in `_MIGRATIONS`
- `pytest tests/test_database.py -x` — 71 passed, 33 warnings (warnings are pre-existing aiosqlite cleanup issues unrelated to this plan)

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- adapters/whatsapp.py: exists and imports correctly
- adapters/instagram.py: exists and imports correctly
- database.py: contains wa_opted_in and wa_last_msg_at in _MIGRATIONS
- Commits d617461 and 9fec858 verified in git log
