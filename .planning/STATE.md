---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: in_progress
stopped_at: Completed 06-admin-tech-debt-cleanup/06-01-PLAN.md
last_updated: "2026-03-14T13:48:20.918Z"
last_activity: 2026-03-14 -- Phase 2 complete (4/4 plans, verification passed)
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 18
  completed_plans: 18
---

---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: in_progress
stopped_at: Completed 02-enhanced-visual-experience/02-04-PLAN.md
last_updated: "2026-03-14T02:04:15.393Z"
last_activity: 2026-03-14 -- Phase 1 complete (4/4 plans, verification passed)
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 8
  completed_plans: 8
---

---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: in_progress
stopped_at: Phase 1 complete, ready to plan Phase 2
last_updated: "2026-03-14"
last_activity: 2026-03-14 -- Phase 1 complete (4/4 plans, verification passed)
progress:
  total_phases: 5
  completed_phases: 1
  total_plans: 4
  completed_plans: 4
  percent: 20
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-14)

**Core value:** When a user sends a photo of any product, the bot must reliably identify it and return relevant Amazon results with accurate Israel shipping information -- fast enough that users don't abandon the interaction.
**Current focus:** Phase 3: Web Admin Dashboard

## Current Position

Phase: 3 of 5 (Web Admin Dashboard)
Plan: Not started
Status: Ready to plan
Last activity: 2026-03-14 -- Phase 2 complete (4/4 plans, verification passed)

Progress: [████████████████████] 8/8 plans (100% of Phases 1-2)

## Performance Metrics

**Velocity:**
- Total plans completed: 4
- Average duration: ~13 min
- Total execution time: ~51 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-stability-and-infrastructure | 4 | ~51 min | ~13 min |

**Recent Trend:**
- Last 4 plans: 14m, 15m, 15m, 7m
- Trend: Stable

*Updated after each plan completion*
| Phase 01-stability-and-infrastructure P01 | 14 | 3 tasks | 18 files |
| Phase 01-stability-and-infrastructure P02 | 15 | 2 tasks | 6 files |
| Phase 01-stability-and-infrastructure P03 | 15 | 2 tasks | 9 files |
| Phase 01-stability-and-infrastructure P04 | 7 | 2 tasks | 3 files |
| Phase 02-enhanced-visual-experience P02 | 10 | 1 tasks | 2 files |
| Phase 02-enhanced-visual-experience P03 | 4 | 2 tasks | 3 files |
| Phase 02-enhanced-visual-experience P01 | 12 | 2 tasks | 3 files |
| Phase 02-enhanced-visual-experience P04 | 8 | 2 tasks | 4 files |
| Phase 03-web-admin-dashboard P01 | 18 | 2 tasks | 15 files |
| Phase 03-web-admin-dashboard P02 | 17 | 2 tasks | 5 files |
| Phase 03-web-admin-dashboard P03 | 14 | 2 tasks | 9 files |
| Phase 04-messaging-platform-expansion P01 | 8 | 2 tasks | 3 files |
| Phase 04-messaging-platform-expansion P03 | 10 | 2 tasks | 3 files |
| Phase 04-messaging-platform-expansion P02 | 10 | 3 tasks | 3 files |
| Phase 05-public-web-application P01 | 27 | 3 tasks | 12 files |
| Phase 05-public-web-application P02 | 3 | 2 tasks | 6 files |
| Phase 05-public-web-application P03 | 7 | 1 tasks | 9 files |
| Phase 06-admin-tech-debt-cleanup P01 | 13 | 1 tasks | 6 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Fix stability before new features (PROJECT.md) — ✓ Done in Phase 1
- Stay on SQLite for now; PostgreSQL migration deferred unless scaling requires it (PROJECT.md)
- FastAPI+HTMX+Jinja2 for all web surfaces (research recommendation)
- Consolidated single FastAPI gateway on port 8080 (Phase 1)
- admin_service.py provides Telegram-decoupled admin logic for Phase 3 web dashboard (Phase 1)
- [Phase 02-enhanced-visual-experience]: Confidence scoring thresholds: green >= 0.7 (ships free), yellow >= 0.4 (ships paid), red < 0.4 (unlikely)
- [Phase 02-enhanced-visual-experience]: Strong signals worth 0.35 each (free_ship_phrase, FBA/ships-from-amazon), medium 0.20 each (Prime, Israel mention), weak 0.10 (add-to-cart sans OOS)
- [Phase 02-enhanced-visual-experience]: Bbox area threshold < 1% or > 90% of image area is unreliable; overlay fallback strategy: use overlay when any reliable bbox exists
- [Phase 02-enhanced-visual-experience]: render_price_bar does not escape for MarkdownV2 — caller handles escaping and monospace wrapping
- [Phase 02-enhanced-visual-experience]: Bar lines wrapped in backtick monospace blocks for proper Unicode block char rendering in Telegram
- [Phase 02-enhanced-visual-experience]: shipping_badge uses result.ships_to_israel + result.is_free_shipping to select emoji tier (green/yellow/red/gray)
- [Phase 02-enhanced-visual-experience]: annotate_with_overlays failure is non-fatal in bot flow — session.annotated_bytes set to None
- [Phase 03-web-admin-dashboard]: Fallback token uses module-level state (not DB) for zero-dependency startup auth
- [Phase 03-web-admin-dashboard]: require_admin raises HTTPException(307) rather than returning RedirectResponse (FastAPI dependency pattern)
- [Phase 03-web-admin-dashboard]: HTMX outerHTML hx-swap on partial wrappers so polling attributes survive DOM replacement
- [Phase 03-web-admin-dashboard]: TagInfo uses .name field (not .tag) — templates adapted to match actual admin_service.py dataclass
- [Phase 03-web-admin-dashboard]: FastAPI route ordering: POST /tags/add defined before POST /tags/{tag_id}/* to avoid path conflict
- [Phase 03-web-admin-dashboard]: notifications.admin() called directly for admin actions (no send_admin_notification wrapper exists)
- [Phase 03-web-admin-dashboard]: ProviderHealth dataclass has name/status/failure_count/last_failure only - templates adapted to actual fields
- [Phase 03-web-admin-dashboard]: reset_provider_health() uses global _providers = {} to force provider cache rebuild after reset
- [Phase 03-web-admin-dashboard]: health routes use provider_name:path type annotation to handle slash-containing provider names like openai/gpt-4o
- [Phase 04-messaging-platform-expansion]: FastAPI Request/PlainTextResponse replaces aiohttp.web types in adapter webhook handlers — purely mechanical type swap, no logic changes
- [Phase 04-messaging-platform-expansion]: WhatsApp opt-in tracked as INTEGER (0/1) in SQLite; wa_last_msg_at as REAL unix timestamp for 24-hour window enforcement
- [Phase 04-messaging-platform-expansion]: Instagram opt-in uses quick replies (not WhatsApp-style templates); commands pass through before opt-in gate
- [Phase 04-messaging-platform-expansion]: Patch adapters.instagram.send_graph_api in tests (not shared_meta) — from-import creates local binding
- [Phase 04-messaging-platform-expansion]: Slash commands pass through WhatsApp opt-in gate matching Telegram behavior for new user onboarding
- [Phase 04-messaging-platform-expansion]: WhatsApp send_list_message uses hasattr check in BotCore for platform-agnostic safety without base class change
- [Phase 05-public-web-application]: Lazy imports inside SSE generator to avoid circular imports between web_app and providers/amazon_search at module load time
- [Phase 05-public-web-application]: Starlette 2.x TemplateResponse API: request as first arg, context dict does not include request key
- [Phase 05-public-web-application]: Affiliate URL built inline in router (f-string) since results are dicts not AmazonItem objects at template render time
- [Phase 05-public-web-application]: get_price_history patched at price_history module level in tests (lazy import creates local binding in router)
- [Phase 05-public-web-application]: Hebrew-first default (lang='he') for all routes — target market is Israeli users
- [Phase 05-public-web-application]: No physical directional Tailwind classes in templates — all logical properties (ms-, me-, text-start, text-end)
- [Phase 05-public-web-application]: Result page lang determined by: request ?lang= param → cookie → stored row.lang
- [Phase 06-admin-tech-debt-cleanup]: get_stats_since returns unique_users and total_searches keys — used directly for today_searches/today_users without aliasing
- [Phase 06-admin-tech-debt-cleanup]: webtoken non-admin path sends Unauthorized message (not silent return) — matches legacy bot.py behavior

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 2] Bbox reliability uses area thresholds (< 1% or > 90% rejected); accuracy across providers not yet validated in production
- Research flag: Phase 4 needs current Meta WhatsApp Business API compliance verification
- Research flag: Phase 5 needs SSE+HTMX production pattern research for FastAPI

## Session Continuity

Last session: 2026-03-14T13:48:20.912Z
Stopped at: Completed 06-admin-tech-debt-cleanup/06-01-PLAN.md
Resume file: None
