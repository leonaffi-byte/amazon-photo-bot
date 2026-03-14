# Phase 8: WhatsApp Compliance Hardening - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

WhatsApp adapter correctly enforces the 24-hour conversation window — checking window status before every outbound send and falling back to approved template messages when the window is closed. This is a compliance gap closure (INT-02, FLOW-02 from v1.0 audit), not new functionality.

</domain>

<decisions>
## Implementation Decisions

### Fallback behavior
- When a send method is called but the 24h window is closed, silently replace the free-form message with `send_template()` — the user gets a re-engagement template instead of the actual content
- Template fires only once per closed window per user. Subsequent sends during the same closed window return an empty `MessageRef` (no-op). This prevents template spam when bot_core sends progress + results + buttons in sequence
- The "template already sent" flag resets when the user sends their next inbound message (in `_process_message()`, alongside the existing `update_wa_last_msg_at` call). Natural reset — no timers needed
- No-op sends return `MessageRef` with empty `message_id` — callers already handle this gracefully

### Send method coverage
- All 4 public send methods get the window check: `send_text`, `send_photo`, `send_list_message`, `edit_text`
- `_send_opt_in_prompt()` is exempt — it's private, always triggered by an inbound message (window is always open)
- `send_template()` is exempt — it IS the fallback
- `edit_text` gets its own guard even though it delegates to `send_text` internally (belt-and-suspenders, explicit compliance)

### Template strategy
- Template name and count: Claude's discretion (one generic or context-specific)
- Language-aware: pass user's stored language preference to `send_template()` lang_code parameter
- Language resolved via DB lookup (`database.get_user_language()`) with fallback to "en"

### Claude's Discretion
- Error signaling approach — how callers (bot_core.py) detect the window was closed (silent swap, return value, logging)
- Whether to use a shared `_guard_window()` helper or inline the check in each method
- Template naming and whether one generic template suffices or context-specific templates are better
- Logging verbosity for window-closed events
- In-memory dict vs other storage for the "template already sent" tracking

</decisions>

<specifics>
## Specific Ideas

- Phase 4 decided: "Template notification for 24-hour window expiry: pre-approved Meta template message ('Your product results are ready! Tap to view.') re-engages user when window closes before results are ready"
- `_is_window_open()` already exists at whatsapp.py:213 — uses `database.get_wa_last_msg_at()` with 86400s threshold
- `send_template()` already exists at whatsapp.py:252 — sends pre-approved template via Graph API
- `wa_last_msg_at` is already tracked on every inbound message at whatsapp.py:127

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_is_window_open(user_key)` (whatsapp.py:213): Already implemented, checks DB timestamp against 86400s threshold
- `send_template(chat_id, template_name, lang_code)` (whatsapp.py:252): Already implemented, sends pre-approved Meta template
- `database.update_wa_last_msg_at(user_key, timestamp)`: Already called on every inbound message
- `database.get_wa_last_msg_at(user_key)`: Returns last inbound timestamp for window calculation
- `database.get_user_language()`: Available for language-aware template sending

### Established Patterns
- WhatsApp adapter uses `aiohttp.ClientSession` for outbound HTTP, `send_graph_api()` shared helper
- `MessageRef` dataclass for all send return values — platform, chat_id, message_id, raw
- WhatsApp edit is already a no-op (sends new message) — same pattern applies for window-closed sends
- `_process_message()` is the single inbound entry point — ideal place to reset template-sent flag

### Integration Points
- `bot_core.py`: Calls adapter send methods during analysis flow (progress, results, navigation buttons)
- `_process_message()`: Inbound handler where template-sent flag should reset
- `send_template()`: The fallback target — may need language parameter wiring
- Tests: Existing WhatsApp test patterns in `tests/` mock `send_graph_api` — extend for window scenarios

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 08-whatsapp-compliance-hardening*
*Context gathered: 2026-03-14*
