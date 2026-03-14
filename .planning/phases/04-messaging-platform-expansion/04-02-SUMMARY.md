---
phase: 04-messaging-platform-expansion
plan: 02
subsystem: whatsapp-adapter
tags: [whatsapp, compliance, opt-in, 24h-window, list-messages, template, bot-core, tdd]
dependency_graph:
  requires: [04-01]
  provides: [whatsapp-compliance-flows, whatsapp-list-messages, whatsapp-template-send]
  affects: [bot_core.py, adapters/whatsapp.py]
tech_stack:
  added: []
  patterns: [opt-in gate, 24h customer care window, WhatsApp list messages, Meta template messages, platform-aware BotCore routing]
key_files:
  created:
    - tests/test_whatsapp_adapter.py
  modified:
    - adapters/whatsapp.py
    - bot_core.py
decisions:
  - Slash commands (/start, /help, /language, /providers) pass through opt-in gate so new users can get help immediately, matching Telegram behavior
  - send_list_message is a WhatsApp-specific method (not in base class) checked via hasattr in BotCore for platform-agnostic safety
  - Annotated photo sent on all platforms (buttons=[]) then list message follows on WhatsApp — WhatsApp cannot attach buttons to image messages with >3 items
  - _compress_image patched in BotCore integration tests to avoid PIL dependency on real JPEG data
metrics:
  duration: 10m
  completed_date: "2026-03-14"
  tasks_completed: 3
  files_modified: 3
---

# Phase 4 Plan 02: WhatsApp Compliance Flows and List Messages Summary

WhatsApp adapter with Meta compliance (opt-in gate, 24h window tracking, template re-engagement), structured list messages for multi-product picker, and platform-aware BotCore routing — all covered by 21 passing unit tests.

## What Was Built

### Task 1: WhatsApp Adapter Compliance and Messaging (f0640df)

Added to `adapters/whatsapp.py`:

- **Opt-in gate in `_process_message()`**: Records timestamp via `database.update_wa_last_msg_at` on every inbound message. Checks `optin:agree` button reply before the gate. Passes slash commands through before the gate. Blocks all other messages (photo, text, non-optin callbacks) until user has consented.
- **`_send_opt_in_prompt()`**: Sends a WhatsApp interactive button message with "I agree" button (reply ID `optin:agree`) to new users.
- **`_is_window_open()`**: Returns `True` if `database.get_wa_last_msg_at` returns a timestamp < 24 hours ago — used for deciding whether to send free-form vs template messages.
- **`send_list_message()`**: Sends a WhatsApp `interactive/list` message with body (truncated to 1024 chars), button label (truncated to 20 chars), and sections with rows.
- **`send_template()`**: Sends a pre-approved Meta template message (default: `product_results_ready`, lang `en`) for re-engagement outside the 24h window.

All new methods use `send_graph_api` from `adapters/shared_meta.py` and `database` module functions from the Plan 01 schema.

### Task 2: BotCore WhatsApp Multi-Product Picker (b3839e5)

Modified `bot_core.py` multi-product branch (`handle_photo`, around line 1009):

1. Sends annotated photo to all platforms with `buttons=[]` (WhatsApp cannot attach buttons to image messages with many products).
2. Detects `platform_name == "whatsapp"` and `hasattr(adapter, "send_list_message")` — if both true, sends a follow-up list message with up to 10 rows mapping to `CB_PICK_PRODUCT{i}` callback IDs.
3. For all other platforms (Telegram, etc.) retains the existing behavior: `send_text` with inline picker buttons.

### Task 3: Comprehensive Unit Tests (ecc5865)

`tests/test_whatsapp_adapter.py` — 21 tests, all passing:

| Class | Tests | What's Covered |
|-------|-------|----------------|
| TestOptIn | 5 | First-time user gets prompt, optin:agree sets DB + confirms, opted-in user dispatches to on_photo, commands pass through pre-opt-in |
| TestListMessage | 3 | Correct interactive list payload, 1024-char body truncation, 20-char button label truncation |
| TestTemplateSend | 2 | Correct template payload shape, default name/lang |
| TestWindowTracking | 3 | Open (< 24h), closed (> 24h), closed (no timestamp) |
| TestWebhookMigration | 4 | Verify challenge/403, signature reject, valid request 200 |
| TestAnnotatedPhoto | 2 | Bytes upload + message send, URL direct send |
| TestTranslation | 1 | `detect_language` invoked through BotCore pipeline |
| TestBotCoreListMessage | 1 | `send_list_message` called with correct sections on WhatsApp multi-product |

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files Created/Modified
- [x] `adapters/whatsapp.py` — all 4 new methods present
- [x] `bot_core.py` — `send_list_message` appears at lines 1032 and 1042
- [x] `tests/test_whatsapp_adapter.py` — 549 lines, 21 tests

### Commits
- [x] f0640df: feat(04-02) WhatsApp adapter compliance
- [x] b3839e5: feat(04-02) BotCore list message routing
- [x] ecc5865: test(04-02) WhatsApp adapter unit tests

### Verification Commands
- [x] `pytest tests/test_whatsapp_adapter.py -x -v` — 21 passed
- [x] `python -c "from adapters.whatsapp import WhatsAppAdapter"` — import ok
- [x] `python -c "from bot_core import BotCore"` — import ok
- [x] No `web.Request`/`web.Response` references in whatsapp.py
- [x] `send_list_message` present in both whatsapp.py and bot_core.py

## Self-Check: PASSED
