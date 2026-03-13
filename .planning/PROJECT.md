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

### Active

<!-- Current scope. Building toward these. -->

**Stability & Reliability:**
- [ ] Enforce timeouts on all vision provider API calls
- [ ] Fix model health tracking (add time-window reset, not permanent disable)
- [ ] Wrap multi-step DB operations in transactions
- [ ] Fix graceful shutdown race condition
- [ ] Add photo size validation before vision API
- [ ] Fix Israel shipping filter accuracy (both false positives and false negatives)
- [ ] Fix settings cache invalidation (admin changes take effect immediately)
- [ ] Add proper error messages showing which backend/provider failed

**Performance:**
- [ ] Reduce end-to-end latency (photo → results)
- [ ] Cache settings, active tag, disabled models with TTL
- [ ] Parallelize proxy attempts in price history
- [ ] Optimize Playwright selector resilience and speed

**Photo Annotations:**
- [ ] Return annotated photo with semi-transparent overlays on detected products
- [ ] Fall back to bounding boxes if shape detection not feasible
- [ ] Let user tap/select which detected product to search for

**Israel Shipping Filter Improvements:**
- [ ] Improve detection accuracy for Israel-eligible products
- [ ] Add confidence scoring to Israel eligibility
- [ ] Show "ships free to Israel" badge on results

**Price History:**
- [ ] Show price chart image alongside product results
- [ ] Show high/low text summary ("Lowest: $X (3mo ago) / Current: $Y")

**Multi-Platform:**
- [ ] WhatsApp Business API integration (production-ready)
- [ ] Web application for photo upload + results browsing
- [ ] Instagram DM integration

**Web Interface:**
- [ ] Public-facing web app for users (photo upload, results, price history)
- [ ] Admin dashboard (replace Telegram-based admin panel)

**UX & Design:**
- [ ] Better message formatting and visual design
- [ ] Faster perceived response (progress indicators, streaming updates)

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
- **Current state:** Working Telegram bot, but unreliable — timeouts, stale caches, inaccurate Israel filter, slow response times
- **Existing adapters:** WhatsApp, Discord, Instagram, Messenger, Viber, LINE adapter code exists in `adapters/` but is untested
- **Tech stack:** Python 3.11+ async, SQLite, aiohttp, Playwright, Docker
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
| Semi-transparent overlays for detection | User preference, better UX than bounding boxes | — Pending |
| WhatsApp + Instagram priority | Largest platforms in Israel after Telegram | — Pending |
| Fix stability before new features | Unreliable core undermines everything built on top | — Pending |

---
*Last updated: 2026-03-13 after initialization*
