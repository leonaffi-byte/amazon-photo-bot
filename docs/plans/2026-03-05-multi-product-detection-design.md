# Multi-Product Detection Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a photo contains multiple products, let the user choose which one to search for. If the user circled a product or added a caption, skip the picker and search directly.

**Architecture:** AI-only bounding boxes via modified vision prompt. Single AI call returns an array of products with approximate bbox coordinates. PIL draws numbered colored boxes on the compressed image. Existing flow unchanged for single-product photos.

## AI Prompt Changes

The shared `SYSTEM_PROMPT` in `providers/base.py` changes to request a `{"products": [...]}` response:

- Each product object keeps the same fields as today (product_name, brand, category, key_features, amazon_search_query, alternative_query, confidence, notes)
- Each product gains a `bbox` field: `[x%, y%, w%, h%]` (percentage-based, relative to image dimensions)
- Prompt instruction: "If the user has drawn a circle, arrow, or highlight on the image, identify ONLY the highlighted product. If a text hint is provided, prioritize matching that."
- Single-product photos: AI returns `{"products": [{ ...one product... }]}`
- Multi-product photos: AI returns `{"products": [{ ...product1... }, { ...product2... }]}`

## Parsing & Backward Compatibility

`parse_json_response` in `providers/base.py`:
- If response is a dict with `"products"` key → extract the array
- If response is a single dict (no `"products"`) → wrap in `[response]`
- If response is an array → use directly

## Data Structures

**`ProductInfo`** (image_analyzer.py): Add optional `bbox: tuple[float, float, float, float] | None = None`

**`ProviderResult`** (providers/base.py): Add `products: list[dict] = field(default_factory=list)` to hold all detected products from multi-product responses. Existing single-product fields populated from first/best product. `to_product_info()` still returns a single `ProductInfo` (the chosen one). New method `to_product_info_list() -> list[ProductInfo]` returns all.

## User Flow

### Multiple products, no hint:
1. User sends plain photo
2. AI returns 2+ products with bounding boxes
3. Bot annotates compressed image with numbered colored boxes (PIL)
4. Bot sends annotated image + buttons: `"1: Nike Air Max"`, `"2: JBL Speaker"`, etc.
5. User taps button → bot stores chosen ProductInfo in session → proceeds to filter keyboard → Amazon search

### Single product or user provided hint:
1. User sends photo (with circle drawn or caption text)
2. AI returns 1 product
3. Bot proceeds directly to identification card + filter keyboard (same as today)

## Image Annotation

New module `image_annotator.py`:
- `annotate_products(image_bytes: bytes, products: list[ProductInfo]) -> bytes`
- Draws on the already-compressed image (max 1024px)
- Color cycle: red, blue, green, orange, purple
- Each box: 3px colored border + number label in a small colored rectangle at top-left corner
- Returns JPEG bytes

## Callback & Session

- New callback prefix: `CB_PICK_PRODUCT = "pick:"` + product index
- Session stores `all_detected_products: list[ProductInfo]` when multiple products found
- On pick, session.product_info is set to the chosen product and normal flow continues

## Locale Keys

- `pick_product`: "I found {count} products in your photo. Which one do you want to search for?"
- `pick_product_hint`: "Tip: You can circle a product or add a caption to skip this step."

## Files Changed

| File | Change |
|------|--------|
| `providers/base.py` | Prompt, parse_json_response, ProviderResult.products field |
| `image_analyzer.py` | ProductInfo.bbox field |
| `providers/manager.py` | No structural change — winner selection unchanged |
| `bot_core.py` | Multi-product check after analysis, picker flow, CB_PICK_PRODUCT handler |
| `formatter.py` | product_picker() method |
| `locale/*.json` | pick_product, pick_product_hint keys |
| **New:** `image_annotator.py` | annotate_products() using PIL |

## Edge Cases

- 1 product → skip picker (today's flow)
- 0 products → error message (today's flow)
- bbox outside image bounds → clamp to edges
- New photo replaces session (existing behavior)
- Compare mode with multi-product → pick product first, then compare providers
