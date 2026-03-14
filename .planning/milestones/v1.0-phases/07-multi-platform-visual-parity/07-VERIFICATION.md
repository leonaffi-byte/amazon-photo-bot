---
phase: 07-multi-platform-visual-parity
verified: 2026-03-14T18:00:00Z
status: passed
score: 12/12 must-haves verified
re_verification: false
---

# Phase 7: Multi-Platform Visual Parity Verification Report

**Phase Goal:** WhatsApp and Instagram users receive the same visual experience as Telegram users — annotated overlays, shipping badges, price bars, and 4-stage progress messages
**Verified:** 2026-03-14T18:00:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | `Formatter.product_caption` includes color emoji shipping badge (green/yellow/red) when `israel_result` is provided | VERIFIED | `formatter.py` lines 128-142: `_shipping_badge()` returns green/yellow/red emoji based on `ships_to_israel` and `is_free_shipping`; integrated into `product_caption` at line 373-376 |
| 2  | `Formatter.product_caption` includes ASCII price bar when `price_history` has valid current/range data | VERIFIED | `formatter.py` lines 144-188: `_render_price_bar_section()` calls `render_price_bar(ph)` from style.py and integrates into `product_caption` at line 354 |
| 3  | `Formatter.product_caption` includes deal label when `price_history.deal_label` is set | VERIFIED | `formatter.py` line 171: `deal_suffix = f" · {self._esc(deal_label)}" if deal_label else ""` appended to summary line |
| 4  | Telegram output wraps bar lines in backtick monospace blocks; WhatsApp/Instagram renders plain text | VERIFIED | `formatter.py` lines 180-185: `if self.platform == "telegram": formatted_bar_lines = [f"\`{self._esc(line)}\`" ...]` else plain `self._esc(line)` |
| 5  | `bot_core.py` calls `annotate_with_overlays` (not `annotate_products`) for multi-product annotation | VERIFIED | `bot_core.py` line 1055-1062: `from image_annotator import annotate_with_overlays, annotate_products` — primary call is `annotate_with_overlays` via `asyncio.to_thread`, `annotate_products` retained only as fallback in `except` clause |
| 6  | `annotate_with_overlays` is called via `asyncio.to_thread` (not synchronously) | VERIFIED | `bot_core.py` lines 1057-1058: `annotated_bytes = await asyncio.to_thread(annotate_with_overlays, image_bytes, detected_products)` and line 717-718 for single-product path |
| 7  | `_compress_image` is called via `asyncio.to_thread` (not synchronously) | VERIFIED | `bot_core.py` line 992: `image_bytes = await asyncio.to_thread(_compress_image, raw_bytes)` |
| 8  | Stage 3 progress message (`loading_search`) is sent during `_search_and_render` | VERIFIED | `bot_core.py` lines 651-658: `await self.adapter.edit_text(loading_msg_ref, text=fmt.loading_search())` |
| 9  | Stage 4 progress message (checking Israel shipping) is sent after search before render | VERIFIED | `bot_core.py` lines 702-710: `await self.adapter.edit_text(loading_msg_ref, text="\U0001f1ee\U0001f1f1 " + fmt._esc(t("product_israel_checking", lang=lang)))` |
| 10 | `UserSession` has `annotated_bytes` field | VERIFIED | `bot_core.py` line 109: `annotated_bytes: Optional[bytes] = None` in `UserSession` dataclass |
| 11 | `_render_product` uses `session.annotated_bytes` as image source when present | VERIFIED | `bot_core.py` line 415: `image_source = session.annotated_bytes if session.annotated_bytes else (item.image_url or _PLACEHOLDER_IMG)` |
| 12 | Overlay failure is non-fatal (try/except sets `annotated_bytes` to None) | VERIFIED | `bot_core.py` lines 714-722: try/except around `annotate_with_overlays`, sets `session.annotated_bytes = None` on failure; same pattern in lines 1060-1062 for multi-product path |

**Score:** 12/12 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `formatter.py` | Shipping badge and price bar rendering in `product_caption` | VERIFIED | Contains `_shipping_badge()` (line 128), `_render_price_bar_section()` (line 144), `israel_result` param in `product_caption` (line 310); 423 lines total — substantive |
| `tests/test_formatter_visual.py` | Unit tests for visual formatter features, min 60 lines | VERIFIED | 184 lines, 11 tests covering all badge tiers, price bar rendering, platform differences, deal labels, backward compatibility |
| `bot_core.py` | Overlay wiring, async compress, 4-stage progress, `annotated_bytes` in render | VERIFIED | Contains `annotate_with_overlays` (lines 715, 1055), `asyncio.to_thread` wrappers, Stage 4 edit (702-710), `annotated_bytes` field and usage |
| `tests/test_bot_core_overlays.py` | Tests for overlay call, progress stages, `annotated_bytes` usage, min 80 lines | VERIFIED | 457 lines, 7 tests covering all required behaviors |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `formatter.py` | `style.py` | `from style import render_price_bar` | WIRED | `formatter.py` line 15: `from style import render_price_bar`; called at line 175 |
| `formatter.py` | `bot_core.py` | `product_caption(israel_result=...)` parameter | WIRED | `bot_core.py` line 410: `israel_result=session._last_israel_result` passed in `_render_product`; line 614: `israel_result=israel_result` in `_update_caption_after_enrichment` |
| `bot_core.py` | `image_annotator.py` | `from image_annotator import annotate_with_overlays` | WIRED | `bot_core.py` lines 715 and 1055: inline import `from image_annotator import annotate_with_overlays`; called via `asyncio.to_thread` |
| `bot_core.py` | `formatter.py` | `product_caption(israel_result=...)` call | WIRED | `bot_core.py` lines 405-413: `fmt.product_caption(..., israel_result=session._last_israel_result, price_history=session._last_price_history, ...)` |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| ISRL-02 | 07-01 | Each product result shows confidence-scored shipping badge (green = ships free, yellow = likely, red = won't ship) | SATISFIED | `formatter.py._shipping_badge()` returns green/yellow/red emoji; called in `product_caption` when `israel_result` provided; tested in `test_formatter_visual.py` |
| PRCE-02 | 07-01 | Product results include ASCII-style price bar showing current price position within 90-day range | SATISFIED | `formatter.py._render_price_bar_section()` imports `render_price_bar` from `style.py` and integrates it into `product_caption`; tested `test_price_bar_in_caption` |
| PRCE-03 | 07-01 | Deal quality indicator shown on results ("Good deal" / "Average price" / "Overpriced") | SATISFIED | `formatter.py` line 171: `deal_suffix` appended from `ph.deal_label`; tested `test_deal_label_in_caption` |
| ANNO-02 | 07-02 | Bot sends back annotated photo with semi-transparent overlays on each detected product | SATISFIED | `bot_core.py`: `annotate_with_overlays` called via `asyncio.to_thread` in both multi-product path (line 1057) and single-product `_search_and_render` path (line 717); `session.annotated_bytes` used as image source in `_render_product` |
| ANNO-03 | 07-02 | If bounding box quality is low, fall back to numbered circles at approximate positions | SATISFIED | `bot_core.py` line 1062: `annotate_products(image_bytes, detected_products)` called as fallback in `except` clause when `annotate_with_overlays` fails; `annotate_with_overlays` itself internally falls back to `annotate_products` on low-quality bboxes (per `image_annotator.py` contract) |
| ANNO-04 | 07-02 | User sees streaming progress updates during analysis ("Analyzing photo...", "Found 3 products...", "Searching Amazon...") | SATISFIED | 4-stage progress confirmed: Stage 1 loading_vision (line 990), Stage 2 identification card (line 1042), Stage 3 loading_search (line 655), Stage 4 Israel checking (line 707) |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot_core.py` | 623 | `_update_caption_after_enrichment` uses `item.image_url` not `session.annotated_bytes` when editing photo after enrichment | Info | Annotated bytes not re-sent on enrichment edit — acceptable as `annotated_bytes` is already the rendered card image; this is a background caption text update, not a primary render |

No blocker or warning severity anti-patterns found. The `item.image_url` usage in `_update_caption_after_enrichment` is intentional — that function updates only the text caption (or re-sends the raw Amazon image), and was not required by either plan to use `annotated_bytes`.

### Human Verification Required

No items require human verification. All phase goal behaviors are verifiable via the code and passing tests.

### Test Results

```
18 passed in 0.98s
tests/test_formatter_visual.py   -- 11 passed (badge tiers, price bar, platform formatting, deal labels, backward compat)
tests/test_bot_core_overlays.py  -- 7 passed (overlay call, async compress, non-fatal failure, stage 4 progress, annotated_bytes routing, israel_result propagation)
```

All 4 TDD commits verified in git history:
- `a791ba7` test(07-01): failing tests for shipping badge and price bar
- `d2c498d` feat(07-01): shipping badge and price bar in formatter.py
- `54f0443` test(07-02): failing tests for overlay wiring
- `c3a1fef` feat(07-02): overlay wiring in bot_core.py

### Gaps Summary

No gaps. All must-haves from both plans are fully implemented, wired, and tested.

---

_Verified: 2026-03-14T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
