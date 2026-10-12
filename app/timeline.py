from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int | None
    ready: bool
    url: str | None = None

    def covers(self, t_ms: int, now_ms: int) -> bool:
        if t_ms < self.start_ms:
            return False
        end = now_ms if self.end_ms is None else self.end_ms
        return t_ms < end


def segment_at(segments: list[Segment], t_ms: int, now_ms: int) -> Segment | None:
    hits = [item for item in segments if item.covers(t_ms, now_ms)]
    if not hits:
        return None
    hits.sort(key=lambda item: (item.ready, item.start_ms))
    return hits[-1]


def next_ready(segments: list[Segment], after_ms: int, gap_ms: int = 3000) -> Segment | None:
    candidates = [
        item
        for item in segments
        if item.ready and item.end_ms is not None and item.start_ms >= after_ms - 200
    ]
    if not candidates:
        return None
    nxt = min(candidates, key=lambda item: item.start_ms)
    if nxt.start_ms - after_ms > gap_ms:
        return None
    return nxt


def describe(t_ms: int, segments: list[Segment], level: int | None, now_ms: int) -> str:
    """Kind of timeline instant: gap, saving, still, or motion."""
    segment = segment_at(segments, t_ms, now_ms)
    if segment is None:
        return "gap"
    if not segment.ready:
        return "saving"
    if not level:
        return "still"
    return "motion"
