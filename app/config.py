from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    url: str
    source: str


@dataclass(frozen=True)
class Settings:
    timezone: str
    data_dir: Path
    retain_days: int
    min_free_gb: float
    segment_seconds: int
    motion_fps: int
    motion_noise: float
    motion_saturate: float
    advertise_host: str
    webrtc_port: int
    http_port: int
    go2rtc_api: str
    go2rtc_rtsp: str
    config_path: Path
    cameras: tuple[Camera, ...]

    @property
    def go2rtc_config_path(self) -> Path:
        return self.config_path.with_name("go2rtc.yaml")

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    def camera(self, camera_id: str) -> Camera | None:
        for item in self.cameras:
            if item.id == camera_id:
                return item
        return None


def public_camera(camera: Camera) -> dict[str, str]:
    return {"id": camera.id, "name": camera.name}


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("NVR_CONFIG", "/config/cameras.yaml"))
    if not config_path.is_file():
        raise ValueError(f"Config file not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Config must be a mapping")

    cameras: list[Camera] = []
    seen: set[str] = set()
    for entry in raw.get("cameras") or []:
        if not isinstance(entry, dict):
            raise ValueError("Each camera must be a mapping")
        camera_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not SAFE_ID.match(camera_id):
            raise ValueError(f"Camera id {camera_id!r} must be letters, numbers, _ or -")
        if camera_id in seen:
            raise ValueError(f"Duplicate camera id {camera_id}")
        if not name:
            raise ValueError(f"Camera {camera_id} needs a name")
        if not url.startswith("rtsp://"):
            raise ValueError(f"Camera {camera_id} needs an rtsp:// url")
        source = str(entry.get("source") or "").strip()
        if not source:
            source = f"ffmpeg:{url}#video=copy#audio=aac#raw=-rtsp_transport tcp"
        seen.add(camera_id)
        cameras.append(Camera(camera_id, name, url, source))

    retain_days = int(raw.get("retain_days", 7))
    motion_fps = int(raw.get("motion_fps", 2))
    segment_seconds = int(raw.get("segment_seconds", 15))
    if retain_days < 1:
        raise ValueError("retain_days must be at least 1")
    if not 1 <= motion_fps <= 5:
        raise ValueError("motion_fps must be from 1 to 5")
    if not 5 <= segment_seconds <= 60:
        raise ValueError("segment_seconds must be from 5 to 60")

    noise = float(raw.get("motion_noise", 2.0))
    saturate = float(raw.get("motion_saturate", 28.0))
    if saturate <= noise:
        raise ValueError("motion_saturate must be greater than motion_noise")

    return Settings(
        timezone=str(raw.get("timezone") or "America/New_York"),
        data_dir=Path(raw.get("data_dir") or "/data"),
        retain_days=retain_days,
        min_free_gb=float(raw.get("min_free_gb", 40)),
        segment_seconds=segment_seconds,
        motion_fps=motion_fps,
        motion_noise=noise,
        motion_saturate=saturate,
        advertise_host=str(raw.get("advertise_host") or "127.0.0.1"),
        webrtc_port=int(raw.get("webrtc_port", 8555)),
        http_port=int(raw.get("http_port", 8090)),
        go2rtc_api=str(raw.get("go2rtc_api") or "http://127.0.0.1:1984").rstrip("/"),
        go2rtc_rtsp=str(raw.get("go2rtc_rtsp") or "rtsp://127.0.0.1:8554").rstrip("/"),
        config_path=config_path,
        cameras=tuple(cameras),
    )
