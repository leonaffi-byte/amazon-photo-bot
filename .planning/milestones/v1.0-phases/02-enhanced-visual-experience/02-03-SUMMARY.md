---
phase: 02-enhanced-visual-experience
plan: 03
subsystem: ui
tags: [pillow, image-annotation, bbox, overlay, tdd]

# Dependency graph
requires:
  - phase: 02-enhanced-visual-experience
    provides: image_annotator.py annotate_products() legend strip foundation
provides:
  - annotate_with_overlays() overlay mode for image annotation
  - _is_bbox_reliable() bbox validation filter
  - _draw_overlay() semi-transparent RGBA overlay compositing
  - ANNO-01/ANNO-02/ANNO-03 requirements verified by tests
affects:
  - bot.py (when invoking annotation functions)
  - 02-enhanced-visual-experience phase 04+ (overlay mode available)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TDD red-green cycle: failing tests committed before implementation"
    - "Overlay compositing via PIL Image.alpha_composite with RGBA layers"
    - "Bbox reliability gate: area fraction + bounds check before drawing"

key-files:
  created:
    - tests/test_image_annotator.py
  modified:
    - image_annotator.py
    - tests/test_providers_base.py

key-decisions:
  - "Bbox area threshold: < 1% or > 90% of image area is unreliable — rejects tiny noise bboxes and full-image bboxes from confused models"
  - "Overlay fallback: if ANY reliable bbox exists, use overlay mode; only fall back to legend strip when ALL bboxes are absent/unreliable"
  - "RGBA intermediate: draw on RGBA canvas then convert to RGB for JPEG output to avoid PIL compositing issues"

patterns-established:
  - "Overlay annotation: _draw_overlay always returns RGBA, caller converts to RGB before JPEG save"
  - "Bbox validation: all callers of bbox data should pass through _is_bbox_reliable before use"

requirements-completed: [ANNO-01, ANNO-02, ANNO-03]

# Metrics
duration: 4min
completed: 2026-03-14
---

# Phase 2 Plan 03: Overlay Annotation Mode Summary

**Semi-transparent product overlay annotation using PIL alpha compositing with bbox reliability gate, falling back to legend strip when bboxes are absent**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-03-14T01:28:45Z
- **Completed:** 2026-03-14T01:32:39Z
- **Tasks:** 2 (Tasks 1 and 2 implemented together in TDD GREEN phase)
- **Files modified:** 3

## Accomplishments

- Verified ANNO-01: `to_product_info_list()` correctly maps bbox from provider JSON to `ProductInfo.bbox` (tuple from list)
- Added `_is_bbox_reliable()` that rejects zero-area, too-small (< 1%), too-large (> 90%), and out-of-bounds bboxes
- Added `_draw_overlay()` that composites a semi-transparent colored rectangle with product number onto an RGBA image using PIL `alpha_composite`
- Added `annotate_with_overlays()` that selects overlay mode when any bbox is reliable, falls back to `annotate_products()` legend strip otherwise
- 37 tests pass (17 new in test_image_annotator.py, 3 new in test_providers_base.py)

## Task Commits

1. **RED: Failing tests** - `3269cea` (test)
2. **GREEN: Implementation** - `31d8b14` (feat)

_Note: TDD tasks use separate test and implementation commits_

## Files Created/Modified

- `image_annotator.py` - Added `_is_bbox_reliable()`, `_draw_overlay()`, `annotate_with_overlays()` (151 new lines)
- `tests/test_image_annotator.py` - New test file: `TestIsBboxReliable` (8 tests), `TestDrawOverlay` (4 tests), `TestAnnotateWithOverlays` (5 tests)
- `tests/test_providers_base.py` - Added `TestBboxMapping` (3 tests) verifying ANNO-01 bbox flow

## Decisions Made

- Bbox area threshold defined as fraction of total image area: `(w * h) / 10000.0`. Below 1% = noise, above 90% = whole-image hallucination.
- Overlay fallback strategy: if at least one product has a reliable bbox, use overlay mode (not legend strip). Products with unreliable/missing bboxes get no overlay and no legend entry in this mode.
- RGBA intermediate approach: `_draw_overlay` always returns RGBA so callers can chain multiple calls; `annotate_with_overlays` converts to RGB only before final JPEG save.

## Deviations from Plan

None - plan executed exactly as written. Task 1 and Task 2 were implemented together in a single TDD green phase since `annotate_with_overlays` depends directly on the Task 1 helpers.

## Issues Encountered

- A linter reverted the first Edit attempt to `image_annotator.py`. Recovered by using the Write tool to write the complete file directly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `annotate_with_overlays()` is available for bot.py integration when a photo is analyzed with multiple products
- The bbox reliability gate ensures robustness across providers that return poor bbox data
- `annotate_products()` legend strip remains unchanged as the fallback path

---
*Phase: 02-enhanced-visual-experience*
*Completed: 2026-03-14*
