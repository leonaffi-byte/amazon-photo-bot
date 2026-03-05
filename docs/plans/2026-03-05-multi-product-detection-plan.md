# Multi-Product Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a photo contains multiple products, detect them all with bounding boxes, let the user pick which one to search. If user circled a product or added a caption, skip the picker.

**Architecture:** Modified AI prompt returns `{"products": [...]}` with bbox coordinates. PIL annotates the compressed image with numbered colored boxes. New picker flow in bot_core.py before the existing filter→search flow. Single-product photos flow unchanged.

**Tech Stack:** Python 3.11+, PIL/Pillow (already installed), existing vision providers (GPT-4o, Claude, Gemini).

**Design doc:** docs/plans/2026-03-05-multi-product-detection-design.md

---

## Task 1: Add `bbox` field to `ProductInfo`

**Files:**
- Modify: `image_analyzer.py:11-29`

Add an optional `bbox` field to the `ProductInfo` dataclass:

```python
@dataclass
class ProductInfo:
    """Structured product identification result passed to Amazon search."""
    product_name: str
    brand: Optional[str]
    category: str
    key_features: list[str]
    amazon_search_query: str
    alternative_query: str
    confidence: str       # high | medium | low
    notes: str            # includes provider name in compare/best mode
    bbox: Optional[tuple[float, float, float, float]] = None  # (x%, y%, w%, h%)
```

The `__post_init__` stays the same — no validation needed for bbox (it's optional).

**Commit:** `feat: add bbox field to ProductInfo`

---

## Task 2: Modify AI prompt for multi-product detection

**Files:**
- Modify: `providers/base.py:50-78`

Replace `SYSTEM_PROMPT` and `USER_PROMPT`:

```python
SYSTEM_PROMPT = """You are an expert product identification assistant.
Analyse the product photo and return ONLY valid JSON — no markdown, no prose.

Return a JSON object with a "products" array. Each element uses this schema:
{
  "products": [
    {
      "product_name":          "concise name — brand + model if visible",
      "brand":                 "brand name or null",
      "category":              "Amazon browse category (e.g. Electronics, Kitchen)",
      "key_features":          ["up to 5 most distinctive features"],
      "amazon_search_query":   "≤100-char optimised Amazon keyword search string",
      "alternative_query":     "broader fallback search if main query fails",
      "confidence":            "high | medium | low",
      "notes":                 "brief note on identification quality",
      "bbox":                  [x_percent, y_percent, width_percent, height_percent]
    }
  ]
}

Rules:
- If the photo contains ONE product, return exactly one element in "products"
- If the photo contains MULTIPLE distinct products, return one element per product (up to 6)
- bbox: approximate bounding box as percentages (0-100) of image width/height. [x, y] is the top-left corner.
- amazon_search_query: most specific terms first, include model# if visible
- If brand unknown, omit it from search query to avoid zero results
- key_features: focus on what distinguishes this from similar products
- If a person is wearing or holding the product, describe ONLY the product — ignore the person entirely
- Never refuse: if the photo shows a person, identify the clothing/accessory/item they are wearing
- If the user has drawn a circle, arrow, highlight, or annotation on the image, identify ONLY the highlighted/circled product and return just that one element
"""

USER_PROMPT = (
    "Analyse this product photo and return the JSON with a \"products\" array. "
    "Focus ONLY on the PRODUCT or ITEM itself (clothing, accessory, gadget, object) — "
    "not on any person who may appear in the photo. "
    "Identify what each item is so a shopper can find it on Amazon."
)
```

If a `context_hint` is provided (user caption), append it: the existing per-provider code already does this — no change needed there.

**Commit:** `feat: update AI prompt for multi-product detection with bounding boxes`

---

## Task 3: Update `parse_json_response` and `ProviderResult`

**Files:**
- Modify: `providers/base.py:93-171`

### 3a: Add `products` field to `ProviderResult`

Add after line 109 (`cost_usd`):

```python
    # Multi-product: raw product dicts from AI response (when multiple detected)
    products_raw: list[dict] = field(default_factory=list)
```

Update `to_product_info` to pass `bbox`:

```python
    def to_product_info(self):
        """Convert to the ProductInfo used by amazon_search."""
        from image_analyzer import ProductInfo
        return ProductInfo(
            product_name=self.product_name,
            brand=self.brand,
            category=self.category,
            key_features=self.key_features,
            amazon_search_query=self.amazon_search_query,
            alternative_query=self.alternative_query,
            confidence=self.confidence,
            notes=f"[{self.provider_name}] {self.notes}",
            bbox=self.bbox,
        )
```

Add `bbox` field (after `cost_usd`, before `products_raw`):

```python
    bbox: Optional[tuple[float, float, float, float]] = None  # (x%, y%, w%, h%)
```

Add new method:

```python
    def to_product_info_list(self) -> list:
        """Convert all detected products to ProductInfo list."""
        from image_analyzer import ProductInfo
        if not self.products_raw:
            return [self.to_product_info()]
        result = []
        for p in self.products_raw:
            bbox_raw = p.get("bbox")
            bbox = tuple(bbox_raw) if bbox_raw and len(bbox_raw) == 4 else None
            result.append(ProductInfo(
                product_name=p.get("product_name", "Unknown Product"),
                brand=p.get("brand"),
                category=p.get("category", ""),
                key_features=p.get("key_features", [])[:5],
                amazon_search_query=p.get("amazon_search_query", ""),
                alternative_query=p.get("alternative_query", ""),
                confidence=p.get("confidence", "medium"),
                notes=f"[{self.provider_name}] {p.get('notes', '')}",
                bbox=bbox,
            ))
        return result
```

### 3b: Update `parse_json_response`

Replace the function to handle the new `{"products": [...]}` format:

```python
def parse_json_response(raw: str, provider_name: str) -> dict:
    """
    Parse JSON from a model response, handling markdown fences gracefully.
    Supports both single-object and {"products": [...]} multi-product format.
    Always returns a dict with a "products" key containing a list.
    Raises ValueError on parse failure.
    """
    text = raw.strip()
    parsed = None

    # Try 1: Direct parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try 2: Extract from markdown fence
    if parsed is None:
        fence_match = _re.search(r"```(?:json)?\s*\n?([\s\S]+?)\n?```", text)
        if fence_match:
            try:
                parsed = json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

    # Try 3: Find first {...} block
    if parsed is None:
        brace_match = _re.search(r"\{[\s\S]+\}", text)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

    if parsed is None:
        logger.error("[%s] Non-JSON response: %s", provider_name, raw[:300])
        raise ValueError(f"[{provider_name}] JSON parse error: could not extract valid JSON")

    # Normalize to {"products": [...]} format
    if isinstance(parsed, list):
        return {"products": parsed}
    if isinstance(parsed, dict):
        if "products" in parsed and isinstance(parsed["products"], list):
            return parsed
        # Single product dict (backward compat) — wrap in products array
        return {"products": [parsed]}

    raise ValueError(f"[{provider_name}] Unexpected JSON type: {type(parsed)}")
```

### 3c: Update provider `_build_result` calls

Each provider builds a `ProviderResult` from the parsed dict. They currently do `data = parse_json_response(...)` and read fields like `data["product_name"]`. Now `parse_json_response` returns `{"products": [...]}`, so providers need to read from the first product but store all products.

Search all provider files for the pattern where they construct `ProviderResult`. Each provider has something like:

```python
data = parse_json_response(raw_text, self.name)
return ProviderResult(
    provider_name=self.name,
    model_id=model_id,
    product_name=data.get("product_name", "Unknown"),
    ...
)
```

This needs to change to:

```python
data = parse_json_response(raw_text, self.name)
products = data["products"]
first = products[0]  # primary product for single-product fields
bbox_raw = first.get("bbox")
bbox = tuple(bbox_raw) if bbox_raw and len(bbox_raw) == 4 else None
return ProviderResult(
    provider_name=self.name,
    model_id=model_id,
    product_name=first.get("product_name", "Unknown"),
    brand=first.get("brand"),
    category=first.get("category", ""),
    key_features=first.get("key_features", [])[:5],
    amazon_search_query=first.get("amazon_search_query", ""),
    alternative_query=first.get("alternative_query", ""),
    confidence=first.get("confidence", "medium"),
    notes=first.get("notes", ""),
    ...existing latency/token/cost fields stay the same...
    bbox=bbox,
    products_raw=products,
)
```

Apply this pattern to ALL provider files:
- `providers/openai_provider.py`
- `providers/anthropic_provider.py`
- `providers/gemini_provider.py`
- `providers/groq_provider.py`
- `providers/azure_openai_provider.py`
- `providers/openrouter_provider.py`

Each provider file has its own `_build_result` or inline result construction — find the exact location in each and update. The key change in each is:
1. `data = parse_json_response(...)` now returns `{"products": [...]}`
2. Extract `products = data["products"]` and `first = products[0]`
3. Read all fields from `first` instead of `data`
4. Pass `bbox=bbox, products_raw=products` to `ProviderResult`

**Commit:** `feat: update ProviderResult and parse_json_response for multi-product`

---

## Task 4: Create `image_annotator.py`

**Files:**
- Create: `image_annotator.py`

```python
"""Annotate product images with numbered colored bounding boxes."""
from __future__ import annotations

import io
from PIL import Image, ImageDraw, ImageFont
from image_analyzer import ProductInfo

# Color cycle for bounding boxes (RGB)
_COLORS = [
    (220, 50, 50),    # red
    (50, 100, 220),   # blue
    (50, 180, 50),    # green
    (230, 150, 30),   # orange
    (150, 50, 200),   # purple
    (0, 180, 180),    # teal
]

_BOX_WIDTH = 3
_LABEL_PAD = 4
_FONT_SIZE = 18


def annotate_products(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Draw numbered colored bounding boxes on the image.

    Args:
        image_bytes: JPEG/PNG image bytes.
        products: List of ProductInfo with bbox fields set.

    Returns:
        Annotated JPEG image bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for i, product in enumerate(products):
        if not product.bbox:
            continue

        color = _COLORS[i % len(_COLORS)]
        bx, by, bw, bh = product.bbox

        # Convert percentages to pixels, clamping to image bounds
        x1 = max(0, int(bx / 100 * w))
        y1 = max(0, int(by / 100 * h))
        x2 = min(w, int((bx + bw) / 100 * w))
        y2 = min(h, int((by + bh) / 100 * h))

        # Draw bounding box
        for offset in range(_BOX_WIDTH):
            draw.rectangle(
                [x1 + offset, y1 + offset, x2 - offset, y2 - offset],
                outline=color,
            )

        # Draw number label
        label = str(i + 1)
        bbox = font.getbbox(label)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        label_bg = [x1, y1 - th - _LABEL_PAD * 2, x1 + tw + _LABEL_PAD * 2, y1]
        if label_bg[1] < 0:
            # Put label inside box if no room above
            label_bg = [x1, y1, x1 + tw + _LABEL_PAD * 2, y1 + th + _LABEL_PAD * 2]
        draw.rectangle(label_bg, fill=color)
        draw.text(
            (label_bg[0] + _LABEL_PAD, label_bg[1] + _LABEL_PAD),
            label, fill=(255, 255, 255), font=font,
        )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
```

**Commit:** `feat: add image_annotator module for multi-product bounding boxes`

---

## Task 5: Add locale keys for product picker

**Files:**
- Modify: `locale/en.json`
- Modify: `locale/he.json`
- Modify: `locale/ru.json`

Add to each locale file:

**English:**
```json
"pick_product": "I found {count} products in your photo. Which one do you want to search for?",
"pick_product_hint": "Tip: Circle a product or add a caption to search it directly."
```

**Hebrew:**
```json
"pick_product": "מצאתי {count} מוצרים בתמונה שלך. איזה מהם תרצה לחפש?",
"pick_product_hint": "טיפ: הקף מוצר בעיגול או הוסף כיתוב כדי לחפש ישירות."
```

**Russian:**
```json
"pick_product": "Я нашёл {count} товаров на фото. Какой вы хотите найти?",
"pick_product_hint": "Совет: Обведите товар или добавьте подпись, чтобы искать сразу."
```

**Commit:** `feat: add product picker locale keys`

---

## Task 6: Add `product_picker()` to `Formatter`

**Files:**
- Modify: `formatter.py`

Add after the `language_picker` method:

```python
    def product_picker(self, products: list) -> str:
        """Format the multi-product picker message."""
        from image_analyzer import ProductInfo
        count = len(products)
        lines = [self._esc(t("pick_product", lang=self.lang, count=count))]
        lines.append("")
        for i, p in enumerate(products):
            name = p.product_name if isinstance(p, ProductInfo) else str(p)
            lines.append(f"{i + 1}\\. {self._bold(self._esc(name))}")
        lines.append("")
        lines.append(self._italic(self._esc(t("pick_product_hint", lang=self.lang))))
        return "\n".join(lines)
```

**Commit:** `feat: add product_picker formatter method`

---

## Task 7: Update `bot_core.py` — multi-product picker flow

**Files:**
- Modify: `bot_core.py`

### 7a: Add new callback prefix

At the top with other CB_ constants (around line 48):

```python
CB_PICK_PRODUCT    = "pick:"           # + product index
```

### 7b: Add `all_detected_products` to `UserSession`

In the `UserSession` dataclass, add:

```python
    all_detected_products: list = field(default_factory=list)  # list[ProductInfo] when multi-product
```

### 7c: Modify `handle_photo` — after analysis, check for multi-product

After the analysis section (around line 968, where `session.chosen_result = winner`), replace the block from `session.chosen_result = winner` through the `edit_text` call that shows the identification card with filter buttons:

```python
        # Check if multiple products detected
        detected_products = winner.to_product_info_list()

        if len(detected_products) > 1 and not context_hint:
            # Multi-product: annotate image and show picker
            session.all_detected_products = detected_products
            from image_annotator import annotate_products
            annotated_bytes = annotate_products(image_bytes, detected_products)

            fmt = self._fmt(lang)
            picker_text = fmt.product_picker(detected_products)
            picker_buttons: list[list[Button]] = []
            for i, p in enumerate(detected_products):
                short_name = p.product_name[:30]
                picker_buttons.append([Button(
                    label=f"{i + 1}: {short_name}",
                    callback_data=f"{CB_PICK_PRODUCT}{i}",
                )])

            # Delete loading message, send annotated photo with picker
            try:
                await self.adapter.delete_message(loading_ref)
            except Exception:
                pass
            await self.adapter.send_photo(
                chat_id,
                photo=annotated_bytes,
                caption=picker_text,
                buttons=picker_buttons,
            )
            return

        # Single product (or user provided hint) — normal flow
        session.chosen_result = winner
        session.product_info = winner.to_product_info()

        card_text = fmt.identification_card(winner, is_admin=is_admin)
        filter_buttons = self._filter_buttons(lang)
        await self.adapter.edit_text(loading_ref, text=card_text, buttons=filter_buttons)
```

### 7d: Add `CB_PICK_PRODUCT` handler in `handle_callback`

Add after the language selection block (after line 1022):

```python
        # ── Product picker (multi-product) ───────────────────────────────
        if data.startswith(CB_PICK_PRODUCT):
            try:
                idx = int(data[len(CB_PICK_PRODUCT):])
                chosen = session.all_detected_products[idx]
            except (ValueError, IndexError):
                await self.adapter.send_text(chat_id, fmt.error("err_generic"))
                return

            session.product_info = chosen
            # Create a minimal ProviderResult-like wrapper for the identification card
            # We already have the winner stored; use it but override with chosen product
            if session.all_provider_results:
                winner = session.all_provider_results[session.chosen_provider_idx] if session.chosen_provider_idx < len(session.all_provider_results) else session.all_provider_results[0]
                # Override winner fields with chosen product for display
                winner_copy = ProviderResult(
                    provider_name=winner.provider_name,
                    model_id=winner.model_id,
                    product_name=chosen.product_name,
                    brand=chosen.brand,
                    category=chosen.category,
                    key_features=chosen.key_features,
                    amazon_search_query=chosen.amazon_search_query,
                    alternative_query=chosen.alternative_query,
                    confidence=chosen.confidence,
                    notes=chosen.notes,
                    latency_ms=winner.latency_ms,
                    input_tokens=winner.input_tokens,
                    output_tokens=winner.output_tokens,
                    cost_usd=winner.cost_usd,
                )
                session.chosen_result = winner_copy
            else:
                session.chosen_result = None

            is_admin = await self._resolve_admin(session, user_id)
            if session.chosen_result:
                card_text = fmt.identification_card(session.chosen_result, is_admin=is_admin)
            else:
                card_text = self._esc(chosen.product_name)

            filter_buttons = self._filter_buttons(lang)
            await self.adapter.send_text(chat_id, card_text, buttons=filter_buttons)
            return
```

Add the import at the top of the file:

```python
from providers.base import ProviderResult
```

**Commit:** `feat: add multi-product picker flow to bot_core`

---

## Task 8: Verify `send_photo` supports `buttons` and `caption` in TelegramAdapter

**Files:**
- Modify: `adapters/telegram.py` (if needed)

Check the existing `send_photo` method signature. It should accept `caption` and `buttons`. If the current signature doesn't support buttons on photos, add support:

```python
async def send_photo(
    self,
    chat_id: str,
    photo: bytes | str,
    caption: str = "",
    buttons: list[list[Button]] | None = None,
) -> MessageRef:
    markup = self._build_keyboard(buttons) if buttons else None
    msg = await self._app.bot.send_photo(
        chat_id=int(chat_id),
        photo=photo,
        caption=caption,
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )
    return MessageRef(
        platform="telegram",
        chat_id=str(msg.chat_id),
        message_id=str(msg.message_id),
        raw=msg,
    )
```

**Commit:** `feat: ensure send_photo supports buttons parameter`

---

## Task 9: End-to-end test and deploy

Build and deploy to VPS:

```bash
ssh root@5.189.145.27 "cd /opt/amazon-photo-bot && git pull origin main && docker compose build && docker compose down && docker compose up -d"
```

Test scenarios:
1. **Single product photo** → should work exactly as before (identification card → filter → search)
2. **Multi-product photo** (e.g. photo of a desk with monitor, keyboard, mouse) → should show annotated image with numbered boxes and picker buttons
3. **Circled product** → user draws circle on photo → should detect only the circled product, skip picker
4. **Captioned photo** → user sends photo with text caption → should use caption as hint, return single product
5. **Pick a product** → tap button in picker → should show identification card for that product → filter → search

Check logs for errors:
```bash
ssh root@5.189.145.27 "docker logs amazon-photo-bot --tail 30"
```

**Commit:** `fix: resolve any integration issues from multi-product feature`

---

## Implementation Order Summary

| Task | Description |
|------|-------------|
| 1 | Add `bbox` to `ProductInfo` |
| 2 | Update AI prompt for multi-product |
| 3 | Update `parse_json_response`, `ProviderResult`, all providers |
| 4 | Create `image_annotator.py` |
| 5 | Add locale keys |
| 6 | Add `product_picker()` to Formatter |
| 7 | Update `bot_core.py` with picker flow |
| 8 | Verify `send_photo` supports buttons |
| 9 | Test and deploy |

**Critical path:** Tasks 1-3 (data layer) → Tasks 4-6 (annotation + UI) → Task 7 (wiring) → Task 8 (adapter check) → Task 9 (deploy + test).
