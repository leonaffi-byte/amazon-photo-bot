# Phase 7: Multi-Platform Visual Parity - Research

**Researched:** 2026-03-14
**Domain:** Python bot_core.py / formatter.py / image_annotator.py wiring
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ANNO-02 | Bot sends back annotated photo with semi-transparent overlays on each detected product | `annotate_with_overlays()` already exists in `image_annotator.py`; `bot_core.py` imports `annotate_products` (legend strip) instead — fix is a targeted import swap + asyncio.to_thread call |
| ANNO-03 | If bounding box quality is low, fall back to numbered circles at approximate positions | `annotate_with_overlays()` already implements this fallback internally (line 264: `if not reliable: return annotate_products(...)`) — no additional logic needed |
| ANNO-04 | User sees streaming progress updates during analysis ("Analyzing photo...", "Found 3 products...", "Searching Amazon...") | `bot.py` has 4-stage progress (Stage 1: loading, Stage 2: id card, Stage 3: "Comparing prices...", Stage 4: "Checking Israel shipping..."); `bot_core.py` has only single `loading_ref` — add Stage 2/3/4 edit calls in `handle_photo` and `handle_callback` |
| PRCE-02 | Product results include ASCII-style price bar showing current price position within 90-day range | `render_price_bar()` in `style.py` is complete; `formatter.py.product_caption()` accepts `price_history` but renders only a plain text line (no bar call); `_price_history_line()` from `style.py` needs to be ported to `formatter.py` |
| PRCE-03 | Deal quality indicator shown on results ("Good deal" / "Average price" / "Overpriced") | `deal_label` is already included in `_price_history_line()` in `style.py`; `formatter.py` accesses `price_history.deal_label` but only appends as plain text — needs ASCII bar + deal label rendering |
| ISRL-02 | Each product result shows confidence-scored shipping badge (green = ships free, yellow = likely, red = won't ship) | `shipping_badge()` in `style.py` returns emoji+text badge from `IsraelShippingResult`; `formatter.py.product_caption()` uses a string enum (`israel_status: "yes"/"no"/"free"`) but no badge — needs `shipping_badge()` ported and called |

</phase_requirements>

---

## Summary

Phase 7 closes the integration gap identified in INT-01 and FLOW-01 of the v1.0 audit. All Phase 2 visual features (annotated overlays, shipping badges, ASCII price bars, deal labels, and 4-stage progress messages) are fully implemented in `style.py` and `bot.py`, but were never wired into the multi-platform path (`formatter.py` and `bot_core.py`).

This is a pure wiring/porting phase. No new algorithms need to be invented. The existing implementations in `style.py` and `image_annotator.py` are the source of truth. The planner's job is to port those implementations into the correct multi-platform locations with appropriate platform-aware adjustments.

The three change sites are: (1) `formatter.py` — add `shipping_badge()` logic and `_price_history_line()` + `render_price_bar()` rendering; (2) `bot_core.py` — replace `annotate_products` import with `annotate_with_overlays`, wrap with `asyncio.to_thread`, and also fix the synchronous `_compress_image` call that blocks the event loop; (3) `bot_core.py.handle_photo` — add Stages 2/3/4 progress edits matching the pattern from `bot.py`.

**Primary recommendation:** Port `shipping_badge`, `render_price_bar`, and `_price_history_line` logic into `formatter.py`, then wire `annotate_with_overlays` and 4-stage progress into `bot_core.py`.

---

## Standard Stack

### Core — already present, no new dependencies
| Module | Location | Purpose |
|--------|----------|---------|
| `image_annotator.annotate_with_overlays` | `image_annotator.py` line 240 | Semi-transparent overlay annotation with bbox fallback to legend strip |
| `style.shipping_badge` | `style.py` line 422 | Emoji shipping badge from `IsraelShippingResult` |
| `style.render_price_bar` | `style.py` line 315 | ASCII bar showing current price in 90d range |
| `style._price_history_line` | `style.py` line 380 | Combined summary + bar rendering |
| `asyncio.to_thread` | stdlib | Offload Pillow CPU work to thread pool (already used in `bot.py` line 687) |

### No new packages required
All needed logic is in existing project modules. Phase 7 is entirely internal refactoring.

---

## Architecture Patterns

### Pattern 1: Port Logic into Formatter (Platform-Aware)

`formatter.py` is platform-aware. `style.py` is Telegram-only (uses MarkdownV2 escaping and backtick monospace blocks). When porting:

- `shipping_badge()` returns plain emoji+text — safe on all platforms; no escaping needed beyond `_esc()` wrapper.
- `render_price_bar()` returns raw Unicode block chars — in `style.py` these are wrapped in backtick monospace blocks. On Telegram (via `formatter.py`) keep backtick wrapping. On WhatsApp/Instagram, do NOT use backticks (monospace not universally supported); render as plain text instead.
- `deal_label` in `price_history` is already plain text — safe as-is.

**Decision already in STATE.md:** `render_price_bar does not escape for MarkdownV2 — caller handles escaping and monospace wrapping` and `Bar lines wrapped in backtick monospace blocks for proper Unicode block char rendering in Telegram`.

```python
# formatter.py: platform-aware price bar rendering (reference pattern from style.py)
def _render_price_bar_section(self, ph) -> str:
    """Port of style._price_history_line — platform-aware."""
    if not ph:
        return ""
    parts = []
    if getattr(ph, "low_all_time", None):
        parts.append(f"ATL ${ph.low_all_time:.2f}")
    if getattr(ph, "avg_90d", None):
        parts.append(f"90d avg ${ph.avg_90d:.2f}")
    elif getattr(ph, "avg_30d", None):
        parts.append(f"30d avg ${ph.avg_30d:.2f}")
    if not parts:
        return ""
    deal = getattr(ph, "deal_label", "")
    summary_raw = " | ".join(parts)
    if deal:
        summary_raw += f" | {deal}"
    summary_line = "\U0001f4ca " + self._esc(summary_raw)

    # ASCII bar
    bar_raw = _render_price_bar(ph)
    if not bar_raw:
        return "\n" + summary_line
    if self.platform == "telegram":
        # Telegram: monospace backtick blocks
        bar_lines = bar_raw.splitlines()
        bar_block = "\n".join(f"`{self._esc(line)}`" for line in bar_lines)
        return f"\n{summary_line}\n{bar_block}"
    # WhatsApp/Instagram: plain text
    return f"\n{summary_line}\n{self._esc(bar_raw)}"
```

### Pattern 2: shipping_badge() Port into formatter.py

`shipping_badge()` in `style.py` takes an `IsraelShippingResult` object. In `bot_core.py`, the israel result is stored as `session._last_israel_result`. The `product_caption()` method in `formatter.py` already receives `israel_status: str | None` — the planner should decide whether to:

**Option A (recommended):** Add a new parameter `israel_result=None` alongside `israel_status`, and derive the badge from the result object when available (mirrors what `style.product_caption` does at line 280-283).

**Option B:** Pre-compute the badge string in `bot_core.py._update_caption_after_enrichment` and pass it as `israel_status`. This avoids API change to `formatter.py` but loses the typed result.

Option A is cleaner and more testable. The planner should use Option A.

```python
# formatter.py addition: shipping badge rendering
def _shipping_badge(self, israel_result) -> str:
    """Port of style.shipping_badge — returns emoji+text badge."""
    if israel_result is None or not getattr(israel_result, "verified", False):
        return ""  # no badge when unverified
    ships = getattr(israel_result, "ships_to_israel", None)
    free = getattr(israel_result, "is_free_shipping", False)
    if ships and free:
        return "\U0001f7e2 " + self._esc("Ships free to Israel")
    if ships:
        return "\U0001f7e1 " + self._esc("Likely ships to Israel")
    return "\U0001f534 " + self._esc("Won't ship to Israel")
```

### Pattern 3: annotate_with_overlays in bot_core.py

Current `bot_core.py` line 1026: `from image_annotator import annotate_products`.

Fix: replace with `from image_annotator import annotate_with_overlays` and call via `asyncio.to_thread`.

Also: `_compress_image` on line 963 is called synchronously (tech debt from AUDIT). Fix it here too using `await asyncio.to_thread(_compress_image, raw_bytes)` matching `bot.py` line 94.

```python
# bot_core.py: replace legend-strip annotation with overlay annotation
# BEFORE (line 1026-1027):
from image_annotator import annotate_products
annotated_bytes = annotate_products(image_bytes, detected_products)

# AFTER:
from image_annotator import annotate_with_overlays
annotated_bytes = await asyncio.to_thread(annotate_with_overlays, image_bytes, detected_products)
```

For the single-product search flow (handle_callback CB_FILTER_YES/CB_FILTER_NO), the overlay generation that exists in `bot.py` at line 683-692 is entirely absent from `bot_core.py`. The session has `image_bytes` and `chosen_result` available. Add the overlay generation step before `_render_product` — store result in `session.annotated_bytes` (field must be added to `UserSession` dataclass).

### Pattern 4: 4-Stage Progress Messages in bot_core.py

`bot.py` has 4 explicit stages. `bot_core.py.handle_photo` has only Stage 1 (single loading message). The stage calls must be added:

| Stage | bot.py text | When |
|-------|------------|------|
| 1 | "Analysing your photo..." | On `send_text` before vision call |
| 2 | identification card | After vision returns, edit loading_ref |
| 3 | "Comparing prices..." | After user picks filter, before Amazon search |
| 4 | "Checking Israel shipping..." | Before overlay generation, before _render_product |

Stage 2 is already done in `bot_core.py` line 1082 (`edit_text(loading_ref, text=card_text)`). Stages 3 and 4 are missing from the `handle_callback` CB_FILTER_YES/NO path.

```python
# bot_core.py: add stages 3 and 4 in handle_callback (CB_FILTER_YES / CB_FILTER_NO)

# Stage 3: update loading message to "Searching Amazon..."
if msg_ref:
    try:
        await self.adapter.edit_text(msg_ref, text=fmt.loading_search())
    except Exception:
        pass

# ... run search_amazon ...

# Stage 4: update to "Checking Israel shipping..."
if msg_ref:
    try:
        await self.adapter.edit_text(msg_ref, text="\U0001f1ee\U0001f1f1 " + self._esc(t("checking_israel", lang=lang)))
    except Exception:
        pass

# ... run overlay generation ...
# ... call _render_product ...
```

Note: `Formatter` already has `loading_search()` (line 139) and `loading_vision()` (line 135). A `loading_israel()` message may need to be added or an existing i18n key reused.

### Recommended Change Sites (3 files only)

```
formatter.py          # Add _shipping_badge(), _render_price_bar_section(), update product_caption()
bot_core.py           # 3 fixes:
                      #   1. annotate_with_overlays (import swap + asyncio.to_thread)
                      #   2. asyncio.to_thread for _compress_image
                      #   3. Stage 3/4 progress messages in handle_callback
                      #   4. UserSession.annotated_bytes field
tests/                # New test file for formatter visual features
                      # New test for bot_core overlay + progress
```

### Anti-Patterns to Avoid

- **Don't duplicate logic:** Do not copy-paste `render_price_bar()` from `style.py` into `formatter.py`. Extract shared pure logic into a helper or call the existing function.
- **Don't block the event loop:** `annotate_with_overlays` uses Pillow (CPU-bound). Always wrap with `asyncio.to_thread`.
- **Don't add backticks on non-Telegram platforms:** WhatsApp and Instagram do not render monospace backticks. Check `self.platform == "telegram"` before wrapping bar lines.
- **Don't make overlay failures fatal:** Match `bot.py` pattern — wrap in try/except, set `session.annotated_bytes = None` on failure.
- **Don't change bot.py:** Telegram path in `bot.py` is already correct. This phase only touches `bot_core.py` and `formatter.py`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---------|-------------|-------------|
| Shipping badge logic | Custom emoji/text logic | Port `style.shipping_badge()` as-is |
| Price bar rendering | Custom ASCII bar | Port `style.render_price_bar()` as-is |
| Price history line | Custom summary formatting | Port `style._price_history_line()` as-is |
| Overlay annotation | Custom Pillow drawing | Use `image_annotator.annotate_with_overlays()` — already has bbox reliability, fallback, and thread safety |
| Progress messages | New Formatter methods | Reuse `fmt.loading_search()`, `fmt.loading_vision()` — already exist in `formatter.py` |

---

## Common Pitfalls

### Pitfall 1: UserSession Missing annotated_bytes Field
**What goes wrong:** `bot.py`'s `UserSession` (defined at top of `bot.py`) has `annotated_bytes: Optional[bytes]`. `bot_core.py`'s `UserSession` dataclass (line 82) does NOT have this field.
**Why it happens:** The two files define separate `UserSession` classes.
**How to avoid:** Add `annotated_bytes: Optional[bytes] = None` to the `UserSession` dataclass in `bot_core.py`. This is needed for `_render_product` to use the annotated image rather than the Amazon product URL.
**Warning signs:** AttributeError on `session.annotated_bytes` at render time.

### Pitfall 2: _compress_image Blocking the Event Loop
**What goes wrong:** `bot_core.py` line 963 calls `_compress_image(raw_bytes)` synchronously. This is already flagged as tech debt in the AUDIT (INFR-02 partial regression).
**Why it happens:** The async wrapper `_compress_image_async` exists in `bot.py` but was not created in `bot_core.py`.
**How to avoid:** Replace `image_bytes = _compress_image(raw_bytes)` with `image_bytes = await asyncio.to_thread(_compress_image, raw_bytes)`. Fix this in the same plan as the overlay change.

### Pitfall 3: Monospace Backticks Breaking WhatsApp/Instagram
**What goes wrong:** If bar lines are wrapped in backticks unconditionally, WhatsApp displays literal backtick characters.
**Why it happens:** `style._price_history_line()` always wraps in backticks for Telegram MarkdownV2.
**How to avoid:** Add `if self.platform == "telegram":` guard before backtick wrapping in `formatter.py`.

### Pitfall 4: israel_result vs israel_status Mismatch
**What goes wrong:** `formatter.py.product_caption()` currently accepts `israel_status: str | None` (a string enum). `bot_core.py._update_caption_after_enrichment` computes this string from the result object. `style.product_caption()` takes `israel_verified` (the actual `IsraelShippingResult` object).
**Why it happens:** The formatter was simplified to use string enums to avoid importing `israel_scraper`.
**How to avoid:** Either (a) add `israel_result=None` parameter to `formatter.py.product_caption()` and derive badge from the object, or (b) compute the badge string in `bot_core.py` and pass it as `israel_status`. Option (a) is cleaner. Do not break existing callers — make the new parameter optional with `None` default.

### Pitfall 5: Stage 3/4 Messages Missing in _search_and_render
**What goes wrong:** `_search_and_render()` already updates the loading message to `loading_search()` (line 647), but skips the "Checking Israel shipping" Stage 4. Without Stage 4, users see the search loading message until the full result appears, missing the annotated overlay generation step.
**Why it happens:** Stage 4 was added in `bot.py` alongside the overlay generation; `bot_core.py` lacks both.
**How to avoid:** Add Stage 4 edit + `asyncio.to_thread(annotate_with_overlays, ...)` call inside `_search_and_render()` after search completes but before `_render_product()`.

### Pitfall 6: Formatter.product_caption Not Receiving annotated_bytes
**What goes wrong:** The annotated image is stored in `session.annotated_bytes`, but `_render_product()` currently passes `item.image_url` to `send_photo()`. On Telegram (`bot.py`), the annotated bytes are passed instead.
**Why it happens:** `bot_core._render_product()` does not check `session.annotated_bytes`.
**How to avoid:** In `_render_product()`, use `session.annotated_bytes` as the image source when present, falling back to `item.image_url`. Match `bot.py`'s `_render_results` pattern.

---

## Code Examples

### Existing shipping_badge in style.py (source of truth)
```python
# style.py line 422
def shipping_badge(result) -> str:
    if result is None or not result.verified:
        return "⚪ Israel shipping unknown"
    if result.ships_to_israel and result.is_free_shipping:
        return "🟢 Ships free to Israel"
    if result.ships_to_israel:
        return "🟡 Likely ships to Israel"
    return "🔴 Won't ship to Israel"
```

### Existing render_price_bar in style.py (source of truth)
```python
# style.py line 315 — does NOT escape MarkdownV2; caller wraps each line in backticks
def render_price_bar(ph, bar_width: int = 10) -> str:
    # Returns empty string when ph.current is None or range invalid
    # Returns 2-3 line string: bar line, pointer line, optional deal label
```

### Existing bot.py overlay generation (reference for bot_core fix)
```python
# bot.py lines 683-692 (inside handle_callback CB_FILTER_YES/NO)
if session.chosen_result is not None and session.image_bytes is not None:
    try:
        products = session.chosen_result.to_product_info_list()
        session.annotated_bytes = await asyncio.to_thread(
            annotate_with_overlays, session.image_bytes, products
        )
    except Exception as exc:
        logger.debug("annotate_with_overlays failed (non-fatal): %s", exc)
        session.annotated_bytes = None
```

### bot_core.py handle_photo current single loading message (gap location)
```python
# bot_core.py line 961 — Stage 1 only
loading_ref = await self.adapter.send_text(chat_id, fmt.loading_vision())
image_bytes = _compress_image(raw_bytes)  # BUG: synchronous, blocks event loop
```

### formatter.py product_caption current israel_status rendering (gap location)
```python
# formatter.py lines 304-321 — uses string enum, no badge
if israel_status == "yes":
    israel_line = "\U0001f1ee\U0001f1f1 " + self._esc(t("product_israel_yes", ...))
# ... etc — no color-coded badge like shipping_badge()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single loading message in bot_core.py | 4-stage progress in bot.py | Phase 2 (only for Telegram) | WhatsApp/Instagram users see one static loading message |
| Legend strip (annotate_products) in bot_core.py | Overlay annotation (annotate_with_overlays) in bot.py | Phase 2 (only for Telegram) | WhatsApp/Instagram get legend strip instead of overlay |
| Plain israel text in formatter.py | Emoji badge (shipping_badge) in style.py | Phase 2 (only for Telegram) | WhatsApp/Instagram get text enum, not color-coded badge |
| No price bar in formatter.py | ASCII bar (render_price_bar) in style.py | Phase 2 (only for Telegram) | WhatsApp/Instagram get no price bar |

---

## Open Questions

1. **i18n key for Stage 4 "Checking Israel shipping" message**
   - What we know: `Formatter` uses `t()` i18n lookup. `loading_vision` and `loading_search` keys exist (lines 136, 140).
   - What's unclear: Is there a `loading_israel` or `checking_israel` key in the locale files?
   - Recommendation: Check `locale/` directory. If missing, add a key or use a hardcoded string with `self._esc()`.

2. **annotated_bytes use in _render_product**
   - What we know: `bot.py._render_results` passes `session.annotated_bytes` as the image when present.
   - What's unclear: `bot_core._render_product` currently always uses `item.image_url`. Is there a `session.annotated_bytes` to check?
   - Recommendation: Add `annotated_bytes` field to `bot_core.UserSession`, and use it in `_render_product` as first choice image source.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_formatter_visual.py tests/test_bot_core_overlays.py -x` |
| Full suite command | `pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ANNO-02 | `bot_core.handle_photo` calls `annotate_with_overlays` (not `annotate_products`) for single-product flow | unit | `pytest tests/test_bot_core_overlays.py::test_annotate_with_overlays_called -x` | Wave 0 |
| ANNO-03 | Fallback to legend strip when no reliable bbox — tested via existing `test_image_annotator.py` | unit | `pytest tests/test_image_annotator.py -x` | YES |
| ANNO-04 | Stage 3 and Stage 4 progress edits sent during handle_callback | unit | `pytest tests/test_bot_core_overlays.py::test_progress_stages -x` | Wave 0 |
| PRCE-02 | `Formatter.product_caption` includes ASCII bar block in output when price_history provided | unit | `pytest tests/test_formatter_visual.py::test_price_bar_in_caption -x` | Wave 0 |
| PRCE-03 | Deal label appears in caption when price_history.deal_label is set | unit | `pytest tests/test_formatter_visual.py::test_deal_label_in_caption -x` | Wave 0 |
| ISRL-02 | `Formatter.product_caption` shows color emoji badge when israel_result provided | unit | `pytest tests/test_formatter_visual.py::test_shipping_badge_in_caption -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_formatter_visual.py tests/test_bot_core_overlays.py tests/test_image_annotator.py -x`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_formatter_visual.py` — covers PRCE-02, PRCE-03, ISRL-02; test `Formatter.product_caption` with mock `price_history` and `israel_result`
- [ ] `tests/test_bot_core_overlays.py` — covers ANNO-02, ANNO-04; test that `annotate_with_overlays` is called via `asyncio.to_thread` and that Stage 3/4 progress edits occur

---

## Sources

### Primary (HIGH confidence)
- `bot.py` — reference implementation of all 4 stages + overlay generation + shipping_badge usage (read in full)
- `formatter.py` — current state of multi-platform formatter showing gaps (read in full)
- `bot_core.py` — current state of platform-agnostic logic showing gaps (read in full, 64KB)
- `style.py` — source of truth for `shipping_badge()`, `render_price_bar()`, `_price_history_line()` (read in full)
- `image_annotator.py` — `annotate_with_overlays()` implementation (read in full)
- `.planning/v1.0-MILESTONE-AUDIT.md` — INT-01 and FLOW-01 gap definitions (read in full)
- `adapters/base.py` — `PlatformAdapter` API including `supports_photo_edit` flag (read in full)

### Secondary (MEDIUM confidence)
- `.planning/STATE.md` Decisions section — confirmed design decisions for price bar escaping, shipping badge tiers, bbox thresholds, and annotate_with_overlays failure behavior
- `tests/test_image_annotator.py` — confirmed test pattern for overlay-related tests

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all code read directly from project source
- Architecture: HIGH — gaps precisely identified from audit + source cross-reference
- Pitfalls: HIGH — each pitfall directly observed in source code (line numbers given)

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable internal codebase, no external dependencies)
