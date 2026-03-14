# Phase 5: Public Web Application - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Public-facing web application where anyone can upload a product photo and browse results with prices, shipping badges, price history, and shareable URLs. No app install or messaging account required. Mobile-first for Israeli audience.

</domain>

<decisions>
## Implementation Decisions

### Upload & Progress UX
- File picker + drag-drop zone for photo upload. On mobile, OS file picker offers camera or gallery — no dedicated camera button needed
- Real-time progress via SSE (Server-Sent Events) using FastAPI StreamingResponse + HTMX hx-ext="sse"
- During analysis: show uploaded photo as thumbnail alongside 4-stage progress steps ("Analyzing photo..." → "Found 3 products..." → "Searching Amazon..." → "Done") — matches existing bot flow
- Fully anonymous usage — no login required. Rate-limited with CAPTCHA after N searches per session/IP to protect API costs

### Results Page Layout
- Vertical card list, one product per row (stacked). Mobile-friendly, similar to Amazon mobile search
- Annotated photo (with numbered product overlays from Phase 2) displayed at top of results page, collapsible on scroll
- Each product card shows: product image, title, price, star rating, Israel shipping badge (green/yellow/red), price history bar with deal quality label, and a prominent "View on Amazon" affiliate link button
- Multi-product navigation via horizontal tabs above results ("Product 1", "Product 2", etc.). Clicking a tab loads that product's Amazon results below

### Shareable URLs & SEO
- URL structure: /search/<short-id> (random short ID, similar to existing URL shortener pattern)
- Search results persist for 30 days in SQLite, then auto-purged
- Open Graph meta tags on result pages using annotated photo as og:image for rich social media previews (WhatsApp, Telegram, Facebook)
- Homepage indexable by search engines (SEO for discovery). Result pages use noindex meta tag — for sharing only, avoids thin content penalties

### Landing Page & Branding
- Upload-first hero: big centered upload zone as hero section. Below it: "How it works" in 3 steps (Upload → AI identifies → Amazon results). Minimal text, fast to action
- Hebrew + English language support with toggle. Hebrew as default for Israeli audience. RTL layout support needed. Reuses existing translator.py and i18n.py
- Clean & minimal visual tone: white/light background, subtle shadows, rounded cards. Professional, not flashy. Similar to Google Lens or Amazon search
- Amazon Associates affiliate disclosure in page footer ("As an Amazon Associate, we earn from qualifying purchases")

### Claude's Discretion
- CAPTCHA implementation details (threshold, provider)
- SSE event format and reconnection handling
- Exact Tailwind styling and spacing
- Rate limiting thresholds and session tracking approach
- Upload file size validation UX (error messages, max size)
- 404/expired result page design

</decisions>

<specifics>
## Specific Ideas

- Homepage should feel like Google's simplicity — upload zone is the primary and nearly only element above the fold
- Product cards should match the quality of information users get from the bot (shipping badge, price bar, deal label — full experience)
- Annotated photo at top of results creates the "wow factor" moment showing AI identified the products
- Short IDs for result URLs reuse the pattern already established by the URL shortener module

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `admin_dashboard/`: Complete FastAPI + Jinja2 + HTMX + Tailwind CDN pattern — templates, auth, router structure to mirror
- `bot_core.py`: Platform-agnostic bot logic with `UserSession` state machine — web becomes another "adapter"
- `image_analyzer.py`: `ProductInfo` dataclass and vision analysis pipeline — fully reusable
- `amazon_search.py`: Search facade with backend fallback — fully reusable
- `url_shortener.py`: Short ID generation logic — reuse for result page IDs
- `style.py` / `formatter.py`: Shipping badges, price bars, deal labels — adapt for HTML output
- `i18n.py` + `translator.py`: Language detection and translation infrastructure
- `admin_dashboard/sparklines.py`: SVG generation pattern — could adapt for price history visualization

### Established Patterns
- FastAPI + Jinja2Templates + HTMX for server-rendered interactive pages (Phase 3)
- Tailwind CSS via CDN Play script tag — no build step
- HTMX polling for live updates (admin dashboard uses hx-trigger="every 30s")
- Async-first: all operations are async def — FastAPI handlers call them directly
- SQLite for persistence — add web_searches table for result storage

### Integration Points
- `main.py`: Web app routes register as another FastAPI router on the existing gateway (port 8080)
- `adapters/base.py`: Web adapter could implement PlatformAdapter interface, or web routes can call bot_core/image_analyzer directly
- `database.py`: Add table for web search results (photo hash, results JSON, created_at, short_id)
- Phase 2 annotator: `image_annotator.py` generates overlay images — serve these as static files or base64

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-public-web-application*
*Context gathered: 2026-03-14*
