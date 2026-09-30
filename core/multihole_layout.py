"""Shared pure layout primitive for multi-hole geometry."""
from __future__ import annotations
from collections.abc import Callable, Iterable


def layout_holes(layout: str, origin_x: float, origin_y: float,
                 sizes: Iterable[tuple[float, float]], gaps: Iterable[float],
                 margin_top: Callable[[int], float],
                 margin_bottom: Callable[[int], float],
                 margin_left: Callable[[int], float],
                 margin_right: Callable[[int], float],
                 size_adjust: float = 0.0) -> list[dict]:
    """Lay out holes and return normalized geometry dictionaries."""
    sizes, gaps = list(sizes), list(gaps)
    result, vertical = [], layout == 'vertical'
    cursor = origin_y + margin_top(0) if vertical else origin_x + margin_left(0)
    for i, (width, height) in enumerate(sizes):
        if i and i - 1 < len(gaps):
            cursor += gaps[i - 1]
        mt, mb, ml, mr = margin_top(i), margin_bottom(i), margin_left(i), margin_right(i)
        width = max(0.0, float(width)) + size_adjust
        height = max(0.0, float(height)) + size_adjust
        result.append({'x_cm': origin_x + ml if vertical else cursor,
                       'y_cm': cursor if vertical else origin_y + mt,
                       'w_cm': width, 'h_cm': height,
                       'mt_cm': mt, 'mb_cm': mb, 'ml_cm': ml, 'mr_cm': mr})
        cursor += height if vertical else width
    return result
