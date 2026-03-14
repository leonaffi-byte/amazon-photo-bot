# Phase 4: Messaging Platform Expansion - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Israeli users can send product photos via WhatsApp or Instagram DMs and receive the same quality results as Telegram users, with platform-appropriate UX. This phase covers WhatsApp Business API integration, Instagram DM integration, Meta compliance flows, and webhook integration into the existing FastAPI gateway.

</domain>

<decisions>
## Implementation Decisions

### WhatsApp Compliance Flow
- First-message opt-in: user sends any message/photo, bot replies with welcome + terms message with an "I agree" button. Only after tapping does the bot process photos
- Template notification for 24-hour window expiry: pre-approved Meta template message ("Your product results are ready! Tap to view.") re-engages user when window closes before results are ready
- Hebrew + English language support from day one, matching Telegram behavior. Reuse existing translator.py logic
- Full command set matching Telegram (/start, /help, /language, /providers) — consistent cross-platform experience

### Result Formatting
- WhatsApp: List message type for product navigation (up to 10 items in dropdown). User taps "View Products" → sees list → taps one → sees results with Next/Prev/Buy buttons (3-button max)
- Emoji-based text badges for shipping and price: 🟢 Ships free / 🟡 Likely ships / 🔴 Won't ship. Price bar as plain text
- Annotated photos with product overlays sent back on both WhatsApp and Instagram, same as Telegram
- Instagram: Quick replies for product navigation ("Product 1" / "Product 2" / "Next page"). Quick replies disappear after tap — expected Instagram pattern

### Webhook Integration
- FastAPI webhook routes: /webhooks/whatsapp and /webhooks/instagram added to existing FastAPI gateway. Migrate adapter webhook handlers from aiohttp.web to FastAPI Request/Response. Single port, consistent with Phase 1 architecture
- Single Meta App for both WhatsApp Business and Instagram. Shared META_APP_SECRET, single webhook URL with routing by payload type
- Always verify webhook signatures: require META_APP_SECRET and verify X-Hub-Signature-256 on every webhook POST. Reject unsigned requests

### Testing & Rollout
- Meta test phone number for WhatsApp sandbox testing (up to 5 registered test recipients)
- Unit tests with mocked Meta API for webhook parsing, message routing, and response formatting. Fits existing pytest pattern
- WhatsApp launches first, Instagram follows. WhatsApp is the bigger platform in Israel; both share Meta infrastructure so Instagram is fast to follow
- Config toggle only for enable/disable: WHATSAPP_TOKEN present = enabled. Remove token and restart to disable. Matches current adapter pattern

### Meta API Health
- Basic error logging for Meta API calls and webhook errors with structured logging. No dashboard widget for this phase

### Claude's Discretion
- FastAPI route structure and middleware organization for webhook handlers
- WhatsApp list message formatting details (section headers, row descriptions)
- Template message wording and Meta approval strategy
- Error retry logic for Meta Graph API calls
- Instagram quick reply payload format and state management

</decisions>

<specifics>
## Specific Ideas

- WhatsApp and Instagram adapters already exist in `adapters/` directory — coded but untested. Real work is migrating webhooks to FastAPI, adding compliance flows, and testing
- `shared_meta.py` already provides Graph API helpers, webhook signature verification, and media download — reuse and extend
- `BotCore` + adapter wiring already exists in `main.py` — WhatsApp/Instagram activate when tokens are configured
- Config vars already defined: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `INSTAGRAM_TOKEN`, `INSTAGRAM_PAGE_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `adapters/whatsapp.py`: WhatsAppAdapter with send_text, send_photo, webhook handling — needs aiohttp→FastAPI migration
- `adapters/instagram.py`: InstagramAdapter with quick replies, photo upload — needs aiohttp→FastAPI migration
- `adapters/shared_meta.py`: `verify_webhook_signature()`, `send_graph_api()`, `download_media()` — Graph API v21.0 helpers
- `adapters/base.py`: PlatformAdapter ABC with capability flags (max_buttons, supports_photo_edit, etc.)
- `adapters/telegram.py`: Reference implementation showing full adapter lifecycle
- `translator.py`: Language detection and translation — reuse for Hebrew/English on new platforms
- `image_analyzer.py`: ProductInfo + annotation pipeline — platform-agnostic, reuse directly
- `style.py`: Message formatting — may need platform-specific formatters

### Established Patterns
- Adapter callback pattern: `on_photo`, `on_callback`, `on_text`, `on_command` callbacks wired via `_make_callbacks()` in main.py
- `BotCore(adapter)` wraps each adapter with shared bot logic
- Config-gated adapter activation: check token presence → instantiate adapter → append to adapters list
- aiohttp.ClientSession per adapter for outbound HTTP

### Integration Points
- `main.py`: Adapter instantiation and lifecycle management — add FastAPI webhook route registration here
- FastAPI gateway (port 8080): Add `/webhooks/whatsapp` and `/webhooks/instagram` routes
- `config.py`: WhatsApp/Instagram config vars already defined
- `database.py`: User tracking tables work with platform-prefixed user IDs (e.g., "whatsapp:123")

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-messaging-platform-expansion*
*Context gathered: 2026-03-14*
