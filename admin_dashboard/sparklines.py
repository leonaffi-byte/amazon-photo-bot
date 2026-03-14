"""
admin_dashboard/sparklines.py — Server-side SVG sparkline generation.

Generates inline SVG polyline charts from a list of integer values.
Used to display 7-day search trends on the dashboard stat cards.
"""
from __future__ import annotations


def points_to_svg(
    values: list[int],
    width: int = 60,
    height: int = 20,
) -> str:
    """
    Convert a list of integer values to an inline SVG sparkline.

    Args:
        values: List of data points (e.g., daily search counts).
                Empty list or all-zeros produce a flat mid-line SVG.
        width:  SVG width in pixels (default 60).
        height: SVG height in pixels (default 20).

    Returns:
        A string of SVG markup starting with '<svg'.
    """
    # Handle empty or single-value lists — flat line at mid-height
    if not values or len(values) == 1:
        mid_y = height / 2
        if not values:
            pts = f"0,{mid_y} {width},{mid_y}"
        else:
            pts = f"0,{mid_y} {width},{mid_y}"
        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline points="{pts}" fill="none" stroke="#6366f1" stroke-width="1.5"/>'
            f"</svg>"
        )

    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    n = len(values)

    # Flat line if all values identical (range is zero)
    if mx == mn:
        mid_y = height / 2
        pts = " ".join(
            f"{i * (width / max(n - 1, 1)):.1f},{mid_y:.1f}"
            for i in range(n)
        )
        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline points="{pts}" fill="none" stroke="#6366f1" stroke-width="1.5"/>'
            f"</svg>"
        )

    # Compute normalized coordinates for each point
    coord_pairs = []
    for i, v in enumerate(values):
        x = i * (width / (n - 1)) if n > 1 else width / 2
        y = height - ((v - mn) / rng) * (height - 4) - 2
        coord_pairs.append(f"{x:.1f},{y:.1f}")

    pts = " ".join(coord_pairs)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline points="{pts}" fill="none" stroke="#6366f1" stroke-width="1.5"/>'
        f"</svg>"
    )


async def build_7day_sparkline(width: int = 60, height: int = 20) -> str:
    """
    Build a sparkline SVG for the last 7 days of search activity.

    Fetches daily search counts from the database and passes them
    to points_to_svg.

    Args:
        width:  SVG width in pixels.
        height: SVG height in pixels.

    Returns:
        SVG markup string.
    """
    from database import get_daily_search_counts
    daily_counts = await get_daily_search_counts(days=7)
    return points_to_svg(daily_counts, width=width, height=height)
