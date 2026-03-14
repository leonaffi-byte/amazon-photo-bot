---
phase: 5
slug: public-web-application
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 5 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| **Config file** | `pytest.ini` (existing) |
| **Quick run command** | `pytest tests/test_web_app.py -x -q` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_web_app.py -x -q`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | WEBA-01 | unit | `pytest tests/test_web_app.py::TestUpload::test_valid_upload -x` | W0 | pending |
| 05-01-02 | 01 | 1 | WEBA-01 | unit | `pytest tests/test_web_app.py::TestUpload::test_oversized_file -x` | W0 | pending |
| 05-01-03 | 01 | 1 | WEBA-01 | unit | `pytest tests/test_web_app.py::TestUpload::test_non_image_rejected -x` | W0 | pending |
| 05-01-04 | 01 | 1 | WEBA-01 | unit | `pytest tests/test_web_app.py::TestSSE::test_stream_stages -x` | W0 | pending |
| 05-02-01 | 02 | 2 | WEBA-02 | unit | `pytest tests/test_web_app.py::TestResultPage::test_product_card_fields -x` | W0 | pending |
| 05-02-02 | 02 | 2 | WEBA-02 | unit | `pytest tests/test_web_app.py::TestResultPage::test_affiliate_url -x` | W0 | pending |
| 05-02-03 | 02 | 2 | WEBA-03 | unit | `pytest tests/test_web_app.py::TestPriceHistoryBar::test_price_history_bar_rendered -x` | W0 | pending |
| 05-02-04 | 02 | 2 | WEBA-04 | unit | `pytest tests/test_web_app.py::TestResultPage::test_og_tags -x` | W0 | pending |
| 05-02-05 | 02 | 2 | WEBA-04 | unit | `pytest tests/test_web_app.py::TestResultPage::test_expired_result -x` | W0 | pending |
| 05-02-06 | 02 | 2 | WEBA-04 | unit | `pytest tests/test_web_app.py::TestSearchStore::test_purge_expired -x` | W0 | pending |
| 05-02-07 | 02 | 2 | WEBA-05 | smoke | `pytest tests/test_web_app.py::TestHomePage::test_mobile_viewport -x` | W0 | pending |
| 05-02-08 | 02 | 2 | WEBA-05 | unit | `pytest tests/test_web_app.py::TestResultPage::test_rtl_dir_attr -x` | W0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_web_app.py` -- stubs for WEBA-01 through WEBA-05
- [ ] `web_app/__init__.py` -- exports router
- [ ] `web_app/router.py` -- FastAPI APIRouter (stub with placeholder routes)
- [ ] `web_app/search_store.py` -- DB functions for web_searches table
- [ ] `web_app/deps.py` -- SlowAPI limiter setup
- [ ] `web_app/templates/web_base.html` -- public base template
- [ ] DB migration: `web_searches` table added to `database.init_db()`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Drag-drop UX on mobile | WEBA-01 | Browser interaction | Open on phone, drag photo onto upload zone |
| Visual appearance of product cards | WEBA-02 | Visual design | Compare rendered cards to expected layout |
| Social media preview rendering | WEBA-04 | External service | Share URL on WhatsApp/Telegram, verify preview |
| RTL Hebrew layout | WEBA-05 | Visual layout | Switch to Hebrew, verify text direction and spacing |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
