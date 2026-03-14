---
phase: 04-messaging-platform-expansion
plan: 03
subsystem: api
tags: [instagram, meta-graph-api, opt-in, compliance, webhook, quick-replies, pytest]

# Dependency graph
requires:
  - phase: 04-messaging-platform-expansion
    provides: InstagramAdapter base, shared_meta helpers, database wa_ opt-in pattern, BotCore handle_photo pipeline

provides:
  - Instagram opt-in gate with quick reply consent prompt (ig_opted_in DB column)
  - get_ig_opt_in / set_ig_opt_in database helpers
  - Command passthrough before opt-in gate (/start, /help, /language, /providers)
  - optin:agree quick reply interception
  - 20 unit tests covering opt-in, quick replies, photo handling, webhook, auth, annotation, translation pipeline

affects:
  - 04-04 (WhatsApp plan - if any, uses same DB pattern)
  - Any future Instagram feature work

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Instagram opt-in gate mirrors WhatsApp wa_ DB pattern with ig_ prefix
    - Slash commands bypass opt-in gate to ensure /start works before consent
    - optin:agree quick reply intercepted before general quick_reply routing
    - Patch adapters.instagram.send_graph_api (not adapters.shared_meta.send_graph_api) because function is imported into module namespace

key-files:
  created:
    - tests/test_instagram_adapter.py
  modified:
    - adapters/instagram.py
    - database.py

key-decisions:
  - "Instagram opt-in uses quick replies (not WhatsApp-style template messages) — Messenger Platform model, no 24h window"
  - "Slash commands pass through BEFORE opt-in gate check per locked decision: full command set matching Telegram"
  - "optin:agree quick reply payload intercepted first in _process_message before general quick_reply routing"
  - "Patch adapters.instagram.send_graph_api in tests (not shared_meta module) — from-import creates local binding"

patterns-established:
  - "Per-platform opt-in stored as ig_opted_in INTEGER (0/1) in users table with INSERT OR CONFLICT upsert"
  - "Migration added to _MIGRATIONS list; try/except per entry handles already-applied case"

requirements-completed: [INST-01, INST-03]

# Metrics
duration: 10min
completed: 2026-03-14
---

# Phase 4 Plan 03: Instagram Opt-In Compliance Gate and Unit Tests Summary

**Instagram opt-in gate with quick reply consent, command passthrough, ig_opted_in DB column, and 20 passing unit tests covering the full adapter compliance surface**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-03-14T09:50:52Z
- **Completed:** 2026-03-14T09:57:10Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added `ig_opted_in` column migration and `get_ig_opt_in`/`set_ig_opt_in` DB helpers mirroring WhatsApp wa_ pattern
- Added opt-in gate to `InstagramAdapter._process_message()` with optin:agree quick reply interception and `_send_opt_in_prompt()` method
- Commands (/start, /help, /language, /providers) pass through before opt-in check, matching Telegram behavior per locked decision
- 20 unit tests passing: TestOptIn, TestQuickReplies, TestPhotoHandling, TestWebhookMigration, TestGraphApiAuth, TestAnnotatedPhoto, TestTranslation

## Task Commits

1. **Task 1: Add opt-in gate, command passthrough, and DB helpers** - `f493a4b` (feat)
2. **Task 2: Create comprehensive Instagram adapter unit tests** - `158781b` (test)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `adapters/instagram.py` - Added `import database`, opt-in gate in `_process_message`, `_send_opt_in_prompt`, command-before-gate ordering
- `database.py` - Added `ig_opted_in` migration to `_MIGRATIONS`, added `get_ig_opt_in` and `set_ig_opt_in` helper functions
- `tests/test_instagram_adapter.py` - 20 unit tests in 7 test classes covering all adapter behaviors

## Decisions Made

- Instagram uses quick replies (not WhatsApp interactive buttons) for opt-in — Messenger Platform model with no 24-hour window constraint
- Commands pass through before opt-in gate to ensure /start, /help work for uninitiated users, matching locked Telegram parity decision
- `optin:agree` payload intercepted as first check in `_process_message` before any other routing to avoid it being routed to `_on_callback`
- Tests patch `adapters.instagram.send_graph_api` (not `adapters.shared_meta.send_graph_api`) because `from-import` creates a local namespace binding in the instagram module

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

During TDD GREEN phase: initial tests used `adapters.shared_meta.send_graph_api` as patch target but `send_graph_api` is imported directly into `adapters/instagram.py` namespace, so the correct patch target is `adapters.instagram.send_graph_api`. Fixed in the same task — one auto-fix iteration. Also, `database.get_rate_limit_override` doesn't exist; corrected to `database.get_user_rate_limit` for the translation pipeline test.

## User Setup Required

External services require manual configuration. See user_setup frontmatter in PLAN.md:
- `INSTAGRAM_TOKEN` — Meta Business Manager -> Instagram -> API Setup -> Page access token
- `INSTAGRAM_PAGE_ID` — Meta Business Manager -> Instagram -> API Setup -> Page ID
- `META_VERIFY_TOKEN` — User-defined string matching webhook configuration
- `META_APP_SECRET` — Meta App Dashboard -> Settings -> Basic -> App Secret
- Webhook URL: `https://{domain}/webhook/instagram` registered in Meta Business Manager

## Next Phase Readiness

- Instagram adapter is fully compliant: opt-in gate, command passthrough, annotation delivery, translator integration
- All behaviors covered by passing unit tests
- Ready for Phase 4 plan 04 (or any remaining Phase 4 plans)

---
*Phase: 04-messaging-platform-expansion*
*Completed: 2026-03-14*
