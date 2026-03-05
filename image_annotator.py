"""Annotate product images with numbered labels."""
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

_BADGE_RADIUS = 18
_FONT_SIZE = 22


def annotate_products(
    image_bytes: bytes,
    products: list[ProductInfo],
) -> bytes:
    """Place numbered circle badges on the image for each detected product.

    Uses bbox center if available, otherwise distributes labels evenly.

    Args:
        image_bytes: JPEG/PNG image bytes.
        products: List of ProductInfo with optional bbox fields.

    Returns:
        Annotated JPEG image bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE
        )
    except (OSError, IOError):
        font = ImageFont.load_default()

    n = len(products)
    for i, product in enumerate(products):
        color = _COLORS[i % len(_COLORS)]

        # Determine badge position
        if product.bbox and _is_valid_bbox(product.bbox):
            bx, by, bw, bh = product.bbox
            cx = int((bx + bw / 2) / 100 * w)
            cy = int((by + bh / 2) / 100 * h)
        else:
            # Distribute labels evenly across the image
            cols = min(n, 3)
            row = i // cols
            col = i % cols
            rows = (n + cols - 1) // cols
            cx = int(w * (col + 0.5) / cols)
            cy = int(h * (row + 0.5) / rows)

        # Clamp to image bounds with padding
        r = _BADGE_RADIUS
        cx = max(r + 2, min(w - r - 2, cx))
        cy = max(r + 2, min(h - r - 2, cy))

        # Draw shadow
        draw.ellipse(
            [cx - r + 2, cy - r + 2, cx + r + 2, cy + r + 2],
            fill=(0, 0, 0, 100),
        )

        # Draw filled circle badge
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=color + (230,),
            outline=(255, 255, 255, 255),
            width=3,
        )

        # Draw number
        label = str(i + 1)
        bbox = font.getbbox(label)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (cx - tw // 2, cy - th // 2 - 2),
            label,
            fill=(255, 255, 255, 255),
            font=font,
        )

    # Convert back to RGB for JPEG output
    img_rgb = img.convert("RGB")
    buf = io.BytesIO()
    img_rgb.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _is_valid_bbox(bbox: tuple) -> bool:
    """Check if bbox is meaningful (not covering nearly the entire image)."""
    x, y, w, h = bbox
    # Reject if it covers more than 80% of the image in either dimension
    if w > 80 or h > 80:
        return False
    # Reject if too small
    if w < 3 or h < 3:
        return False
    return True
