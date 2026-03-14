# Deferred Items

Pre-existing issues discovered during Phase 02 execution that are out-of-scope for the current plan.

## Pre-existing Test Failures

### TestParseCccHtml::test_fallback_to_fullpage_scan (tests/test_price_history.py)

**Discovered during:** Plan 02-01 Task 2
**Status:** Pre-existing failure before any Phase 02 changes
**Root cause:** `_CCC_HTML_NO_AMAZON_SECTION` fixture uses a plain `<ul>` with price list items, but `_parse_ccc_html` only invokes `_extract_stats_from_section` when it finds an element with id/class matching "amazon". The fixture has no such element, so fallback scan returns nothing.
**Fix needed:** Either update `_parse_ccc_html` to also run a full-page scan via `_extract_stats_from_section` when the amazon-section strategy fails, or update the test fixture to have proper amazon-section markup.
**Files:** `price_history.py` (`_parse_ccc_html`), `tests/test_price_history.py`
