# Display & UX Fixes Design

**Date:** 2026-03-05

## Problem

After multi-product detection deployment, several display and UX issues surfaced:
1. Bounding boxes drawn in wrong places — hard boxes look bad with inaccurate coords
2. Gender not detected — women's clothing returns men's results
3. Price history fetched but never displayed in product cards
4. Playwright browser crashes during Israel shipping checks
5. Review count parentheses not escaped for MarkdownV2 (already fixed)

## Solutions

### 1. Semi-transparent overlay annotation

Replace bounding box rectangles with semi-transparent colored fills.
- 30% opacity color wash over each product's bbox area
- Numbered badge centered in the bbox area
- More forgiving of inaccurate bbox coordinates
- File: `image_annotator.py`

### 2. Gender detection in search query

Add instruction to SYSTEM_PROMPT: when product is clothing/footwear/accessories and appears gendered, prefix amazon_search_query with "women's" or "men's".
- No new fields needed — just smarter search queries
- File: `providers/base.py` (SYSTEM_PROMPT)

### 3. Price history in product caption

Wire up PriceHistory data to product_caption formatter:
- Add `price_history` param to `product_caption()` in formatter.py
- Add price line: `📉 90d avg: $X | Low: $Y` + deal label
- Pass PriceHistory from `_update_caption_after_enrichment` in bot_core.py
- Files: `formatter.py`, `bot_core.py`

### 4. Fix Playwright Israel shipping crashes

The `TargetClosedError` indicates browser lifecycle issue.
- Add try/except around navigation steps
- Ensure browser.close() only after operations complete
- Add timeout guards
- File: `israel_scraper.py`
