# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — MVP

**Shipped:** 2026-03-14
**Phases:** 8 | **Plans:** 21 | **Timeline:** 19 days

### What Was Built
- Hardened core: unified timeouts, health tracking, atomic transactions, single FastAPI gateway
- Enhanced visuals: photo overlays, confidence-scored shipping badges, ASCII price bars, 4-stage progress
- Web admin dashboard: auth, stats with sparklines, key/tag/settings management, provider health
- Multi-platform: WhatsApp and Instagram adapters with opt-in, list messages, 24h window enforcement
- Public web app: photo upload with SSE, product result pages, OG tags, Hebrew/English i18n, mobile RTL
- Cross-platform parity: all visual features wired into bot_core.py for WhatsApp/Instagram

### What Worked
- **TDD approach** across all phases caught integration issues early (especially WhatsApp window enforcement)
- **Gap-closure phases (6, 7, 8)** added after audit caught real integration gaps — audit-before-complete proved valuable
- **Phase-by-phase execution** with verification kept quality high despite fast pace
- **Single gateway consolidation** in Phase 1 paid dividends — all subsequent web work (dashboard, web app, webhooks) mounted cleanly
- **Admin service layer extraction** in Phase 1 enabled Phase 3 dashboard to reuse all logic without Telegram coupling

### What Was Inefficient
- **SUMMARY.md frontmatter** mostly left empty (12/21 files) — the format wasn't worth the overhead for quick plans
- **Phase 2 ROADMAP.md** showed "3/4 In Progress" even though all 4 plans completed — status tracking drifted
- **Research phases** for WhatsApp/Instagram compliance and SSE patterns were flagged but never formally run — worked out fine but could have caught issues earlier
- **Nyquist validation** marked PARTIAL for all 8 phases — frontmatter never updated after execution despite tests passing

### Patterns Established
- FastAPI+HTMX+Jinja2 for all web surfaces (dashboard and public app)
- Guard pattern for WhatsApp 24h window (_guard_window returns no-op, not exception)
- Platform-aware rendering in formatter.py (Telegram monospace vs plain text for others)
- Hebrew-first default with logical CSS properties (ms-, me-) for RTL
- Lazy imports in SSE generators to avoid circular imports

### Key Lessons
1. **Run milestone audit before completion** — Phases 6-8 were created entirely from audit findings, closing real gaps that would have shipped broken
2. **Admin service layer extraction is worth doing early** — decoupling business logic from Telegram handlers in Phase 1 made Phase 3 (web dashboard) and Phase 6 (webtoken) straightforward
3. **Gap-closure phases are cheap** — Phases 6, 7, 8 averaged 1-2 plans each and took minimal time, but closed critical integration holes
4. **SUMMARY frontmatter has low ROI** — one-liners and requirements_completed fields were rarely filled; consider simplifying the format

### Cost Observations
- Model mix: primarily opus for planning/execution, sonnet for verification agents
- Sessions: ~20+ sessions across 19 days
- Notable: Phase 7 Plan 02 took 54 minutes (outlier — complex bot_core.py wiring), average plan was ~12 minutes

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Timeline | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 19 days | 8 | First milestone — established TDD, audit-before-complete, gap-closure patterns |

### Cumulative Quality

| Milestone | Plans | Avg Duration | Requirements |
|-----------|-------|-------------|--------------|
| v1.0 | 21 | ~12 min | 40/40 satisfied |

### Top Lessons (Verified Across Milestones)

1. Run milestone audit before marking complete — catches integration gaps that phase-level verification misses
2. Extract shared service layers early — pays dividends when building multiple frontends
