from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.config import Camera, Settings
from app.go2rtc_config import live_source, render_go2rtc

log = logging.getLogger("nvr.relay")


def write_go2rtc_file(settings: Settings, cameras: list[Camera]) -> None:
    path = settings.go2rtc_config_path
    updated = Settings(
        timezone=settings.timezone,
        data_dir=settings.data_dir,
        retain_days=settings.retain_days,
        min_free_gb=settings.min_free_gb,
        segment_seconds=settings.segment_seconds,
        motion_fps=settings.motion_fps,
        motion_noise=settings.motion_noise,
        motion_saturate=settings.motion_saturate,
        advertise_host=settings.advertise_host,
        webrtc_port=settings.webrtc_port,
        http_port=settings.http_port,
        go2rtc_api=settings.go2rtc_api,
        go2rtc_rtsp=settings.go2rtc_rtsp,
        config_path=settings.config_path,
        cameras=tuple(cameras),
    )
    path.write_text(render_go2rtc(updated))
    try:
        path.chmod(0o600)
    except OSError:
        log.warning("could not restrict permissions on %s", path)


def stream_query(camera_id: str, url: str) -> str:
    # go2rtc names the stream from "name". "dst" is a different call and is ignored here.
    return urllib.parse.urlencode({"name": camera_id, "src": live_source(url)})


def put_stream(api: str, camera_id: str, url: str) -> None:
    request = urllib.request.Request(f"{api}/api/streams?{stream_query(camera_id, url)}", method="PUT")
    with urllib.request.urlopen(request, timeout=8) as response:
        response.read()


def delete_stream(api: str, camera_id: str) -> None:
    query = urllib.parse.urlencode({"src": camera_id})
    request = urllib.request.Request(f"{api}/api/streams?{query}", method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in {404, 400}:
            raise


def sync_streams(settings: Settings, cameras: list[Camera]) -> str | None:
    try:
        write_go2rtc_file(settings, cameras)
    except OSError as exc:
        log.warning("could not save the relay config: %s", exc)
    problems: list[str] = []
    for camera in cameras:
        try:
            put_stream(settings.go2rtc_api, camera.id, camera.url)
        except Exception as exc:
            problems.append(f"{camera.name}: {exc}")
    return "; ".join(problems) if problems else None


def snapshot(api: str, camera_id: str) -> tuple[bytes, str]:
    query = urllib.parse.urlencode({"src": camera_id})
    request = urllib.request.Request(f"{api}/api/frame.jpeg?{query}")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            kind = response.headers.get("content-type") or "image/jpeg"
            return response.read(), kind
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode(errors="replace")
        raise RuntimeError(detail or f"snapshot failed ({exc.code})") from exc


def loads_json(raw: str | None, fallback: dict) -> dict:
    if not raw:
        return dict(fallback)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return dict(fallback)
    return value if isinstance(value, dict) else dict(fallback)
