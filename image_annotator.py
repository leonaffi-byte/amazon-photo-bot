"""Annotate product images with a numbered product legend strip or overlay mode."""
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


# ── Overlay annotation helpers ─────────────────────────────────────────────────

def _is_bbox_reliable(bbox: tuple[float, float, float, float]) -> bool:
    """Return True if the bounding box represents a plausible product region.

    Args:
        bbox: (x%, y%, w%, h%) as percentages of image dimensions.

    Returns:
        True if the bbox is valid and within useful bounds.
    """
    x, y, w, h = bbox

    # Reject zero or negative dimensions
    if w <= 0 or h <= 0:
        return False

    # Reject area too small or too large.
    # w and h are percentages (0-100); area fraction = (w/100)*(h/100) = w*h/10000
    area_fraction = (w * h) / 10000.0  # fraction of total image area (0.0-1.0)
    if area_fraction < 0.01:  # less than 1% of image
        return False
    if area_fraction > 0.90:  # more than 90% of image
        return False

    # Reject out-of-bounds coordinates (allow 5% tolerance)
    if x < -5.0 or y < -5.0:
        return False
    if (x + w) > 105.0 or (y + h) > 105.0:
        return False

    return True


def _draw_overlay(
    img: Image.Image,
    bbox: tuple[float, float, float, float],
    color_rgb: tuple[int, int, int],
    number: int,
    opacity: float = 0.4,
) -> Image.Image:
    """Draw a semi-transparent colored rectangle overlay on the image.

    Args:
        img: PIL Image to annotate (will be converted to RGBA if needed).
        bbox: (x%, y%, w%, h%) bounding box as percentages.
        color_rgb: RGB color tuple for the overlay.
        number: Product number to display inside the overlay.
        opacity: Overlay opacity, 0.0-1.0.

    Returns:
        RGBA PIL Image with overlay composited.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    iw, ih = img.size
    x_pct, y_pct, w_pct, h_pct = bbox

    # Convert percentages to pixel coordinates
    x1 = int(x_pct / 100.0 * iw)
    y1 = int(y_pct / 100.0 * ih)
    x2 = int((x_pct + w_pct) / 100.0 * iw)
    y2 = int((y_pct + h_pct) / 100.0 * ih)

    # Clamp to image bounds
    x1 = max(0, min(x1, iw - 1))
    y1 = max(0, min(y1, ih - 1))
    x2 = max(x1 + 1, min(x2, iw))
    y2 = max(y1 + 1, min(y2, ih))

    alpha = int(opacity * 255)

    # Create transparent overlay layer
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw filled semi-transparent rectangle
    draw.rectangle([x1, y1, x2, y2], fill=(*color_rgb, alpha))

    # Draw product number centered in the rectangle
    label = str(number)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE
        )
    except (OSError, IOError):
        font = ImageFont.load_default()

    try:
        text_bbox = font.getbbox(label)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
    except AttributeError:
        tw, th = _FONT_SIZE, _FONT_SIZE

    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    draw.text(
        (cx - tw // 2, cy - th // 2),
        label,
        fill=(255, 255, 255, 255),
        font=font,
    )

    return Image.alpha_composite(img, overlay)


def annotate_with_overlays(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Annotate products using semi-transparent overlays when bboxes are reliable.

    When all products lack reliable bboxes, falls back to `annotate_products()`
    (the numbered legend strip). When at least one product has a reliable bbox,
    overlay mode is used and overlays are drawn only for reliable products.

    Args:
        image_bytes: JPEG/PNG image bytes.
        products: List of ProductInfo with optional bbox attributes.

    Returns:
        Annotated JPEG image bytes.
    """
    # Check which products have reliable bboxes
    reliable = [
        (i, p) for i, p in enumerate(products)
        if p.bbox is not None and _is_bbox_reliable(p.bbox)
    ]

    # Fall back to legend strip if no reliable bboxes
    if not reliable:
        return annotate_products(image_bytes, products)

    # Draw overlays for products with reliable bboxes
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_rgba = img.convert("RGBA")

    for orig_i, product in reliable:
        color = _COLORS[orig_i % len(_COLORS)]
        img_rgba = _draw_overlay(img_rgba, product.bbox, color, orig_i + 1)

    # Convert RGBA back to RGB and save as JPEG
    result_img = img_rgba.convert("RGB")
    buf = io.BytesIO()
    result_img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
