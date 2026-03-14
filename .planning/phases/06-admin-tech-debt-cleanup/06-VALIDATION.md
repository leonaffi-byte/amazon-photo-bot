---
phase: 6
slug: admin-tech-debt-cleanup
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-14
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio (asyncio_mode = auto) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_admin_service.py tests/test_bot.py -x -q` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_admin_service.py tests/test_bot.py -x -q`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 0 | ADMN-02 | unit | `pytest tests/test_admin_service.py -x -q -k "today"` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 0 | ADMN-06 | unit | `pytest tests/test_bot.py -x -q -k "webtoken"` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | ADMN-02 | unit | `pytest tests/test_admin_service.py -x -q -k "today"` | ❌ W0 | ⬜ pending |
| 06-01-04 | 01 | 1 | ADMN-06 | unit | `pytest tests/test_bot.py -x -q -k "webtoken"` | ❌ W0 | ⬜ pending |
| 06-01-05 | 01 | 1 | ADMN-06 | unit | `pytest tests/test_bot.py -x -q -k "webtoken"` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_admin_service.py` — Add `test_today_searches_reflects_actual_data` and `test_today_users_reflects_actual_data` (today-specific assertions in existing `TestStatsService` class)
- [ ] `tests/test_bot.py` — Add `test_webtoken_command_admin_only` and `test_webtoken_command_delivers_token` (mock `generate_fallback_token`, assert `send_text` called with token)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Dashboard stat cards render today counts visually | ADMN-02 | Requires browser rendering of Jinja2 template | Open `/admin` in browser, verify "Today Searches" and "Today Users" show non-zero values after performing a search |
| `/webtoken` sends token via Telegram | ADMN-06 | Requires live Telegram bot | Send `/webtoken` in Telegram, verify token appears in MarkdownV2 format |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
