from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RecordingPolicy:
    limit_hours: bool
    only_motion: bool
    days: tuple[int, ...]
    start: str
    end: str
    motion_percent: int
    retain_days: int
    min_free_gb: float

    def as_dict(self) -> dict:
        return {
            "limitHours": self.limit_hours,
            "onlyMotion": self.only_motion,
            "days": list(self.days),
            "start": self.start,
            "end": self.end,
            "motionPercent": self.motion_percent,
            "retainDays": self.retain_days,
            "minFreeGb": self.min_free_gb,
        }


def default_policy(retain_days: int, min_free_gb: float) -> RecordingPolicy:
    return RecordingPolicy(
        limit_hours=False,
        only_motion=False,
        days=(0, 1, 2, 3, 4, 5, 6),
        start="08:00",
        end="18:00",
        motion_percent=12,
        retain_days=retain_days,
        min_free_gb=min_free_gb,
    )


def parse_policy(raw: dict, fallback: RecordingPolicy) -> RecordingPolicy:
    days = tuple(int(day) for day in raw.get("days", fallback.days))
    if any(day < 0 or day > 6 for day in days):
        raise ValueError("Days must be 0 (Monday) through 6 (Sunday).")
    motion = int(raw.get("motionPercent", raw.get("motion_percent", fallback.motion_percent)))
    retain = int(raw.get("retainDays", raw.get("retain_days", fallback.retain_days)))
    free = float(raw.get("minFreeGb", raw.get("min_free_gb", fallback.min_free_gb)))
    if not 1 <= motion <= 100:
        raise ValueError("Motion threshold must be from 1 to 100.")
    if retain < 1:
        raise ValueError("Keep recordings for at least one day.")
    if free < 1:
        raise ValueError("Free space floor must be at least 1 GB.")
    start = str(raw.get("start", fallback.start))
    end = str(raw.get("end", fallback.end))
    _minutes(start)
    _minutes(end)
    return RecordingPolicy(
        limit_hours=bool(raw.get("limitHours", raw.get("limit_hours", fallback.limit_hours))),
        only_motion=bool(raw.get("onlyMotion", raw.get("only_motion", fallback.only_motion))),
        days=days,
        start=start,
        end=end,
        motion_percent=motion,
        retain_days=retain,
        min_free_gb=free,
    )


def allows_recording(policy: RecordingPolicy, now: datetime) -> bool:
    if not policy.limit_hours:
        return True
    if now.weekday() not in policy.days:
        return False
    return _in_hours(now, policy.start, policy.end)


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    hour_i, minute_i = int(hour), int(minute)
    if not 0 <= hour_i <= 23 or not 0 <= minute_i <= 59:
        raise ValueError("Times must look like 08:00.")
    return hour_i * 60 + minute_i


def _in_hours(now: datetime, start: str, end: str) -> bool:
    current = now.hour * 60 + now.minute
    begin = _minutes(start)
    finish = _minutes(end)
    if begin == finish:
        return True
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish
