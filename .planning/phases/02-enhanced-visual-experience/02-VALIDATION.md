---
phase: 02
slug: enhanced-visual-experience
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| **Config file** | `pytest.ini` (already configured) |
| **Quick run command** | `pytest tests/test_israel_scraper.py tests/test_price_history.py tests/test_style.py tests/test_image_annotator.py -x` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_israel_scraper.py tests/test_price_history.py tests/test_style.py tests/test_image_annotator.py -x`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | ANNO-01 | unit | `pytest tests/test_providers_base.py -x -k bbox` | ✅ | ⬜ pending |
| 02-01-02 | 01 | 1 | ANNO-02 | unit | `pytest tests/test_image_annotator.py -x -k overlay` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | ANNO-03 | unit | `pytest tests/test_image_annotator.py -x -k fallback` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | ISRL-01 | unit | `pytest tests/test_israel_scraper.py -x -k confidence` | ✅ (extend) | ⬜ pending |
| 02-02-02 | 02 | 1 | ISRL-02 | unit | `pytest tests/test_style.py -x -k shipping_badge` | ✅ (extend) | ⬜ pending |
| 02-02-03 | 02 | 1 | ISRL-03 | unit | `pytest tests/test_israel_scraper.py -x -k false_positive` | ❌ W0 | ⬜ pending |
| 02-02-04 | 02 | 1 | ISRL-04 | unit | `pytest tests/test_israel_scraper.py -x -k false_negative` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 2 | PRCE-01 | unit | `pytest tests/test_style.py -x -k price_summary` | ✅ (extend) | ⬜ pending |
| 02-03-02 | 03 | 2 | PRCE-02 | unit | `pytest tests/test_price_history.py -x -k price_bar` | ❌ W0 | ⬜ pending |
| 02-03-03 | 03 | 2 | PRCE-03 | unit | `pytest tests/test_price_history.py -x -k deal_label` | ✅ | ⬜ pending |
| 02-04-01 | 04 | 2 | ANNO-04 | unit | `pytest tests/test_bot.py -x -k progress` | ✅ (extend) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_image_annotator.py` — stubs for ANNO-02, ANNO-03 (overlay drawing, bbox threshold, RGBA→JPEG conversion)
- [ ] `tests/test_israel_scraper.py::TestFalsePositive` — stubs for ISRL-03 with 5+ known-negative HTML fixtures
- [ ] `tests/test_israel_scraper.py::TestFalseNegative` — stubs for ISRL-04 with 5+ known-positive HTML fixtures
- [ ] `tests/test_price_history.py::TestPriceBar` — stubs for PRCE-02 (render_price_bar edge cases)

*Existing infrastructure covers framework and conftest — only test file stubs needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual overlay quality on real photos | ANNO-02 | Subjective visual quality | Send 3+ multi-product photos to bot, verify overlay visibility and positioning |
| Progress message timing feels right | ANNO-04 | Subjective UX timing | Send photo, observe progress message flow, verify stages appear at natural intervals |
| Israel shipping accuracy on live Amazon | ISRL-03, ISRL-04 | Requires real Amazon responses | Check 20+ products with known shipping status, compare to badge shown |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
