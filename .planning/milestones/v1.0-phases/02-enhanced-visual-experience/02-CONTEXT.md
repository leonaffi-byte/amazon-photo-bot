# Phase 2: Enhanced Visual Experience - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Enrich product results with visual annotations on photos (semi-transparent overlays highlighting detected products), confidence-scored Israel shipping badges (green/yellow/red), price history context (ASCII bar + deal label), and real-time progress updates during analysis. The existing Telegram bot's result quality and user feedback loop improve significantly after this phase.

</domain>

<decisions>
## Implementation Decisions

### Photo Annotation Style
- Semi-transparent overlay rectangles drawn on detected products using bounding box coordinates from vision providers
- Medium opacity (~40%) — product details visible through overlay but highlight is clearly noticeable
- Each product gets a color from the existing _COLORS cycle in image_annotator.py, with its number displayed inside the overlay
- Colors match between photo overlays and result text (product 1 = red overlay + red indicator in results)
- When bounding boxes are missing or unreliable: fall back to the existing numbered legend strip at image bottom (current image_annotator.py behavior) — no overlay drawn on photo itself

### Shipping Badge Presentation
- Colored emoji + short text format: `🟢 Ships free to Israel` / `🟡 Likely ships to Israel` / `🔴 Won't ship to Israel`
- When shipping status unknown (scraper failed, proxy down): gray badge `⚪ Israel shipping unknown`
- Shipping verification runs in parallel with search results — start checking ASINs as soon as they come in from search
- Results show immediately; shipping badges appear as checks complete (progressive enhancement via message edits)

### Price History Display
- ASCII bar in message text showing current price position within 90-day range
- Format: `$89 ▖██████▎───── $149` with `^ $112 now` indicator and deal label below
- Price history shown for every product that has data available (skip silently if unavailable)
- Results show immediately without price data; message edited to add price bars as they come in (progressive enrichment)
- deal_label already implemented in PriceHistory dataclass — reuse as-is

### Progress Update Flow
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

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `image_annotator.py`: Existing annotator with _COLORS cycle, Pillow drawing, legend strip — extend with overlay support
- `ProductInfo.bbox`: Already has optional `tuple[float, float, float, float]` field (x%, y%, w%, h%) — just needs providers to populate it
- `price_history.py`: PriceHistory dataclass with `deal_label` property already computes "All-time low" / "Great deal" / "Below avg"
- `israel_scraper.py`: Full Playwright-based verification with proxy support, circuit breaker, 24h cache
- `formatter.py`: Multi-platform message formatter with per-platform caption limits
- `style.py`: Telegram-specific message formatting with MarkdownV2 escaping
- `bot.py`: Already uses `edit_message_text` for in-place status updates

### Established Patterns
- Async-first: all I/O uses async APIs — new progress/shipping/price fetching must follow
- `asyncio.to_thread` for Pillow CPU work (established in Phase 1 for _compress_image_async)
- Provider results via `ProviderResult` dataclass from `providers/base.py`
- Search results via `AmazonItem` dataclass from `search_backends/base.py`
- Error handling: try-except with fallback chains, never raise to user

### Integration Points
- `providers/base.py` prompt: needs to request bounding box coordinates from vision models
- `bot.py` handle_photo flow: needs progress message creation + stage-by-stage edits
- `style.py` result formatting: needs shipping badge + price bar sections added to product cards
- `amazon_search.py`: needs to trigger parallel shipping checks as ASINs arrive
- `image_annotator.py`: needs overlay mode alongside existing legend strip mode

</code_context>

<specifics>
## Specific Ideas

- Overlay preview mockup: colored rectangles with number + truncated product name inside, ~40% opacity
- Shipping badge preview: `📦 Product Name` / `⭐ Rating` / `💰 Price` / `🟢 Ships free to Israel` format
- Price bar preview: `$89 ▖██████▎───── $149` with `^ $112 now` below
- Progressive enrichment: show results fast, then edit in shipping badges and price bars as async fetches complete

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-enhanced-visual-experience*
*Context gathered: 2026-03-14*
