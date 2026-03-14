# Phase 3: Web Admin Dashboard - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Browser-based admin dashboard at `/admin` replacing Telegram-only admin commands. Covers authentication, statistics overview, API key management, affiliate tag management, bot settings editing, and provider health monitoring. All backed by the existing `admin_service.py` service layer from Phase 1.

</domain>

<decisions>
## Implementation Decisions

### Authentication
- Primary: Telegram Login Widget — admins click "Log in with Telegram", verified against existing ADMIN_IDS list
- Fallback: Auto-generated login token shown in bot startup logs and sendable via `/webtoken` Telegram command (for environments without domain configuration)
- Session duration: 24 hours before requiring re-login
- Single admin role — everyone in ADMIN_IDS gets full access (no viewer/admin split)
- Public internet access with auth as only protection layer

### Dashboard Home & Statistics
- Landing page shows stats overview with key metrics: total users, searches today/week/month, photos analyzed, products found
- Sidebar navigation to detail pages (Tags, Keys, Settings, Health)
- Stats include: usage metrics, provider performance (success rate, latency, cost), search backend stats (hit rate, fallback frequency)
- No revenue estimates on dashboard
- Per-tag performance shown on tag management page (click count, search count from DB)
- Totals with simple CSS/SVG sparklines showing 7-day trends (no charting library)

### Live Updates & Interaction
- Provider health auto-refreshes via HTMX `hx-trigger="every 30s"`
- Home page stat cards auto-refresh via HTMX polling every 60s
- Admin action feedback via inline HTMX swap (no page reload, no toast component)
- Admin actions on web dashboard trigger Telegram notifications to other admins via existing `notifications.py`

### Mobile & Styling
- Fully responsive: sidebar collapses to hamburger menu on mobile, stat cards stack vertically, tables scroll horizontally
- Tailwind CSS via CDN (Play CDN script tag — no build step, acceptable for admin-only pages)
- HTMX via CDN for interactivity

### Claude's Discretion
- Exact Jinja2 template structure and inheritance hierarchy
- HTMX partial endpoint naming conventions
- Sparkline implementation approach (inline SVG vs CSS-only)
- Login rate limiting details
- Exact sidebar navigation items and ordering
- Table pagination for logs/history views

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `admin_service.py`: Complete service layer with all admin operations (tags, keys, settings, stats, health, admins) returning plain dataclasses — direct data source for web dashboard
- `admin_models.py`: Pydantic models for admin UI (can inform web form structures)
- `notifications.py`: Telegram notification delivery — reuse for cross-channel admin action alerts
- `database.py`: `search_logs` table already tracks per-tag click/search counts for performance metrics

### Established Patterns
- FastAPI gateway on port 8080 — dashboard routes mount as a sub-application or router
- Async-first: all admin_service functions are `async def` — FastAPI route handlers call them directly
- Config priority: DB > .env > defaults — settings page edits go through `settings_store.py`
- `key_store.py`: API key storage with DB > .env priority — key management page uses this

### Integration Points
- `main.py`: Dashboard FastAPI router registers at `/admin` on the existing gateway
- `admin_service.py`: All CRUD operations already extracted — web handlers are thin wrappers
- `providers/manager.py`: Health tracking data via `admin_service.get_provider_health()`
- `notifications.py`: Web actions call `send_admin_notification()` for cross-channel alerts

</code_context>

<specifics>
## Specific Ideas

- Stats overview layout: metric cards at top (Users, Searches, Clicks) with provider health summary below
- Tag management shows per-tag click/search counts from existing DB data
- HTMX polling intervals: 30s for health, 60s for stats — keeps dashboard feeling alive without DB pressure

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-web-admin-dashboard*
*Context gathered: 2026-03-14*
