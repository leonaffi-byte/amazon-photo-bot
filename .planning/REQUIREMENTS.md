# Requirements: Amazon Photo Bot

**Defined:** 2026-03-13
**Core Value:** When a user sends a photo of any product, the bot must reliably identify it and return relevant Amazon results with accurate Israel shipping information — fast enough that users don't abandon the interaction.

## v1 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Stability

- [x] **STAB-01**: All vision provider API calls enforce timeout (max 60s per provider)
- [x] **STAB-02**: Model health tracking resets failure counter after configurable time window (not permanent disable)
- [x] **STAB-03**: Multi-step database operations wrapped in atomic transactions
- [x] **STAB-04**: Graceful shutdown properly awaits all background tasks before exit
- [x] **STAB-05**: Photo size validated before sending to vision API (reject >10MB with user-friendly message)
- [x] **STAB-06**: Settings, active tag, and disabled models cached with TTL and invalidated on admin changes
- [x] **STAB-07**: Error messages specify which provider/backend failed (not generic "something went wrong")

### Photo Annotation

- [x] **ANNO-01**: Vision providers return bounding box coordinates for detected products
- [x] **ANNO-02**: Bot sends back annotated photo with semi-transparent overlays on each detected product
- [x] **ANNO-03**: If bounding box quality is low, fall back to numbered circles at approximate positions
- [x] **ANNO-04**: User sees streaming progress updates during analysis ("Analyzing photo...", "Found 3 products...", "Searching Amazon...")

### Israel Shipping

- [x] **ISRL-01**: Israel shipping detection uses multi-signal approach (FBA status + seller identity + Prime status + address verification)
- [x] **ISRL-02**: Each product result shows confidence-scored shipping badge (green = ships free, yellow = likely, red = won't ship)
- [x] **ISRL-03**: False positive rate for Israel shipping reduced below 10%
- [x] **ISRL-04**: False negative rate for Israel shipping reduced below 15%

### Price History

- [x] **PRCE-01**: Product results include text summary of price history ("Lowest: $X (3mo ago) / Current: $Y")
- [x] **PRCE-02**: Product results include ASCII-style price bar showing current price position within 90-day range
- [x] **PRCE-03**: Deal quality indicator shown on results ("Good deal" / "Average price" / "Overpriced")

### Web Admin Dashboard

- [x] **ADMN-01**: Web-based admin dashboard accessible at `/admin` with authentication
- [x] **ADMN-02**: Dashboard shows bot statistics (users, searches, clicks, revenue estimates)
- [x] **ADMN-03**: Admin can manage API keys (add, remove, view status) via web UI
- [x] **ADMN-04**: Admin can manage affiliate tags (activate, deactivate, add) via web UI
- [x] **ADMN-05**: Admin can edit bot settings (vision mode, search backend, thresholds) via web UI
- [x] **ADMN-06**: Admin can view and manage provider health status via web UI

### Public Web Application

- [ ] **WEBA-01**: Public web page where users can upload a photo to identify products
- [ ] **WEBA-02**: Web app displays product results with prices, ratings, affiliate links, and shipping badges
- [ ] **WEBA-03**: Web app shows price history visualization for each product
- [ ] **WEBA-04**: Search results have shareable URLs for SEO and link sharing
- [ ] **WEBA-05**: Web app is mobile-responsive (majority of users on phones)

### WhatsApp Integration

- [ ] **WHAT-01**: Bot responds to photo messages on WhatsApp with product results
- [ ] **WHAT-02**: All WhatsApp interactions use structured messages (buttons, list messages) — no free-text AI chat
- [ ] **WHAT-03**: WhatsApp message templates approved by Meta for outbound messages
- [ ] **WHAT-04**: WhatsApp adapter handles 24-hour conversation window correctly
- [ ] **WHAT-05**: User opt-in flow before receiving messages (WhatsApp compliance)

### Instagram Integration

- [ ] **INST-01**: Bot responds to photo messages in Instagram DMs with product results
- [ ] **INST-02**: Instagram adapter uses Meta Graph API with proper authentication
- [ ] **INST-03**: Instagram interactions use structured replies where supported

### Infrastructure

- [x] **INFR-01**: Consolidate 3 HTTP servers (aiohttp shortener, aiohttp webhooks, FastAPI API) into single FastAPI gateway
- [x] **INFR-02**: Pillow CPU-bound operations (photo annotation) offloaded to executor (not blocking async loop)
- [x] **INFR-03**: Admin service layer extracted from admin.py (shared between Telegram admin and web dashboard)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Retention Features

- **RETN-01**: User can save products to a watchlist
- **RETN-02**: User receives notification when watched product price drops
- **RETN-03**: "Shop the Look" — suggest complementary products for clothing photos

### Enhanced Search

- **SRCH-01**: Multi-product comparison view (side-by-side price, rating, shipping)
- **SRCH-02**: Price history chart image (rendered with matplotlib, sent as inline image)

### Scaling

- **SCAL-01**: Migrate from SQLite to PostgreSQL for multi-platform write concurrency
- **SCAL-02**: Move rate limiter to shared state (Redis) for multi-process deployments

## Out of Scope

| Feature | Reason |
|---------|--------|
| Discord / LINE / Viber integrations | Israeli market doesn't use these platforms at scale |
| Native mobile app | Web PWA provides app-like experience without app store overhead |
| Open-ended AI chatbot conversation | Meta banned on WhatsApp; unpredictable costs; scope creep |
| Multi-marketplace search (eBay, AliExpress) | Amazon Associates TOS concerns; complexity multiplies; not core value |
| Automatic purchase / one-click buy | Amazon Associates TOS violation; security/liability exposure |
| Barcode/QR scanning feature | Vision providers can read barcodes from photos already; limited added value |
| User accounts and profiles | Use platform user IDs as implicit identity; GDPR complexity |
| Real-time price monitoring | CamelCamelCamel does this better; API costs scale linearly |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STAB-01 | Phase 1 | Complete |
| STAB-02 | Phase 1 | Complete |
| STAB-03 | Phase 1 | Complete |
| STAB-04 | Phase 1 | Complete |
| STAB-05 | Phase 1 | Complete |
| STAB-06 | Phase 1 | Complete |
| STAB-07 | Phase 1 | Complete |
| ANNO-01 | Phase 2 | Complete |
| ANNO-02 | Phase 2 | Complete |
| ANNO-03 | Phase 2 | Complete |
| ANNO-04 | Phase 2 | Complete |
| ISRL-01 | Phase 2 | Complete |
| ISRL-02 | Phase 2 | Complete |
| ISRL-03 | Phase 2 | Complete |
| ISRL-04 | Phase 2 | Complete |
| PRCE-01 | Phase 2 | Complete |
| PRCE-02 | Phase 2 | Complete |
| PRCE-03 | Phase 2 | Complete |
| ADMN-01 | Phase 3 | Complete |
| ADMN-02 | Phase 3 | Complete |
| ADMN-03 | Phase 3 | Complete |
| ADMN-04 | Phase 3 | Complete |
| ADMN-05 | Phase 3 | Complete |
| ADMN-06 | Phase 3 | Complete |
| WEBA-01 | Phase 5 | Pending |
| WEBA-02 | Phase 5 | Pending |
| WEBA-03 | Phase 5 | Pending |
| WEBA-04 | Phase 5 | Pending |
| WEBA-05 | Phase 5 | Pending |
| WHAT-01 | Phase 4 | Pending |
| WHAT-02 | Phase 4 | Pending |
| WHAT-03 | Phase 4 | Pending |
| WHAT-04 | Phase 4 | Pending |
| WHAT-05 | Phase 4 | Pending |
| INST-01 | Phase 4 | Pending |
| INST-02 | Phase 4 | Pending |
| INST-03 | Phase 4 | Pending |
| INFR-01 | Phase 1 | Complete |
| INFR-02 | Phase 1 | Complete |
| INFR-03 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 40
- Unmapped: 0

---
*Requirements defined: 2026-03-13*
*Last updated: 2026-03-13 after roadmap creation*
