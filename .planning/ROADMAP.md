# Roadmap: Amazon Photo Bot

## Overview

This roadmap transforms the Amazon Photo Bot from a working but unreliable Telegram-only bot into a stable, feature-rich, multi-platform product search service. The journey starts by hardening the existing core (stability fixes, server consolidation), then enriches the visual experience for current Telegram users (photo annotations, shipping badges, price history), builds a web admin dashboard to prove the web stack, expands to WhatsApp and Instagram for Israeli market reach, and finally ships a public web application for SEO-driven discovery.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Stability and Infrastructure** - Fix critical bugs, enforce timeouts, consolidate servers into single FastAPI gateway (completed 2026-03-14)
- [ ] **Phase 2: Enhanced Visual Experience** - Photo annotations, Israel shipping badges, price history summaries, progress streaming
- [x] **Phase 3: Web Admin Dashboard** - Browser-based admin panel replacing Telegram-only admin commands (completed 2026-03-14)
- [x] **Phase 4: Messaging Platform Expansion** - WhatsApp Business API and Instagram DM integrations (completed 2026-03-14)
- [ ] **Phase 5: Public Web Application** - Photo upload web app with shareable result pages for SEO

## Phase Details

### Phase 1: Stability and Infrastructure
**Goal**: The existing Telegram bot runs reliably with no hung requests, no stale caches, no data corruption, and all HTTP services consolidated into a single FastAPI gateway
**Depends on**: Nothing (first phase)
**Requirements**: STAB-01, STAB-02, STAB-03, STAB-04, STAB-05, STAB-06, STAB-07, INFR-01, INFR-02, INFR-03
**Success Criteria** (what must be TRUE):
  1. A vision provider that hangs for >60s is terminated and the bot returns an error to the user within 65s (never hangs indefinitely)
  2. A provider that fails 5 times in a row recovers automatically after the configured time window passes (not permanently disabled)
  3. Admin changes to settings, tags, or model config take effect on the next user request without bot restart
  4. Sending a >10MB photo returns a friendly "photo too large" message instead of a silent failure or API error
  5. All bot HTTP endpoints (shortener, webhooks, API) are served from a single FastAPI process on one port
**Plans:** 4/4 plans complete

Plans:
- [ ] 01-01-PLAN.md -- Provider stability: timeout unification, health tracking, error messages (STAB-01, STAB-02, STAB-07)
- [ ] 01-02-PLAN.md -- Data integrity: DB transactions, photo validation, cache invalidation, Pillow offload (STAB-03, STAB-05, STAB-06, INFR-02)
- [ ] 01-03-PLAN.md -- Server consolidation and graceful shutdown (INFR-01, STAB-04)
- [ ] 01-04-PLAN.md -- Admin service layer extraction (INFR-03)

### Phase 2: Enhanced Visual Experience
**Goal**: Users receive enriched product results with visual annotations on photos, confidence-scored Israel shipping badges, and price history context that helps them decide whether to buy
**Depends on**: Phase 1
**Requirements**: ANNO-01, ANNO-02, ANNO-03, ANNO-04, ISRL-01, ISRL-02, ISRL-03, ISRL-04, PRCE-01, PRCE-02, PRCE-03
**Success Criteria** (what must be TRUE):
  1. After analysis, the user receives their photo back with colored overlays highlighting each detected product, numbered to match the results list
  2. Each product result displays a shipping badge (green/yellow/red) indicating Israel shipping confidence, with false positive rate under 10% and false negative rate under 15%
  3. Each product result includes a text price summary and visual bar showing where the current price sits in the 90-day range, plus a deal quality label
  4. The user sees real-time progress messages during analysis ("Analyzing photo...", "Found 3 products...", "Searching Amazon...")
**Plans**: TBD

Plans:
- [ ] 02-01: TBD
- [ ] 02-02: TBD
- [ ] 02-03: TBD

### Phase 3: Web Admin Dashboard
**Goal**: Admins manage the bot entirely through a browser-based dashboard, with all functionality currently available only via Telegram admin commands
**Depends on**: Phase 1
**Requirements**: ADMN-01, ADMN-02, ADMN-03, ADMN-04, ADMN-05, ADMN-06
**Success Criteria** (what must be TRUE):
  1. Admin can log in to /admin in a browser with credentials and see a dashboard showing user count, search count, click count, and revenue estimates
  2. Admin can add, remove, and view status of all API keys through the web UI without touching .env or Telegram
  3. Admin can activate/deactivate affiliate tags and change bot settings (vision mode, search backend, thresholds) through the web UI, with changes taking effect immediately
  4. Admin can view real-time provider health status (healthy, degraded, disabled) and manually reset a provider through the web UI
**Plans:** 3/3 plans complete

Plans:
- [ ] 03-01-PLAN.md -- Module scaffold, auth system (Telegram widget + fallback token), home page with stats and sparklines (ADMN-01, ADMN-02)
- [ ] 03-02-PLAN.md -- API key management and affiliate tag management pages (ADMN-03, ADMN-04)
- [ ] 03-03-PLAN.md -- Bot settings editor, provider health management, /webtoken command (ADMN-05, ADMN-06)

### Phase 4: Messaging Platform Expansion
**Goal**: Israeli users can send product photos via WhatsApp or Instagram DMs and receive the same quality results as Telegram users, with platform-appropriate UX
**Depends on**: Phase 1, Phase 2
**Requirements**: WHAT-01, WHAT-02, WHAT-03, WHAT-04, WHAT-05, INST-01, INST-02, INST-03
**Success Criteria** (what must be TRUE):
  1. A WhatsApp user can send a product photo and receive results with buttons for navigation, affiliate links, and shipping badges -- all within WhatsApp's structured message constraints (3-button max, list messages)
  2. WhatsApp 24-hour conversation window is respected: if the window closes before results are ready, the bot sends an approved template message to re-engage rather than silently failing
  3. WhatsApp users complete an opt-in flow before receiving any messages (Meta compliance)
  4. An Instagram user can send a product photo via DM and receive product results with proper formatting
**Plans:** 3/3 plans complete

Plans:
- [ ] 04-01-PLAN.md -- Foundation: aiohttp-to-FastAPI webhook migration + DB opt-in columns (INST-02)
- [ ] 04-02-PLAN.md -- WhatsApp compliance: opt-in gate, 24h window, list messages, template send, tests (WHAT-01, WHAT-02, WHAT-03, WHAT-04, WHAT-05)
- [ ] 04-03-PLAN.md -- Instagram compliance: opt-in gate, quick reply navigation, tests (INST-01, INST-03)

### Phase 5: Public Web Application
**Goal**: Anyone can visit the website, upload a product photo, and browse results with prices, shipping badges, price history, and shareable URLs -- no app install or messaging account required
**Depends on**: Phase 2, Phase 3
**Requirements**: WEBA-01, WEBA-02, WEBA-03, WEBA-04, WEBA-05
**Success Criteria** (what must be TRUE):
  1. A user can visit the site on a mobile phone, upload or drag-drop a photo, and see product results appear with real-time progress updates
  2. Product results display prices, ratings, affiliate links, Israel shipping badges, and price history -- matching the quality of bot results
  3. Each search result page has a unique shareable URL that renders correctly when shared on social media or messaged to a friend
  4. The web app is fully functional and readable on mobile screens (where the majority of Israeli users will access it)
**Plans:** 2/3 plans executed

Plans:
- [ ] 05-01-PLAN.md -- Upload pipeline: web_app module, SSE streaming, search_store, DB table, rate limiting, landing page (WEBA-01, WEBA-05)
- [ ] 05-02-PLAN.md -- Result page: product cards with badges/prices/affiliate links, product tabs, OG tags, shareable URLs (WEBA-02, WEBA-03, WEBA-04)
- [ ] 05-03-PLAN.md -- Mobile responsive polish, Hebrew/English i18n, RTL support, end-to-end verification (WEBA-05)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5
Note: Phase 3 depends only on Phase 1 (not Phase 2), so Phases 2 and 3 could theoretically run in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Stability and Infrastructure | 4/4 | Complete   | 2026-03-14 |
| 2. Enhanced Visual Experience | 3/4 | In Progress|  |
| 3. Web Admin Dashboard | 3/3 | Complete   | 2026-03-14 |
| 4. Messaging Platform Expansion | 3/3 | Complete   | 2026-03-14 |
| 5. Public Web Application | 2/3 | In Progress|  |
