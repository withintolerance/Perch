from __future__ import annotations

from pathlib import Path

import yaml

from app.config import Settings


def live_source(source: str) -> str:
    """Use go2rtc's own RTSP client. The ffmpeg wrapper waits several seconds to probe."""
    text = source.strip()
    if text.startswith("ffmpeg:"):
        text = text[len("ffmpeg:") :]
    return text.split("#", 1)[0]


def go2rtc_document(settings: Settings) -> dict:
    streams = {camera.id: [live_source(camera.source)] for camera in settings.cameras}
    return {
        "api": {"listen": "127.0.0.1:1984", "origin": "*"},
        "rtsp": {"listen": "127.0.0.1:8554"},
        "ffmpeg": {
            "rtsp": (
                "-fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 "
                "-timeout 5000000 -user_agent go2rtc -rtsp_transport tcp -i {input}"
            )
        },
        "webrtc": {
            "listen": f":{settings.webrtc_port}",
            "candidates": [f"{settings.advertise_host}:{settings.webrtc_port}"],
        },
        "streams": streams,
    }


def render_go2rtc(settings: Settings) -> str:
    return yaml.safe_dump(go2rtc_document(settings), sort_keys=False)


def write_go2rtc(settings: Settings, path: Path) -> None:
    path.write_text(render_go2rtc(settings))
