---
phase: 05-public-web-application
plan: "02"
subsystem: web_app
tags: [fastapi, jinja2, price-history, og-tags, rtl, shipping-badge]
dependency_graph:
  requires:
    - web_app/router.py (result_page stub from Plan 01)
    - web_app/search_store.py (get_web_search)
    - web_app/templates/web_base.html (base template with og_tags block)
    - price_history.py (get_price_history, PriceHistory)
    - database.py (get_active_tag, _get_conn)
    - config.py (SHORTENER_BASE_URL)
  provides:
    - "GET /search/{short_id} — full result page with product cards, tabs, OG tags"
    - "web_app/templates/search.html — complete result page template"
    - "web_app/templates/partials/product_tabs.html — multi-product tab navigation"
    - "web_app/templates/partials/product_cards.html — product cards with price bar and badge"
    - "web_app/templates/error.html — generic 404/410 error page"
  affects:
    - web_app/router.py (result_page stub replaced with full implementation)
tech_stack:
  added: []
  patterns:
    - "asyncio.gather for concurrent price history fetching (max 10 items)"
    - "Jinja2 template min/max filter for clamping price bar position"
    - "Inline badge/affiliate computation in router — dicts not dataclasses at template boundary"
    - "TemplateResponse with status_code kwarg for 404/410 error responses"
key_files:
  created:
    - web_app/templates/search.html
    - web_app/templates/partials/product_tabs.html
    - web_app/templates/partials/product_cards.html
    - web_app/templates/error.html
  modified:
    - web_app/router.py (result_page stub replaced)
    - tests/test_web_app.py (12 new tests added)
decisions:
  - "Affiliate URL built inline in router (f-string) since results are dicts not AmazonItem objects at render time"
  - "Shipping badge computed in router as dict {text, bg, color} passed to template — avoids template logic complexity"
  - "Price bar position clamped to 5-95% using Jinja2 min/max filters to avoid invisible bar edges"
  - "error.html template used for 404/410 to get proper HTML with base layout instead of raw HTMLResponse"
  - "get_price_history patched at price_history module level in tests (lazy import creates local binding in router)"
metrics:
  duration_min: 3
  completed_date: "2026-03-14"
  tasks_completed: 2
  files_created: 4
  files_modified: 2
---

# Phase 5 Plan 2: Result Page with Product Cards Summary

**One-liner:** Full result page with concurrent price history fetching, color-coded Israel shipping badges, affiliate-tagged product cards, multi-product tabs, OG meta tags for social sharing, and noindex/nofollow SEO guards.

## What Was Built

### Updated files (2)

- `web_app/router.py` — `result_page` stub replaced with full implementation:
  - Loads stored results via `get_web_search(short_id)`
  - Returns 404 (via error.html) for missing results
  - Returns 410 (via error.html) for expired results
  - Parses `results_json` and `products_json` from DB JSON strings
  - Fetches price history concurrently with `asyncio.gather` for active product's items (max 10)
  - Computes affiliate URLs and shipping badges per item
  - Builds OG meta data (og:title, og:description, og:image with absolute URL)
  - Accepts `?product=N` query param for multi-product tab navigation
  - Passes `price_histories` dict (keyed by ASIN) to template

- `tests/test_web_app.py` — 12 new tests in 2 new classes:

### Created files (4)

- `web_app/templates/search.html` — Main result page template:
  - `{% block og_tags %}` with og:type, og:title, og:description, og:image, og:url, robots noindex
  - Collapsible annotated photo via `<details open>`
  - Includes product_tabs.html (only when >1 product)
  - Includes product_cards.html for active product

- `web_app/templates/partials/product_tabs.html` — Horizontal scroll tabs:
  - Active tab: `bg-indigo-600 text-white`
  - Inactive tabs: `bg-gray-100 text-gray-700 hover:bg-gray-200`
  - Links to `?product=N&lang={{ lang }}`
  - Uses logical Tailwind properties throughout

- `web_app/templates/partials/product_cards.html` — Product card list:
  - Product image, title (line-clamp-2), price (formatted), star rating + review count
  - Israel shipping badge: green/yellow/red based on is_sold_by_amazon/is_amazon_fulfilled/is_prime
  - Price history bar: width% position clamped 5-95%, green if deal_label non-empty
  - Deal label text below bar (e.g., "Great deal", "All-time low")
  - "Price history unavailable" placeholder when no data
  - "View on Amazon" affiliate link button (amber, full-width)
  - Empty state when no results

- `web_app/templates/error.html` — Simple error page extending web_base.html

## Test Results

32/32 tests pass (20 from Plan 01 + 12 new).

| Class | Tests | Coverage |
|-------|-------|----------|
| TestResultPage | 10 | card fields, affiliate URL, green/red badge, 2-product tabs, tab switch, OG tags, noindex, 410 expired, 404 missing |
| TestPriceHistoryBar | 2 | rendered bar with deal label, unavailable placeholder |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Error template for 404/410 responses**
- **Found during:** Task 1 implementation
- **Issue:** Plan specified `HTMLResponse(content="<h1>Not found</h1>")` but this produces un-styled plain HTML without the base layout
- **Fix:** Created `web_app/templates/error.html` extending web_base.html; used `TemplateResponse(..., status_code=404/410)`
- **Files modified:** web_app/templates/error.html (created), web_app/router.py
- **Commit:** 19ff2f8

## Self-Check: PASSED

All 4 created files verified on disk. Both commits (19ff2f8, e5dba54) present in git log. 32/32 tests pass.
