---
phase: 03-web-admin-dashboard
plan: "02"
subsystem: admin-dashboard
tags: [fastapi, jinja2, htmx, tailwind, api-keys, affiliate-tags]
dependency_graph:
  requires:
    - admin_dashboard/router.py (Phase 03-01)
    - admin_service.py (list_key_groups, get_key_group, set_api_key, delete_api_key, list_tags, add_tag, remove_tag, set_active_tag, deactivate_all_tags)
    - notifications.py (notifications.admin)
    - admin_dashboard/templates/base.html
  provides:
    - /admin/keys page and HTMX partial routes
    - /admin/tags page and HTMX partial routes
  affects:
    - admin_dashboard/router.py (6 new routes added)
tech_stack:
  added: []
  patterns:
    - HTMX outerHTML swap for self-updating key group cards
    - HTMX beforeend swap for appending new tag rows to table body
    - HTMX outerHTML swap for activating/deactivating/removing tag rows
    - Key masking pattern: never render actual key values, only Set/Not set
    - FastAPI route ordering: static POST /tags/add before dynamic POST /tags/{tag_id}/*
key_files:
  created:
    - admin_dashboard/templates/keys.html
    - admin_dashboard/templates/partials/key_group.html
    - admin_dashboard/templates/tags.html
    - admin_dashboard/templates/partials/tag_row.html
  modified:
    - admin_dashboard/router.py (added 6 new routes: 3 for keys, 3+2 for tags)
decisions:
  - "TagInfo uses .name field (not .tag) — adapted templates to match actual admin_service.py dataclass"
  - "add_tag() requires description param — passed empty string for web-initiated adds"
  - "notifications.admin() used directly (no send_admin_notification wrapper exists in notifications.py)"
  - "FastAPI route ordering: POST /tags/add defined before POST /tags/{tag_id}/activate to avoid path conflict"
  - "Optional key groups rendered in separate section with 'Optional' label for clarity"
metrics:
  duration_minutes: 17
  completed_date: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 1
---

# Phase 3 Plan 2: API Key and Affiliate Tag Management Summary

**One-liner:** HTMX-powered /admin/keys and /admin/tags pages with inline save/delete/activate/deactivate/add/remove actions, key masking, and Telegram admin notifications.

## What Was Built

### Task 1 — API key management page and routes (commit: 822265f)

Added 3 routes to `admin_dashboard/router.py`:

- **`GET /keys`**: Lists all 18 API key groups via `admin_service.list_key_groups()`, renders `keys.html` with navigation anchors.
- **`POST /keys/{group_name}/{key_name}/save`**: Validates value is non-empty, calls `admin_service.set_api_key()`, sends Telegram notification, re-fetches group via `admin_service.get_key_group()`, returns `partials/key_group.html` fragment.
- **`POST /keys/{group_name}/{key_name}/delete`**: Calls `admin_service.delete_api_key()`, sends notification, returns updated `partials/key_group.html` fragment.

Created templates:

- **`keys.html`**: Extends base.html. Shows page header with description, quick navigation anchor links per group, then loops over all groups including the partial.
- **`partials/key_group.html`**: `id="key-group-{group_name}"` as HTMX target. Shows group label, all-set/missing status badge, required keys section (key name + Set/Not set + save form + delete button), optional keys section. Input `value=""` is always empty.

### Task 2 — Affiliate tag management page and routes (commit: 92147d5)

Added 5 routes to `admin_dashboard/router.py`:

- **`GET /tags`**: Lists all affiliate tags via `admin_service.list_tags()`, renders `tags.html`.
- **`POST /tags/add`**: Validates non-empty tag string, calls `admin_service.add_tag()` with empty description, sends notification, returns `partials/tag_row.html` fragment (HTMX appends with `hx-swap="beforeend"` on `#tag-table-body`).
- **`POST /tags/{tag_id}/activate`**: Calls `admin_service.set_active_tag()`, sends notification, re-fetches and returns updated tag row fragment.
- **`POST /tags/{tag_id}/deactivate`**: Calls `admin_service.deactivate_all_tags()`, sends notification, re-fetches and returns updated tag row fragment.
- **`POST /tags/{tag_id}/remove`**: Calls `admin_service.remove_tag()`, sends notification, returns `HTMLResponse("")` — HTMX removes row via `outerHTML` swap.

Created templates:

- **`tags.html`**: Extends base.html. Add Tag form at top (hx-target="#tag-table-body" hx-swap="beforeend"). Table with thead (Tag, Status, Searches, Actions) and `<tbody id="tag-table-body">` with included partials.
- **`partials/tag_row.html`**: `<tr id="tag-row-{tag.id}">` as HTMX target. Columns: tag name, status badge (green Active / gray Inactive), search count, Actions (Activate or Deactivate + Remove buttons).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TagInfo.name vs .tag field name mismatch**
- **Found during:** Task 2 template creation
- **Issue:** Plan specified `{{ tag.tag }}` in templates, but the actual `TagInfo` dataclass in `admin_service.py` uses `name` (not `tag`) for the tag string field
- **Fix:** Templates use `{{ tag.name }}` to match the actual dataclass
- **Files modified:** `admin_dashboard/templates/tags.html`, `admin_dashboard/templates/partials/tag_row.html`
- **Commit:** 92147d5

**2. [Rule 1 - Bug] add_tag() requires description parameter**
- **Found during:** Task 2 route implementation
- **Issue:** Plan showed `add_tag(tag, admin_id=admin_id)` but actual signature is `add_tag(tag, description, admin_id, make_active=False)` — description is required positional
- **Fix:** Pass empty string `description=""` for web-initiated adds
- **Files modified:** `admin_dashboard/router.py`
- **Commit:** 92147d5

**3. [Rule 1 - Bug] send_admin_notification() does not exist**
- **Found during:** Task 1 route implementation
- **Issue:** Plan referenced `from notifications import send_admin_notification` but `notifications.py` exposes `notifications.admin()` function, not `send_admin_notification`
- **Fix:** Call `await notifications.admin(message, parse_mode="MarkdownV2")` directly
- **Files modified:** `admin_dashboard/router.py`
- **Commit:** 822265f

**4. [Rule 1 - Bug] TagInfo has no click_count field**
- **Found during:** Task 2 template creation
- **Issue:** Plan mentioned `tag.click_count` column but actual TagInfo dataclass only has `search_count` (no click tracking yet)
- **Fix:** Removed Clicks column from tags table; only Searches column shown
- **Files modified:** `admin_dashboard/templates/tags.html`, `admin_dashboard/templates/partials/tag_row.html`
- **Commit:** 92147d5

## Self-Check: PASSED

### Files Exist

- `admin_dashboard/templates/keys.html` — exists
- `admin_dashboard/templates/partials/key_group.html` — exists
- `admin_dashboard/templates/tags.html` — exists
- `admin_dashboard/templates/partials/tag_row.html` — exists

### Commits Verified

- `822265f` — feat(03-02): API key management page and routes
- `92147d5` — feat(03-02): Affiliate tag management page and routes

### Test Results

142 passed, 14 skipped, 0 failures (excluding pre-existing test_malformed_responses.py Windows timeout issue and test_israel_scraper.py Playwright tests).
