# Amazon Photo Bot

## What This Is

A multi-platform product identification service that uses AI vision to identify products in user photos and finds matching items on Amazon with Israel shipping information. Supports Telegram, WhatsApp, Instagram, and a public web application. Features a browser-based admin dashboard, confidence-scored shipping badges, annotated photo overlays, and price history visualization. Earns revenue through Amazon Associates affiliate links targeting Israeli consumers.

## Core Value

When a user sends a photo of any product, the bot must reliably identify it and return relevant Amazon results with accurate Israel shipping information — fast enough that users don't abandon the interaction.

## Requirements

### Validated

- ✓ Vision analysis via 7 providers (OpenAI, Anthropic, Gemini, Groq, Azure, OpenRouter) — existing
- ✓ Amazon search via 4 backends (PA-API, RapidAPI, DataForSEO, Playwright) — existing
- ✓ Telegram bot with photo → search → results flow — existing
- ✓ Multi-product detection from single photo — existing
- ✓ Israel shipping eligibility filtering (FBA/Prime/sold-by-Amazon signals) — existing
- ✓ Affiliate link generation with configurable tags — existing
- ✓ Admin panel via Telegram (/admin, /settings, /addtag) — existing
- ✓ Custom URL shortener with click tracking — existing
- ✓ Price history via CamelCamelCamel + Keepa — existing
- ✓ Scheduled admin reports (daily/weekly/monthly) — existing
- ✓ Provider health tracking with auto-disable/recovery — existing
- ✓ Enforce timeouts on all vision provider API calls — v1.0
- ✓ Wrap multi-step DB operations in transactions — v1.0
- ✓ Fix graceful shutdown race condition — v1.0
- ✓ Fix settings cache invalidation (admin changes take effect immediately) — v1.0
- ✓ Add proper error messages showing which backend/provider failed — v1.0
- ✓ Consolidated single FastAPI gateway for all HTTP services — v1.0
- ✓ Admin service layer decoupled from Telegram — v1.0
- ✓ ASCII price bar visualization in product captions — v1.0
- ✓ Multi-signal Israel shipping confidence scoring (replaces binary detection) — v1.0
- ✓ Semi-transparent overlay annotations on detected products — v1.0
- ✓ Shipping badges (green/yellow/red/gray) in product results — v1.0
- ✓ 4-stage progress messages during analysis — v1.0
- ✓ Web admin dashboard with auth, stats, key/tag/settings management — v1.0
- ✓ Provider health monitoring and reset via web UI — v1.0
- ✓ WhatsApp Business API integration with opt-in compliance — v1.0
- ✓ Instagram DM integration with opt-in and quick replies — v1.0
- ✓ WhatsApp 24h window enforcement with template fallback — v1.0
- ✓ Public web app with photo upload, SSE progress, shareable result pages — v1.0
- ✓ Hebrew/English i18n with RTL support — v1.0
- ✓ Mobile-responsive web application — v1.0
- ✓ Cross-platform visual parity (overlays, badges, price bars on all platforms) — v1.0

### Active

- [ ] Reduce end-to-end latency (photo → results)
- [ ] Let user tap/select which detected product to search for
- [ ] Price chart image alongside product results (rendered visual, not ASCII)
- [ ] Improve Israel shipping detection accuracy beyond confidence scoring
- [ ] Click tracking in DB for dashboard analytics

### Out of Scope

- Discord / LINE / Viber integrations — low demand in Israeli market
- Mobile native app — web PWA provides app-like experience
- Real-time price monitoring / deal alerts — future milestone
- Multi-language support beyond Hebrew/English — future milestone
- Payment processing / premium tiers — future milestone
- Open-ended AI chatbot conversation — Meta banned on WhatsApp; unpredictable costs
- Multi-marketplace search (eBay, AliExpress) — Amazon Associates TOS concerns
- Automatic purchase / one-click buy — Amazon Associates TOS violation

## Context

- **Audience:** Israeli consumers shopping on Amazon (Hebrew + English speakers)
- **Monetization:** Amazon Associates affiliate commission on purchases via bot links
- **Current state:** v1.0 shipped with 22,651 LOC Python across 250 files. All 40 requirements satisfied. 8 phases, 21 plans executed over 19 days.
- **Platforms:** Telegram (primary), WhatsApp, Instagram, Web — all with visual parity
- **Tech stack:** Python 3.11+ async, SQLite, FastAPI (single gateway), HTMX+Jinja2, Playwright, Docker
- **Tech debt:** `total_clicks` hardcoded to 0, WhatsApp `edit_text` double-guards, legacy adapters (messenger/viber/line) use stale aiohttp types

## Constraints

- **Tech stack**: Python 3.11+ async — entire codebase is async-first, must stay that way
- **Database**: SQLite for now (migration to PostgreSQL deferred unless scaling requires it)
- **Budget**: Minimize vision API costs — use cheapest effective provider, cache when possible
- **Israel focus**: Israel shipping detection accuracy is business-critical
- **Affiliate compliance**: Amazon Associates program rules must be followed (disclosure, no misleading)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Stay on SQLite | Works for current scale, migration cost high | ⚠️ Revisit if multi-platform write contention appears |
| Fix stability before new features | Unreliable core undermines everything built on top | ✓ Good (v1.0 Phase 1) |
| Consolidate HTTP servers into single gateway | 3 ports → 1, simpler ops and Docker config | ✓ Good (v1.0 Phase 1) |
| Extract admin service layer | Enables web dashboard to reuse admin logic without Telegram coupling | ✓ Good (v1.0 Phase 1) |
| Semi-transparent overlays for detection | User preference, better UX than bounding boxes | ✓ Good (v1.0 Phase 2) |
| Confidence-based Israel shipping tiers | Replaces binary yes/no with green/yellow/red scoring | ✓ Good (v1.0 Phase 2) |
| 4-stage progress messages | Perceived speed improvement during analysis flow | ✓ Good (v1.0 Phase 2) |
| FastAPI+HTMX+Jinja2 for web surfaces | Lightweight, server-rendered, works with existing async stack | ✓ Good (v1.0 Phase 3/5) |
| WhatsApp + Instagram priority | Largest platforms in Israel after Telegram | ✓ Good (v1.0 Phase 4) |
| Web app for public access | Telegram alone limits reach; web enables SEO + sharing | ✓ Good (v1.0 Phase 5) |
| Hebrew-first default (lang='he') | Target market is Israeli users | ✓ Good (v1.0 Phase 5) |
| Guard pattern for WhatsApp 24h window | _guard_window returns no-op instead of exceptions | ✓ Good (v1.0 Phase 8) |

---
*Last updated: 2026-03-14 after v1.0 milestone*
