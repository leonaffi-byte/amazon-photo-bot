"""
Tests for image_annotator.py — overlay annotation mode.

Covers:
  - _is_bbox_reliable: valid/invalid bbox classification
  - _draw_overlay: colored rectangle drawing on images
  - annotate_with_overlays: main entry point with fallback logic
"""
from __future__ import annotations

import io
import pytest
from PIL import Image

from image_annotator import (
    _is_bbox_reliable,
    _draw_overlay,
    annotate_with_overlays,
    annotate_products,
)
from image_analyzer import ProductInfo


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_white_image(w: int = 200, h: int = 200) -> bytes:
    """Return JPEG bytes of a solid white image."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def make_product(
    name: str = "Test Product",
    bbox: tuple | None = None,
) -> ProductInfo:
    return ProductInfo(
        product_name=name,
        brand=None,
        category="Electronics",
        key_features=["Feature A"],
        amazon_search_query="test product",
        alternative_query="product",
        confidence="high",
        notes="test note",
        bbox=bbox,
    )


# ── _is_bbox_reliable ─────────────────────────────────────────────────────────

class TestIsBboxReliable:
    def test_overlay_normal_bbox_reliable(self):
        """Standard bbox in the middle of the image is reliable."""
        assert _is_bbox_reliable((25.0, 25.0, 50.0, 50.0)) is True

    def test_overlay_zero_area_rejected(self):
        """(0, 0, 0, 0) has zero area — rejected."""
        assert _is_bbox_reliable((0.0, 0.0, 0.0, 0.0)) is False

    def test_overlay_too_large_rejected(self):
        """bbox covering more than 90% of image area rejected."""
        # area = 95 * 95 = 9025 > 90% of 10000
        assert _is_bbox_reliable((0.0, 0.0, 95.0, 95.0)) is False

    def test_overlay_too_small_rejected(self):
        """bbox with area < 1% of image rejected."""
        # area = 0.5 * 0.5 = 0.25 < 1.0
        assert _is_bbox_reliable((0.0, 0.0, 0.5, 0.5)) is False

    def test_overlay_out_of_bounds_negative_x_rejected(self):
        """Negative x coord rejected."""
        assert _is_bbox_reliable((-10.0, 0.0, 50.0, 50.0)) is False

    def test_overlay_out_of_bounds_negative_y_rejected(self):
        """Negative y coord beyond tolerance rejected."""
        assert _is_bbox_reliable((0.0, -10.0, 50.0, 50.0)) is False

    def test_overlay_bbox_at_edge_reliable(self):
        """Small valid bbox at edge of image is still reliable."""
        assert _is_bbox_reliable((80.0, 80.0, 15.0, 15.0)) is True

    def test_overlay_exactly_at_bounds_rejected(self):
        """bbox sum exceeding 105 is rejected."""
        # x + w = 10 + 100 = 110 > 105
        assert _is_bbox_reliable((10.0, 0.0, 100.0, 50.0)) is False


# ── _draw_overlay ─────────────────────────────────────────────────────────────

class TestDrawOverlay:
    def test_overlay_draw_changes_pixels(self):
        """Drawing overlay on white image produces non-white pixels in bbox region."""
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        result = _draw_overlay(img, (10.0, 10.0, 50.0, 50.0), (220, 50, 50), 1)
        # Get a pixel inside the bbox region (center: x=35, y=35)
        rgb = result.convert("RGB").getpixel((35, 35))
        # Should be different from pure white (255, 255, 255)
        assert rgb != (255, 255, 255)

    def test_overlay_draw_returns_rgba(self):
        """_draw_overlay returns an RGBA image."""
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        result = _draw_overlay(img, (10.0, 10.0, 50.0, 50.0), (50, 100, 220), 2)
        assert result.mode == "RGBA"

    def test_overlay_draw_rgba_input_also_works(self):
        """_draw_overlay also works with RGBA input image."""
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        result = _draw_overlay(img, (20.0, 20.0, 40.0, 40.0), (50, 180, 50), 3)
        assert result.mode == "RGBA"

    def test_overlay_draw_composited_correctly(self):
        """After drawing, the overlay region has altered alpha-composited colors."""
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        result = _draw_overlay(img, (0.0, 0.0, 100.0, 100.0), (220, 50, 50), 1, opacity=0.5)
        # Center pixel should be a blend of red+white, not pure white or pure red
        px = result.convert("RGB").getpixel((50, 50))
        # Red channel should be significantly above 0, but not 255
        assert px[0] > 200  # red is strong in blend
        assert px[1] < 255  # green channel lowered due to red blend


# ── annotate_with_overlays ────────────────────────────────────────────────────

class TestAnnotateWithOverlays:
    def test_overlay_draws_overlay_when_bbox_reliable(self):
        """Products with valid bbox produce image different from original."""
        original = make_white_image(200, 200)
        products = [make_product("Product 1", bbox=(10.0, 10.0, 40.0, 40.0))]
        result = annotate_with_overlays(original, products)
        # Result is JPEG bytes
        assert result != original

    def test_overlay_fallback_no_bbox(self):
        """Products with no bbox fall back to legend strip (image height increases)."""
        original = make_white_image(200, 200)
        products = [
            make_product("Product 1", bbox=None),
            make_product("Product 2", bbox=None),
        ]
        result = annotate_with_overlays(original, products)
        result_img = Image.open(io.BytesIO(result))
        # Legend strip adds height
        assert result_img.height > 200

    def test_overlay_mixed_products_only_draws_reliable(self):
        """Some products with bbox, some without — overlay mode active, no strip added."""
        original = make_white_image(200, 200)
        products = [
            make_product("Product 1", bbox=(10.0, 10.0, 40.0, 40.0)),
            make_product("Product 2", bbox=None),
        ]
        result = annotate_with_overlays(original, products)
        result_img = Image.open(io.BytesIO(result))
        # Height should not increase (no legend strip added for mixed case)
        assert result_img.height == 200

    def test_overlay_single_product_draws_overlay(self):
        """Single product with bbox still draws overlay (unlike annotate_products which skips)."""
        original = make_white_image(200, 200)
        products = [make_product("Product 1", bbox=(10.0, 10.0, 80.0, 80.0))]
        result = annotate_with_overlays(original, products)
        result_img = Image.open(io.BytesIO(result))
        # Height unchanged — no strip, just overlay
        assert result_img.height == 200
        # But image content differs
        assert result != original

    def test_overlay_returns_jpeg(self):
        """Output bytes start with JPEG magic bytes (FF D8 FF)."""
        original = make_white_image(200, 200)
        products = [make_product("Product 1", bbox=(10.0, 10.0, 50.0, 50.0))]
        result = annotate_with_overlays(original, products)
        assert result[:3] == b"\xff\xd8\xff"
