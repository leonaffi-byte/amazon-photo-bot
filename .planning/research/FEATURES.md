# Feature Research

**Domain:** AI-powered visual product search bot with affiliate monetization
**Researched:** 2026-03-13
**Confidence:** MEDIUM-HIGH (based on competitor analysis, industry trends, and existing codebase review)

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete or broken.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Fast photo-to-results pipeline (<10s) | Google Lens returns results in 1-2s. Users abandon after 10s wait. | MEDIUM | Currently bottlenecked by sequential vision+search. Need parallel processing, progress indicators, and caching. Existing code has no enforced timeouts. |
| Accurate product identification | Core promise of the bot. Wrong products = zero trust. | MEDIUM | Already works via 7 providers. Main gap: no confidence scoring shown to user. Need to surface "80% confident this is X" when uncertain. |
| Multi-product detection from single photo | Users photograph shelves, outfits, rooms. Expecting single-product detection is naive. | MEDIUM | Already implemented. `image_annotator.py` adds numbered legend strip at bottom. Picker buttons let user choose which product to search. |
| Relevant Amazon search results | Users expect the top 3-5 results to actually match what they photographed. | MEDIUM | Already works via 4 backends with fallback chain. Main gap: result relevance varies by backend. |
| Israel shipping eligibility indication | Core value proposition for target audience. Without this, bot is just a worse Google Lens. | HIGH | Exists but unreliable (documented in CONCERNS.md). False positives and false negatives undermine trust. Needs confidence scoring and better detection signals. |
| Price display with currency | Users expect to see price immediately, not click through to Amazon. | LOW | Already shown in result cards. Need to ensure ILS conversion or at minimum USD display. |
| Clickable purchase links | Users must be able to buy the product directly from results. | LOW | Already implemented with affiliate tags and URL shortener. |
| Progress feedback during search | Users need to know the bot is working, not frozen. | LOW | Partially implemented (loading messages). Dead code exists for animation frames in `style.py`. Need streaming progress: "Analyzing photo... Found 3 products... Searching Amazon..." |
| Error messages that explain what failed | Generic "something went wrong" destroys trust. | LOW | Currently generic errors. Need "Amazon search timed out, try again" specificity. |
| Mobile-friendly photo handling | Most photos come from phone cameras (12MP+). Bot must handle large files gracefully. | LOW | Photo resize exists in `bot_core.py` (max 1280px). Missing: file size validation before vision API call (documented bug). |

### Differentiators (Competitive Advantage)

Features that set the product apart from Google Lens, Amazon's own visual search, and generic shopping bots.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Photo annotation with product highlighting | Visual confirmation of what the bot detected. Google Lens does this; bot currently only adds a legend strip, not overlays on the actual products. | MEDIUM | Current `image_annotator.py` adds a numbered strip below the image. PROJECT.md targets semi-transparent overlays on detected products. Requires vision providers to return bounding box coordinates (already in prompt template in `providers/base.py` as `bbox` field). Fallback: colored numbered circles at approximate positions. |
| Price history visualization (chart image) | "Is this a good deal?" is the #1 question after finding a product. CamelCamelCamel and Keepa show charts; bot should too. | MEDIUM | `price_history.py` already fetches price data (current, low, avg) from CamelCamelCamel/Keepa. Missing: chart image generation. Use matplotlib or Pillow to render a simple line chart (current price vs. 90-day range). Send as inline image in result card. |
| Israel-specific free shipping badge | No competitor surfaces Israel shipping eligibility inside a chat bot. This is the unique wedge for the Israeli market. | HIGH | Exists but accuracy is the problem. Needs multi-signal approach: FBA status + seller identity + address-based verification. Show confidence: green checkmark (high confidence ships free), yellow question mark (maybe), red X (won't ship). |
| Hebrew-first UX with translation | Israeli users search in Hebrew; Amazon needs English queries. Seamless translation is invisible but critical. | LOW | `translator.py` exists. Need to verify it handles Hebrew product names, slang, and transliteration reliably. |
| WhatsApp integration | WhatsApp is the dominant messaging platform in Israel (~95% penetration). Telegram-only limits reach to tech-savvy users. | HIGH | Adapter code exists in `adapters/whatsapp.py` but is untested. WhatsApp Business API has strict compliance requirements (opt-in, template messages, no open-ended AI chat as of Jan 2026 Meta policy). Per-message costs (~$0.01-0.03). Needs webhook server, template approval, and compliance review. |
| Web application for photo upload | Enables SEO-driven discovery, link sharing, and users who don't use Telegram/WhatsApp. | HIGH | No existing web frontend. Would need: photo upload UI, results display, price history charts, shareable result pages. FastAPI backend exists (`api_server.py`) but only handles Israel scraper endpoints. |
| Multi-product comparison view | When photo contains multiple products, show side-by-side comparison (price, rating, shipping). | MEDIUM | Multi-product detection exists. Missing: comparison formatting. After user selects a product from picker, show alternatives with "compare" option. |
| Price drop alerts (watchlist) | User saves a product, gets notified when price drops below threshold. | HIGH | No existing implementation. Requires: persistent watchlist in DB, scheduled price checks, notification delivery across platforms. Explicitly listed as "Out of Scope" in PROJECT.md but is a natural differentiator for retention. |
| "Shop the Look" / outfit completion | When user photographs clothing, suggest complementary items (shoes for a dress, accessories for an outfit). | MEDIUM | Not implemented. Vision providers can be prompted to suggest complementary products. Amazon's StyleSnap already does this. Would require additional vision prompts and parallel searches. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems -- deliberately do NOT build these.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Real-time price monitoring for all products | Users want to know the instant a price drops. | Requires constant polling of Amazon for every tracked product. API costs scale linearly with watchlist size. CamelCamelCamel already does this better. Rate limits will kill you. | Offer price history snapshot at search time + link to CamelCamelCamel for ongoing tracking. If watchlist is built later, batch-check on a daily schedule, not real-time. |
| Open-ended AI chatbot conversation | Users may want to ask follow-up questions like "is this brand good?" or "compare this to Brand X". | Meta explicitly banned open-ended AI chat on WhatsApp Business API (Jan 2026). On Telegram it works but creates unpredictable costs and scope creep. Vision analysis costs $0.01-0.10 per call; chat costs add up. | Keep interactions transactional: photo in, results out. Add structured follow-ups (e.g., "See price history", "Check Israel shipping", "Find similar") via buttons, not free-text chat. |
| Multi-marketplace search (eBay, AliExpress, etc.) | Users want the cheapest option across all platforms. | Each marketplace needs its own search backend, affiliate program, and result formatting. Complexity multiplies. Amazon Associates TOS may prohibit showing competitor prices alongside affiliate links. | Stay Amazon-only. The value is Israel shipping verification on Amazon specifically. If users want AliExpress, that's a different product. |
| Native mobile app | "Apps are better than bots." | Development cost is 10x. App store approval process. Users must download and install. Messaging bots have zero-install friction. | Web app (PWA) gives app-like experience without app store overhead. Messaging bots for quick interactions. |
| Automatic purchase / one-click buy | Remove friction by letting bot purchase on user's behalf. | Amazon Associates TOS violation. Requires handling user payment info. Massive security and liability exposure. | Affiliate link to Amazon product page is the correct boundary. User completes purchase on Amazon. |
| Barcode/QR code scanning | Users may want to scan a barcode instead of photographing the product. | Barcode scanning requires camera access patterns that don't work well in messaging bots. Vision providers can read barcodes but accuracy is inconsistent. Limited value -- if you have the barcode, just search Amazon directly. | If a user sends a barcode photo, the vision provider will likely read it and can search by product name. No special barcode-scanning feature needed. |
| User accounts and profiles | Track purchase history, preferences, saved searches. | Adds GDPR/privacy complexity. Israeli Privacy Protection Law requirements. Most bot users expect anonymity. | Use Telegram/WhatsApp user ID as implicit identity. Store search history per user ID in DB (already done). No explicit account creation flow needed. |
| Discord / LINE / Viber integrations | "Be everywhere." | Israeli market doesn't use these platforms at scale. Each adapter requires maintenance, testing, and platform-specific compliance. Adapter code exists but is untested -- maintaining 6+ platforms is a recipe for bugs. | Focus on Telegram (current), WhatsApp (highest ROI), and Web (broadest reach). Revisit others only with evidence of demand. |

## Feature Dependencies

```
[Israel Shipping Verification (improved)]
    |
    +-- requires --> [Playwright scraper reliability]
    |                    |
    |                    +-- requires --> [Proxy health tracking]
    |
    +-- enhances --> [Result cards with shipping badge]

[Photo Annotation (overlays)]
    |
    +-- requires --> [Vision provider bbox coordinates]
    |                    |
    |                    +-- requires --> [Provider prompt engineering]
    |
    +-- enhances --> [Multi-product picker UX]

[Price History Chart]
    |
    +-- requires --> [Price history data fetch (exists)]
    |
    +-- requires --> [Chart image generation library]
    |
    +-- enhances --> [Result cards]

[Web Application]
    |
    +-- requires --> [FastAPI backend expansion]
    |
    +-- requires --> [Frontend (HTML/JS or framework)]
    |
    +-- requires --> [Photo upload + processing pipeline]
    |
    +-- enhances --> [SEO / link sharing]
    |
    +-- enhances --> [Price history visualization]

[WhatsApp Integration]
    |
    +-- requires --> [Webhook server (exists: webhook_server.py)]
    |
    +-- requires --> [WhatsApp Business API approval]
    |
    +-- requires --> [Message template approval from Meta]
    |
    +-- requires --> [Adapter testing + compliance review]
    |
    +-- conflicts with --> [Open-ended AI chat (Meta ban)]

[Stability & Reliability fixes]
    |
    +-- required by --> [ALL other features]
    |
    +-- includes --> [Timeout enforcement]
    +-- includes --> [Health tracking fix]
    +-- includes --> [Cache invalidation]
    +-- includes --> [Transaction safety]
```

### Dependency Notes

- **All new features require stability fixes first:** The 68 documented concerns (8 critical) mean any new feature built on the current foundation inherits unreliability. Timeouts, health tracking, and cache invalidation must be fixed before building on top.
- **Photo annotation requires vision provider bbox data:** The prompt in `providers/base.py` already asks for bbox coordinates, but providers return them inconsistently. Need to validate bbox quality per provider before investing in overlay rendering.
- **Web application is the heaviest dependency chain:** Requires backend expansion, frontend build, and assumes the core pipeline is stable. Should come after messaging platform stabilization.
- **WhatsApp conflicts with open-ended AI chat:** Meta's Jan 2026 policy explicitly bans mainstream chatbots on WhatsApp Business API. Bot interactions must be structured (buttons, templates) not conversational.
- **Price history chart requires no new data sources:** `price_history.py` already fetches the data. Only needs a chart rendering step (Pillow or matplotlib) and integration into result cards.

## MVP Definition

This project is not a new product launch -- it's extending an existing working bot. "MVP" here means "minimum viable milestone" for each feature cluster.

### Launch With (Stability Phase -- P0)

These must be done before any new features ship.

- [ ] Enforce timeouts on all vision provider API calls -- prevents hung requests from blocking the bot
- [ ] Fix model health tracking with time-window decay -- prevents permanent model disablement after transient failures
- [ ] Wrap multi-step DB operations in transactions -- prevents data corruption on crash
- [ ] Add photo size validation before vision API -- prevents quota exhaustion from oversized images
- [ ] Fix cache invalidation for settings/tags/models -- ensures admin changes take effect immediately
- [ ] Add proper error messages with backend/provider attribution -- enables debugging and user trust

### Add After Validation (Enhancement Phase -- P1)

Features that improve the core experience once stability is solid.

- [ ] Photo annotation with overlays -- when bbox quality is confirmed reliable from providers
- [ ] Price history chart image -- low-hanging fruit, data already exists, just needs rendering
- [ ] Israel shipping confidence badge -- multi-signal detection with green/yellow/red indicators
- [ ] Progress indicators -- streaming status updates during analysis
- [ ] WhatsApp Business API integration -- after compliance review and template approval

### Future Consideration (Expansion Phase -- P2+)

Features to defer until core platforms are stable and usage validates demand.

- [ ] Web application -- highest complexity, requires frontend development skills, defer until messaging platforms are solid
- [ ] Instagram DM integration -- lower priority than WhatsApp, similar Meta API compliance requirements
- [ ] Price drop alerts / watchlist -- requires scheduled jobs, notification infrastructure, and ongoing API costs
- [ ] "Shop the Look" complementary suggestions -- nice differentiator but not core to value proposition
- [ ] Multi-product comparison view -- useful but adds UX complexity

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Timeout enforcement | HIGH | LOW | P0 |
| Health tracking fix | HIGH | LOW | P0 |
| DB transaction safety | HIGH | LOW | P0 |
| Photo size validation | MEDIUM | LOW | P0 |
| Cache invalidation | HIGH | MEDIUM | P0 |
| Error message specificity | MEDIUM | LOW | P0 |
| Price history chart image | HIGH | LOW | P1 |
| Progress indicators | MEDIUM | LOW | P1 |
| Israel shipping confidence badge | HIGH | HIGH | P1 |
| Photo annotation overlays | MEDIUM | MEDIUM | P1 |
| WhatsApp integration | HIGH | HIGH | P1 |
| Web application | HIGH | HIGH | P2 |
| Instagram DM integration | MEDIUM | HIGH | P2 |
| Price drop alerts | MEDIUM | HIGH | P3 |
| Shop the Look | LOW | MEDIUM | P3 |
| Multi-product comparison | LOW | MEDIUM | P3 |

**Priority key:**
- P0: Must fix before adding features (stability)
- P1: High-value enhancements to ship next
- P2: Expansion features requiring significant investment
- P3: Nice to have, defer until demand is proven

## Competitor Feature Analysis

| Feature | Google Lens | Amazon StyleSnap | CamelCamelCamel | This Bot (Current) | This Bot (Target) |
|---------|-------------|-----------------|-----------------|--------------------|--------------------|
| Photo to product ID | Instant, high accuracy | Fashion-focused, in-app only | N/A | Works, 3-15s latency | <10s with progress |
| Multi-product detection | Yes, tap to select | Single product focus | N/A | Yes, legend strip + picker | Overlay annotations |
| Product overlay/highlight | Circles detected items | Bounding boxes on fashion | N/A | Legend strip only | Semi-transparent overlays |
| Price display | Shows from multiple sellers | Amazon price only | Historical chart | Amazon price in card | + chart image |
| Price history | No | No | Full interactive chart | Text summary only | Chart image in card |
| Israel shipping info | No | No | No | Exists but unreliable | Confidence-scored badge |
| Affiliate monetization | Google Shopping ads | Amazon directly | Affiliate links | Amazon Associates | Same, improved tracking |
| Platform | Mobile app / web | Amazon app only | Web + browser extension | Telegram only | Telegram + WhatsApp + Web |
| Hebrew support | Yes | No | No | Yes (via translator) | Improved Hebrew UX |
| Deal quality indicator | No | No | Yes (price vs. history) | Text-based deal label | Visual badge + chart |

## Implementation Notes for Key Features

### Photo Annotation with Overlays

The existing `image_annotator.py` takes a practical approach: it adds a numbered legend strip below the image rather than overlaying on detected products. This was a deliberate choice because "AI-generated bounding boxes" are unreliable (comment in code). The vision prompt in `providers/base.py` asks for `bbox` as percentage coordinates, but quality varies by provider.

**Recommended approach:**
1. Validate bbox accuracy across providers (GPT-4o, Claude, Gemini) with a test set of 20-30 product photos
2. If bbox accuracy >80%: render semi-transparent colored overlays with numbered labels at bbox positions
3. If bbox accuracy <80%: keep legend strip, but add numbered circles at approximate center-of-bbox positions
4. Always include the legend strip as fallback for accessibility

### Price History Chart Generation

No chart library is currently in `requirements.txt`. Options:
- **Pillow (already installed):** Can draw simple line charts manually. No axes/labels built-in. Acceptable for a minimal "sparkline" showing price trend.
- **matplotlib:** Full charting library. Heavy dependency (~30MB). Produces publication-quality charts. Overkill for a chat bot thumbnail.

**Recommended approach:** Use Pillow to render a compact price chart (400x200px): horizontal line for current price, shaded band for 90-day range, dots for low/high points. Keep it simple -- this appears as a small image in a chat message, not a dashboard. If more sophistication is needed later, add matplotlib.

### WhatsApp Business API Integration

Critical compliance considerations (as of Jan 2026):
- Meta banned "mainstream chatbot" behavior on WhatsApp Business API
- Bot must use structured interactions: buttons, list messages, template messages
- All outbound messages require pre-approved templates
- Per-message pricing: ~$0.01 (utility) to ~$0.03 (marketing) per message
- User must opt-in before receiving any messages
- 24-hour conversation window: free replies within 24h of user message

**Recommended approach:** Photo analysis responses fit the "utility" category. Use interactive list messages for product results (WhatsApp supports up to 10 list items with titles). Keep all interactions button-driven, never free-text conversational.

### Web Application

No frontend code exists. The FastAPI server (`api_server.py`) only handles Israel scraper API.

**Recommended approach:** Start minimal:
1. Single-page upload form (HTML + vanilla JS, no framework)
2. POST photo to FastAPI endpoint, return JSON results
3. Render results client-side with product cards, price history, shipping badges
4. Add shareable result URLs (e.g., `/results/<search_id>`) for SEO
5. Consider a lightweight framework (htmx or Alpine.js) only if interactivity needs grow

## Sources

- [Visual Search and the New Rules of Retail Discovery in 2026 - Imagga](https://imagga.com/blog/visual-search-and-the-new-rules-of-retail-discovery-in-2026/)
- [Google Lens - Search What You See](https://lens.google/howlensworks/)
- [Google Lens now offers shopping product details](https://blog.google/products/shopping/visual-search-lens-shopping/)
- [Amazon StyleSnap - Amazon Science](https://www.amazon.science/latest-news/the-science-behind-amazons-new-stylesnap-for-home-feature)
- [WhatsApp Business API Compliance 2026](https://gmcsco.com/your-simple-guide-to-whatsapp-api-compliance-2026/)
- [Building WhatsApp Business Bots with the Official API - DEV Community](https://dev.to/achiya-automation/building-whatsapp-business-bots-with-the-official-api-architecture-webhooks-and-automation-1ce4)
- [CamelCamelCamel vs Keepa comparison](https://goaura.com/blog/camelcamelcamel-vs-keepa)
- [Amazon launches free shipping to Israel - Times of Israel](https://www.timesofisrael.com/amazon-launches-free-shipping-to-israel-with-numerous-caveats/)
- [Israel Shipping & Selling eCommerce Guide - Easyship](https://www.easyship.com/blog/shipping-to-israel)
- [Pillow ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)
- [AI-Powered Visual Search Solutions for E-commerce - Layers](https://www.uselayers.com/articles/ai-visual-search-ecommerce-shopify)

---
*Feature research for: AI-powered visual product search bot with affiliate monetization*
*Researched: 2026-03-13*
