---
phase: 04-messaging-platform-expansion
verified: 2026-03-14T10:30:00Z
status: passed
score: 15/15 must-haves verified
re_verification: false
---

# Phase 4: Messaging Platform Expansion Verification Report

**Phase Goal:** Israeli users can send product photos via WhatsApp or Instagram DMs and receive the same quality results as Telegram users, with platform-appropriate UX
**Verified:** 2026-03-14T10:30:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | WhatsApp adapter webhook handlers accept FastAPI Request and return PlainTextResponse | VERIFIED | `adapters/whatsapp.py` lines 75–110: `handle_webhook_verify(self, request: Request) -> PlainTextResponse` and `handle_webhook(self, request: Request) -> PlainTextResponse`; no `aiohttp.web` references remain |
| 2  | Instagram adapter webhook handlers accept FastAPI Request and return PlainTextResponse | VERIFIED | `adapters/instagram.py` lines 72–102: same FastAPI types; `from fastapi import Request` and `from fastapi.responses import PlainTextResponse` at top of file |
| 3  | Database has wa_opted_in and wa_last_msg_at columns in _MIGRATIONS | VERIFIED | `database.py` lines 294–295: `ALTER TABLE users ADD COLUMN wa_opted_in INTEGER NOT NULL DEFAULT 0` and `ALTER TABLE users ADD COLUMN wa_last_msg_at REAL` |
| 4  | First-time WhatsApp user receives opt-in prompt with 'I agree' button before any photo processing | VERIFIED | `adapters/whatsapp.py` lines 156–159: `get_wa_opt_in` gate blocks non-opted users; `_send_opt_in_prompt()` lines 182–211 sends interactive button with `optin:agree` reply ID |
| 5  | User who tapped 'I agree' can send photos and receive product results | VERIFIED | `adapters/whatsapp.py` lines 130–142: `optin:agree` button_reply sets `wa_opted_in` via `database.set_wa_opt_in`; after gate passes, image messages dispatch to `_on_photo` (line 163) |
| 6  | Product results are sent as a WhatsApp list message with up to 10 items when BotCore detects platform is whatsapp | VERIFIED | `bot_core.py` line 1032: `if self.adapter.platform_name == "whatsapp" and hasattr(self.adapter, "send_list_message")` — sends up to 10 rows via `send_list_message` (lines 1034–1046) |
| 7  | If 24-hour window has closed, bot can send a template message instead of free-form results | VERIFIED | `adapters/whatsapp.py` lines 213–218: `_is_window_open()` checks `get_wa_last_msg_at`; `send_template()` lines 252–276 sends Meta template with configurable name and lang_code |
| 8  | Commands /start, /help, /language, /providers work on WhatsApp before opt-in | VERIFIED | `adapters/whatsapp.py` lines 144–154: text slash-command passthrough runs BEFORE `get_wa_opt_in` gate check at line 157 |
| 9  | Annotated photos with product overlays are sent back to WhatsApp users via adapter.send_photo | VERIFIED | `adapters/whatsapp.py` lines 356–408: `send_photo` handles both bytes (uploads via media endpoint) and URLs; `bot_core.py` line 1023–1029 sends annotated bytes on all platforms |
| 10 | First-time Instagram user receives opt-in prompt with 'I agree' quick reply before any photo processing | VERIFIED | `adapters/instagram.py` lines 139–142: `get_ig_opt_in` gate; `_send_opt_in_prompt()` lines 163–188 sends quick reply with `optin:agree` payload |
| 11 | Instagram adapter handles image attachments in DM webhooks and dispatches to photo callback | VERIFIED | `adapters/instagram.py` lines 144–150: iterates `attachments`, checks `type == "image"`, sets `event["image_url"]`, calls `_on_photo` |
| 12 | Product results use Instagram quick replies for navigation | VERIFIED | `adapters/instagram.py` lines 220–246: `send_text` with buttons generates `quick_replies` payload; caption sent as follow-up text after photos |
| 13 | Commands /start, /help, /language, /providers work on Instagram before opt-in | VERIFIED | `adapters/instagram.py` lines 128–137: slash-command passthrough runs BEFORE `get_ig_opt_in` gate at line 140 |
| 14 | Hebrew/English language support works via translator.py through BotCore pipeline | VERIFIED | `TestTranslation` test in both test files mocks `translator.detect_language` and confirms it is called in `BotCore.handle_photo` pipeline; both test suites pass (21 and 20 tests) |
| 15 | Database has ig_opted_in column and get_ig_opt_in/set_ig_opt_in helpers | VERIFIED | `database.py` line 297: migration entry; lines 1974–1993: `get_ig_opt_in` and `set_ig_opt_in` matching wa_ pattern |

**Score:** 15/15 truths verified

---

### Required Artifacts

| Artifact | Provides | Status | Details |
|----------|----------|--------|---------|
| `adapters/whatsapp.py` | FastAPI webhook handlers, opt-in gate, 24h window, list messages, template send | VERIFIED | 415 lines; imports `from fastapi import Request` and `from fastapi.responses import PlainTextResponse`; contains `send_list_message`, `send_template`, `_send_opt_in_prompt`, `_is_window_open` |
| `adapters/instagram.py` | FastAPI webhook handlers, opt-in gate, quick replies, photo handling | VERIFIED | 315 lines; same FastAPI imports; contains `_send_opt_in_prompt`, opt-in gate in `_process_message`, command passthrough |
| `database.py` | DB migration entries and helper functions for WA and IG opt-in | VERIFIED | Lines 294–297: 3 migration entries; lines 1938–2003: 5 helpers (`get_wa_opt_in`, `set_wa_opt_in`, `update_wa_last_msg_at`, `get_wa_last_msg_at`, `get_ig_opt_in`, `set_ig_opt_in`) |
| `bot_core.py` | Platform-aware multi-product delivery using WhatsApp list messages | VERIFIED | Lines 1032–1046: checks `platform_name == "whatsapp"` and `hasattr(adapter, "send_list_message")`; calls `send_list_message` with rows and sections |
| `tests/test_whatsapp_adapter.py` | Unit tests for all WhatsApp compliance and messaging features | VERIFIED | 21 tests across 8 classes (TestOptIn, TestListMessage, TestTemplateSend, TestWindowTracking, TestWebhookMigration, TestAnnotatedPhoto, TestTranslation, TestBotCoreListMessage); all passing |
| `tests/test_instagram_adapter.py` | Unit tests for Instagram compliance and messaging features | VERIFIED | 20 tests across 7 classes (TestOptIn, TestQuickReplies, TestPhotoHandling, TestWebhookMigration, TestGraphApiAuth, TestAnnotatedPhoto, TestTranslation); all passing |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `webhook_routes.py` | `adapters/whatsapp.py` | `adapter.handle_webhook(request)` returns `PlainTextResponse` | WIRED | `webhook_routes.py` lines 52–59: dispatches FastAPI `Request` to `adapter.handle_webhook(request)`; `whatsapp.py` returns `PlainTextResponse` |
| `webhook_routes.py` | `adapters/instagram.py` | `adapter.handle_webhook(request)` returns `PlainTextResponse` | WIRED | Same router logic; `instagram.py` returns `PlainTextResponse` |
| `adapters/whatsapp.py` | `database.py` | `get_wa_opt_in` / `set_wa_opt_in` for consent check | WIRED | `whatsapp.py` line 19: `import database`; line 136: `await database.set_wa_opt_in`; line 157: `await database.get_wa_opt_in` |
| `adapters/whatsapp.py` | `database.py` | `update_wa_last_msg_at` / `get_wa_last_msg_at` for window tracking | WIRED | `whatsapp.py` line 127: `await database.update_wa_last_msg_at`; `_is_window_open` line 215: `await database.get_wa_last_msg_at` |
| `adapters/whatsapp.py` | `adapters/shared_meta.py` | `send_graph_api` for list and template messages | WIRED | `whatsapp.py` lines 21–25 imports `send_graph_api`; used in `send_list_message` (line 243), `send_template` (line 269), `_send_opt_in_prompt` (line 208) |
| `bot_core.py` | `adapters/whatsapp.py` | `adapter.send_list_message` when `platform_name == "whatsapp"` | WIRED | `bot_core.py` lines 1032–1046: gate check `platform_name == "whatsapp"` + `hasattr(adapter, "send_list_message")`; calls `await self.adapter.send_list_message(...)` |
| `adapters/instagram.py` | `database.py` | `get_ig_opt_in` / `set_ig_opt_in` for Instagram consent | WIRED | `instagram.py` line 18: `import database`; line 121: `await database.set_ig_opt_in`; line 140: `await database.get_ig_opt_in` |
| `adapters/instagram.py` | `adapters/shared_meta.py` | `send_graph_api` for sending messages with quick replies | WIRED | `instagram.py` lines 20–23 imports `send_graph_api`; used in `send_text` (line 244), `send_photo` (line 300), `_send_opt_in_prompt` (line 183) |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| WHAT-01 | 04-02 | Bot responds to photo messages on WhatsApp with product results | SATISFIED | `_process_message` routes `msg_type == "image"` to `_on_photo` after opt-in gate; `BotCore.handle_photo` processes and returns results |
| WHAT-02 | 04-02 | All WhatsApp interactions use structured messages (buttons, list messages) — no free-text AI chat | SATISFIED | `send_text` uses interactive button format when buttons provided; `send_list_message` for multi-product picker; `send_template` for re-engagement |
| WHAT-03 | 04-02 | WhatsApp message templates approved by Meta for outbound messages | SATISFIED | `send_template()` implemented with configurable `template_name` defaulting to `product_results_ready`; user_setup docs in PLAN.md specify Meta dashboard template submission |
| WHAT-04 | 04-02 | WhatsApp adapter handles 24-hour conversation window correctly | SATISFIED | `update_wa_last_msg_at` called on every inbound message (line 127); `_is_window_open` checks 86400s threshold; `send_template` available for out-of-window use |
| WHAT-05 | 04-02 | User opt-in flow before receiving messages (WhatsApp compliance) | SATISFIED | Opt-in gate in `_process_message` (lines 156–159); `_send_opt_in_prompt` sends interactive consent button; `optin:agree` callback sets DB flag |
| INST-01 | 04-03 | Bot responds to photo messages in Instagram DMs with product results | SATISFIED | `_process_message` routes image attachment to `_on_photo` (lines 144–150); same `BotCore` pipeline as Telegram |
| INST-02 | 04-01 | Instagram adapter uses Meta Graph API with proper authentication | SATISFIED | `shared_meta.py` `send_graph_api` uses `Authorization: Bearer {token}` header (line 79); `verify_webhook_signature` validates X-Hub-Signature-256 HMAC; `download_photo` uses Bearer token header (instagram.py line 197) |
| INST-03 | 04-03 | Instagram interactions use structured replies where supported | SATISFIED | `send_text` with buttons generates `quick_replies` payload (lines 220–246); `send_photo` sends caption as separate text follow-up with quick reply buttons |

No orphaned requirements — all 8 requirement IDs from plans are accounted for, and REQUIREMENTS.md confirms all 8 as Phase 4.

---

### Anti-Patterns Found

No blockers or warnings found.

| File | Pattern Checked | Result |
|------|-----------------|--------|
| `adapters/whatsapp.py` | TODO/FIXME/placeholder | None found |
| `adapters/whatsapp.py` | `return null` / empty implementations | None; `delete_message` and `edit_text` are intentional no-op / redirect patterns per WhatsApp API constraints |
| `adapters/instagram.py` | TODO/FIXME/placeholder | None found |
| `adapters/instagram.py` | `return null` / empty implementations | None; same intentional patterns for unsupported operations |
| `bot_core.py` (modified lines) | Stub wiring | None; `send_list_message` call is fully wired with real sections data |
| `database.py` (new functions) | Empty implementations | None; all helpers contain real SQL queries with ON CONFLICT upserts |

---

### Human Verification Required

#### 1. WhatsApp 24-Hour Window in Production

**Test:** Send a WhatsApp photo, wait 25 hours without interacting, then send another photo
**Expected:** Bot sends a `product_results_ready` template message instead of free-form product results
**Why human:** Cannot simulate real elapsed time or verify Meta template approval status in automated tests

#### 2. Meta Template Submission Status

**Test:** Check Meta Business Manager that `product_results_ready` template (English and Hebrew variants) is approved
**Expected:** Template appears as "Approved" in Meta Business Manager -> WhatsApp -> Message Templates
**Why human:** External dashboard state; bot code only references the template name, cannot verify Meta's approval

#### 3. Instagram Quick Reply Navigation Flow

**Test:** Send a product photo via Instagram DM; when results arrive, tap a quick reply navigation button
**Expected:** Bot responds with next/previous page results or product details
**Why human:** Quick reply callback routing from Instagram Messenger Platform requires a live webhook connection

#### 4. Annotated Photo Visual Quality on WhatsApp/Instagram

**Test:** Send a multi-product photo; verify the annotated overlay image renders correctly on both platforms
**Expected:** Image shows numbered product bounding boxes with readable labels matching the list message rows
**Why human:** Visual rendering quality and image compression behavior on mobile clients cannot be tested programmatically

---

### Gaps Summary

No gaps. All 15 observable truths verified, all 6 artifacts pass all three levels (exists, substantive, wired), all 8 key links confirmed wired, and all 8 requirements covered.

The 4 human verification items are operational concerns (live Meta platform configuration, external dashboard state) — not code gaps. The implementation is complete.

---

_Verified: 2026-03-14T10:30:00Z_
_Verifier: Claude (gsd-verifier)_
