---
phase: 01-stability-and-infrastructure
plan: 02
subsystem: database
tags: [aiosqlite, sqlite, asyncio, pillow, csv-import, cache-invalidation]

# Dependency graph
requires: []
provides:
  - Atomic CSV tag import using _get_conn + BEGIN IMMEDIATE + rollback on error
  - Cache invalidation (_active_tag_cache, _disabled_models_cache) after admin writes
  - Photo size validation with user-friendly rejection message
  - Pillow image compression offloaded to asyncio.to_thread (non-blocking)
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CSV bulk import: use _get_conn with BEGIN IMMEDIATE for atomicity, rollback on exception"
    - "Admin settings writes: always invalidate _active_tag_cache and _disabled_models_cache after commit"
    - "Sync Pillow ops: always wrap in asyncio.to_thread to avoid blocking the event loop"

key-files:
  created: []
  modified:
    - database.py
    - settings_store.py
    - bot.py
    - tests/test_database.py
    - tests/test_settings_store.py
    - tests/test_bot.py

key-decisions:
  - "import_tags_csv now uses the persistent _get_conn connection (not a separate aiosqlite.connect) to avoid the event-loop-closed warning and ensure atomicity"
  - "Cache invalidation placed after successful commit in import_tags_csv, matching the pattern in add_tag"
  - "settings_store._invalidate_db_caches() extracted as a helper so set() and delete() both invalidate consistently"
  - "_compress_image_async wrapper added in bot.py; existing sync _compress_image kept for direct unit-test use"

patterns-established:
  - "Async Pillow pattern: wrap synchronous image ops in asyncio.to_thread inside an async wrapper function"
  - "Cache invalidation pattern: invalidate after commit, not before, to avoid stale-read windows"

requirements-completed:
  - STAB-03
  - STAB-05
  - STAB-06
  - INFR-02

# Metrics
duration: 15min
completed: 2026-03-14
---

# Phase 1 Plan 2: Database Transaction Safety and Async Image Compression Summary

**Atomic CSV tag imports via _get_conn + BEGIN IMMEDIATE, cache invalidation on all admin writes, and Pillow offloaded to asyncio.to_thread**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-14T00:00:00Z
- **Completed:** 2026-03-14T00:02:36Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Rewrote `import_tags_csv` to use `_get_conn` persistent connection and `BEGIN IMMEDIATE` transaction — eliminates partial imports and the aiosqlite "event loop closed" warning caused by the old separate connection
- Added cache invalidation (`_active_tag_cache = None`, `_disabled_models_cache = None`) to `settings_store.set()` and `settings_store.delete()` via new `_invalidate_db_caches()` helper — admin changes now take effect on the next user request
- Added `_compress_image_async()` wrapper in `bot.py` using `asyncio.to_thread` — Pillow no longer blocks the async event loop during image compression
- Added 8 new tests (4 database, 4 bot) covering atomicity, persistent connection use, cache invalidation, oversized photo rejection, and thread offloading

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix CSV import transactions and cache invalidation** - `3db9407` (feat)
2. **Task 2: Photo size validation UX and Pillow async offloading** - `3410968` (feat)

## Files Created/Modified
- `database.py` - Rewrote `import_tags_csv`: uses `_get_conn`, `BEGIN IMMEDIATE`, rollback on error, cache invalidated after commit
- `settings_store.py` - Added `_invalidate_db_caches()` helper; `set()` and `delete()` both call it after DB write
- `bot.py` - Added `_compress_image_async()` wrapper; `handle_photo` now awaits it instead of calling sync version
- `tests/test_database.py` - Added: `test_import_tags_csv_atomic_success`, `test_import_tags_csv_uses_persistent_connection`, `test_import_tags_csv_cache_invalidated_after_commit`
- `tests/test_settings_store.py` - Added: `TestCacheInvalidation` class with 4 tests covering set/delete on both caches
- `tests/test_bot.py` - Added: `TestOversizedPhoto` (2 tests), `TestCompressImageAsync` (2 tests)

## Decisions Made
- Kept the existing `_compress_image` sync function for testability; added a thin `_compress_image_async` wrapper rather than rewriting the sync function
- Cache invalidation placed after `await conn.commit()` in `import_tags_csv` to match the pattern used in `add_tag` — no stale-read window between commit and invalidation
- `_invalidate_db_caches()` added as a private helper in `settings_store.py` to avoid duplicating the two-line invalidation in both `set()` and `delete()`

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- The pre-existing `RuntimeWarning: Event loop is closed` warning from aiosqlite's connection worker thread appears in tests after fixing `import_tags_csv`. This warning was present before (caused by the old separate `aiosqlite.connect()` context) and is an aiosqlite lifecycle artifact, not caused by our changes. It does not affect test outcomes.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 01-02 complete. All 4 requirements (STAB-03, STAB-05, STAB-06, INFR-02) satisfied.
- Ready to continue with Phase 1 Plan 3.

---
*Phase: 01-stability-and-infrastructure*
*Completed: 2026-03-14*
