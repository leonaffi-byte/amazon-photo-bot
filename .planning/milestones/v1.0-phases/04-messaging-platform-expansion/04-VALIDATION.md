---
phase: 4
slug: messaging-platform-expansion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | `pytest.ini` (`asyncio_mode = auto`, `testpaths = tests`) |
| **Quick run command** | `pytest tests/test_whatsapp_adapter.py tests/test_instagram_adapter.py -x` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_whatsapp_adapter.py tests/test_instagram_adapter.py -x`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | WHAT-01 | unit | `pytest tests/test_whatsapp_adapter.py::TestPhotoHandling -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | WHAT-02 | unit | `pytest tests/test_whatsapp_adapter.py::TestListMessage -x` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | WHAT-03 | unit | `pytest tests/test_whatsapp_adapter.py::TestTemplateSend -x` | ❌ W0 | ⬜ pending |
| 04-01-04 | 01 | 1 | WHAT-04 | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowTracking -x` | ❌ W0 | ⬜ pending |
| 04-01-05 | 01 | 1 | WHAT-05 | unit | `pytest tests/test_whatsapp_adapter.py::TestOptIn -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | INST-01 | unit | `pytest tests/test_instagram_adapter.py::TestPhotoHandling -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 1 | INST-02 | unit | `pytest tests/test_instagram_adapter.py::TestGraphApiAuth -x` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 1 | INST-03 | unit | `pytest tests/test_instagram_adapter.py::TestQuickReplies -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_whatsapp_adapter.py` — stubs for WHAT-01 through WHAT-05
- [ ] `tests/test_instagram_adapter.py` — stubs for INST-01, INST-02, INST-03
- [ ] DB migration verification in `tests/test_database.py` (add opt-in column tests)
- No new framework install needed — pytest + pytest-asyncio already in use

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Meta template approval | WHAT-03 | Requires Meta Business Manager dashboard | Submit template in en/he; verify approval within 24h |
| WhatsApp sandbox opt-in bypass | WHAT-05 | Sandbox may skip opt-in rules | Test opt-in flow against production config, not sandbox |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
