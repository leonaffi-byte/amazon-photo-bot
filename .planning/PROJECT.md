# Amazon Photo Bot

## What This Is

A multi-platform bot that identifies products in user-submitted photos using AI vision models and finds matching items on Amazon, earning revenue through affiliate links. Currently works on Telegram with 7 vision providers and 4 search backends. Targeting Israeli consumers who want to find Amazon products that ship free to Israel.

## Core Value

When a user sends a photo of any product, the bot must reliably identify it and return relevant Amazon results with accurate Israel shipping information — fast enough that users don't abandon the interaction.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. Inferred from existing code. -->

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
- ✓ Multi-adapter architecture (Telegram, WhatsApp, Discord, Instagram, etc.) — existing (adapters coded, untested)
- ✓ Provider health tracking with auto-disable/recovery — existing
- ✓ Enforce timeouts on all vision provider API calls — Phase 1
- ✓ Wrap multi-step DB operations in transactions — Phase 1
- ✓ Fix graceful shutdown race condition — Phase 1
- ✓ Fix settings cache invalidation (admin changes take effect immediately) — Phase 1
- ✓ Add proper error messages showing which backend/provider failed — Phase 1
- ✓ Consolidated single FastAPI gateway for all HTTP services — Phase 1
- ✓ Admin service layer decoupled from Telegram — Phase 1
- ✓ ASCII price bar visualization in product captions — Phase 2
- ✓ Multi-signal Israel shipping confidence scoring (replaces binary detection) — Phase 2
- ✓ Semi-transparent overlay annotations on detected products — Phase 2
- ✓ Shipping badges (green/yellow/red/gray) in product results — Phase 2
- ✓ 4-stage progress messages during analysis — Phase 2

### Active

<!-- Current scope. Building toward these. -->

**Stability & Reliability:**
- [ ] Fix model health tracking (add time-window reset, not permanent disable)
- [ ] Add photo size validation before vision API
- [ ] Fix Israel shipping filter accuracy (both false positives and false negatives)

**Performance:**
- [ ] Reduce end-to-end latency (photo → results)
- [ ] Cache settings, active tag, disabled models with TTL
- [ ] Parallelize proxy attempts in price history
- [ ] Optimize Playwright selector resilience and speed

**Photo Annotations:**
- [ ] Let user tap/select which detected product to search for

**Israel Shipping Filter Improvements:**
- [ ] Improve detection accuracy for Israel-eligible products (beyond confidence scoring)

**Price History:**
- [ ] Show price chart image alongside product results

**Multi-Platform:**
- [ ] WhatsApp Business API integration (production-ready)
- [ ] Web application for photo upload + results browsing
- [ ] Instagram DM integration

**Web Interface:**
- [ ] Public-facing web app for users (photo upload, results, price history)
- [ ] Admin dashboard (replace Telegram-based admin panel)

**UX & Design:**
- [ ] Better message formatting and visual design

### Out of Scope

- Discord integration — low priority for Israeli audience
- LINE / Viber adapters — low demand
- Mobile native app — web-first approach
- Real-time price monitoring / deal alerts — future milestone
- Multi-language support beyond Hebrew/English — future milestone
- Payment processing / premium tiers — future milestone

## Context

- **Audience:** Israeli consumers shopping on Amazon (Hebrew + English speakers)
- **Monetization:** Amazon Associates affiliate commission on purchases via bot links
- **Current state:** Working Telegram bot with hardened stability and enhanced visual experience (price bars, shipping badges, photo overlays, progress messages). Israel filter now uses multi-signal confidence scoring.
- **Existing adapters:** WhatsApp, Discord, Instagram, Messenger, Viber, LINE adapter code exists in `adapters/` but is untested
- **Tech stack:** Python 3.11+ async, SQLite, FastAPI (single gateway), Playwright, Docker
- **68 known issues** documented in `.planning/codebase/CONCERNS.md` ranging from critical bugs to code quality

## Constraints

- **Tech stack**: Python 3.11+ async — entire codebase is async-first, must stay that way
- **Database**: SQLite for now (migration to PostgreSQL deferred unless scaling requires it)
- **Budget**: Minimize vision API costs — use cheapest effective provider, cache when possible
- **Israel focus**: Israel shipping detection accuracy is business-critical
- **Affiliate compliance**: Amazon Associates program rules must be followed (disclosure, no misleading)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Stay on SQLite | Works for current scale, migration cost high | — Pending |
| Web app for public access | Telegram alone limits reach; web enables SEO + sharing | — Pending |
| Semi-transparent overlays for detection | User preference, better UX than bounding boxes | ✓ Phase 2 |
| Confidence-based Israel shipping tiers | Replaces binary yes/no with green/yellow/red scoring | ✓ Phase 2 |
| 4-stage progress messages | Perceived speed improvement during analysis flow | ✓ Phase 2 |
| WhatsApp + Instagram priority | Largest platforms in Israel after Telegram | — Pending |
| Fix stability before new features | Unreliable core undermines everything built on top | ✓ Phase 1 |
| Consolidate HTTP servers into single gateway | 3 ports → 1, simpler ops and Docker config | ✓ Phase 1 |
| Extract admin service layer | Enables web dashboard to reuse admin logic without Telegram coupling | ✓ Phase 1 |

---
*Last updated: 2026-03-14 after Phase 2*
