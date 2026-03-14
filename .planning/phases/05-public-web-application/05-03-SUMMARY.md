---
phase: 05-public-web-application
plan: 03
subsystem: ui
tags: [jinja2, tailwind, htmx, rtl, i18n, hebrew, mobile, fastapi]

# Dependency graph
requires:
  - phase: 05-public-web-application/05-01
    provides: web_app/router.py with homepage and upload routes, web_base.html base template
  - phase: 05-public-web-application/05-02
    provides: search.html result page, product_cards.html, product_tabs.html, price history bar

provides:
  - Hebrew/English translation dict (_STRINGS) with Unicode Hebrew text for all user-visible strings
  - _get_lang() helper reading ?lang= param → cookie → "he" default
  - _t(lang) helper returning translation dict
  - Mobile-responsive upload zone with 200px+ height and 48px+ tap targets
  - RTL-safe Tailwind (all logical properties: ms-, me-, ps-, pe-, text-start, text-end)
  - Language toggle in header (active lang bold indigo, inactive gray)
  - Bilingual SSE progress strings (Analyzing, Found N products, Searching)
  - Bilingual error messages for upload validation (not_an_image, photo_too_large)

affects:
  - web_app
  - templates

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_STRINGS dict with 'he'/'en' keys provides all user-visible text at module level"
    - "_get_lang() reads ?lang= param first, then cookie, then defaults to 'he' (Israeli-first)"
    - "Result page lang determined by: request ?lang= param → cookie → stored row.lang"
    - "Tests for English-specific text use ?lang=en query param explicitly"

key-files:
  created: []
  modified:
    - web_app/router.py
    - web_app/templates/web_base.html
    - web_app/templates/home.html
    - web_app/templates/search.html
    - web_app/templates/error.html
    - web_app/templates/partials/progress.html
    - web_app/templates/partials/product_tabs.html
    - web_app/templates/partials/product_cards.html
    - tests/test_web_app.py

key-decisions:
  - "Hebrew-first default (lang='he') for all routes — target market is Israeli users"
  - "Badge text computed in Python via _shipping_badge(item) using t dict — template uses {{ item.badge.text }} not a separate key lookup"
  - "Result page lang fallback: ?lang= param overrides cookie, cookie overrides stored row.lang"
  - "Tests updated to use ?lang=en for English-specific string assertions (not changing test logic, only request lang)"
  - "No physical directional Tailwind classes (ml-, mr-, text-left, text-right) — all templates use logical properties (ms-, me-, text-start, text-end)"

patterns-established:
  - "i18n Pattern: _STRINGS['he'/'en'] dict at module level, _t(lang) helper, t=_t(lang) passed to all TemplateResponse calls"
  - "Touch Target Pattern: buttons use min-height: 48px inline style to guarantee mobile tap target compliance"
  - "RTL-safe Pattern: Tailwind logical properties (ms-, me-, start-0, end-0) instead of directional (ml-, mr-, left-0, right-0)"

requirements-completed: [WEBA-05]

# Metrics
duration: 7min
completed: 2026-03-14
---

# Phase 05 Plan 03: Mobile RTL Polish and Hebrew/English i18n Summary

**Full Hebrew/English i18n with Unicode Hebrew text, mobile-responsive RTL layout, and touch-friendly upload zone across all web templates**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-14T12:38:12Z
- **Completed:** 2026-03-14T12:45:25Z
- **Tasks:** 1 auto + 1 checkpoint (auto-approved)
- **Files modified:** 9

## Accomplishments
- Added complete Hebrew/English translation dict with 30+ Unicode Hebrew strings covering all user-visible text
- Replaced all hardcoded English text in templates with `{{ t.xxx }}` Jinja2 template variables
- Mobile-responsive upload hero zone with 200px+ drop area and 48px+ tap-target buttons
- All Tailwind classes converted to logical properties (ms-, me-, text-start, text-end) — zero physical directional classes remain
- Language toggle in header shows active lang in bold indigo, persists via cookie across page loads
- SSE progress strings (Analyzing, Found N products, Searching) translated per request lang

## Task Commits

1. **Task 1: Add Hebrew/English i18n content and RTL polish to all templates** - `e5232b7` (feat)
2. **Task 2: Verify complete web application end-to-end** - auto-approved (auto_advance=true)

**Plan metadata:** [pending final commit]

## Files Created/Modified
- `web_app/router.py` - Added _STRINGS dict, _get_lang(), _t() helpers; updated all routes to pass t=_t(lang); SSE generator now accepts lang param
- `web_app/templates/web_base.html` - i18n header with lang toggle, t.site_title, t.affiliate_disclosure in footer; logical Tailwind classes
- `web_app/templates/home.html` - i18n upload hero (t.upload_title, t.upload_button, t.how_it_works, etc.); touch-friendly 200px+ drop zone, 48px+ button
- `web_app/templates/search.html` - t.photo_detected in collapsible summary
- `web_app/templates/error.html` - t.search_again for button text
- `web_app/templates/partials/progress.html` - t.analyzing for status header, mobile-friendly padding
- `web_app/templates/partials/product_tabs.html` - t.product for tab labels, min-height 44px for touch
- `web_app/templates/partials/product_cards.html` - t.view_on_amazon, t.price_history_unavailable, t.no_results, t.reviews, t.price_unavailable; 48px+ Amazon button
- `tests/test_web_app.py` - Updated 8 tests to use ?lang=en for English-specific string assertions

## Decisions Made
- Hebrew-first default (lang="he") for all routes — target market is Israeli users
- Badge text computed in Python via `_shipping_badge(item, t)` using t dict — template uses `{{ item.badge.text }}` rather than a separate key lookup in template
- Result page lang fallback: ?lang= param → cookie → stored row.lang (not always "he")
- Tests updated to use `?lang=en` for English-specific string assertions — preserves test logic, adds explicit lang context
- No physical directional Tailwind classes anywhere in templates — verified with grep

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated tests to use explicit ?lang=en for English string assertions**
- **Found during:** Task 1 (i18n implementation)
- **Issue:** Existing tests asserted English strings ("View on Amazon", "Ships to Israel", etc.) but saved search data has lang="he". After i18n, Hebrew pages show Hebrew text, breaking 8 test assertions.
- **Fix:** Added `?lang=en` query param to 8 test requests that check English-specific text (test_affiliate_disclosure, test_product_card_fields, test_shipping_badge_green, test_shipping_badge_red, test_product_tabs, test_price_history_unavailable, test_stream_stages, test_stream_invalid_session).
- **Files modified:** tests/test_web_app.py
- **Verification:** All 32 web app tests pass
- **Committed in:** e5232b7 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - test update for i18n compatibility)
**Impact on plan:** Minimal — test logic unchanged, only request lang made explicit. No scope creep.

## Issues Encountered
- Pre-existing test failure in `tests/test_malformed_responses.py::TestAnthropicProviderErrors::test_anthropic_api_error_raises` (anthropic library version incompatibility with httpx AsyncClient) — confirmed pre-existing before our changes, out of scope.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 05 plan 03 complete — mobile RTL polish and i18n fully implemented
- All Phase 05 plans (01-03) now complete
- The web application is ready for end-to-end human verification
- Deploy configuration (reverse proxy, production env vars) needed before public launch

---
*Phase: 05-public-web-application*
*Completed: 2026-03-14*
