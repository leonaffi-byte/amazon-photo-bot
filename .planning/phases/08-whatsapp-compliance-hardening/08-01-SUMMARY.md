---
phase: 08-whatsapp-compliance-hardening
plan: "01"
subsystem: whatsapp-adapter
tags: [whatsapp, compliance, 24h-window, template, tdd]
dependency_graph:
  requires: []
  provides: [window-enforcement, _guard_window, _template_sent]
  affects: [adapters/whatsapp.py, tests/test_whatsapp_adapter.py]
tech_stack:
  added: []
  patterns: [guard-pattern, no-op-MessageRef, template-fallback]
key_files:
  created: []
  modified:
    - adapters/whatsapp.py
    - tests/test_whatsapp_adapter.py
decisions:
  - "_guard_window returns None (open) or no-op MessageRef (closed) — avoids exception-based flow control"
  - "edit_text returns None (not MessageRef) on closed window — consistent with void return type signature"
  - "_template_sent keyed by bare chat_id (phone number), matching how send methods receive it"
  - "Existing tests updated with open-window timestamp mock — Rule 1 auto-fix required by new guard"
metrics:
  duration: 12 min
  completed: "2026-03-14"
  tasks_completed: 2
  files_modified: 2
requirements_met: [WHAT-03, WHAT-04]
---

# Phase 8 Plan 01: Window Enforcement for WhatsApp Outbound Methods Summary

**One-liner:** 24h conversation window guard with single-fire template fallback and language-aware re-engagement wired into all 4 WhatsApp send methods via `_guard_window()`.

## What Was Built

`_guard_window(chat_id)` is a new async helper on `WhatsAppAdapter` that enforces Meta's 24-hour customer care window before any outbound send. When the window is open it returns `None` (proceed normally). When closed, it fires `send_template()` exactly once per user (using their stored language from the DB), sets `_template_sent[chat_id] = True`, and returns a no-op `MessageRef(message_id="")`.

All four public send methods are guarded:

- `send_text` — guard at top of body, returns no-op MessageRef
- `send_photo` — guard at top of body, prevents media upload entirely
- `send_list_message` — guard at top of body, returns no-op MessageRef
- `edit_text` — guard returns early with `None` (matching void return type)

The `_template_sent` flag resets in `_process_message` on any inbound message via `self._template_sent.pop(user_id, None)`, so the next closed-window encounter fires a fresh template.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Write failing tests for window enforcement | dedae51 | tests/test_whatsapp_adapter.py |
| 2 | Implement _guard_window and wire into all send methods | 473c486 | adapters/whatsapp.py, tests/test_whatsapp_adapter.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed 5 existing tests broken by new guard**

- **Found during:** Task 2 (GREEN phase)
- **Issue:** `TestListMessage` (3 tests) and `TestAnnotatedPhoto` (2 tests) did not mock `database.get_wa_last_msg_at`. After guarding `send_list_message` and `send_photo`, the guard calls `_is_window_open` which queries the DB. In tests the DB returns `None` (no timestamp), making the window appear closed, which diverted execution to the template path instead of the actual message path.
- **Fix:** Added `patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=time.time() - 3600))` to each of the 5 affected tests.
- **Files modified:** tests/test_whatsapp_adapter.py
- **Commit:** 473c486

## Verification Results

```
pytest tests/test_whatsapp_adapter.py -v
28 passed (7 new TestWindowEnforcement + 21 pre-existing)

grep -n "_guard_window" adapters/whatsapp.py
→ line 223: def, line 256: send_list_message, line 335: send_text,
  line 385: edit_text, line 399: send_photo (5 occurrences = def + 4 calls)

grep -n "_template_sent" adapters/whatsapp.py
→ line 60: __init__, line 130: _process_message pop, line 238: guard check,
  line 241: guard set (4 occurrences)
```

## Self-Check: PASSED

- adapters/whatsapp.py — FOUND, contains _guard_window and _template_sent
- tests/test_whatsapp_adapter.py — FOUND, contains TestWindowEnforcement
- Commit dedae51 — FOUND (RED phase tests)
- Commit 473c486 — FOUND (GREEN phase implementation)
