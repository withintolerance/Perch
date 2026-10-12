"""Motion intensity from two small grayscale frames.

The hottest cell is ignored so a clock burned into a corner does not paint the
whole day. The next few cells are averaged, which is enough for a person and
still ignores a single noisy spot.
"""

from __future__ import annotations

CELLS_X = 16
CELLS_Y = 9


def cell_means(prev: bytes, curr: bytes, width: int, height: int) -> list[float]:
    expected = width * height
    if width < CELLS_X or height < CELLS_Y:
        raise ValueError("frame is smaller than the motion grid")
    if len(prev) != expected or len(curr) != expected:
        raise ValueError("frame size does not match width and height")
    cell_w = width // CELLS_X
    cell_h = height // CELLS_Y
    scores: list[float] = []
    for cy in range(CELLS_Y):
        for cx in range(CELLS_X):
            total = 0
            y0 = cy * cell_h
            x0 = cx * cell_w
            for y in range(y0, y0 + cell_h):
                row = y * width
                for x in range(x0, x0 + cell_w):
                    diff = curr[row + x] - prev[row + x]
                    total += diff if diff >= 0 else -diff
            scores.append(total / (cell_w * cell_h))
    return scores


def percent_from_scores(scores: list[float], noise: float, saturate: float, *, drop_hottest: bool = True) -> int:
    if not scores:
        return 0
    ordered = sorted(scores, reverse=True)
    sample = ordered[1:5] if drop_hottest and len(ordered) > 1 else ordered[:4]
    if not sample:
        return 0
    score = sum(sample) / len(sample)
    if score <= noise:
        return 0
    percent = int(round((score - noise) / (saturate - noise) * 100))
    return max(0, min(100, percent))


def motion_percent(
    prev: bytes,
    curr: bytes,
    width: int,
    height: int,
    *,
    noise: float,
    saturate: float,
) -> int:
    return percent_from_scores(cell_means(prev, curr, width, height), noise, saturate)


def zone_levels(
    means: list[float],
    zones: list[dict],
    noise: float,
    saturate: float,
) -> dict[str, int]:
    levels: dict[str, int] = {}
    for zone in zones:
        selected: list[float] = []
        for index, score in enumerate(means):
            cx = index % CELLS_X
            cy = index // CELLS_X
            px = (cx + 0.5) / CELLS_X
            py = (cy + 0.5) / CELLS_Y
            if zone["x"] <= px <= zone["x"] + zone["w"] and zone["y"] <= py <= zone["y"] + zone["h"]:
                selected.append(score)
        levels[str(zone["id"])] = percent_from_scores(selected, noise, saturate, drop_hottest=False) if selected else 0
    return levels


def analyze_motion(
    prev: bytes,
    curr: bytes,
    width: int,
    height: int,
    *,
    noise: float,
    saturate: float,
    zones: list[dict] | None = None,
) -> tuple[int, dict[str, int]]:
    means = cell_means(prev, curr, width, height)
    by_zone = zone_levels(means, zones or [], noise, saturate)
    if zones:
        return max(by_zone.values(), default=0), by_zone
    return percent_from_scores(means, noise, saturate), {}
