---
phase: 01-stability-and-infrastructure
plan: "04"
subsystem: admin
tags: [service-layer, refactoring, architecture, testability]
dependency_graph:
  requires: ["01-02"]
  provides: ["admin_service.py"]
  affects: ["admin.py", "phase-3-web-admin"]
tech_stack:
  added: []
  patterns: ["service layer", "dataclass DTOs", "thin controller pattern"]
key_files:
  created:
    - admin_service.py
    - tests/test_admin_service.py
  modified:
    - admin.py
decisions:
  - "admin_service.py returns plain dataclasses (TagInfo, KeyGroupStatus, SettingInfo, BotStats, ProviderHealth) with no Telegram imports"
  - "admin.py keeps key_store calls for display-specific masking and source badges (not business logic)"
  - "_test_api in admin.py replaced with thin wrapper delegating to admin_service.test_api_group"
  - "TagInfo uses .name field (not .tag) to match the service layer abstraction over DB schema"
metrics:
  duration_minutes: 7
  completed_date: "2026-03-14"
  tasks_completed: 2
  files_modified: 3
---

# Phase 1 Plan 4: Admin Service Layer Extraction Summary

Extracted admin business logic from admin.py into a reusable admin_service.py module that returns plain data, making admin.py a thin Telegram formatting layer. This enables the future web admin dashboard (Phase 3) to share the same business logic without importing Telegram types.

## What Was Built

### admin_service.py (new, 370 lines)

Five function groups, all async, all returning plain Python dataclasses or dicts:

**Tags group:** `list_tags() -> list[TagInfo]`, `add_tag()`, `remove_tag()`, `set_active_tag()`, `deactivate_all_tags()`, `set_default_tag()`, `clear_default_tag()`

**Keys group:** `list_key_groups() -> list[KeyGroupStatus]`, `get_key_group()`, `set_api_key()`, `delete_api_key()`, `test_api_group()` (full API connectivity testing for all 18 provider groups)

**Settings group:** `list_settings() -> list[SettingInfo]`, `set_setting()`, `reset_setting()`

**Stats group:** `get_stats() -> BotStats`, `get_shortener_stats() -> dict`

**Health group:** `get_provider_health() -> list[ProviderHealth]`

**Admin management:** `list_admins() -> list[int]`, `is_admin() -> bool`

No Telegram imports anywhere in admin_service.py.

### admin.py (refactored)

- Added `import admin_service` at top
- `_panel_content()`: uses `admin_service.list_tags()`, `get_stats()`, `list_key_groups()`
- `_tags_content()`: uses `admin_service.list_tags()`
- `_stats_content()`: uses `admin_service.get_stats()`, `list_tags()`
- `_shortener_content()`: uses `admin_service.get_shortener_stats()`
- `_settings_content()`: uses `admin_service.list_settings()`
- `_group_content()`: uses `admin_service.get_key_group()`
- `_test_api()`: thin wrapper → `admin_service.test_api_group()`
- All write callbacks (tags, keys, settings) use admin_service functions
- 270 lines removed (duplicate API test logic eliminated)

### tests/test_admin_service.py (new, 36 tests)

Tests verify:
- All service functions return correct dataclass types
- No Telegram types in return values (`"telegram" not in str(type(result))`)
- Module has no `from telegram` or `import telegram` statements
- TagInfo fields (id, name, description, is_active, search_count)
- BotStats fields (total_users, total_searches, total_clicks)
- KeyGroupStatus fields (group_name, label, keys, all_required_set)
- SettingInfo fields (key, value, default, type, description)
- ProviderHealth fields (name, status, failure_count, last_failure)

## Test Results

```
54 passed (18 test_admin.py + 36 test_admin_service.py)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test used non-existent `db.record_model_failure` function**
- **Found during:** Task 1 test run
- **Issue:** Test called `db.record_model_failure()` which doesn't exist; actual function is `db.increment_model_failures()`
- **Fix:** Updated test to use `db.increment_model_failures("openai/gpt-4o", "test error")`
- **Files modified:** tests/test_admin_service.py
- **Commit:** 5ceb4f2

**2. [Rule 2 - Architecture] admin_service.add_tag drops admin_name parameter**
- **Found during:** Task 1 implementation
- **Issue:** The plan showed `add_tag(tag, description, admin_id, make_active)` but `db.add_tag` requires `admin_name`. The service layer derives the admin_name from `str(admin_id)` as a reasonable default.
- **Fix:** `admin_service.add_tag()` passes `admin_name=str(admin_id)` to `db.add_tag()`
- **Files modified:** admin_service.py

**3. [Rule 2 - Architecture] admin.py keeps key_store for display-specific operations**
- **Found during:** Task 2 — `_keys_content()` and `_group_content()` need masking + source badges
- **Issue:** Masking (`key_store.mask()`) and source detection (`key_store.get_with_source()`) are display-specific concerns not appropriate for the service layer
- **Decision:** `_keys_content()` and `_group_content()` retain direct key_store calls for display metadata only; all status logic uses admin_service. This is correct layering.

## Self-Check: PASSED

- admin_service.py: EXISTS (572 lines, min 200 required)
- tests/test_admin_service.py: EXISTS
- commit 5ceb4f2 (Task 1): EXISTS
- commit d0d0723 (Task 2): EXISTS
- admin_service.py has 20 async functions, 0 telegram imports
- admin.py references admin_service 23 times
