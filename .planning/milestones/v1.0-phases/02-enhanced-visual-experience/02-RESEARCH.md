# Phase 02: Enhanced Visual Experience - Research

**Researched:** 2026-03-14
**Domain:** Pillow image annotation, Telegram progressive message editing, Israel shipping confidence scoring, ASCII price bar rendering
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Photo Annotation Style**
- Semi-transparent overlay rectangles drawn on detected products using bounding box coordinates from vision providers
- Medium opacity (~40%) — product details visible through overlay but highlight is clearly noticeable
- Each product gets a color from the existing _COLORS cycle in image_annotator.py, with its number displayed inside the overlay
- Colors match between photo overlays and result text (product 1 = red overlay + red indicator in results)
- When bounding boxes are missing or unreliable: fall back to the existing numbered legend strip at image bottom (current image_annotator.py behavior) — no overlay drawn on photo itself

**Shipping Badge Presentation**
- Colored emoji + short text format: `🟢 Ships free to Israel` / `🟡 Likely ships to Israel` / `🔴 Won't ship to Israel`
- When shipping status unknown (scraper failed, proxy down): gray badge `⚪ Israel shipping unknown`
- Shipping verification runs in parallel with search results — start checking ASINs as soon as they come in from search
- Results show immediately; shipping badges appear as checks complete (progressive enhancement via message edits)

**Price History Display**
- ASCII bar in message text showing current price position within 90-day range
- Format: `$89 ▖██████▎───── $149` with `^ $112 now` indicator and deal label below
- Price history shown for every product that has data available (skip silently if unavailable)
- Results show immediately without price data; message edited to add price bars as they come in (progressive enrichment)
- deal_label already implemented in PriceHistory dataclass — reuse as-is

**Progress Update Flow**
- Single status message edited in-place at each stage (no stacked messages, no typing-only)
- 4 stages: `🔍 Analyzing your photo...` → `📦 Found N products! Searching Amazon...` → `🛒 Comparing prices...` → `🇮🇱 Checking Israel shipping...` → [Full results]
- No time estimates, no progress bar, no animated dots — stage progression itself implies progress
- Final results replace the progress message entirely

### Claude's Discretion
- Confidence scoring algorithm for Israel shipping (multi-signal weighted score vs simple rules — must meet FP < 10%, FN < 15% targets)
- Exact bounding box quality threshold for overlay vs fallback decision
- How to handle message edit race conditions when shipping + price data arrive simultaneously
- Exact ASCII bar rendering implementation details
- Message length management when all enrichments (annotation, shipping, price, deal label) make messages very long

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ANNO-01 | Vision providers return bounding box coordinates for detected products | Already in SYSTEM_PROMPT and ProductInfo.bbox — providers already return bbox; research confirms prompting is sufficient |
| ANNO-02 | Bot sends back annotated photo with semi-transparent overlays on each detected product | Pillow RGBA compositing with `Image.blend` or paste with alpha mask; `asyncio.to_thread` for CPU work |
| ANNO-03 | If bounding box quality is low, fall back to numbered circles at approximate positions | Threshold logic: check bbox area, aspect ratio, and overlap — existing legend strip is the fallback |
| ANNO-04 | User sees streaming progress updates during analysis | `msg.edit_text` already used in handle_photo; needs 4-stage expansion with product count interpolation |
| ISRL-01 | Israel shipping detection uses multi-signal approach (FBA + seller + Prime + address verification) | Current _parse_html uses phrase matching only; needs FBA badge, seller type, Prime eligibility signals |
| ISRL-02 | Each product result shows confidence-scored shipping badge (green/yellow/red) | IsraelShippingResult exists; style.product_caption already accepts israel_verified; needs badge formatter |
| ISRL-03 | False positive rate for Israel shipping reduced below 10% | Multi-signal scoring with conservative threshold; specific FP-prone phrases identified in existing code |
| ISRL-04 | False negative rate for Israel shipping reduced below 15% | Avoid over-strict no-ship detection; "item not available in" fires for OOS — already handled in _NO_SHIP_PHRASES |
| PRCE-01 | Product results include text summary of price history | _price_history_line() in style.py already outputs ATL + 90d avg; needs expansion to match specified format |
| PRCE-02 | Product results include ASCII-style price bar showing current price position within 90-day range | New render_price_bar() function; uses Unicode block chars; 90d range is low_90d→(current or high) |
| PRCE-03 | Deal quality indicator shown on results | deal_label property on PriceHistory already implements this with three tiers |
</phase_requirements>

## Summary

Phase 2 is a **feature enrichment phase** that builds on top of a well-structured existing codebase. The core data models and background task patterns are already in place. The work divides cleanly into four sub-domains: (1) photo overlay annotation using Pillow, (2) 4-stage progress updates in handle_photo, (3) confidence-scored Israel shipping badges replacing binary true/false, and (4) ASCII price bar rendering in product captions.

The most important discovery from code reading: **much of the infrastructure is already implemented**. `bot.py` already has `_verify_israel_async` and `_verify_price_async` background tasks that edit captions progressively. `style.product_caption` already accepts `israel_verified` and `price_history` parameters. `_price_history_line` already formats price history. The gaps are: (a) `image_annotator.py` needs overlay mode alongside existing legend strip, (b) `handle_photo` needs 4-stage progress message, (c) `_parse_html` needs multi-signal confidence scoring, and (d) the price bar needs ASCII visualization instead of text-only.

**Primary recommendation:** Implement in order — progress stages (lowest risk, no external deps), then price bar (pure rendering), then photo overlays (Pillow CPU work), then Israel shipping confidence (highest complexity due to FP/FN targets).

## Standard Stack

### Core (already in requirements.txt)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Pillow | >=10.0 | Image annotation: drawing, compositing, alpha blend | Already used in image_annotator.py and bot.py |
| python-telegram-bot | v20+ | PTB async; `bot.edit_message_caption`, `send_photo` | Already the bot framework |
| aiosqlite | current | Caching layer for shipping + price history | Already in database.py |
| BeautifulSoup4 | current | HTML parsing for israel_scraper._parse_html | Already used |
| asyncio | stdlib | Background tasks via `asyncio.create_task` | Project pattern |

### Supporting (already in project)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio.to_thread | stdlib | Offload Pillow CPU work off event loop | Any synchronous Pillow operation |
| aiohttp | current | BrightData requests in price_history + israel_scraper | Already used for brightdata calls |
| playwright | current | CCC + Keepa scraping for price history | Already integrated |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Pillow RGBA compositing | OpenCV | OpenCV adds ~50MB to image; Pillow already present |
| ASCII bar rendering | matplotlib bar chart | matplotlib = heavy dep, image-in-image complexity; ASCII fits in caption |
| Multi-signal HTML parsing | ML classifier | Overkill for phrase-based signals; rules are more debuggable |

**Installation:** No new packages needed. All dependencies already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
image_annotator.py         # Add: annotate_with_overlays(), _is_bbox_reliable(), _draw_overlay()
style.py                   # Add: render_price_bar(), update product_caption() israel badge format
israel_scraper.py          # Add: _score_shipping_confidence(), update _parse_html() with multi-signal
bot.py                     # Update: handle_photo() 4-stage progress; update _verify_israel_async badge
tests/
  test_image_annotator.py  # New: overlay drawing, bbox threshold, fallback behavior
  test_price_bar.py        # New: ASCII bar rendering, edge cases
  test_israel_confidence.py # New (or extend test_israel_scraper.py): multi-signal scoring
```

### Pattern 1: Pillow Semi-Transparent Overlay
**What:** Draw colored rectangle with alpha compositing over original image
**When to use:** When bbox is present and passes quality threshold
**Example:**
```python
# Source: Pillow documentation (Image.paste with mask)
def _draw_overlay(img: Image.Image, bbox: tuple, color_rgb: tuple, opacity: float = 0.4) -> Image.Image:
    """Draw semi-transparent rectangle overlay on img (mutates a copy)."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    x, y, bw, bh = bbox  # percentages
    x1 = int(x / 100 * w)
    y1 = int(y / 100 * h)
    x2 = int((x + bw) / 100 * w)
    y2 = int((y + bh) / 100 * h)
    alpha = int(opacity * 255)
    draw.rectangle([x1, y1, x2, y2], fill=(*color_rgb, alpha))
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return Image.alpha_composite(img, overlay)
```

### Pattern 2: Bounding Box Quality Threshold
**What:** Decide whether overlay is reliable enough to show vs falling back to legend strip
**When to use:** Before calling annotate_with_overlays()
**Example:**
```python
# Source: project convention — based on CONTEXT.md guidance + code review
_MIN_BBOX_AREA_PCT = 1.0   # at least 1% of image area
_MAX_BBOX_AREA_PCT = 90.0  # not the whole image
_MAX_OVERLAP_RATIO = 0.8   # boxes shouldn't cover each other > 80%

def _is_bbox_reliable(bbox: tuple[float, float, float, float]) -> bool:
    """Return True if bbox is worth drawing an overlay for."""
    x, y, w, h = bbox
    # Reject degenerate boxes
    if w <= 0 or h <= 0:
        return False
    area = w * h
    if area < _MIN_BBOX_AREA_PCT or area > _MAX_BBOX_AREA_PCT:
        return False
    # Reject boxes that escape image bounds significantly
    if x < -5 or y < -5 or (x + w) > 105 or (y + h) > 105:
        return False
    return True
```

### Pattern 3: 4-Stage Progress Message
**What:** Edit a single status text message through 4 stages in handle_photo
**When to use:** Every photo submission — replaces current 2-stage approach
**Example:**
```python
# Source: existing bot.py handle_photo pattern + CONTEXT.md spec
# Stage 1 (immediate)
msg = await update.message.reply_text("🔍 Analysing your photo…", parse_mode="MarkdownV2")

# Stage 2 (after vision analysis returns)
n = len(products)
await msg.edit_text(f"📦 Found {n} product{'s' if n != 1 else ''}\\! Searching Amazon…", parse_mode="MarkdownV2")

# Stage 3 (after search returns)
await msg.edit_text("🛒 Comparing prices…", parse_mode="MarkdownV2")

# Stage 4 (before sending annotated photo)
await msg.edit_text("🇮🇱 Checking Israel shipping…", parse_mode="MarkdownV2")

# Final: delete progress msg, send annotated photo with results
try:
    await msg.delete()
except Exception:
    pass
```

### Pattern 4: ASCII Price Bar
**What:** Render current price position as Unicode block characters within 90-day range
**When to use:** In product captions when price history has low_90d or avg_90d + current
**Example:**
```python
# Source: project design spec (CONTEXT.md) — Unicode block chars
_BAR_WIDTH = 10  # total bar segments
_FILL = "█"
_PARTIAL = ["▏", "▎", "▍", "▌", "▋", "▊", "▉"]  # 1/8 increments
_EMPTY = "─"

def render_price_bar(ph: "PriceHistory", bar_width: int = 10) -> str:
    """
    Returns multi-line string like:
      $89 ████████── $149
           ^ $112 now
      💸 Great deal
    """
    low = ph.low_90d or ph.low_all_time
    high = ph.avg_90d  # use 90d avg as "high" reference when no explicit high
    current = ph.current
    if not all([low, current]):
        return ""
    if low >= (high or current):
        high = current * 1.3  # synthetic range if all same
    rng = (high or current * 1.3) - low
    if rng <= 0:
        return ""
    ratio = max(0.0, min(1.0, (current - low) / rng))
    filled = int(ratio * bar_width)
    partial_idx = int((ratio * bar_width - filled) * 7)
    partial = _PARTIAL[partial_idx] if partial_idx > 0 else ""
    empty = bar_width - filled - (1 if partial else 0)
    bar = _FILL * filled + partial + _EMPTY * empty
    position = int(ratio * bar_width)
    pointer = " " * position + "^"
    low_str = f"${low:.0f}"
    high_str = f"${(high or current * 1.3):.0f}"
    deal = ph.deal_label
    lines = [f"{low_str} {bar} {high_str}", f"{pointer} ${current:.0f} now"]
    if deal:
        lines.append(deal)
    return "\n".join(lines)
```

### Pattern 5: Multi-Signal Israel Shipping Confidence
**What:** Weighted score combining FBA badge, seller type, Prime status, address verification
**When to use:** In `_parse_html` to replace binary true/false with confidence tier
**Example:**
```python
# Source: codebase analysis of _parse_html + ISRL-01 requirement
# Confidence tiers map to badge emoji in style layer:
#   score >= 0.7  → ships_free=True   → 🟢 Ships free to Israel
#   score >= 0.4  → ships=True        → 🟡 Likely ships to Israel
#   score < 0.4   → ships=False       → 🔴 Won't ship to Israel
#   not verified  → unknown           → ⚪ Israel shipping unknown

def _score_shipping_confidence(html_lower: str, delivery_section: str) -> float:
    """Return confidence score 0.0–1.0 for Israel shipping."""
    score = 0.0
    # Strong signals (0.35 each, sum to 0.7 for two strong signals)
    if any(p in html_lower for p in _FREE_SHIP_PHRASES):
        score += 0.35
    if "fulfilled by amazon" in html_lower or "ships from amazon" in html_lower:
        score += 0.35
    # Medium signals (0.2 each)
    if "prime" in delivery_section.lower():
        score += 0.2
    if "israel" in delivery_section.lower() or "deliver to il" in delivery_section.lower():
        score += 0.2
    # Weak positive signal (0.1)
    if "add to cart" in html_lower and "currently unavailable" not in html_lower:
        score += 0.1
    return min(score, 1.0)
```

### Anti-Patterns to Avoid
- **Drawing overlays synchronously in the async handler:** Always wrap `annotate_products()` / `annotate_with_overlays()` in `asyncio.to_thread()` — established pattern from _compress_image_async.
- **Sending annotated photo before progress stage 4:** The progress message should reach all 4 stages before deletion to avoid jarring UX where message disappears instantly.
- **Race condition in background edits:** When Israel and price tasks both try to edit the same message simultaneously, one will get a Telegram "message not modified" error. Use try-except and always re-read current session state before editing.
- **Hard-coding font paths:** image_annotator.py already handles OSError on font load with ImageFont.load_default() fallback — extend this pattern, don't hard-code /usr/share/fonts paths without fallback.
- **Mutating AmazonItem for shipping badge:** Shipping badge belongs in the style layer (product_caption), not stored on AmazonItem. The existing `_last_israel_result` session pattern is correct.
- **Message length violations with all enrichments:** Telegram photo caption limit is 1024 chars. style.product_caption already truncates at 1020. The ASCII bar adds ~3 lines (~40 chars). Measure and test at limit.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Alpha compositing | Custom pixel-level blending | `Image.alpha_composite` (Pillow) | Already in requirements, handles all edge cases |
| Background async tasks | Thread pool or custom task runner | `asyncio.create_task` + `_background_tasks` set | Established pattern in bot.py, prevents GC, handles cleanup |
| Price history storage | Custom cache module | `db.get_price_cache` / `db.set_price_cache` | Already in database.py with TTL |
| Israel shipping cache | Custom dict | `db.get_israel_cache` / `db.set_israel_cache` | 24h TTL, already used in israel_scraper.py |
| MarkdownV2 escaping | Custom regex | `style.esc()` | Already escapes all 18 MarkdownV2 special chars |

**Key insight:** The bot.py pattern of spawning `_verify_israel_async` and `_verify_price_async` as background tasks is already established and working. Phase 2 extends what's there, not replaces it.

## Common Pitfalls

### Pitfall 1: Telegram "Message Is Not Modified" Error
**What goes wrong:** Two background tasks (Israel + price) both try to `edit_message_caption` within milliseconds of each other. First succeeds; second gets `BadRequest: Message is not modified` because the text didn't change between task scheduling and execution.
**Why it happens:** asyncio.create_task spawns both tasks concurrently; they race to the same message.
**How to avoid:** Wrap all `edit_message_caption` in try-except and swallow `BadRequest`. The existing code in `_verify_israel_async` already does this (`except Exception: pass`). For the new badge content, always re-read the latest session state (israel_result + price_history) before building the caption so either task produces the most complete version.
**Warning signs:** `BadRequest: Message is not modified` in logs at high rate.

### Pitfall 2: Pillow RGBA→JPEG Conversion
**What goes wrong:** Pillow cannot save RGBA images as JPEG (no alpha channel). After alpha compositing, the image is RGBA. Saving to JPEG without converting first raises `OSError: cannot write mode RGBA as JPEG`.
**Why it happens:** `Image.alpha_composite` returns RGBA even when source was RGB.
**How to avoid:** Always call `.convert("RGB")` before `img.save(buf, format="JPEG")`. The existing `annotate_products` already does `Image.open(...).convert("RGB")` at the start — do the same after compositing.
**Warning signs:** `OSError: cannot write mode RGBA` in logs.

### Pitfall 3: Bounding Box Coordinate System Mismatch
**What goes wrong:** Vision providers return bbox as `[x_percent, y_percent, width_percent, height_percent]` where `[x, y]` is top-left corner. If percentages are treated as fractions (0–1) instead of 0–100, overlays appear in wrong position.
**Why it happens:** SYSTEM_PROMPT specifies 0–100 but some providers may return 0–1 or inverted coordinates.
**How to avoid:** Validate: if all 4 values < 1.5, multiply by 100. Check `_is_bbox_reliable()` rejects values far outside 0–105 range. Add test with known bbox that validates pixel positions.
**Warning signs:** Overlays drawn in the top-left corner (near 0,0) when product is elsewhere.

### Pitfall 4: Progress Stage Skipping on Fast Vision
**What goes wrong:** When vision analysis completes in < 500ms (e.g., cached result), `edit_text` for stage 1 may not reach the user before stage 2 overwrites it. User sees only later stages.
**Why it happens:** Telegram rate limits message edits to once per second per chat. Two rapid edits may cause the first to be silently dropped.
**How to avoid:** This is acceptable behavior per CONTEXT.md ("stage progression itself implies progress"). Don't add artificial sleep. If stage 2 overwrites stage 1 instantly, that's fine.
**Warning signs:** N/A — not a problem to fix.

### Pitfall 5: ASCII Bar Breaking MarkdownV2
**What goes wrong:** `█`, `─`, `▖`, `▎` Unicode characters pass through fine, but `$` followed by numbers is safe in MarkdownV2 (not a special char). The `^` caret character is also safe. However, if the bar is inside an italic/code span, the content must still be escaped.
**Why it happens:** MarkdownV2 has 18 special chars: `_ * [ ] ( ) ~ \` > # + - = | { } . !`. Dollar sign and caret are NOT in this list.
**How to avoid:** Wrap bar lines in `_italic_` span using `style.esc()` on the price values. Test output with actual Telegram API call.
**Warning signs:** Telegram `BadRequest: Can't parse entities` error.

### Pitfall 6: IsraelShippingResult.note vs Badge
**What goes wrong:** Current `product_caption` passes `israel_verified.note` raw through `esc()` to display as the shipping line. This is the detailed note like "Verified: ships free to Israel (cart >= $49)". For Phase 2, the badge format is a structured emoji+text (`🟢 Ships free to Israel`) — not the raw note.
**Why it happens:** The existing note field was designed for admin debugging, not user display.
**How to avoid:** Add a new `shipping_badge()` function in style.py that maps IsraelShippingResult fields to the 4 emoji badges. Keep `.note` for logging. Update `product_caption` to use `shipping_badge()` instead of `esc(israel_verified.note)`.
**Warning signs:** Users see developer-facing notes like "(Could not verify: No proxy configured)" instead of `⚪ Israel shipping unknown`.

## Code Examples

Verified patterns from official sources:

### Async CPU-bound Pillow work (established pattern in bot.py)
```python
# Source: bot.py _compress_image_async — project established pattern
async def annotate_with_overlays_async(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Offload Pillow annotation to thread pool."""
    return await asyncio.to_thread(annotate_with_overlays, image_bytes, products)
```

### Telegram caption edit (from bot.py _verify_israel_async)
```python
# Source: bot.py lines 902-911
try:
    await bot.edit_message_caption(
        chat_id      = chat_id,
        message_id   = msg_id,
        caption      = caption,
        parse_mode   = "MarkdownV2",
        reply_markup = keyboard,
    )
except Exception:
    pass   # message may have been deleted/edited — that's fine
```

### Progress message pattern (from bot.py handle_photo)
```python
# Source: bot.py lines 457-460 — edit_text pattern
msg = await update.message.reply_text(
    "🔍 Analysing your photo…",
    parse_mode="MarkdownV2",
)
# ... later ...
await msg.edit_text("📦 Found 3 products\\! Searching Amazon…", parse_mode="MarkdownV2")
```

### ProductInfo bbox field (from providers/base.py)
```python
# Source: providers/base.py line 126
bbox: Optional[tuple[float, float, float, float]] = None  # (x%, y%, w%, h%)
# Already populated by to_product_info_list() from products_raw
```

### Israel confidence badge mapping (new in style.py)
```python
# New function to add to style.py
def shipping_badge(result: "IsraelShippingResult") -> str:
    """Return formatted shipping badge line for product caption."""
    if not result or not result.verified:
        return "⚪ Israel shipping unknown"
    if result.ships_to_israel and result.is_free_shipping:
        return "🟢 Ships free to Israel"
    if result.ships_to_israel:
        return "🟡 Likely ships to Israel"
    return "🔴 Won't ship to Israel"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Binary ships/doesn't ship | Multi-signal confidence tiers (ISRL-01) | Phase 2 | Reduces FP/FN, shows yellow "likely" tier |
| Text-only price ATL + avg | ASCII bar showing price position in range (PRCE-02) | Phase 2 | More visual, scannable |
| Legend strip only annotation | Overlay rectangles when bbox available, legend strip fallback (ANNO-02/03) | Phase 2 | Highlights products in photo |
| Single "Analysing..." message | 4-stage progress (ANNO-04) | Phase 2 | Shows progress per analysis phase |

**Existing (already working, do not replace):**
- `_verify_israel_async` / `_verify_price_async` background task pattern in bot.py — extend, don't rewrite
- `style.product_caption` with `israel_verified` + `price_history` params — extend the rendering
- `_price_history_line` in style.py — replace with richer bar format, keep function name for compat
- `PriceHistory.deal_label` property — reuse as-is, three tiers already correct

## Open Questions

1. **Bounding box reliability threshold — exact values**
   - What we know: bbox is `[x%, y%, w%, h%]`; degenerate values (area < 1% or > 90%) should fall back
   - What's unclear: whether to check all-products-have-reliable-bbox (show overlays) vs any-product-missing (fall back entirely vs mixed)
   - Recommendation: Mixed mode — draw overlay where reliable, skip (no overlay, no legend entry) where unreliable. If all products lack bbox, fall back to full legend strip.

2. **Race condition: simultaneous shipping + price edits**
   - What we know: both fire as `asyncio.create_task` immediately after `send_photo`; they race
   - What's unclear: whether to serialize them (price then Israel) or let them race with merged state
   - Recommendation: After each task completes, read `session._last_israel_result` and `session._last_price_history` to build the most complete caption available. The "last writer wins" is acceptable because both tasks read the combined state and produce identical captions once both complete.

3. **Multi-signal scoring thresholds for FP < 10% / FN < 15%**
   - What we know: Current code has `_NO_SHIP_PHRASES` (definitive negative) and `_FREE_SHIP_PHRASES` + `israel_mentioned` (positive)
   - What's unclear: The false positive rate of current heuristic — we don't have labeled test data
   - Recommendation: Score with the algorithm in Code Examples section; tune thresholds conservatively (prefer FN < FP miss given target is FP < 10% which is stricter). Add test fixtures with known-good and known-bad Amazon HTML snippets.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| Config file | `pytest.ini` (already configured) |
| Quick run command | `pytest tests/test_israel_scraper.py tests/test_price_history.py tests/test_style.py -x` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ANNO-01 | bbox populated in ProviderResult.products_raw | unit | `pytest tests/test_providers_base.py -x -k bbox` | ✅ |
| ANNO-02 | overlay drawn when bbox reliable | unit | `pytest tests/test_image_annotator.py -x -k overlay` | ❌ Wave 0 |
| ANNO-03 | fallback to legend strip when no reliable bbox | unit | `pytest tests/test_image_annotator.py -x -k fallback` | ❌ Wave 0 |
| ANNO-04 | 4-stage progress message in handle_photo | unit | `pytest tests/test_bot.py -x -k progress` | ✅ (extend) |
| ISRL-01 | multi-signal scoring function returns 0–1.0 | unit | `pytest tests/test_israel_scraper.py -x -k confidence` | ✅ (extend) |
| ISRL-02 | shipping_badge() returns correct emoji for each tier | unit | `pytest tests/test_style.py -x -k shipping_badge` | ✅ (extend) |
| ISRL-03 | FP rate < 10% on known-negative HTML fixtures | unit | `pytest tests/test_israel_scraper.py -x -k false_positive` | ❌ Wave 0 |
| ISRL-04 | FN rate < 15% on known-positive HTML fixtures | unit | `pytest tests/test_israel_scraper.py -x -k false_negative` | ❌ Wave 0 |
| PRCE-01 | price summary text appears in caption | unit | `pytest tests/test_style.py -x -k price_summary` | ✅ (extend) |
| PRCE-02 | render_price_bar() renders correct bar width/position | unit | `pytest tests/test_price_history.py -x -k price_bar` | ❌ Wave 0 |
| PRCE-03 | deal_label shown in caption when available | unit | `pytest tests/test_price_history.py -x -k deal_label` | ✅ |

### Sampling Rate
- **Per task commit:** `pytest tests/test_israel_scraper.py tests/test_price_history.py tests/test_style.py tests/test_image_annotator.py -x`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_image_annotator.py` — covers ANNO-02, ANNO-03 (overlay drawing, bbox threshold, RGBA→JPEG conversion)
- [ ] Add `test_israel_scraper.py::TestFalsePositive` class — covers ISRL-03 with 5+ known-negative HTML fixtures
- [ ] Add `test_israel_scraper.py::TestFalseNegative` class — covers ISRL-04 with 5+ known-positive HTML fixtures
- [ ] Add `test_price_history.py::TestPriceBar` class — covers PRCE-02 (render_price_bar edge cases: equal prices, missing low_90d, very wide range)

## Sources

### Primary (HIGH confidence)
- Codebase direct read: `bot.py`, `image_annotator.py`, `style.py`, `israel_scraper.py`, `price_history.py`, `providers/base.py` — all patterns verified by reading production code
- `pytest.ini` + `tests/conftest.py` — test framework configuration confirmed

### Secondary (MEDIUM confidence)
- Pillow documentation: `Image.alpha_composite`, `ImageDraw.rectangle` — standard Pillow API, stable across versions
- Telegram MarkdownV2 special chars: confirmed `$` and `^` are NOT special chars in MarkdownV2

### Tertiary (LOW confidence)
- FP/FN threshold values (0.7 / 0.4) for Israel shipping confidence: derived from reasoning about signal strength, not validated against labeled data

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in project; no new deps
- Architecture: HIGH — patterns derived directly from reading production code
- Pitfalls: HIGH — RGBA/JPEG and race condition pitfalls are well-known Pillow/asyncio behaviors; MarkdownV2 escaping verified from character list
- Israel confidence thresholds: LOW — needs validation against real Amazon HTML samples

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable Python/Telegram stack; Amazon HTML structure may change)
