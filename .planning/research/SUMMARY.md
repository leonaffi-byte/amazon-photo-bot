# Project Research Summary

**Project:** Amazon Photo Bot - Multi-Platform Expansion
**Domain:** AI-powered visual product search bot with affiliate monetization
**Researched:** 2026-03-13
**Confidence:** MEDIUM-HIGH

## Executive Summary

The Amazon Photo Bot is a functioning Telegram bot that identifies products from photos using AI vision providers and finds matching items on Amazon with Israel shipping verification. The expansion goal is to extend this working core to WhatsApp (Israel's dominant messaging platform), a public web application, photo annotation overlays, price history charts, and a proper web-based admin dashboard. All research converges on one strong recommendation: the existing core has 68 documented issues including 8 critical bugs that must be fixed before any new platform is added, or every bug will multiply across every surface simultaneously.

The recommended approach for all new surfaces is additive rather than parallel: treat the web application and WhatsApp adapter as additional `PlatformAdapter` implementations that route through the existing `BotCore` business logic rather than duplicating it. On the frontend side, FastAPI+HTMX+Jinja2 delivers full interactivity for both the web app and admin dashboard with zero JavaScript build tooling — the right tradeoff for a Python team building interfaces used by at most a few hundred concurrent users. Matplotlib handles price history charts natively (no subprocess overhead), and Pillow handles photo annotation (already a dependency).

The primary risks are: (1) shipping features before stability fixes, which causes cross-platform failures that are extremely difficult to diagnose; (2) SQLite collapsing under concurrent writes from multiple adapters — PostgreSQL migration is required before the second adapter goes live; (3) WhatsApp's 24-hour messaging window and 3-button-maximum constraints breaking the Telegram-designed UX if not treated as a separate UX design problem; and (4) Pillow image annotation blocking the asyncio event loop if not wrapped in `run_in_executor()`.

## Key Findings

### Recommended Stack

The project already has the right foundation. The expansion requires only three new pip dependencies: `python-multipart` (FastAPI file uploads), `matplotlib` (price history chart PNGs), and a Pillow version upgrade to `>=12.1.0`. All web interactivity comes from CDN-loaded HTMX, Alpine.js, and Tailwind CSS — no Node.js toolchain. The existing FastAPI server, aiohttp, and Pillow cover the rest.

**Core technologies:**
- FastAPI (existing): Unified HTTP gateway for web app, admin dashboard, webhooks, and API — extend, don't add another framework
- HTMX 2.0.8 (CDN): Dynamic UI without JavaScript framework — 14KB, zero build step, FastAPI+HTMX is the dominant Python admin pattern in 2025-2026
- Jinja2 (ships with FastAPI): Server-side HTML templating for both admin and public surfaces
- Pillow >=12.1.0 (upgrade existing): Photo annotation overlays via RGBA compositing — no new dependency
- matplotlib >=3.10.0 (new): Static price history chart PNG generation into BytesIO — avoids Plotly/Kaleido's Chromium subprocess overhead
- aiohttp (existing): WhatsApp and Instagram adapters already exist using Meta Graph API — no new library needed

### Expected Features

**Must have (table stakes — P0 stability first):**
- Timeout enforcement on all vision provider API calls — prevents hung requests from blocking the bot
- Model health tracking fix with time-window decay — prevents permanent disablement after transient failures
- DB transaction safety for multi-step operations — prevents data corruption on crash
- Photo size validation before vision API call — prevents quota exhaustion from oversized images
- Cache invalidation for settings/tags/models — ensures admin changes take effect immediately
- Specific error messages with backend/provider attribution — user trust and debuggability

**Should have (differentiators — P1):**
- Photo annotation with semi-transparent bounding box overlays — visual confirmation of detection
- Price history chart image in result cards — "is this a good deal?" is the #1 post-search question
- Israel shipping confidence badge (green/yellow/red) — the unique wedge for the Israeli market
- WhatsApp Business API integration — WhatsApp has ~95% penetration in Israel vs. Telegram's tech-savvy subset
- Web application with photo upload — SEO-driven discovery, shareable result pages

**Defer (v2+):**
- Price drop alerts / watchlist — requires scheduled jobs and ongoing API costs; CamelCamelCamel already does this
- "Shop the Look" complementary suggestions — nice differentiator but not core value
- Multi-product comparison view — useful but adds UX complexity
- Instagram DM integration — lower priority than WhatsApp, same Meta API compliance requirements
- Discord / LINE / Viber — Israeli market doesn't use these at scale

**Anti-features (deliberately excluded):**
- Real-time price monitoring for all tracked products — API costs scale linearly, CamelCamelCamel does it better
- Open-ended AI chatbot conversation — Meta banned it on WhatsApp Business API (Jan 2026); costs unpredictable on Telegram
- Multi-marketplace search — Amazon Associates TOS conflicts with showing competitor prices alongside affiliate links

### Architecture Approach

The architecture treats all new surfaces — web frontend, admin dashboard, WhatsApp — as additional `PlatformAdapter` implementations over an unchanged `BotCore`. The web frontend is not a separate React SPA; it is a `WebAdapter` that maps HTTP/SSE to the same bot interface. This avoids duplicating the vision-search-format pipeline. Three currently separate HTTP servers (aiohttp shortener on port 8080, aiohttp webhook server on port 8081, FastAPI API on port 8001) must be consolidated into a single FastAPI application with routers before any new web surface is added — this is prerequisite infrastructure with no user-visible change.

**Major components:**
1. **Unified FastAPI Gateway** — single HTTP entry point with routers for API, web app, admin, webhooks, and shortener; replaces three separate servers
2. **WebAdapter** — PlatformAdapter implementation that maps HTTP POST + SSE streams to BotCore; progress updates via SSE (not WebSockets — simpler, auto-reconnecting, HTMX-native)
3. **PhotoAnnotator** — Pillow RGBA overlay drawing, always wrapped in `run_in_executor()`; requires per-provider bounding box coordinate normalization
4. **Admin Dashboard** — HTMX+Jinja2 pages at `/admin/*`; business logic extracted to `admin_service.py` shared with Telegram admin handlers
5. **WhatsApp Adapter** — existing adapter code tested and hardened; WhatsApp-specific UX (numbered lists, 3-button limit, 24h window handling) designed independently

### Critical Pitfalls

1. **Building multi-platform on an unstable core** — 68 known issues (8 critical) multiply across every platform. Fix timeout enforcement, DB transactions, graceful shutdown, and cache invalidation before any new adapter ships. Gate: no new adapter until `pytest` passes with concurrent adapter simulation.

2. **SQLite collapse under concurrent writes** — WAL mode doesn't solve write contention. WhatsApp webhook bursts can send 1,500+ status callbacks/second. Migrate to PostgreSQL (asyncpg) before launching the second platform adapter. If deferred: implement a write-serialization queue as a minimum.

3. **WhatsApp 24-hour messaging window** — The bot assumes it can message users at any time. After a 24-hour window closes, only pre-approved templates are allowed. Results can be ready but undeliverable. Design a `can_send_freeform()` check and a pending-results delivery queue into the WhatsApp adapter from day one.

4. **Pillow annotation blocking the asyncio event loop** — CPU-bound PIL operations on a 4000x3000 phone photo take 500ms-2s. Without `run_in_executor()`, all concurrent users freeze. Always offload to the thread executor; resize images to max 1280px before annotation.

5. **Vision provider bounding box format inconsistency** — GPT-4o returns `[x_min, y_min, x_max, y_max]` pixel coordinates; Gemini returns normalized coordinates in `[y_min, x_min, y_max, x_max]` (swapped axis order); Claude returns text descriptions with no coordinates. Add a provider-specific coordinate normalization layer and extend `ProductInfo` with an optional `bbox` field before investing in overlay rendering.

## Implications for Roadmap

Based on combined research, a 5-phase structure is recommended with clear dependency gates between phases.

### Phase 1: Stability and Foundation
**Rationale:** All research converges here. FEATURES.md identifies stability as P0 before any new feature. PITFALLS.md identifies 8 critical bugs that compound across platforms. ARCHITECTURE.md identifies the multi-server consolidation as prerequisite infrastructure. Without this phase, every subsequent phase inherits fragility.
**Delivers:** A reliable, production-quality single-platform bot; unified FastAPI gateway replacing three separate servers; all 8 critical bugs fixed
**Addresses:** Timeout enforcement, health tracking decay, DB transaction safety, cache invalidation, photo size validation, error message specificity (FEATURES.md P0 list)
**Avoids:** Pitfall 1 (building on unstable core), Pitfall 11 (memory leaks from long-running process), start of Pitfall 3 (write queue as SQLite interim measure)
**Research flag:** Standard patterns — well-documented bug fixes and FastAPI router consolidation. Skip phase research.

### Phase 2: Enhanced Visual Experience
**Rationale:** Photo annotation and price history charts add value to existing Telegram users immediately, require no new external dependencies or API approvals, and de-risk the annotation approach (bounding box accuracy must be validated) before committing to web UI investment.
**Delivers:** Semi-transparent product overlays on detected items; price history chart images in result cards; progress streaming indicators
**Uses:** Pillow >=12.1.0 RGBA compositing; matplotlib PNG generation; both wrapped in run_in_executor()
**Implements:** PhotoAnnotator component; per-provider bounding box normalization; price chart generation in price_history.py
**Avoids:** Pitfall 4 (Pillow blocking event loop), Pitfall 6 (bbox format inconsistency), Pitfall 7 (CamelCamelCamel scraping rate limits — cache aggressively, consider Keepa API)
**Research flag:** Photo annotation needs per-provider bounding box accuracy testing. Run /gsd:research-phase if bbox validation shows <80% accuracy across providers.

### Phase 3: Web Admin Dashboard
**Rationale:** A web admin dashboard is simpler than the web public app (no user identity problem, no photo upload pipeline) and provides immediate operational value. Building it first proves out the HTMX+Jinja2+FastAPI stack and the admin service layer extraction before the more complex public web app.
**Delivers:** Browser-based admin at /admin/*; replaces Telegram-only admin commands; admin_service.py service layer shared with existing Telegram admin handlers
**Uses:** HTMX 2.0.8 (CDN), Jinja2, Alpine.js, Tailwind CDN; itsdangerous session signing (already a Starlette dep)
**Implements:** Admin Dashboard component; admin_service.py extraction from admin.py
**Avoids:** Pitfall 9 (admin logic duplication), Pitfall 10 (webhook security — add auth to admin routes)
**Research flag:** Standard patterns — FastAPI+HTMX admin dashboards are well-documented. Skip phase research.

### Phase 4: WhatsApp Integration
**Rationale:** WhatsApp is the highest-ROI platform expansion for the Israeli market (95% penetration vs. Telegram's tech-savvy subset). However, it requires Meta Business API approval, message template pre-approval (1-7 day lead time), and WhatsApp-specific UX design. Placing it after stability and admin ensures the core pipeline is solid before adding cross-platform complexity.
**Delivers:** WhatsApp photo-to-results flow; structured (button/list) product navigation; delivery queue for 24-hour window handling; PostgreSQL migration (required before launch)
**Uses:** aiohttp (existing Meta Graph API calls via shared_meta.py); webhook server merged into FastAPI gateway
**Implements:** WhatsApp adapter hardening and testing; can_send_freeform() check; unified user identity schema (optional telegram_id, whatsapp_id columns)
**Avoids:** Pitfall 2 (24-hour window), Pitfall 3 (SQLite write contention — PostgreSQL required), Pitfall 8 (WhatsApp 3-button limit), Pitfall 10 (webhook security), Pitfall 12 (affiliate disclosure on WhatsApp), Pitfall 13 (photo format normalization in base adapter)
**Research flag:** WhatsApp Business API compliance requirements change frequently. Run /gsd:research-phase for: current Meta policy on AI bots, template message approval process, and per-message pricing tiers.

### Phase 5: Public Web Application
**Rationale:** The web app is the most complex new surface — it needs user identity design, photo upload pipeline, SSE-based progress streaming, shareable result pages, and SEO optimization. Placing it last means the stable pipeline, proven HTMX stack (from admin dashboard), and multi-platform WebAdapter pattern are all established.
**Delivers:** Public photo upload at /app/*; shareable result URLs for SEO; WebAdapter implementing PlatformAdapter for browser sessions; price history charts and annotation embedded in web results
**Uses:** python-multipart (FastAPI file uploads); Dropzone.js 6 (CDN drag-and-drop upload); HTMX SSE for progress streaming; itsdangerous for anonymous session tokens
**Implements:** WebAdapter component; SSE manager; web session management; i18n string audit for web interface
**Avoids:** Pitfall 5 (session/auth model mismatch — unified user identity designed in Phase 4), Pitfall 15 (hardcoded strings — audit before building web)
**Research flag:** SSE + HTMX production patterns for FastAPI are relatively new. Run /gsd:research-phase for SSE connection handling under load and proxy configuration for SSE.

### Phase Ordering Rationale

- Stability (Phase 1) is a hard prerequisite: every pitfall that involves multi-platform failures traces back to existing instability
- Visual enhancements (Phase 2) come before new platforms because they validate bounding box data quality and create immediate user value on the existing platform with zero new infrastructure risk
- Admin dashboard (Phase 3) before public web because: simpler auth (no public users), proves the HTMX stack, and extracts the admin service layer that reduces technical debt
- WhatsApp (Phase 4) before web because: higher market impact for the Israeli audience, but gated on Meta API approval so starting the approval process early pays off
- Web app (Phase 5) last because: heaviest dependency chain, benefits from all prior work being stable and proven

### Research Flags

Phases requiring deeper research during planning:
- **Phase 4 (WhatsApp):** Meta's API policies and WhatsApp bot compliance rules are updated frequently; template approval process and per-message costs need current verification before committing to implementation approach
- **Phase 5 (Web App):** SSE under load with HTMX in production FastAPI is a newer pattern; proxy/nginx configuration for SSE connections (buffering must be disabled) needs research before architecture is finalized

Phases with standard patterns (skip research-phase):
- **Phase 1 (Stability):** Bug fixes and FastAPI router consolidation follow well-established Python async patterns
- **Phase 2 (Visual):** Pillow RGBA compositing and matplotlib chart generation are thoroughly documented
- **Phase 3 (Admin Dashboard):** FastAPI+HTMX admin dashboards have extensive documentation and examples in 2025-2026

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Minimal new dependencies; all recommendations extend existing libraries already in the project |
| Features | MEDIUM-HIGH | Feature priorities are grounded in the 68 documented issues and competitor analysis; WhatsApp feature constraints verified against Meta Jan 2026 policy |
| Architecture | HIGH | WebAdapter pattern matches existing PlatformAdapter abstraction exactly; HTMX+FastAPI patterns well-documented |
| Pitfalls | HIGH | Most pitfalls are derived from the existing CONCERNS.md (internal source) combined with platform-specific API documentation; SQLite limitations and WhatsApp constraints are well-documented externally |

**Overall confidence:** HIGH

### Gaps to Address

- **Bounding box accuracy per provider:** The pitch that providers return reliable bounding box coordinates has not been empirically validated on real product photos. This is the key uncertainty for Phase 2. Plan a 20-30 image test set early in Phase 2 to validate accuracy before committing to overlay implementation.
- **Keepa API vs. CamelCamelCamel scraping decision:** Pitfall 7 recommends Keepa API ($19/mo) over scraping CamelCamelCamel. This cost decision and the API's rate limit details need a go/no-go before Phase 2 price history work begins.
- **PostgreSQL migration timing:** Pitfall 3 says PostgreSQL is required before launching the second adapter. Whether this lands in Phase 1 (safe, early) or Phase 4 (just-in-time) is a scope decision that affects Phase 1 sizing significantly. Flag for roadmap planning.
- **WhatsApp Business API approval lead time:** Meta's approval process takes 1-7 days for templates and varies for business verification. The Phase 4 plan must account for a queue period that cannot be accelerated by engineering effort.

## Sources

### Primary (HIGH confidence)
- Internal: `.planning/codebase/CONCERNS.md` — 68 documented issues, 8 critical bugs
- [Pillow 12.1.1 ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)
- [matplotlib 3.10 documentation](https://matplotlib.org/stable/index.html)
- [HTMX 2.0 documentation](https://htmx.org/docs/)
- [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy)
- [FastAPI documentation](https://fastapi.tiangolo.com/)

### Secondary (MEDIUM confidence)
- [FastAPI + HTMX patterns 2025](https://johal.in/htmx-fastapi-patterns-hypermedia-driven-single-page-applications-2025/)
- [WhatsApp 24-hour window guide](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/)
- [SQLite scalability limitations](https://www.slingacademy.com/article/sqlite-scalability-limitations-and-workarounds/)
- [Visual Search and the New Rules of Retail Discovery in 2026 - Imagga](https://imagga.com/blog/visual-search-and-the-new-rules-of-retail-discovery-in-2026/)
- [Amazon launches free shipping to Israel](https://www.timesofisrael.com/amazon-launches-free-shipping-to-israel-with-numerous-caveats/)
- [WhatsApp Business API Compliance 2026](https://gmcsco.com/your-simple-guide-to-whatsapp-api-compliance-2026/)

### Tertiary (LOW confidence)
- [CamelCamelCamel scraping notice](https://camelcamelcamel.com/blog/a-kind-note-to-those-crawling-our-sites/) — referenced but scraping behavior at scale unverified
- [HTMX Renaissance - Rethinking Web Architecture for 2026](https://www.softwareseni.com/the-htmx-renaissance-rethinking-web-architecture-for-2026/) — community perspective, needs validation for production SSE patterns

---
*Research completed: 2026-03-13*
*Ready for roadmap: yes*
