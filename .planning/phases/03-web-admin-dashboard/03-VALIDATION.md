---
phase: 03
slug: web-admin-dashboard
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/test_admin_web.py -x -q` |
| **Full suite command** | `pytest --deselect tests/test_israel_scraper.py::TestParseCccHtml::test_fallback_to_fullpage_scan -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_admin_web.py -x -q`
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | ADMN-01 | unit+integration | `pytest tests/test_admin_web.py -k auth -q` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | ADMN-02 | unit | `pytest tests/test_admin_web.py -k stats -q` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | ADMN-03 | unit | `pytest tests/test_admin_web.py -k keys -q` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | ADMN-04 | unit | `pytest tests/test_admin_web.py -k tags -q` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 2 | ADMN-05 | unit | `pytest tests/test_admin_web.py -k settings -q` | ❌ W0 | ⬜ pending |
| 03-03-02 | 03 | 2 | ADMN-06 | unit | `pytest tests/test_admin_web.py -k health -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_admin_web.py` — test stubs for all ADMN requirements
- [ ] `tests/conftest.py` — existing fixtures cover tmp_data_dir; may need test client fixture

*Existing pytest infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Telegram Login Widget flow | ADMN-01 | Requires real Telegram auth callback | Click login button, verify redirect to Telegram, verify session created |
| HTMX polling visual updates | ADMN-06 | Visual real-time behavior | Open health page, change provider status, verify auto-refresh |
| Responsive mobile layout | All | CSS layout testing | Open /admin on mobile viewport, verify sidebar collapses |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
