---
phase: 02-enhanced-visual-experience
plan: "04"
subsystem: bot-ux
tags: [progress-messages, shipping-badge, overlay-wiring, telegram-bot]
dependency_graph:
  requires: [02-01, 02-02, 02-03]
  provides: [4-stage-progress, shipping-badge, overlay-photo-send]
  affects: [bot.py, style.py]
tech_stack:
  added: []
  patterns: [asyncio.to_thread, InputFile-bytes, badge-formatter, try-except-graceful-edit]
key_files:
  created: []
  modified:
    - style.py
    - tests/test_style.py
    - bot.py
    - tests/test_bot.py
decisions:
  - shipping_badge uses result.ships_to_israel + result.is_free_shipping to select emoji tier
  - product_caption uses shipping_badge when israel_verified is not None (even if unverified)
  - annotate_with_overlays failure is non-fatal — session.annotated_bytes set to None on error
  - stage 1 message stored as progress_msg_id on UserSession for continuity across handlers
  - loading_vision replaced entirely by "Analysing your photo..." for clean 4-stage UX
metrics:
  duration_minutes: 8
  completed_date: "2026-03-14"
  tasks_completed: 2
  files_changed: 4
---

# Phase 2 Plan 4: Bot Integration — 4-Stage Progress, Shipping Badges, and Overlays Summary

**One-liner:** Wired all Phase 2 visual features into the Telegram bot flow: 4-stage progress messages, shipping_badge() formatter, and annotated photo overlays delivered via asyncio.to_thread.

## What Was Built

### Task 1: shipping_badge() in style.py + product_caption update

Added `shipping_badge(result) -> str` to `style.py` that converts an `IsraelShippingResult` into an emoji+text badge:

- `🟢 Ships free to Israel` — verified + ships + free
- `🟡 Likely ships to Israel` — verified + ships + not free
- `🔴 Won't ship to Israel` — verified + does not ship
- `⚪ Israel shipping unknown` — None or unverified

Updated `product_caption()` to call `shipping_badge()` when `israel_verified` is provided (replacing raw `israel_verified.note`). When `israel_verified` is `None`, the existing heuristic `item.israel_delivery_note` continues to be used.

### Task 2: 4-stage progress + overlay wiring in bot.py

**handle_photo (stages 1-2):**
- Stage 1 replaces `loading_vision` call: sends `"🔍 Analysing your photo\.\.\."` and stores `msg.message_id` as `session.progress_msg_id`
- Stage 2: edits the same message with the `identification_card` after vision analysis (wrapped in try-except)
- Compare mode path left unchanged

**handle_callback CB_FILTER_YES/NO (stages 3-4):**
- Stage 3 replaces `loading_search` call: edits message with `"🛒 Comparing prices\.\.\."`
- After `search_amazon()` returns: Stage 4 edits with `"🇮🇱 Checking Israel shipping\.\.\."`
- Then calls `annotate_with_overlays` via `asyncio.to_thread` (non-fatal: failure sets `session.annotated_bytes = None`)
- Then calls `_render_results` as before

**_render_results (overlay photo send):**
- On first render: if `session.annotated_bytes` is not None, sends `InputFile(BytesIO(annotated_bytes), filename="results.jpg")` instead of the plain image URL
- Falls back to URL if no annotated bytes available

**New UserSession fields:**
- `annotated_bytes: Optional[bytes]` — holds annotated photo from image_annotator
- `progress_msg_id: Optional[int]` — tracks stage 1 message ID

## Tests Added

- `test_style.py`: 6 tests covering all `shipping_badge()` tiers and `product_caption` badge integration
- `test_bot.py`: 7 tests covering stages 1-4 progress messages, progress_msg_id storage, and annotate_with_overlays call verification

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 9b2d53b | feat(02-04): add shipping_badge() to style.py and update product_caption |
| 2 | 19ec89f | feat(02-04): wire 4-stage progress and overlays into bot flow |

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- `style.py` exists and contains `def shipping_badge`
- `bot.py` exists and contains "Checking Israel shipping"
- `tests/test_style.py` exists and contains "shipping_badge"
- `tests/test_bot.py` exists and contains "progress"
- Commit `9b2d53b` exists
- Commit `19ec89f` exists
- All 118 tests in test_bot.py + test_style.py pass
