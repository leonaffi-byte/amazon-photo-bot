# Phase 08: WhatsApp Compliance Hardening - Research

**Researched:** 2026-03-14
**Domain:** WhatsApp Cloud API 24-hour conversation window enforcement (Python / async adapter pattern)
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Fallback behavior:**
- When a send method is called but the 24h window is closed, silently replace the free-form message with `send_template()` — the user gets a re-engagement template instead of the actual content
- Template fires only once per closed window per user. Subsequent sends during the same closed window return an empty `MessageRef` (no-op). This prevents template spam when bot_core sends progress + results + buttons in sequence
- The "template already sent" flag resets when the user sends their next inbound message (in `_process_message()`, alongside the existing `update_wa_last_msg_at` call). Natural reset — no timers needed
- No-op sends return `MessageRef` with empty `message_id` — callers already handle this gracefully

**Send method coverage:**
- All 4 public send methods get the window check: `send_text`, `send_photo`, `send_list_message`, `edit_text`
- `_send_opt_in_prompt()` is exempt — it's private, always triggered by an inbound message (window is always open)
- `send_template()` is exempt — it IS the fallback
- `edit_text` gets its own guard even though it delegates to `send_text` internally (belt-and-suspenders, explicit compliance)

**Template strategy:**
- Template name and count: Claude's discretion (one generic or context-specific)
- Language-aware: pass user's stored language preference to `send_template()` lang_code parameter
- Language resolved via DB lookup (`database.get_user_lang()`) with fallback to "en"

### Claude's Discretion

- Error signaling approach — how callers (bot_core.py) detect the window was closed (silent swap, return value, logging)
- Whether to use a shared `_guard_window()` helper or inline the check in each method
- Template naming and whether one generic template suffices or context-specific templates are better
- Logging verbosity for window-closed events
- In-memory dict vs other storage for the "template already sent" tracking

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WHAT-03 | WhatsApp message templates approved by Meta for outbound messages | Template payload shape confirmed in existing `send_template()` (whatsapp.py:252). One generic template `product_results_ready` already in codebase. Language parameter already wired. |
| WHAT-04 | WhatsApp adapter handles 24-hour conversation window correctly | `_is_window_open()` (whatsapp.py:213) already exists. Gap: no send methods check it before calling `send_graph_api`. This phase closes the gap by inserting the guard into all 4 public send methods. |
</phase_requirements>

---

## Summary

Phase 8 is a targeted compliance gap-closure. The core machinery — `_is_window_open()`, `send_template()`, `database.get_wa_last_msg_at()`, and `database.update_wa_last_msg_at()` — is already implemented and tested (Phase 4). The gap is that none of the four public send methods (`send_text`, `send_photo`, `send_list_message`, `edit_text`) call `_is_window_open()` before dispatching to the Graph API. A closed window causes Meta to reject the API call with a 131026 error, which leaks as an unhandled exception rather than a graceful fallback.

The implementation inserts a `_guard_window()` helper inside `WhatsAppAdapter`. Each guarded send method calls it first. If the window is open, execution continues normally. If closed and no template has been sent to this user yet (tracked via an in-memory `dict[str, bool]`), the helper fires `send_template()` and marks the flag. If the template was already sent, it returns a no-op `MessageRef` immediately. The "template already sent" flag resets in `_process_message()` alongside the existing `update_wa_last_msg_at` call — no timers, no DB overhead.

The test surface adds two new scenarios to the existing `TestWindowTracking` class (or a new `TestWindowEnforcement` class): window-open path (send proceeds normally) and window-closed path (template fires once; second send is a no-op). All tests mock `send_graph_api` and `database`, consistent with the existing test patterns.

**Primary recommendation:** Implement a single `_guard_window(chat_id)` async helper that encapsulates the open-check, template-once, and no-op logic. All 4 guarded methods call it as the first `await`; on `True` return they proceed, on `False` they return the no-op `MessageRef` from the helper. This keeps the guard logic in one place, DRY, and easy to test in isolation.

---

## Standard Stack

### Core (already in use — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `aiohttp` | project-pinned | Outbound HTTP to Graph API | Already the HTTP client in `WhatsAppAdapter._session` |
| `aiosqlite` | project-pinned | Async SQLite for `wa_last_msg_at` / `wa_opted_in` | Already the DB layer; `get_wa_last_msg_at` and `update_wa_last_msg_at` exist |
| `pytest` + `pytest-asyncio` | project-pinned | Unit test framework | `asyncio_mode = auto`, `tests/` discovery — existing patterns |

### No new packages required

This phase involves zero new dependencies. The entire implementation lives inside `adapters/whatsapp.py` and the matching test file.

---

## Architecture Patterns

### Recommended Code Structure

```
adapters/
└── whatsapp.py          # Only file that changes

tests/
└── test_whatsapp_adapter.py   # New test class added
```

### Pattern 1: `_guard_window()` Helper Method

**What:** A private async method on `WhatsAppAdapter` that:
1. Derives `user_key` from `chat_id` (`f"whatsapp:{chat_id}"`)
2. Calls `_is_window_open(user_key)`
3. If open: returns `None` (proceed)
4. If closed and template not yet sent: calls `send_template()` with language-aware lang_code, sets in-memory flag, returns a no-op `MessageRef`
5. If closed and template already sent: returns a no-op `MessageRef` immediately

**When to use:** Called at the top of `send_text`, `send_photo`, `send_list_message`, and `edit_text`.

**Example:**
```python
# adapters/whatsapp.py — _guard_window implementation

def __init__(self, ...) -> None:
    ...
    # "template already sent" flag per-user for current closed window
    self._template_sent: dict[str, bool] = {}

async def _guard_window(self, chat_id: str) -> MessageRef | None:
    """
    Returns None if the 24-hour window is open (caller should proceed normally).
    Returns a no-op MessageRef if the window is closed (caller should return it).
    Fires send_template() exactly once per closed window per user.
    """
    user_key = f"whatsapp:{chat_id}"
    if await self._is_window_open(user_key):
        return None  # window open — proceed

    if not self._template_sent.get(chat_id, False):
        # First send attempt in closed window — fire template, mark flag
        lang_code = await database.get_user_lang(user_key) or "en"
        await self.send_template(chat_id, template_name="product_results_ready", lang_code=lang_code)
        self._template_sent[chat_id] = True
        logger.info("WhatsApp 24h window closed for %s — sent template re-engagement", chat_id)
    else:
        logger.debug("WhatsApp 24h window closed for %s — no-op (template already sent)", chat_id)

    return MessageRef(platform="whatsapp", chat_id=chat_id, message_id="", raw=None)
```

**Guarded send method example (`send_text`):**
```python
async def send_text(self, chat_id: str, text: str, buttons: list[list[Button]] | None = None) -> MessageRef:
    guard = await self._guard_window(chat_id)
    if guard is not None:
        return guard
    # ... existing send_text body unchanged ...
```

**`_process_message()` reset (alongside existing `update_wa_last_msg_at` call):**
```python
# Record last message timestamp for 24h window tracking
await database.update_wa_last_msg_at(user_key, _time.time())
# Reset template-sent flag now that window is open again
self._template_sent.pop(chat_id, None)
```

### Pattern 2: `edit_text` Belt-and-Suspenders Guard

`edit_text` delegates to `send_text` internally, but the guard is added at the `edit_text` level too. This is explicit compliance: any future refactor of `edit_text` to bypass `send_text` won't silently break window enforcement.

```python
async def edit_text(self, ref: MessageRef, text: str, buttons: list[list[Button]] | None = None) -> None:
    guard = await self._guard_window(ref.chat_id)
    if guard is not None:
        return  # edit_text returns None by contract
    await self.send_text(ref.chat_id, text, buttons)
```

### Anti-Patterns to Avoid

- **Checking the window inside `send_template()` itself:** `send_template` is the fallback — adding a guard there creates infinite recursion.
- **Checking window only in `edit_text` delegation path:** The guard must be at each method's entry point, not solely inside the delegate.
- **Storing `_template_sent` in the DB:** In-memory is correct here. If the process restarts the user will likely send a new message before the bot retries; DB overhead for a flag that resets on any inbound is not justified.
- **Deriving `user_key` as `chat_id` directly:** Always prepend `"whatsapp:"` — the DB keys are namespaced platform-prefixed strings (e.g. `"whatsapp:15551234"`).
- **Blocking `_is_window_open` on slow DB calls in tight loops:** The function is already async; callers already `await` it. No issue.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 24h window timestamp check | Custom time math | `_is_window_open()` at whatsapp.py:213 | Already implemented and unit-tested |
| Template dispatch | Custom Graph API call | `send_template()` at whatsapp.py:252 | Already implemented, already tested in `TestTemplateSend` |
| User language lookup | Custom DB query | `database.get_user_lang()` | Already in database module, consistent with project pattern |
| "Once per window" state | External storage, Redis, DB flag | `dict[str, bool]` instance attribute | Stateless per-restart, inbound message resets naturally |

**Key insight:** Every required primitive is already built. This phase is purely wiring — no new functions, no new DB columns, no new Graph API endpoints.

---

## Common Pitfalls

### Pitfall 1: `edit_text` Returns `None`, Not `MessageRef`

**What goes wrong:** `edit_text` signature is `-> None`. When the guard fires, returning the no-op `MessageRef` from `_guard_window()` is a type error for `edit_text` callers.
**Why it happens:** The abstract base `edit_text` returns `None`. The guard pattern that works for `send_text` (return the guard result) must be adapted: `edit_text` should `return` early (void) when the guard fires.
**How to avoid:** In `edit_text`, call `if await self._guard_window(ref.chat_id) is not None: return`. Do not `return guard`.

### Pitfall 2: Template Spam if Flag Resets Too Eagerly

**What goes wrong:** If the flag is cleared on every new `send_*` call (rather than on inbound), successive calls from bot_core during one analysis flow all fire templates.
**Why it happens:** bot_core sends 3–5 messages in sequence (progress, annotated photo, results, nav buttons). Without the "template already sent" guard, each triggers `send_template()`.
**How to avoid:** Flag resets ONLY in `_process_message()` when the user sends an inbound message. Never reset it inside `_guard_window()` or any send method.

### Pitfall 3: Patching Wrong Target in Tests

**What goes wrong:** Tests patch `database.get_wa_last_msg_at` at the wrong import path and the real DB is hit.
**Why it happens:** `whatsapp.py` uses `import database` (module-level), so patches must target `adapters.whatsapp.database.get_wa_last_msg_at` — consistent with existing `_WA_DB = "adapters.whatsapp.database"` pattern in `test_whatsapp_adapter.py`.
**How to avoid:** Use the existing `_WA_DB` patch-prefix constant. All new tests follow the same pattern.

### Pitfall 4: `user_key` vs `chat_id` Confusion

**What goes wrong:** Passing `chat_id` (e.g. `"15551234"`) to `_is_window_open()` instead of the DB key `"whatsapp:15551234"`.
**Why it happens:** `_is_window_open()` takes a `user_key` (platform-prefixed) but `chat_id` inside send methods is the bare phone number.
**How to avoid:** `_guard_window()` builds `user_key = f"whatsapp:{chat_id}"` internally. Send methods pass `chat_id`; `_guard_window` does the prefixing. Consistent with `_process_message()` which does `user_key = f"whatsapp:{user_id}"`.

---

## Code Examples

Verified patterns from existing codebase (HIGH confidence — source code read directly):

### Existing `_is_window_open` (whatsapp.py:213)
```python
async def _is_window_open(self, user_key: str) -> bool:
    """Return True if the 24-hour customer care window is still open."""
    ts = await database.get_wa_last_msg_at(user_key)
    if ts is None:
        return False
    return (_time.time() - ts) < 86400
```

### Existing `send_template` (whatsapp.py:252)
```python
async def send_template(
    self,
    chat_id: str,
    template_name: str = "product_results_ready",
    lang_code: str = "en",
) -> MessageRef:
    """Send a pre-approved Meta template message (for use outside 24h window)."""
    ...
    data = {
        "messaging_product": "whatsapp",
        "to": chat_id,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code},
        },
    }
    ...
```

### Existing test patch pattern (test_whatsapp_adapter.py)
```python
_WA_DB   = "adapters.whatsapp.database"
_WA_GAPI = "adapters.whatsapp.send_graph_api"

with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=recent_ts)):
    result = await adapter._is_window_open("whatsapp:111")
```

### `MessageRef` no-op pattern (adapters/base.py dataclass)
```python
@dataclass
class MessageRef:
    platform: str
    chat_id: str
    message_id: str   # Empty string "" signals no-op
    raw: Any = field(default=None, repr=False)
```

### New test structure (extends TestWindowTracking or new class)
```python
class TestWindowEnforcement:
    async def test_send_text_window_open_proceeds_normally(self):
        """send_text calls Graph API when 24h window is open."""
        adapter = _make_adapter()
        recent_ts = time.time() - 3600

        with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=recent_ts)), \
             patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result = await adapter.send_text("user123", "Hello")

        mock_api.assert_called_once()
        assert result.message_id == "msg_abc"

    async def test_send_text_window_closed_fires_template_once(self):
        """send_text fires send_template on first call when window is closed."""
        adapter = _make_adapter()
        old_ts = time.time() - 90000  # 25h ago

        with patch(f"{_WA_DB}.get_wa_last_msg_at", new=AsyncMock(return_value=old_ts)), \
             patch(f"{_WA_DB}.get_user_lang", new=AsyncMock(return_value="en")), \
             patch(_WA_GAPI, new=AsyncMock(return_value=_FAKE_GRAPH_RESPONSE)) as mock_api:
            result1 = await adapter.send_text("user123", "First message")
            result2 = await adapter.send_text("user123", "Second message")

        # Graph API called once (for the template), not for raw send_text content
        mock_api.assert_called_once()
        data = mock_api.call_args[0][2]
        assert data["type"] == "template"
        # Both sends return no-op (empty message_id after first template)
        assert result1.message_id == ""
        assert result2.message_id == ""

    async def test_template_flag_resets_on_inbound_message(self):
        """_process_message resets template-sent flag so next send fires template again."""
        adapter = _make_adapter()
        adapter._template_sent["user123"] = True

        with patch(f"{_WA_DB}.update_wa_last_msg_at", new=AsyncMock()), \
             patch(f"{_WA_DB}.get_wa_opt_in", new=AsyncMock(return_value=True)):
            await adapter._process_message(
                {"from": "user123", "type": "text", "text": {"body": "hello"}}, {}
            )

        assert adapter._template_sent.get("user123", False) is False
```

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (asyncio_mode = auto) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_whatsapp_adapter.py -x` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WHAT-03 | `send_template()` sends correct Meta template payload | unit | `pytest tests/test_whatsapp_adapter.py::TestTemplateSend -x` | YES (existing) |
| WHAT-04 | Window-open: send proceeds normally | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |
| WHAT-04 | Window-closed: template fires once, subsequent sends are no-op | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |
| WHAT-04 | Inbound message resets template-sent flag | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |
| WHAT-04 | `edit_text` returns void (not MessageRef) on closed window | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |
| WHAT-04 | `send_photo` respects window guard | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |
| WHAT-04 | `send_list_message` respects window guard | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowEnforcement -x` | NO — Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_whatsapp_adapter.py -x`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_whatsapp_adapter.py::TestWindowEnforcement` — covers WHAT-04 window enforcement scenarios (6+ test methods). Add to existing file — no new file needed.

*(Existing test infrastructure covers WHAT-03. Only new test class is needed.)*

---

## Sources

### Primary (HIGH confidence)
- `adapters/whatsapp.py` — Read directly; all methods, line numbers, and patterns confirmed
- `adapters/base.py` — Read directly; `MessageRef` dataclass, `edit_text` return type (`-> None`)
- `tests/test_whatsapp_adapter.py` — Read directly; patch targets, fixture patterns, existing test classes
- `database.py` — Read directly; `get_wa_last_msg_at`, `update_wa_last_msg_at`, `get_user_lang` confirmed
- `.planning/phases/08-whatsapp-compliance-hardening/08-CONTEXT.md` — Locked decisions confirmed

### Secondary (MEDIUM confidence)
- WhatsApp Business Platform docs (Meta Developer docs): 131026 error code is the documented Graph API error when sending a free-form message outside the 24-hour window. Error behavior aligns with the fallback design in CONTEXT.md.

### Tertiary (LOW confidence)
- None — all findings are code-verified or lock-file-constrained.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies, all primitives confirmed in source
- Architecture: HIGH — `_guard_window` pattern derived directly from existing `_is_window_open` and `send_template` signatures
- Pitfalls: HIGH — `edit_text -> None` return type, `user_key` prefixing, and patch targets all confirmed from source code

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable — Meta Graph API 24h window policy unchanged since 2021; internal code is frozen at read time)
