from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE = re.compile(r"^\d{2}-\d{2}-\d{2}\.mp4$")


def media_file(recordings: Path, camera_id: str, day: str, name: str) -> Path:
    if not DAY.match(day) or not FILE.match(name):
        raise ValueError("Invalid recording path")
    if camera_id in {"", ".", ".."} or "/" in camera_id or "\\" in camera_id:
        raise ValueError("Invalid recording path")
    root = recordings.resolve()
    target = (root / camera_id / day / name).resolve()
    if target.parent.parent.parent != root:
        raise ValueError("Invalid recording path")
    return target


def start_ms_from_segment(path: Path) -> int:
    day = path.parent.name
    stamp = path.stem
    parsed = datetime.strptime(f"{day} {stamp}", "%Y-%m-%d %H-%M-%S")
    return int(parsed.replace(tzinfo=timezone.utc).timestamp()) * 1000
