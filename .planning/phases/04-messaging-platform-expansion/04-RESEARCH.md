# Phase 4: Messaging Platform Expansion - Research

**Researched:** 2026-03-14
**Domain:** Meta WhatsApp Cloud API + Instagram DM API, FastAPI webhook migration, compliance flows
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**WhatsApp Compliance Flow**
- First-message opt-in: user sends any message/photo, bot replies with welcome + terms message with an "I agree" button. Only after tapping does the bot process photos.
- Template notification for 24-hour window expiry: pre-approved Meta template message ("Your product results are ready! Tap to view.") re-engages user when window closes before results are ready.
- Hebrew + English language support from day one, matching Telegram behavior. Reuse existing translator.py logic.
- Full command set matching Telegram (/start, /help, /language, /providers) — consistent cross-platform experience.

**Result Formatting**
- WhatsApp: List message type for product navigation (up to 10 items in dropdown). User taps "View Products" → sees list → taps one → sees results with Next/Prev/Buy buttons (3-button max).
- Emoji-based text badges for shipping and price: 🟢 Ships free / 🟡 Likely ships / 🔴 Won't ship. Price bar as plain text.
- Annotated photos with product overlays sent back on both WhatsApp and Instagram, same as Telegram.
- Instagram: Quick replies for product navigation ("Product 1" / "Product 2" / "Next page"). Quick replies disappear after tap — expected Instagram pattern.

**Webhook Integration**
- FastAPI webhook routes: /webhooks/whatsapp and /webhooks/instagram added to existing FastAPI gateway. Migrate adapter webhook handlers from aiohttp.web to FastAPI Request/Response. Single port, consistent with Phase 1 architecture.
- Single Meta App for both WhatsApp Business and Instagram. Shared META_APP_SECRET, single webhook URL with routing by payload type.
- Always verify webhook signatures: require META_APP_SECRET and verify X-Hub-Signature-256 on every webhook POST. Reject unsigned requests.

**Testing & Rollout**
- Meta test phone number for WhatsApp sandbox testing (up to 5 registered test recipients).
- Unit tests with mocked Meta API for webhook parsing, message routing, and response formatting. Fits existing pytest pattern.
- WhatsApp launches first, Instagram follows. WhatsApp is the bigger platform in Israel; both share Meta infrastructure so Instagram is fast to follow.
- Config toggle only for enable/disable: WHATSAPP_TOKEN present = enabled. Remove token and restart to disable. Matches current adapter pattern.

**Meta API Health**
- Basic error logging for Meta API calls and webhook errors with structured logging. No dashboard widget for this phase.

### Claude's Discretion
- FastAPI route structure and middleware organization for webhook handlers
- WhatsApp list message formatting details (section headers, row descriptions)
- Template message wording and Meta approval strategy
- Error retry logic for Meta Graph API calls
- Instagram quick reply payload format and state management

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WHAT-01 | Bot responds to photo messages on WhatsApp with product results | Adapter already parses `type == "image"` messages; needs opt-in gate and list-message result delivery |
| WHAT-02 | All WhatsApp interactions use structured messages (buttons, list messages) — no free-text AI chat | List message payload format verified; send_list_message method needed on WhatsApp adapter |
| WHAT-03 | WhatsApp message templates approved by Meta for outbound messages | Template approval flow documented; utility template category applicable for re-engagement |
| WHAT-04 | WhatsApp adapter handles 24-hour conversation window correctly | 24-hour window rules verified; session timestamp tracking + template fallback pattern defined |
| WHAT-05 | User opt-in flow before receiving messages (WhatsApp compliance) | Opt-in gate pattern defined; DB migration for consent state needed |
| INST-01 | Bot responds to photo messages in Instagram DMs with product results | Adapter already parses image attachments; needs opt-in gate and quick-reply result delivery |
| INST-02 | Instagram adapter uses Meta Graph API with proper authentication | shared_meta.py already provides Graph API v21.0 helpers; no new auth layer needed |
| INST-03 | Instagram interactions use structured replies where supported | Quick reply payload format verified; existing send_text() with quick_replies already implemented |
</phase_requirements>

---

## Summary

Phase 4 activates WhatsApp and Instagram messaging for the bot. The good news: the adapters are largely already written (`adapters/whatsapp.py` and `adapters/instagram.py` both exist), the gateway infrastructure is in place (`webhook_routes.py` dispatches by platform name), and `shared_meta.py` handles Graph API communication and signature verification. The bot core (`bot_core.py`) and formatter (`formatter.py`) are already platform-aware.

The real work falls into three areas. First, the adapter webhook handlers are written against `aiohttp.web.Request/Response` but the gateway now uses FastAPI `Request/Response` — both adapters need this migration. Second, neither adapter implements the opt-in compliance gate (checking user consent before processing photos) or the 24-hour conversation window tracking (storing last-message timestamps and sending template re-engagements when the window is closed). Third, the WhatsApp adapter is missing a `send_list_message()` method — the existing `send_text()` with buttons sends button-type interactive messages (max 3), but the product list flow requires list-type interactive messages (up to 10 rows).

Meta compliance requirements as of 2026: explicit opt-in (affirmative action with business name stated) is required before any outbound message; the 24-hour customer care window allows free-form responses; outside that window only pre-approved templates work. Utility templates sent inside an open window are free of charge; templates sent outside the window are charged per-message.

**Primary recommendation:** Migrate both adapter webhook handlers to FastAPI, add `send_list_message()` to `WhatsAppAdapter`, implement opt-in state in the DB and gate logic in the adapters, add 24-hour window tracking with template fallback, then write mocked unit tests covering all new behaviors.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Meta Graph API | v21.0 | WhatsApp Cloud API + Instagram DM API | Already in use via `shared_meta.py`; v21.0 active in codebase |
| FastAPI | (existing) | Webhook route handlers | Phase 1 consolidated all HTTP to FastAPI; already mounted in `gateway.py` |
| aiohttp | (existing) | Outbound Graph API calls | `aiohttp.ClientSession` per adapter — already established pattern |
| pytest + pytest-asyncio | (existing) | Unit tests with mocked Meta API | Project standard, `asyncio_mode = auto` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `adapters/shared_meta.py` | — | HMAC-SHA256 signature verification, Graph API POST, media download | Already used by both adapters — extend, don't replace |
| `database.py` | — | User consent/opt-in state persistence, last-message timestamp | Add new columns via `_MIGRATIONS` list |
| `formatter.py` | — | Platform-aware message formatting (WhatsApp/Instagram already handled) | Use `Formatter("whatsapp", lang)` for text rendering |
| `i18n.py` | — | Hebrew + English translations | Already has `he.json` and `en.json` locale files |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `shared_meta.py` direct Graph API | PyWa library | PyWa adds third-party dependency; shared_meta.py already provides everything needed |
| Custom opt-in state in memory | DB-persisted opt-in | Memory loses state on restart; DB is correct for compliance audit trail |

**No new packages required.** Phase 4 is purely wiring existing infrastructure.

## Architecture Patterns

### Existing Structure (what's already there)
```
adapters/
├── base.py              # PlatformAdapter ABC, Button, MessageRef
├── shared_meta.py       # Graph API v21.0 helpers (verify_sig, send, download)
├── whatsapp.py          # WhatsAppAdapter — needs aiohttp→FastAPI + list_msg + compliance
├── instagram.py         # InstagramAdapter — needs aiohttp→FastAPI + compliance gate
└── telegram.py          # Reference implementation (no webhook handlers, uses polling)

webhook_routes.py        # FastAPI router: POST/GET /webhook/{platform} → adapter dispatch
gateway.py               # Mounts webhook_routes when adapters have handle_webhook()
database.py              # users table needs wa_opted_in + wa_last_msg_at columns
```

### Pattern 1: FastAPI Webhook Handler Migration

**What:** Both adapters use `aiohttp.web.Request` and return `aiohttp.web.Response`. The gateway uses FastAPI `Request` and expects `Response`. The `webhook_routes.py` calls `adapter.handle_webhook(request)` passing a FastAPI request.

**When to use:** Every adapter with `handle_webhook` and `handle_webhook_verify`.

**Migration pattern:**
```python
# BEFORE (aiohttp — what exists now)
from aiohttp import web

async def handle_webhook(self, request: web.Request) -> web.Response:
    payload = await request.read()
    signature = request.headers.get("X-Hub-Signature-256", "")
    ...
    return web.Response(text="OK", status=200)

# AFTER (FastAPI — what it needs to be)
from fastapi import Request
from fastapi.responses import PlainTextResponse

async def handle_webhook(self, request: Request) -> PlainTextResponse:
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    ...
    return PlainTextResponse("OK", status_code=200)
```

**Key differences:**
- `request.read()` → `request.body()` (awaitable in both, same semantics)
- `request.json()` → `await request.json()` (FastAPI) — already awaited in both adapters
- `request.query` → `request.query_params` (for GET verify handler)
- `web.Response(text=..., status=...)` → `PlainTextResponse(..., status_code=...)`

### Pattern 2: WhatsApp Opt-In Gate

**What:** On first contact, bot checks DB for `wa_opted_in` flag. If not set, bot sends a welcome+terms interactive button message. Only on "I agree" button reply does the bot set the flag and resume normal processing.

**When to use:** Every `_process_message()` invocation in `WhatsAppAdapter` before dispatching to `_on_photo`, `_on_text`, or `_on_command`.

**Flow:**
```
Incoming message
    ↓
get_opt_in_status(user_id)  [DB lookup]
    ├── NOT OPTED IN → send_opt_in_prompt(user_id) → return (drop message)
    └── OPTED IN
            ↓
        route to _on_photo / _on_text / _on_command
```

**Opt-in prompt payload:**
```python
# Sent when user contacts bot for first time
data = {
    "messaging_product": "whatsapp",
    "to": user_id,
    "type": "interactive",
    "interactive": {
        "type": "button",
        "body": {
            "text": "Welcome to Amazon Photo Bot! 🛍️\n\nSend a photo of any product to find it on Amazon with Israel shipping info.\n\nBy tapping 'I agree' you confirm you wish to receive product search results from us."
        },
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": "optin:agree", "title": "I agree ✓"}
                }
            ]
        }
    }
}
```

**DB schema addition (migration):**
```sql
ALTER TABLE users ADD COLUMN wa_opted_in INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN wa_last_msg_at REAL;  -- Unix timestamp
```

### Pattern 3: WhatsApp 24-Hour Window Tracking

**What:** On every incoming message, record `wa_last_msg_at = time.time()` for the user. When sending a result, check if now - wa_last_msg_at < 86400. If outside window, send a pre-approved template instead of the result.

**When to use:** In `WhatsAppAdapter` around result delivery (called from BotCore).

**Template message payload (for re-engagement):**
```python
# Source: Meta Graph API docs — template send format
data = {
    "messaging_product": "whatsapp",
    "to": chat_id,
    "type": "template",
    "template": {
        "name": "product_results_ready",   # Pre-approved template name
        "language": {"code": "en"},
        "components": []                    # No variables for simple notification
    }
}
```

**Window check helper:**
```python
import time

async def _is_window_open(self, user_id: str) -> bool:
    """Return True if the 24-hour customer care window is still open."""
    ts = await db.get_wa_last_msg_at(user_id)
    if ts is None:
        return False
    return (time.time() - ts) < 86400
```

### Pattern 4: WhatsApp List Message (New Method)

**What:** `send_list_message()` needed on `WhatsAppAdapter` — not in base class (WhatsApp-specific). Called by BotCore when delivering product results list.

**Payload format (verified from Meta docs):**
```python
# Source: developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages
async def send_list_message(
    self,
    chat_id: str,
    body: str,
    button_label: str,
    sections: list[dict],   # [{"title": str, "rows": [{"id": str, "title": str, "description": str}]}]
) -> MessageRef:
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": chat_id,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_label,   # max 20 chars
                "sections": sections      # max 10 sections, max 10 rows total
            }
        }
    }
    resp = await send_graph_api(
        f"{self._phone_number_id}/messages",
        self._token, data, self._session
    )
    msg_id = resp.get("messages", [{}])[0].get("id", "")
    return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)
```

### Pattern 5: Instagram Opt-In Gate

**What:** Same concept as WhatsApp but Instagram. Instagram doesn't have the same template/window rules as WhatsApp (it uses the Messenger Platform model — 24h standard messaging window but no explicit template requirement). Still implement a consent gate for consistency and policy compliance.

**Key difference from WhatsApp:** Instagram quick replies can serve as the consent confirmation button. No template approval needed for Instagram re-engagement since it uses standard messaging rules.

### Anti-Patterns to Avoid

- **Processing photos before checking opt-in:** Always check the DB flag before dispatching to `_on_photo`. Sending product results to a user who hasn't opted in violates Meta policy.
- **Returning `web.Response` from FastAPI route:** The gateway passes a FastAPI `Request` object; returning `aiohttp.web.Response` will cause a runtime error. The adapter must use `PlainTextResponse` or `Response`.
- **Ignoring Graph API error responses:** `send_graph_api()` returns the JSON body even on 4xx. Check for `error` key in response and log structured error with `endpoint`, `status`, and `error.message`.
- **Sending template outside 24h window with wrong category:** Use `utility` template category for re-engagement notifications. `marketing` templates have more restrictions and higher cost.
- **Using list messages with more than 10 total rows:** Meta enforces a hard limit of 10 rows across all sections in a list message. Paginate if more results exist.
- **Assuming `request.query` works in FastAPI:** Use `request.query_params.get("hub.mode")` not `request.query.get(...)`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HMAC-SHA256 webhook signature verification | Custom crypto | `shared_meta.verify_webhook_signature()` | Already correct, constant-time compare via `hmac.compare_digest` |
| Graph API HTTP calls | Custom aiohttp session management | `shared_meta.send_graph_api()` | Already handles auth headers, error logging |
| Media download from Meta CDN | Direct URL fetch without auth | `shared_meta.download_media()` | Meta requires Bearer token on CDN requests; two-step flow already handled |
| User opt-in state storage | In-memory dict | `database.py` (SQLite, async) | Persists across restarts; existing DB connection pool |
| Platform-aware text formatting | Custom string builders | `formatter.Formatter(platform, lang)` | Already handles WhatsApp/Instagram caption limits and character sets |

**Key insight:** The project already has the full Meta API client in `shared_meta.py`. No new HTTP client code is needed — only new payload shapes for list messages and templates.

## Common Pitfalls

### Pitfall 1: aiohttp.web types in FastAPI route
**What goes wrong:** `handle_webhook(request: web.Request)` raises `AttributeError: 'Request' object has no attribute 'read'` (aiohttp) or the response type is not recognized by FastAPI.
**Why it happens:** The adapters were written before the FastAPI migration was done. `web.Request.read()` is synchronous in aiohttp; FastAPI's `Request.body()` is a coroutine.
**How to avoid:** Systematically replace all `web.Request` → `fastapi.Request`, `request.read()` → `await request.body()`, `request.query` → `request.query_params`, `web.Response(text=..., status=...)` → `PlainTextResponse(..., status_code=...)`.
**Warning signs:** Import of `from aiohttp import web` anywhere in an adapter that has `handle_webhook`.

### Pitfall 2: Bot sends messages to users who haven't opted in
**What goes wrong:** Meta can suspend or warn the Business Account if the bot sends unsolicited messages (photo results) before the user explicitly agrees.
**Why it happens:** No opt-in gate in `_process_message()` — the adapter dispatches to `_on_photo` immediately.
**How to avoid:** Check `wa_opted_in` flag (DB) at the top of `_process_message()`. If not set, send opt-in prompt and `return` without dispatching.
**Warning signs:** `_process_message` calls `self._on_photo` without a preceding DB check.

### Pitfall 3: Sending free-form messages outside the 24-hour window
**What goes wrong:** Graph API returns `{"error": {"code": 131047, "message": "Message failed to send because more than 24 hours have passed..."}}`.
**Why it happens:** User asked for results, analysis took time or was queued, window expired.
**How to avoid:** Before sending results, check `time.time() - wa_last_msg_at`. If >= 86400 seconds, send the pre-approved utility template instead of the free-form result message.
**Warning signs:** Graph API error code 131047 in logs.

### Pitfall 4: List message rows exceed 10
**What goes wrong:** Graph API rejects the payload with a validation error.
**Why it happens:** Naive mapping of all search results into list rows without capping.
**How to avoid:** Cap at `min(len(results), 10)` when building list sections. First page shows rows 1-10 with a "More results" row as the 10th if pagination is needed.
**Warning signs:** Any code that creates `sections` without a `[:10]` cap on rows.

### Pitfall 5: Template not approved before bot goes live
**What goes wrong:** The template send succeeds via API but Meta rejects delivery; error code 132001 "Template does not exist in the translation and has not been approved."
**Why it happens:** Templates must be submitted and approved before use — approval can take up to 24 hours.
**How to avoid:** Submit the re-engagement utility template ("Your product results are ready! Tap here to view.") in both English (`en`) and Hebrew (`he`) during setup, not at code execution time. Add a Wave 0 task for template pre-registration.
**Warning signs:** Template send attempted without confirming Meta approval status.

### Pitfall 6: Instagram photo download without auth header
**What goes wrong:** `403 Forbidden` when downloading the image from the CDN URL provided in the webhook payload.
**Why it happens:** Instagram CDN URLs for DM attachments require the page access token as a Bearer header.
**How to avoid:** The existing `download_photo()` in `InstagramAdapter` already does this correctly (`headers = {"Authorization": f"Bearer {self._token}"}`). Don't change this to a plain GET.
**Warning signs:** CDN download returning non-200 status.

### Pitfall 7: Callback data "optin:agree" not intercepted before BotCore routing
**What goes wrong:** The opt-in "I agree" button callback goes to `BotCore.handle_callback()` which doesn't know about opt-in state, resulting in a confusing "no session" error or silent drop.
**Why it happens:** The adapter hands off all callbacks to `_on_callback` without inspecting callback IDs first.
**How to avoid:** In `_process_message()`, check if the callback ID starts with `optin:` before calling `_on_callback`. Handle opt-in callbacks within the adapter's `_process_message()` method directly (set DB flag, send confirmation, return).
**Warning signs:** `optin:agree` appearing in BotCore callback logs.

## Code Examples

### FastAPI Webhook Handler Migration Pattern
```python
# Source: FastAPI docs + webhook_routes.py pattern in this project
from fastapi import Request
from fastapi.responses import PlainTextResponse

async def handle_webhook_verify(self, request: Request) -> PlainTextResponse:
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == self._verify_token:
        logger.info("WhatsApp webhook verified.")
        return PlainTextResponse(challenge or "")
    logger.warning("WhatsApp webhook verification failed (bad token).")
    return PlainTextResponse("Forbidden", status_code=403)

async def handle_webhook(self, request: Request) -> PlainTextResponse:
    payload = await request.body()           # was: await request.read()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if self._app_secret and not verify_webhook_signature(payload, signature, self._app_secret):
        logger.warning("WhatsApp webhook: invalid signature.")
        return PlainTextResponse("Invalid signature", status_code=403)

    body = await request.json()              # same as before
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    await self._process_message(msg, value)
    except Exception:
        logger.exception("Error processing WhatsApp webhook")

    return PlainTextResponse("OK")           # was: web.Response(text="OK", status=200)
```

### WhatsApp List Message for Product Results
```python
# Source: Meta Graph API Interactive List Messages documentation
# developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages
async def send_list_message(
    self,
    chat_id: str,
    body: str,
    button_label: str,
    sections: list[dict],
) -> MessageRef:
    """Send a WhatsApp list-type interactive message (up to 10 rows total)."""
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": chat_id,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {
                "button": button_label[:20],
                "sections": sections,
            },
        },
    }
    assert self._session is not None
    resp = await send_graph_api(
        f"{self._phone_number_id}/messages",
        self._token, data, self._session
    )
    msg_id = ""
    if "messages" in resp:
        msg_id = resp["messages"][0].get("id", "")
    return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)
```

### WhatsApp Template Re-engagement Send
```python
# Source: Meta Graph API template message documentation
async def send_template(
    self,
    chat_id: str,
    template_name: str,
    lang_code: str = "en",
) -> MessageRef:
    """Send a pre-approved Meta template message (for use outside 24h window)."""
    data = {
        "messaging_product": "whatsapp",
        "to": chat_id,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code},
        },
    }
    assert self._session is not None
    resp = await send_graph_api(
        f"{self._phone_number_id}/messages",
        self._token, data, self._session
    )
    msg_id = ""
    if "messages" in resp:
        msg_id = resp["messages"][0].get("id", "")
    return MessageRef(platform="whatsapp", chat_id=chat_id, message_id=msg_id, raw=resp)
```

### DB Helper for Opt-In State
```python
# Pattern: follows existing set_user_lang() style in database.py
async def get_wa_opt_in(user_key: str) -> bool:
    """Return True if user has opted in on WhatsApp."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT wa_opted_in FROM users WHERE user_key = ?", (user_key,)
        ) as cursor:
            row = await cursor.fetchone()
    return bool(row[0]) if row else False


async def set_wa_opt_in(user_key: str, opted_in: bool) -> None:
    """Record WhatsApp opt-in consent."""
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO users (user_key, wa_opted_in)
               VALUES (?, ?)
               ON CONFLICT(user_key) DO UPDATE SET wa_opted_in = ?""",
            (user_key, int(opted_in), int(opted_in)),
        )
        await db.commit()


async def update_wa_last_msg_at(user_key: str, ts: float) -> None:
    """Update the timestamp of the last user-initiated WhatsApp message."""
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO users (user_key, wa_last_msg_at)
               VALUES (?, ?)
               ON CONFLICT(user_key) DO UPDATE SET wa_last_msg_at = ?""",
            (user_key, ts, ts),
        )
        await db.commit()
```

### DB Migration Entries
```python
# Add to _MIGRATIONS list in database.py
"ALTER TABLE users ADD COLUMN wa_opted_in INTEGER NOT NULL DEFAULT 0",
"ALTER TABLE users ADD COLUMN wa_last_msg_at REAL",
```

### Mocked Webhook Test Pattern (WhatsApp)
```python
# Follows test_gateway.py pattern — uses Starlette TestClient + AsyncMock
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from gateway import create_app

def make_whatsapp_adapter():
    from adapters.whatsapp import WhatsAppAdapter
    return WhatsAppAdapter(
        on_photo=AsyncMock(),
        on_callback=AsyncMock(),
        on_text=AsyncMock(),
        on_command=AsyncMock(),
    )

def test_whatsapp_webhook_valid_signature():
    import hmac, hashlib
    adapter = make_whatsapp_adapter()
    adapter._app_secret = "test_secret"
    body = b'{"entry": []}'
    sig = "sha256=" + hmac.new(b"test_secret", body, hashlib.sha256).hexdigest()
    with patch("database.init_db", new=AsyncMock()):
        app = create_app(webhook_adapters=[adapter])
        client = TestClient(app)
        resp = client.post(
            "/webhook/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| aiohttp webhook server (webhook_server.py) | FastAPI gateway (gateway.py) | Phase 1 | Both adapters still use aiohttp.web types — migration needed |
| Conversation-based pricing (per 24h session) | Per-message pricing | July 2025 | Templates sent outside open window are charged per delivery; utility templates inside window are FREE |
| WhatsApp On-Premises API | Cloud API only | October 2025 (sunset) | No impact — codebase already uses Cloud API via graph.facebook.com |
| Graph API v20 and below | v21.0 | Late 2024 | `shared_meta.py` already uses v21.0 (`GRAPH_API_BASE = ".../v21.0"`) |

**Deprecated/outdated:**
- `webhook_server.py`: The old aiohttp webhook server exists as a file but is no longer used — gateway.py replaced it in Phase 1. The adapters still import from `aiohttp.web` — that import is what needs removing.
- WhatsApp On-Premises API: Fully sunsetted October 23, 2025. No action needed — project always used Cloud API.

## Open Questions

1. **Meta template pre-approval timing**
   - What we know: Template approval can take up to 24 hours; utility templates approved faster; English + Hebrew versions both needed.
   - What's unclear: Exact template name to register with Meta ("product_results_ready" or similar). Category must be `utility` not `marketing` for re-engagement.
   - Recommendation: Submit template with body "Your Amazon product search results are ready. Reply here to view them." in Wave 0 as a setup step. Treat template name as a config variable (`WHATSAPP_REENGAGEMENT_TEMPLATE_NAME`, default `product_results_ready`).

2. **Instagram opt-in scope**
   - What we know: Instagram uses Messenger Platform rules (24h standard messaging window), not the stricter WhatsApp template requirement.
   - What's unclear: Whether Meta requires an explicit opt-in button for Instagram DMs or just clear labeling in terms of service.
   - Recommendation: Implement the same opt-in gate as WhatsApp for consistency and future-proofing. The consent message is simpler (no template needed for Instagram re-engagement).

3. **WhatsApp sandbox test phone number constraints**
   - What we know: Meta provides a test phone number usable without full business verification, limited to 5 registered test recipients.
   - What's unclear: Whether opt-in compliance flows are enforced in the sandbox environment.
   - Recommendation: Implement and test compliance flows in production config. Don't assume sandbox bypasses the opt-in check.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `pytest.ini` (`asyncio_mode = auto`, `testpaths = tests`) |
| Quick run command | `pytest tests/test_whatsapp_adapter.py tests/test_instagram_adapter.py -x` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WHAT-01 | Photo message triggers opt-in check, then processing | unit | `pytest tests/test_whatsapp_adapter.py::TestPhotoHandling -x` | ❌ Wave 0 |
| WHAT-02 | List message payload sent for product results | unit | `pytest tests/test_whatsapp_adapter.py::TestListMessage -x` | ❌ Wave 0 |
| WHAT-03 | Template message sent with correct payload shape | unit | `pytest tests/test_whatsapp_adapter.py::TestTemplateSend -x` | ❌ Wave 0 |
| WHAT-04 | 24h window open → free-form; closed → template fallback | unit | `pytest tests/test_whatsapp_adapter.py::TestWindowTracking -x` | ❌ Wave 0 |
| WHAT-05 | First contact sends opt-in prompt; "agree" sets DB flag | unit | `pytest tests/test_whatsapp_adapter.py::TestOptIn -x` | ❌ Wave 0 |
| INST-01 | Image attachment webhook triggers photo processing | unit | `pytest tests/test_instagram_adapter.py::TestPhotoHandling -x` | ❌ Wave 0 |
| INST-02 | Graph API called with correct auth header | unit | `pytest tests/test_instagram_adapter.py::TestGraphApiAuth -x` | ❌ Wave 0 |
| INST-03 | Quick replies sent for product navigation | unit | `pytest tests/test_instagram_adapter.py::TestQuickReplies -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_whatsapp_adapter.py tests/test_instagram_adapter.py -x`
- **Per wave merge:** `pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_whatsapp_adapter.py` — covers WHAT-01 through WHAT-05
- [ ] `tests/test_instagram_adapter.py` — covers INST-01, INST-02, INST-03
- [ ] DB migration verification in `tests/test_database.py` (add opt-in column tests)
- [ ] No new framework install needed — pytest + pytest-asyncio already in use

## Sources

### Primary (HIGH confidence)
- `adapters/whatsapp.py`, `adapters/instagram.py`, `adapters/shared_meta.py` — Read directly; codebase as ground truth for what exists and what needs changing
- `webhook_routes.py`, `gateway.py` — FastAPI route dispatch pattern; confirmed `platform_name` routing
- `database.py` — `_MIGRATIONS` pattern; `users` table schema; upsert pattern for new columns
- Meta Graph API docs (Interactive List Messages) — verified list message payload structure via web search, cross-referenced with Cognigy.AI docs showing same format

### Secondary (MEDIUM confidence)
- [WhatsApp API Compliance 2026 Guide](https://gmcsco.com/your-simple-guide-to-whatsapp-api-compliance-2026/) — Opt-in requirements, 24h window rules, template requirements verified here
- [Meta's Opt-In Documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in) — Confirmed opt-in must include affirmative action + business name + WhatsApp-specific statement
- [WhatsApp 24-Hour Window and Templates](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/) — 24h free-form window, template requirement outside window
- [Meta July 2025 Pricing Change](https://wetarseel.ai/whatsapp-api-pricing-all-you-need-to-know-in-2026/) — Per-message pricing since July 2025; utility templates inside open window are free

### Tertiary (LOW confidence)
- Meta template approval timing "up to 24 hours" — multiple secondary sources agree; not verified against current official Meta dashboard flow

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — No new packages; all libraries in active use in this codebase
- Architecture patterns: HIGH — Based on direct code reading of whatsapp.py, instagram.py, gateway.py, webhook_routes.py
- FastAPI migration pattern: HIGH — webhook_routes.py already passes FastAPI Request; aiohttp import confirmed in both adapters
- WhatsApp compliance rules: MEDIUM — Cross-verified across 3 sources; Meta docs inaccessible directly (CSS-only page content returned)
- List message payload: MEDIUM — Multiple sources agree on format; Meta official link found but returned CSS only
- Pitfalls: HIGH — Derived from direct code analysis (aiohttp imports, missing opt-in gate, no list_message method)

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (Meta Graph API v21.0 stable; compliance rules stable; 30 days)
