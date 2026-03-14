---
phase: 07-multi-platform-visual-parity
plan: 02
subsystem: bot_core
tags: [overlay, annotation, async, progress, whatsapp, instagram, telegram, image-annotator]

# Dependency graph
requires:
  - phase: 07-multi-platform-visual-parity
    plan: 01
    provides: formatter.py with israel_result= parameter in product_caption()
  - phase: 02-enhanced-visual-experience
    provides: image_annotator.py annotate_with_overlays()
provides:
  - bot_core.py: annotate_with_overlays wired into multi-product and single-product paths
  - bot_core.py: _compress_image called via asyncio.to_thread (non-blocking)
  - bot_core.py: Stage 4 progress message (Israel checking) during _search_and_render
  - bot_core.py: session.annotated_bytes used as image source in _render_product
  - bot_core.py: israel_result= and price_history= passed to formatter in _render_product
  - bot_core.py: israel_result= passed to formatter in _update_caption_after_enrichment
affects:
  - All platforms (Telegram, WhatsApp, Instagram) get overlay-annotated product images

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "annotate_with_overlays called via asyncio.to_thread to avoid blocking event loop"
    - "Overlay failure is non-fatal: try/except sets session.annotated_bytes = None, flow continues"
    - "image_source = session.annotated_bytes if session.annotated_bytes else (item.image_url or placeholder)"
    - "Stage 4 progress edit uses fmt._esc(t('product_israel_checking')) with flag emoji prefix"

key-files:
  created:
    - tests/test_bot_core_overlays.py
  modified:
    - bot_core.py

key-decisions:
  - "annotate_with_overlays replaces annotate_products in multi-product path; annotate_products kept only as fallback in except clause"
  - "_compress_image wrapped in asyncio.to_thread to prevent event loop blocking on CPU-bound Pillow operations"
  - "Single-product overlay generated inside _search_and_render (after search, before render) so all platforms get annotated image"
  - "Stage 4 progress message placed before overlay generation and _render_product call for accurate user feedback"
  - "israel_result= and price_history= both passed to product_caption in _render_product (previously only in _update_caption_after_enrichment)"

requirements-completed: [ANNO-02, ANNO-03, ANNO-04]

# Metrics
duration: 54min
completed: 2026-03-14
---

# Phase 7 Plan 02: Multi-Platform Visual Parity - Bot Core Overlay Wiring Summary

**Wired annotate_with_overlays (async via to_thread), 4-stage progress messages, session.annotated_bytes image routing, and israel_result parameter propagation into bot_core.py for full visual parity across Telegram, WhatsApp, and Instagram**

## Performance

- **Duration:** 54 min
- **Started:** 2026-03-14T14:36:23Z
- **Completed:** 2026-03-14T15:30:30Z
- **Tasks:** 1 (TDD: test + feat commits)
- **Files modified:** 2

## Accomplishments

- Added `annotated_bytes: Optional[bytes] = None` field to `UserSession` dataclass for overlay byte storage
- Fixed `_compress_image` to run via `asyncio.to_thread` (was blocking the event loop with CPU-bound Pillow operations)
- Replaced `annotate_products` call with `annotate_with_overlays` in multi-product path, wrapped in `asyncio.to_thread`, with `annotate_products` as fallback in except clause
- Added Stage 4 progress message (Israel flag + "Checking Israel shipping...") in `_search_and_render` after search completes
- Added single-product overlay generation in `_search_and_render` (stored in `session.annotated_bytes`)
- Updated `_render_product` to use `session.annotated_bytes` as image source when present (falls back to `item.image_url`)
- Updated `_render_product` to pass both `israel_result=` and `price_history=` to `product_caption`
- Updated `_update_caption_after_enrichment` to pass `israel_result=` to `product_caption`
- 7 unit tests covering all behaviors

## Task Commits

Each task committed atomically (TDD: two commits):

1. **Task 1 RED: Failing tests** - `54f0443` (test)
2. **Task 1 GREEN: Implementation** - `c3a1fef` (feat)

## Files Created/Modified

- `bot_core.py` - UserSession.annotated_bytes field, async _compress_image, annotate_with_overlays, Stage 4 progress, image routing, israel_result propagation
- `tests/test_bot_core_overlays.py` - 7 unit tests for all overlay wiring behaviors (new file)

## Decisions Made

- `annotate_with_overlays` replaces `annotate_products` as the primary annotation function; `annotate_products` retained in the except clause as legacy fallback
- `_compress_image` wrapped in `asyncio.to_thread` to unblock the event loop for CPU-bound Pillow resize/compress operations
- Single-product overlay generated in `_search_and_render` (not `handle_photo`) so it happens after search completes and product_info is confirmed
- Stage 4 progress message positioned before overlay generation and `_render_product` call to give accurate progress feedback
- `israel_result=` and `price_history=` added to `_render_product`'s `product_caption` call for completeness (previously only `_update_caption_after_enrichment` included them)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

Pre-existing: `tests/test_malformed_responses.py` hangs with timeout due to Anthropic SDK vs httpx version incompatibility (`proxies` argument removed in newer httpx). This is the same pre-existing issue documented in 07-01-SUMMARY. Not caused by changes in this plan.

## Self-Check: PASSED
