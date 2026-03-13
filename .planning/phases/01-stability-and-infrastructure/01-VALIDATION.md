---
phase: 1
slug: stability-and-infrastructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | `pytest.ini` (asyncio_mode = auto) |
| **Quick run command** | `pytest tests/ -x --timeout=30` |
| **Full suite command** | `pytest tests/` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x --timeout=30`
- **After every plan wave:** Run `pytest tests/`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | STAB-01 | unit | `pytest tests/test_providers_manager.py -x -k timeout` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | STAB-02 | unit | `pytest tests/test_progressive_health.py -x` | ✅ | ⬜ pending |
| 01-01-03 | 01 | 1 | STAB-03 | unit | `pytest tests/test_database.py -x -k import` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | STAB-04 | integration | `pytest tests/test_shutdown.py -x` | ❌ W0 | ⬜ pending |
| 01-01-05 | 01 | 1 | STAB-05 | unit | `pytest tests/test_bot.py -x -k "photo_size or oversized"` | ❌ W0 | ⬜ pending |
| 01-01-06 | 01 | 1 | STAB-06 | unit | `pytest tests/test_settings_store.py -x` | ✅ | ⬜ pending |
| 01-01-07 | 01 | 1 | STAB-07 | unit | `pytest tests/test_style.py -x -k error` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | INFR-01 | integration | `pytest tests/test_gateway.py -x` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | INFR-02 | unit | `pytest tests/test_bot.py -x -k compress` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 2 | INFR-03 | unit | `pytest tests/test_admin_service.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_shutdown.py` — stubs for STAB-04 (graceful shutdown)
- [ ] `tests/test_gateway.py` — stubs for INFR-01 (consolidated FastAPI gateway)
- [ ] `tests/test_admin_service.py` — stubs for INFR-03 (admin service layer)
- [ ] Add timeout-specific tests to `tests/test_providers_manager.py` — covers STAB-01
- [ ] Add oversized photo test to `tests/test_bot.py` — covers STAB-05
- [ ] Add error message admin/user differentiation tests to `tests/test_style.py` — covers STAB-07
- [ ] Add CSV import atomic transaction tests to `tests/test_database.py` — covers STAB-03
- [ ] Add compress image offloading test to `tests/test_bot.py` — covers INFR-02

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Docker single-port exposure | INFR-01 | Requires Docker runtime | `docker compose up amazon-bot` → verify only port 8080 exposed |
| Graceful shutdown with real Telegram connection | STAB-04 | Requires live PTB polling | Start bot, send SIGINT, verify no orphaned connections in logs |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
