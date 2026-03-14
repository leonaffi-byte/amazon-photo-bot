---
phase: 02-enhanced-visual-experience
plan: 01
subsystem: style
tags: [visual, price-history, ascii-bar, formatting, markdownv2]
dependency_graph:
  requires: []
  provides: [render_price_bar, enhanced _price_history_line]
  affects: [style.py, product_caption, tests/test_style.py, tests/test_price_history.py]
tech_stack:
  added: []
  patterns: [ASCII bar rendering with Unicode block chars, MarkdownV2 monospace wrapping]
key_files:
  created: []
  modified:
    - style.py
    - tests/test_price_history.py
    - tests/test_style.py
decisions:
  - render_price_bar does not escape for MarkdownV2 (caller handles escaping)
  - Bar lines wrapped in backtick monospace blocks for proper Telegram rendering
  - low_90d preferred as range low; falls back to low_all_time
  - avg_90d preferred as range high; falls back to current * 1.3 (synthetic)
  - Equal range (low == high) handled via synthetic 30% expansion
metrics:
  duration: ~12 min
  completed: 2026-03-14
  tasks_completed: 2
  files_modified: 3
---

# Phase 2 Plan 1: ASCII Price Bar Rendering Summary

**One-liner:** ASCII price bar using Unicode block chars (█/─) placed in MarkdownV2 monospace blocks showing current price position in 90-day range with deal quality label.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add render_price_bar() and test suite | bee0d55 | style.py, tests/test_price_history.py |
| 2 | Update _price_history_line() to include price bar | ce5a7ff | style.py, tests/test_style.py |

## What Was Built

### render_price_bar() — style.py

New function producing a 2-3 line ASCII visualization:

```
$89 ████████── $149
        ^ $112 now
💸 Great deal
```

- Range low: `low_90d` (fallback: `low_all_time`)
- Range high: `avg_90d` (fallback: `current * 1.3` synthetic)
- Equal range: expanded by 30% to avoid division by zero
- Deal label from `ph.deal_label` appended as third line when present
- Returns empty string when `current` is None or range is invalid

### _price_history_line() update — style.py

Enhanced to include the bar below the summary line:

```
📊 _ATL $24.99 · 90d avg $38.50 · ✅ Below avg_
`$89 ████████── $149`
`     ^ $112 now`
```

- Bar lines wrapped in backtick monospace blocks for proper Telegram rendering
- Each line escaped with `esc()` for MarkdownV2 compliance
- Falls back to summary-only when `render_price_bar()` returns empty string
- `product_caption` verified to stay under 1024 chars with bar (~80 extra chars)

### Tests Added

**tests/test_price_history.py — TestPriceBar (11 tests):**
- Normal case: multiline, contains price labels, contains current price
- Edge cases: pointer at left/right edge, equal range, None current
- Fallback: low_all_time used when low_90d is None
- Content: block chars present, deal label when applicable, no deal label when not

**tests/test_style.py — TestPriceHistoryLineWithBar (6 tests):**
- Bar present (backtick blocks) when full data available
- Summary-only when no current price
- Empty when ph is None
- Starts with chart emoji
- product_caption under 1024 chars with bar

## Decisions Made

1. **render_price_bar does not escape for MarkdownV2** — keeps the function clean and testable; caller (_price_history_line) applies `esc()` and backtick wrapping.
2. **Backtick monospace wrapping** — Unicode block characters render correctly in Telegram when in inline code blocks; otherwise they may display inconsistently.
3. **Synthetic range fallback** — when avg_90d is missing, use `current * 1.3` so the function can still produce a bar rather than silently returning empty.

## Deviations from Plan

### Pre-existing Issue (Out-of-scope, Deferred)

**TestParseCccHtml::test_fallback_to_fullpage_scan** was already failing before Phase 02 work began (verified via git stash). Root cause: `_parse_ccc_html` only calls `_extract_stats_from_section` when it finds an element with id/class matching "amazon"; the test fixture lacks such an element.

Logged to `deferred-items.md` in phase directory. Not fixed — out-of-scope for this plan.

## Verification

```
pytest tests/test_style.py tests/test_price_history.py -k "not test_fallback_to_fullpage_scan"
107 passed, 1 deselected in 0.37s
```

All new tests pass. The 1 deselected test is a pre-existing failure unrelated to this plan.

## Self-Check: PASSED

Files exist:
- style.py: FOUND (contains `def render_price_bar`)
- tests/test_price_history.py: FOUND (contains `TestPriceBar`)
- tests/test_style.py: FOUND (contains `TestPriceHistoryLineWithBar`)

Commits exist:
- bee0d55: feat(02-01): add render_price_bar()
- ce5a7ff: feat(02-01): update _price_history_line()
