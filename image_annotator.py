"""Annotate product images with numbered colored overlays."""
from __future__ import annotations

import io
import math
from PIL import Image, ImageDraw, ImageFont
from image_analyzer import ProductInfo

# Color cycle for overlays (RGB)
_COLORS = [
    (220, 50, 50),    # red
    (50, 100, 220),   # blue
    (50, 180, 50),    # green
    (230, 150, 30),   # orange
    (150, 50, 200),   # purple
    (0, 180, 180),    # teal
]

_OVERLAY_ALPHA = 77       # ~30% opacity (0-255)
_BADGE_RADIUS = 16
_FONT_SIZE = 20


def annotate_products(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Draw semi-transparent colored overlays on each product region.

    Args:
        image_bytes: JPEG/PNG image bytes.
        products: List of ProductInfo with bbox fields set.

    Returns:
        Annotated JPEG image bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    w, h = img.size

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE
        )
    except (OSError, IOError):
        font = ImageFont.load_default()

    badge_positions = []

    for i, product in enumerate(products):
        if not product.bbox:
            continue

        color = _COLORS[i % len(_COLORS)]
        bx, by, bw, bh = product.bbox

        # Convert percentages to pixels
        x1 = max(0, int(bx / 100 * w))
        y1 = max(0, int(by / 100 * h))
        x2 = min(w, int((bx + bw) / 100 * w))
        y2 = min(h, int((by + bh) / 100 * h))

        # Draw semi-transparent fill
        fill_color = color + (_OVERLAY_ALPHA,)
        draw_overlay.rectangle([x1, y1, x2, y2], fill=fill_color)

        # Also draw a thin border for definition
        border_color = color + (180,)
        draw_overlay.rectangle([x1, y1, x2, y2], outline=border_color, width=2)

        # Calculate badge center position
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        badge_positions.append((i, cx, cy, color))

    # Composite overlay onto original
    img = Image.alpha_composite(img, overlay)

    # Draw badges on top (fully opaque)
    draw_final = ImageDraw.Draw(img)
    for i, cx, cy, color in badge_positions:
        r = _BADGE_RADIUS
        # Draw filled circle badge
        draw_final.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=color + (240,),
            outline=(255, 255, 255, 255),
            width=2,
        )
        # Draw number
        label = str(i + 1)
        bbox = font.getbbox(label)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw_final.text(
            (cx - tw // 2, cy - th // 2 - 1),
            label,
            fill=(255, 255, 255, 255),
            font=font,
        )

    # Convert back to RGB for JPEG output
    img_rgb = img.convert("RGB")
    buf = io.BytesIO()
    img_rgb.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
