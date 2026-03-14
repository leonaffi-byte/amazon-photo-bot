---
phase: 06-admin-tech-debt-cleanup
plan: 01
subsystem: admin
tags: [dashboard, stats, sqlite, telegram, bot-core, webtoken]

# Dependency graph
requires:
  - phase: 03-web-admin-dashboard
    provides: admin_service.py service layer and BotStats dataclass
  - phase: 04-messaging-platform-expansion
    provides: BotCore.handle_command() and adapter pattern
provides:
  - Dashboard today_searches and today_users stats wired to db.get_stats_since()
  - /webtoken command registered and functional in production TelegramAdapter
  - webtoken branch in bot_core.handle_command() with admin guard
affects:
  - admin dashboard rendering
  - bot command handling

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "UTC midnight boundary computed with datetime.now(timezone.utc).replace(hour=0, ...) for today-scoped DB queries"
    - "New bot commands added via: (1) bot_core elif branch, (2) CommandHandler registration in telegram.py"

key-files:
  created:
    - tests/test_bot_core_webtoken.py
  modified:
    - admin_service.py
    - bot_core.py
    - adapters/telegram.py
    - bot.py
    - tests/test_admin_service.py

key-decisions:
  - "get_stats_since uses unique_users and total_searches keys (not today_users/today_searches) — field names match database.py return dict"
  - "webtoken non-admin path sends Unauthorized message (not silent return) — matches legacy bot.py behavior"
  - "Legacy bot.py::webtoken_command marked DEAD CODE with comment, not deleted — safe option per plan spec"

patterns-established:
  - "Today stats pattern: call db.get_stats_since(today_midnight) where today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)"
  - "New bot command checklist: add elif branch in bot_core.handle_command() + CommandHandler registration in adapters/telegram.py"

requirements-completed: [ADMN-02, ADMN-06]

# Metrics
duration: 12min
completed: 2026-03-14
---

# Phase 6 Plan 1: Admin Tech Debt Cleanup — Today Stats and Webtoken Wiring

**Real today stats wired to db.get_stats_since(UTC midnight) and /webtoken command registered in production TelegramAdapter via BotCore pattern**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-14T13:34:26Z
- **Completed:** 2026-03-14T13:46:32Z
- **Tasks:** 1 (TDD: 2 commits — RED then GREEN)
- **Files modified:** 5

## Accomplishments

- Dashboard today_searches and today_users now return real counts from database instead of hardcoded 0
- /webtoken command registered in TelegramAdapter and implemented in BotCore with proper admin guard
- Legacy webtoken_command in bot.py marked as dead code with explanatory comment
- 5 new tests added (3 today-stats, 2 webtoken) — all pass

## Task Commits

Each task was committed atomically (TDD pattern):

1. **RED: Failing tests** - `3ff026f` (test) — added today-stats tests to test_admin_service.py + created test_bot_core_webtoken.py
2. **GREEN: Implementation** - `1b0de47` (feat) — wired get_stats_since(), added webtoken to bot_core + telegram adapter, marked dead code

**Plan metadata:** (docs commit follows)

_Note: TDD task has two commits (test RED → feat GREEN)_

## Files Created/Modified

- `tests/test_admin_service.py` — Added 3 today-stat test cases to TestStatsService
- `tests/test_bot_core_webtoken.py` — New: admin/non-admin webtoken command tests
- `admin_service.py` — Added datetime/timezone import; get_stats() calls db.get_stats_since(today_midnight) for today_searches/today_users
- `bot_core.py` — Added elif command == "webtoken" branch with admin guard and generate_fallback_token call
- `adapters/telegram.py` — Added CommandHandler("webtoken", self._handle_command) registration
- `bot.py` — Added "# DEAD CODE" comment above legacy webtoken_command function

## Decisions Made

- `get_stats_since` returns dict keys `unique_users` and `total_searches` — used those directly without aliasing
- webtoken non-admin path sends "Unauthorized." message (matching legacy bot.py behavior) rather than silent return
- Legacy function not deleted — safer to mark dead per plan spec; other legacy code in bot.py may reference it

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test used wrong handle_command() signature**
- **Found during:** Task 1 GREEN verification
- **Issue:** `handle_command()` requires positional `args: list[str]` argument; test was calling without it
- **Fix:** Added `[]` as args argument to both test calls
- **Files modified:** tests/test_bot_core_webtoken.py
- **Verification:** Tests pass after fix
- **Committed in:** 1b0de47 (included in GREEN commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug in test signature)
**Impact on plan:** Minor test fix only, no scope creep.

## Issues Encountered

- Pre-existing test failure in `tests/test_malformed_responses.py::TestAnthropicProviderErrors::test_anthropic_api_error_raises` — verified it exists on main branch before any changes; not caused by this plan's work.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ADMN-02 (today stats) and ADMN-06 (/webtoken) are now fully satisfied
- Phase 6 Plan 1 complete; ready for any remaining admin tech debt plans
- Full test suite passes (580 passed, 14 skipped, 1 pre-existing failure in unrelated test)

---
*Phase: 06-admin-tech-debt-cleanup*
*Completed: 2026-03-14*
