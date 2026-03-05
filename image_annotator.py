"""Annotate product images with a numbered product legend strip."""
from __future__ import annotations

import io
from PIL import Image, ImageDraw, ImageFont
from image_analyzer import ProductInfo

# Color cycle for labels (RGB)
_COLORS = [
    (220, 50, 50),    # red
    (50, 100, 220),   # blue
    (50, 180, 50),    # green
    (230, 150, 30),   # orange
    (150, 50, 200),   # purple
    (0, 180, 180),    # teal
]

_BADGE_RADIUS = 16
_FONT_SIZE = 18
_STRIP_HEIGHT = 44
_STRIP_PADDING = 10
_NAME_FONT_SIZE = 15


def annotate_products(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Add a numbered product legend strip at the bottom of the image.

    Each product gets a colored circle with its number and a truncated name.
    This avoids relying on unreliable AI-generated bounding boxes.

    Args:
        image_bytes: JPEG/PNG image bytes.
        products: List of ProductInfo.

    Returns:
        Annotated JPEG image bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    n = len(products)
    if n <= 1:
        # Single product — no annotation needed
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    # Load fonts
    try:
        badge_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE
        )
        name_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", _NAME_FONT_SIZE
        )
    except (OSError, IOError):
        badge_font = ImageFont.load_default()
        name_font = badge_font

    # Calculate strip height based on number of products
    row_height = _STRIP_HEIGHT
    max_per_row = max(1, w // 200)  # roughly 200px per item
    rows = (n + max_per_row - 1) // max_per_row
    total_strip_h = rows * row_height + _STRIP_PADDING

    # Create new image with strip at bottom
    new_h = h + total_strip_h
    canvas = Image.new("RGB", (w, new_h), (30, 30, 30))
    canvas.paste(img, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # Draw each product entry in the strip
    for i, product in enumerate(products):
        row = i // max_per_row
        col = i % max_per_row
        items_in_row = min(max_per_row, n - row * max_per_row)
        cell_w = w // items_in_row

        x_start = col * cell_w + _STRIP_PADDING
        y_center = h + row * row_height + row_height // 2 + _STRIP_PADDING // 2

        color = _COLORS[i % len(_COLORS)]
        r = _BADGE_RADIUS

        # Draw circle badge
        cx = x_start + r
        cy = y_center
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=color,
            outline=(255, 255, 255),
            width=2,
        )

        # Draw number
        label = str(i + 1)
        bbox = badge_font.getbbox(label)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (cx - tw // 2, cy - th // 2 - 1),
            label,
            fill=(255, 255, 255),
            font=badge_font,
        )

        # Draw truncated product name
        name = product.product_name if isinstance(product, ProductInfo) else str(product)
        max_name_w = cell_w - 2 * r - 3 * _STRIP_PADDING
        # Truncate name to fit
        display_name = name
        while name_font.getlength(display_name) > max_name_w and len(display_name) > 5:
            display_name = display_name[:-2] + "…"

        text_x = cx + r + _STRIP_PADDING
        text_y = cy - _NAME_FONT_SIZE // 2
        draw.text(
            (text_x, text_y),
            display_name,
            fill=(240, 240, 240),
            font=name_font,
        )

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
