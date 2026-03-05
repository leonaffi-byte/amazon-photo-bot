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
