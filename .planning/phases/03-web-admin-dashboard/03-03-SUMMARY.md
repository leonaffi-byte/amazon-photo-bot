---
phase: 03-web-admin-dashboard
plan: "03"
subsystem: ui
tags: [fastapi, htmx, jinja2, telegram-bot, admin-dashboard]

# Dependency graph
requires:
  - phase: 03-01
    provides: auth, base templates, home dashboard, admin_service.py
  - phase: 03-02
    provides: keys and tags management pages

provides:
  - /admin/settings page with inline edit forms per setting type
  - /admin/health page with provider status, failure counts, and reset buttons
  - partials/setting_row.html HTMX swap target for inline settings editing
  - partials/provider_health_row.html HTMX swap target for per-provider reset
  - POST /admin/settings/{key}/update and /reset endpoints
  - POST /admin/health/{provider}/reset endpoint
  - reset_provider_health() in providers/manager.py
  - /webtoken Telegram command for fallback token delivery
  - Startup fallback token generation and logging

affects: [phase-04, phase-05, bot-commands]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Settings inline edit: HTMX form with hx-post on submit, outerHTML swap on setting row
    - Provider reset: HTMX button hx-post with hx-confirm dialog, outerHTML swap on health row
    - Dynamic input type selection: Jinja2 if/elif chain for choices/bool/int/float/text

key-files:
  created:
    - admin_dashboard/templates/settings.html
    - admin_dashboard/templates/partials/setting_row.html
    - admin_dashboard/templates/health.html
    - admin_dashboard/templates/partials/provider_health_row.html
  modified:
    - admin_dashboard/router.py
    - admin_dashboard/templates/partials/provider_health.html
    - providers/manager.py
    - bot.py
    - main.py

key-decisions:
  - "ProviderHealth dataclass has name/status/failure_count/last_failure only — templates adapted to actual fields (no success_rate or avg_latency_ms)"
  - "reset_provider_health() uses global _providers = {} to force provider cache rebuild after reset"
  - "health routes use provider_name:path type annotation to handle slash-containing names like openai/gpt-4o"
  - "Startup fallback token generation wrapped in try/except to avoid blocking bot startup"

patterns-established:
  - "Inline edit form pattern: <tr id='setting-row-{key}'> as HTMX swap target, form hx-post to update, button hx-post to reset"
  - "Provider health row: <tr id='health-row-{name|replace('/','-')}'> for valid HTML id, reset button conditional on failure_count > 0"

requirements-completed: [ADMN-05, ADMN-06]

# Metrics
duration: 14min
completed: 2026-03-14
---

# Phase 3 Plan 03: Settings Editor and Provider Health Management Summary

**HTMX-powered bot settings editor with per-type input controls, provider health table with reset buttons, and /webtoken Telegram command for fallback auth token delivery**

## Performance

- **Duration:** 14 min
- **Started:** 2026-03-14T08:00:38Z
- **Completed:** 2026-03-14T08:14:27Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Settings editor at /admin/settings with select dropdowns for choice/bool settings, number inputs for int/float, text inputs for free-form — all inline HTMX edit with immediate save
- Provider health page at /admin/health showing status badges (green/yellow/red), failure counts, last failure reason, and per-provider Reset button (HTMX outerHTML swap)
- /webtoken Telegram command lets admins DM themselves the web admin fallback token without needing server log access
- Startup fallback token generation logs "Use /webtoken in Telegram or paste at /admin/login" for initial setup
- reset_provider_health() in providers/manager.py clears DB failure state and resets provider cache

## Task Commits

1. **Task 1: Settings management page and routes** - `9be6329` (feat)
2. **Task 2: Provider health page, provider reset, and /webtoken command** - `08d2905` (feat)

## Files Created/Modified

- `admin_dashboard/router.py` - Added settings (GET/POST update/reset) and health (GET/POST reset) routes
- `admin_dashboard/templates/settings.html` - Settings editor full page extending base.html
- `admin_dashboard/templates/partials/setting_row.html` - Single setting row with inline edit form, HTMX swap target
- `admin_dashboard/templates/health.html` - Provider health detail page with HTMX polling div
- `admin_dashboard/templates/partials/provider_health_row.html` - Single provider row with reset button, HTMX swap target
- `admin_dashboard/templates/partials/provider_health.html` - Updated to full table with provider_health_row.html partials
- `providers/manager.py` - Added reset_provider_health() async function
- `bot.py` - Added webtoken_command() and CommandHandler("webtoken") registration
- `main.py` - Added startup fallback token generation with logging

## Decisions Made

- ProviderHealth dataclass only has name/status/failure_count/last_failure (no success_rate/avg_latency_ms as plan specified) — templates adapted to actual fields
- health routes use `provider_name:path` FastAPI type annotation to handle provider names containing slashes (e.g., openai/gpt-4o)
- reset_provider_health() clears the entire `_providers` cache (not just one entry) to ensure disabled providers are re-loaded from DB on next analysis call

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ProviderHealth dataclass missing success_rate and avg_latency_ms fields**
- **Found during:** Task 2 (provider health page)
- **Issue:** Plan's interface spec included `success_rate: float` and `avg_latency_ms: float` on ProviderHealth, but the actual admin_service.py dataclass only has `name`, `status`, `failure_count`, `last_failure`
- **Fix:** Adapted health.html and provider_health_row.html to use actual fields; removed columns for success rate and latency
- **Files modified:** admin_dashboard/templates/health.html, admin_dashboard/templates/partials/provider_health_row.html
- **Verification:** Templates render correctly with actual data structure
- **Committed in:** 08d2905 (Task 2 commit)

**2. [Rule 1 - Bug] SyntaxError: global declaration inside if block**
- **Found during:** Task 2 (testing)
- **Issue:** `global _providers` inside an `if` block in reset_provider_health() caused SyntaxError in Python 3.13
- **Fix:** Moved `global _providers` to the top of the function body (before any use of the variable)
- **Files modified:** providers/manager.py
- **Verification:** `python -c "import providers.manager"` succeeded; tests pass
- **Committed in:** 08d2905 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness. The ProviderHealth field mismatch was a plan spec error vs actual code; the global declaration bug was introduced during implementation.

## Issues Encountered

- Python 3.13 enforces that `global` declarations must appear before any use of the variable in the function, and cannot be inside conditional blocks

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All ADMN-01 through ADMN-06 requirements now satisfied
- /admin/settings, /admin/health, /admin/keys, /admin/tags, /admin/ all functional
- /webtoken Telegram command ready for initial auth flow
- Phase 3 is complete — all 3 execution plans delivered

---
*Phase: 03-web-admin-dashboard*
*Completed: 2026-03-14*
