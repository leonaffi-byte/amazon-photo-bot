---
phase: 7
slug: multi-platform-visual-parity
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode = auto`) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_formatter_visual.py tests/test_bot_core_overlays.py -x` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_formatter_visual.py tests/test_bot_core_overlays.py tests/test_image_annotator.py -x`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 0 | PRCE-02 | unit | `pytest tests/test_formatter_visual.py::test_price_bar_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 0 | PRCE-03 | unit | `pytest tests/test_formatter_visual.py::test_deal_label_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 0 | ISRL-02 | unit | `pytest tests/test_formatter_visual.py::test_shipping_badge_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-01-04 | 01 | 0 | ANNO-02 | unit | `pytest tests/test_bot_core_overlays.py::test_annotate_with_overlays_called -x` | ❌ W0 | ⬜ pending |
| 07-01-05 | 01 | 0 | ANNO-04 | unit | `pytest tests/test_bot_core_overlays.py::test_progress_stages -x` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | ANNO-02 | unit | `pytest tests/test_bot_core_overlays.py::test_annotate_with_overlays_called -x` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | ANNO-03 | unit | `pytest tests/test_image_annotator.py -x` | ✅ | ⬜ pending |
| 07-03-01 | 03 | 1 | PRCE-02 | unit | `pytest tests/test_formatter_visual.py::test_price_bar_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-03-02 | 03 | 1 | PRCE-03 | unit | `pytest tests/test_formatter_visual.py::test_deal_label_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-03-03 | 03 | 1 | ISRL-02 | unit | `pytest tests/test_formatter_visual.py::test_shipping_badge_in_caption -x` | ❌ W0 | ⬜ pending |
| 07-04-01 | 04 | 2 | ANNO-04 | unit | `pytest tests/test_bot_core_overlays.py::test_progress_stages -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_formatter_visual.py` — stubs for PRCE-02, PRCE-03, ISRL-02; test `Formatter.product_caption` with mock `price_history` and `israel_result`
- [ ] `tests/test_bot_core_overlays.py` — stubs for ANNO-02, ANNO-04; test that `annotate_with_overlays` is called via `asyncio.to_thread` and that Stage 3/4 progress edits occur

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual overlay appearance on WhatsApp | ANNO-02 | WhatsApp rendering is platform-specific | Send test photo via WhatsApp, verify overlay image received |
| Progress message timing UX | ANNO-04 | Timing perception is subjective | Send photo, observe 4-stage messages appear sequentially |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
