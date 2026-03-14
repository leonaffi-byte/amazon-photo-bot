---
phase: 07-multi-platform-visual-parity
plan: 01
subsystem: ui
tags: [formatter, whatsapp, instagram, telegram, shipping-badge, price-bar, multi-platform]

# Dependency graph
requires:
  - phase: 02-enhanced-visual-experience
    provides: render_price_bar() and shipping_badge() in style.py
  - phase: 04-messaging-platform-expansion
    provides: formatter.py Formatter class with platform-aware rendering
provides:
  - formatter.py._shipping_badge(): green/yellow/red emoji badge for Israel shipping
  - formatter.py._render_price_bar_section(): platform-aware ASCII price bar
  - formatter.py.product_caption(israel_result=...): new parameter accepting IsraelShippingResult
affects:
  - 07-02 (bot_core.py wiring of israel_result to formatter)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Platform-aware rendering: Telegram wraps bar lines in backtick monospace, WhatsApp/Instagram uses plain text"
    - "Israel result overrides israel_status string enum when both provided"
    - "render_price_bar() imported from style.py into formatter.py (single source of truth for bar logic)"

key-files:
  created:
    - tests/test_formatter_visual.py
  modified:
    - formatter.py

key-decisions:
  - "israel_result parameter added AFTER israel_status in product_caption() signature for backward compatibility — existing callers unaffected"
  - "Telegram backtick wrapping applied per-line around each bar line (not the whole block) for correct MarkdownV2 monospace rendering"
  - "WhatsApp/Instagram render bar as plain escaped text without backticks — backticks would appear as literal characters"
  - "Fallback chain: _render_price_bar_section() returns empty string for ph without .current field; old plain-text fallback then activates"
  - "Pre-existing test failure (test_malformed_responses.py anthropic proxies TypeError) is a dependency version incompatibility — out of scope"

patterns-established:
  - "Visual feature ports: import function from style.py, wrap in platform-aware Formatter method, keep style.py as source of truth"

requirements-completed: [ISRL-02, PRCE-02, PRCE-03]

# Metrics
duration: 3min
completed: 2026-03-14
---

# Phase 7 Plan 01: Multi-Platform Visual Parity - Shipping Badge and Price Bar Summary

**Ported shipping badge (green/yellow/red emoji) and ASCII price bar from Telegram-only style.py into platform-agnostic formatter.py, with backtick monospace for Telegram and plain text for WhatsApp/Instagram**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-14T14:30:43Z
- **Completed:** 2026-03-14T14:33:51Z
- **Tasks:** 1 (TDD: test + feat commits)
- **Files modified:** 2

## Accomplishments

- Added `_shipping_badge()` method to Formatter: ports style.shipping_badge() logic with platform-aware escaping, returns green/yellow/red emoji badge when israel_result is verified
- Added `_render_price_bar_section()` method: calls render_price_bar() from style.py, wraps bar lines in backtick monospace for Telegram, uses plain text for WhatsApp/Instagram
- Updated `product_caption()` with `israel_result=None` parameter — when provided, badge takes priority over legacy `israel_status` string enum (full backward compatibility)
- 11 unit tests covering all badge tiers, price bar rendering, platform differences, deal labels, and backward compatibility

## Task Commits

Each task was committed atomically (TDD: two commits per task):

1. **Task 1 RED: Failing tests** - `a791ba7` (test)
2. **Task 1 GREEN: Implementation** - `d2c498d` (feat)

## Files Created/Modified

- `formatter.py` - Added `_shipping_badge()`, `_render_price_bar_section()`, updated `product_caption()` signature with `israel_result` param
- `tests/test_formatter_visual.py` - 11 unit tests for visual formatter features (new file)

## Decisions Made

- `israel_result` parameter placed AFTER `israel_status` in `product_caption()` signature for backward compatibility — existing callers pass positional args up to `price_history` and are unaffected
- Telegram backtick wrapping applied per-line (each bar line wrapped independently) matching the established pattern in style._price_history_line()
- WhatsApp/Instagram receive plain escaped text — backtick characters would render literally on those platforms
- Fallback chain preserved: if `_render_price_bar_section()` returns empty (e.g., ph has no `.current` field), old plain-text `avg_90d`/`low_all_time` fallback activates

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

Pre-existing test `tests/test_malformed_responses.py::TestAnthropicProviderErrors::test_anthropic_api_error_raises` fails due to Anthropic SDK vs httpx version incompatibility (`proxies` argument removed in newer httpx). This is out of scope — not caused by changes in this plan. All 591 other tests pass.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `formatter.py` now has `israel_result` parameter ready to receive live scraper results
- Plan 07-02 can wire `bot_core.py` to pass `session._last_israel_result` directly to `fmt.product_caption(israel_result=...)` instead of converting to the string enum
- Price bar and shipping badge will appear identically on WhatsApp and Instagram as on Telegram

---
*Phase: 07-multi-platform-visual-parity*
*Completed: 2026-03-14*
