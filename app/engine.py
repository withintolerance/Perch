from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import Database
from app.paths import start_ms_from_segment
from app.policy import allows_recording
from app.score import analyze_motion

log = logging.getLogger("nvr.engine")

FRAME_W = 160
FRAME_H = 90
FRAME_BYTES = FRAME_W * FRAME_H
OPENING = re.compile(r"Opening '([^']+)'")
URLISH = re.compile(r"rtsp://\S+")


def safe_error(text: str) -> str:
    return URLISH.sub("rtsp://…", text).strip()[:200]


class Engine:
    """One go2rtc connection per camera. Recorders and motion only read that."""

    def __init__(self, settings: Settings, db: Database, store) -> None:
        self.settings = settings
        self.db = db
        self.store = store
        self.stop = threading.Event()
        self.on_levels = None
        self._lock = threading.Lock()
        self.active_path: dict[str, Path] = {}
        self.errors: dict[str, str | None] = {}
        self.online: dict[str, bool] = {}
        self.recording_paused = False
        self.pause_message: str | None = None
        self._fail_counts: dict[Path, int] = {}
        self._procs: list[subprocess.Popen] = []
        self._threads: list[threading.Thread] = []
        self._camera_stop: dict[str, threading.Event] = {}
        self._record_proc: dict[str, subprocess.Popen] = {}
        self._motion_proc: dict[str, subprocess.Popen] = {}

    def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._recordings().mkdir(parents=True, exist_ok=True)
        self._ensure_day_dirs()
        self.apply_cameras(self.store.cameras())
        for target in (
            self._status_loop,
            self._finalize_loop,
            self._retention_loop,
            self._dir_loop,
            self._motion_summary_loop,
        ):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def apply_cameras(self, cameras) -> None:
        wanted = {camera.id: camera for camera in cameras}
        for camera_id in list(self._camera_stop):
            if camera_id not in wanted:
                self._stop_worker(camera_id)
        for camera in cameras:
            if camera.id not in self._camera_stop:
                self._start_worker(camera.id)
        with self._lock:
            for camera in cameras:
                self.errors.setdefault(camera.id, None)
                self.online.setdefault(camera.id, False)

    def restart_worker(self, camera_id: str) -> None:
        if camera_id in self._camera_stop:
            self._stop_worker(camera_id)
        self._start_worker(camera_id)

    def _start_worker(self, camera_id: str) -> None:
        camera_stop = threading.Event()
        self._camera_stop[camera_id] = camera_stop
        for target in (self._record_loop, self._motion_loop):
            thread = threading.Thread(target=target, args=(camera_id, camera_stop), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _stop_worker(self, camera_id: str) -> None:
        camera_stop = self._camera_stop.pop(camera_id, None)
        if camera_stop is not None:
            camera_stop.set()
        for table in (self._record_proc, self._motion_proc):
            proc = table.pop(camera_id, None)
            if proc is not None:
                _stop_proc(proc)

    def shutdown(self) -> None:
        self.stop.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _stop_proc(proc)
        for thread in self._threads:
            thread.join(timeout=5)
        self._remux_finished(include_active=True)

    def status_cameras(self) -> list[dict]:
        now_ms = int(time.time() * 1000)
        rows = []
        for camera in self.store.cameras():
            end = self.db.newest_end(camera.id)
            with self._lock:
                active = camera.id in self.active_path
                error = self.errors.get(camera.id)
                online = self.online.get(camera.id, False)
            recording = active or (end is not None and now_ms - end < 45_000)
            asleep = not recording and not self._recording_allowed()
            rows.append(
                {
                    "id": camera.id,
                    "name": camera.name,
                    "online": online,
                    "recording": recording,
                    "asleep": asleep,
                    "error": None if recording or asleep else error,
                }
            )
        return rows

    def open_segment(self, camera_id: str) -> dict | None:
        with self._lock:
            path = self.active_path.get(camera_id)
        if path is None or not path.exists():
            return None
        try:
            start_ms = start_ms_from_segment(path)
        except ValueError:
            return None
        return {"startMs": start_ms, "endMs": None, "ready": False, "url": None}

    def disk_free_gb(self) -> float:
        usage = shutil.disk_usage(self._recordings())
        return usage.free / (1024**3)

    def _recordings(self) -> Path:
        return self.store.recordings_dir()

    def restart_all_workers(self) -> None:
        for camera_id in list(self._camera_stop):
            self.restart_worker(camera_id)

    def _ensure_day_dirs(self) -> None:
        now = time.time()
        for offset in (0, 86400):
            day = time.strftime("%Y-%m-%d", time.gmtime(now + offset))
            for camera in self.store.cameras():
                (self._recordings() / camera.id / day).mkdir(parents=True, exist_ok=True)

    def _dir_loop(self) -> None:
        while not self.stop.wait(600):
            self._ensure_day_dirs()

    def _record_loop(self, camera_id: str, camera_stop: threading.Event) -> None:
        failures = 0
        while not self._halted(camera_stop):
            if self.recording_paused or not self._recording_allowed():
                self._set_error(camera_id, None)
                if self._wait(5, camera_stop):
                    return
                continue
            self._ensure_day_dirs()
            url = f"{self.settings.go2rtc_rtsp}/{camera_id}"
            pattern = str(self._recordings() / camera_id / "%Y-%m-%d" / "%H-%M-%S.ts")
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "info",
                "-nostdin",
                "-rtsp_transport",
                "tcp",
                "-i",
                url,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(self.settings.segment_seconds),
                "-reset_timestamps",
                "1",
                "-strftime",
                "1",
                "-segment_format",
                "mpegts",
                pattern,
            ]
            started = time.time()
            proc = self._popen(cmd, capture_stdout=False)
            self._record_proc[camera_id] = proc
            log.info("recording %s", camera_id)
            reader = threading.Thread(target=self._read_record_stderr, args=(camera_id, proc), daemon=True)
            reader.start()
            proc.wait()
            reader.join(timeout=2)
            self._record_proc.pop(camera_id, None)
            self._release_proc(proc)
            with self._lock:
                self.active_path.pop(camera_id, None)
            if self._halted(camera_stop):
                return
            if not self._recording_allowed():
                continue
            if time.time() - started > 60:
                failures = 0
            else:
                failures += 1
            delay = min(30, 2 ** failures)
            self._set_error(camera_id, f"Recorder stopped. Retrying in {delay}s.")
            log.warning("recorder %s exited %s, retry in %ss", camera_id, proc.returncode, delay)
            if self._wait(delay, camera_stop):
                return
        return

    def _read_record_stderr(self, camera_id: str, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            match = OPENING.search(line)
            if match:
                path = Path(match.group(1))
                with self._lock:
                    self.active_path[camera_id] = path
                    self.errors[camera_id] = None
                continue
            if "error" in line.lower() or "fail" in line.lower():
                self._set_error(camera_id, safe_error(line))
                log.warning("recorder %s: %s", camera_id, safe_error(line))

    def _motion_loop(self, camera_id: str, camera_stop: threading.Event) -> None:
        failures = 0
        url = f"{self.settings.go2rtc_rtsp}/{camera_id}"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-threads",
            "1",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-an",
            "-vf",
            f"fps={self.settings.motion_fps},scale={FRAME_W}:{FRAME_H},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]
        zones: list[dict] = []
        zones_at = 0.0
        while not self._halted(camera_stop):
            started = time.time()
            proc = self._popen(cmd, capture_stdout=True)
            self._motion_proc[camera_id] = proc
            reader = threading.Thread(target=self._drain_stderr, args=(camera_id, proc, "motion"), daemon=True)
            reader.start()
            previous: bytes | None = None
            assert proc.stdout is not None
            while not self._halted(camera_stop):
                frame = proc.stdout.read(FRAME_BYTES)
                if len(frame) != FRAME_BYTES:
                    break
                if previous is not None:
                    if time.time() - zones_at > 2:
                        zones = self.store.zones(camera_id)
                        zones_at = time.time()
                    try:
                        level, by_zone = analyze_motion(
                            previous,
                            frame,
                            FRAME_W,
                            FRAME_H,
                            noise=self.settings.motion_noise,
                            saturate=self.settings.motion_saturate,
                            zones=zones,
                        )
                    except ValueError:
                        log.exception("motion score failed for %s", camera_id)
                        break
                    self.db.add_motion(camera_id, int(time.time()), level)
                    if self.on_levels is not None:
                        try:
                            self.on_levels(camera_id, level, by_zone)
                        except Exception:
                            log.exception("motion publish failed for %s", camera_id)
                previous = frame
            self._motion_proc.pop(camera_id, None)
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("motion %s did not exit", camera_id)
            reader.join(timeout=2)
            self._release_proc(proc)
            if self._halted(camera_stop):
                return
            if time.time() - started > 60:
                failures = 0
            else:
                failures += 1
            delay = min(30, 2 ** failures)
            log.warning("motion %s stopped, retry in %ss", camera_id, delay)
            if self._wait(delay, camera_stop):
                return

    def _drain_stderr(self, camera_id: str, proc: subprocess.Popen, kind: str) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if line and ("error" in line.lower() or "fail" in line.lower()):
                log.warning("%s %s: %s", kind, camera_id, safe_error(line))

    def _motion_summary_loop(self) -> None:
        while not self.stop.wait(30):
            parts = []
            now_s = int(time.time())
            for camera in self.store.cameras():
                rows = self.db.motion_between(camera.id, now_s - 30, now_s + 1)
                level = max((int(row["level"]) for row in rows), default=0)
                parts.append(f"{camera.id}={level}")
            log.info("motion %s", " ".join(parts))

    def _finalize_loop(self) -> None:
        while not self.stop.wait(2):
            self._remux_finished()

    def _remux_finished(self, include_active: bool = False) -> None:
        root = self._recordings()
        if not root.exists():
            return
        candidates = list(root.glob("*/*/*.ts")) + list(root.glob("*/*/*.ts.bad"))
        newest: dict[str, Path] = {}
        for path in candidates:
            if path.suffix == ".bad":
                continue
            camera_id = path.parent.parent.name
            key = (path.parent.name, path.name)
            current = newest.get(camera_id)
            if current is None or key > (current.parent.name, current.name):
                newest[camera_id] = path
        for path in candidates:
            camera_id = path.parent.parent.name
            open_file = newest.get(camera_id)
            if path.suffix == ".ts" and not include_active and open_file is not None and path == open_file:
                continue
            self._remux_one(path)

    def _remux_one(self, ts_path: Path) -> None:
        if ts_path.name.endswith(".ts.bad") and self._fail_counts.get(ts_path, 0) >= 5:
            return
        if ts_path.name.endswith(".ts.bad"):
            mp4_path = ts_path.with_name(ts_path.name[: -len(".ts.bad")] + ".mp4")
            clock_name = ts_path.name[: -len(".bad")]
        elif ts_path.suffix == ".ts":
            mp4_path = ts_path.with_suffix(".mp4")
            clock_name = ts_path.name
        else:
            return
        part = mp4_path.with_name(mp4_path.stem + ".part.mp4")
        camera_id = ts_path.parent.parent.name
        try:
            start_ms = start_ms_from_segment(ts_path.with_name(clock_name))
        except ValueError:
            log.warning("skipping oddly named segment %s", ts_path.name)
            return
        if mp4_path.exists():
            ts_path.unlink(missing_ok=True)
            return
        ok, detail = _remux(ts_path, part)
        if not ok:
            count = self._fail_counts.get(ts_path, 0) + 1
            self._fail_counts[ts_path] = count
            if count >= 5 and ts_path.suffix == ".ts":
                bad = ts_path.with_suffix(".ts.bad")
                ts_path.replace(bad)
                log.error("gave up remuxing %s: %s", ts_path.name, detail)
            elif count < 5 or ts_path.suffix == ".ts":
                log.warning("remux failed for %s (%s): %s", ts_path.name, count, detail)
            part.unlink(missing_ok=True)
            return
        duration = _duration_seconds(part)
        if duration is None or duration < 0.4:
            log.warning("segment %s has no usable duration", ts_path.name)
            part.unlink(missing_ok=True)
            return
        end_ms = start_ms + int(round(duration * 1000))
        part.replace(mp4_path)
        ts_path.unlink(missing_ok=True)
        self._fail_counts.pop(ts_path, None)
        if self._quiet_segment(camera_id, start_ms, end_ms):
            mp4_path.unlink(missing_ok=True)
            log.info("dropped quiet segment for %s", camera_id)
            return
        self.db.add_segment(camera_id, start_ms, end_ms, str(mp4_path))
        self._set_error(camera_id, None)

    def _recording_allowed(self) -> bool:
        if self.recording_paused:
            return False
        try:
            now = datetime.now(ZoneInfo(self.store.timezone()))
        except Exception:
            now = datetime.now(ZoneInfo("UTC"))
        return allows_recording(self.store.policy(), now)

    def _quiet_segment(self, camera_id: str, start_ms: int, end_ms: int) -> bool:
        if not self.store.policy().only_motion:
            return False
        peak = self.db.peak_motion(camera_id, start_ms // 1000, end_ms // 1000 + 1)
        if peak is None:
            return False
        return peak < self.store.policy().motion_percent

    def _halted(self, camera_stop: threading.Event) -> bool:
        return self.stop.is_set() or camera_stop.is_set()

    def _wait(self, seconds: float, camera_stop: threading.Event) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._halted(camera_stop):
                return True
            time.sleep(0.25)
        return self._halted(camera_stop)

    def _retention_loop(self) -> None:
        while not self.stop.wait(30):
            self._enforce_retention()

    def _enforce_retention(self) -> None:
        cutoff_ms = int(time.time() * 1000) - self.store.policy().retain_days * 86_400_000
        for row in self.db.segments_ended_before(cutoff_ms):
            self._delete_row(row)
        self.db.delete_motion_before(cutoff_ms // 1000)

        free = self.disk_free_gb()
        if free >= self.store.policy().min_free_gb:
            if self.recording_paused:
                log.info("disk has %.1f GB free, recording again", free)
            self.recording_paused = False
            self.pause_message = None
            return

        removed = 0
        while self.disk_free_gb() < self.store.policy().min_free_gb and removed < 500:
            oldest = self.db.oldest_segments(20)
            if not oldest:
                break
            for row in oldest:
                self._delete_row(row)
                removed += 1
                if self.disk_free_gb() >= self.store.policy().min_free_gb:
                    break
        free = self.disk_free_gb()
        if free < self.store.policy().min_free_gb:
            self.recording_paused = True
            self.pause_message = (
                f"Recording is paused. Only {free:.0f} GB is free and there is "
                "nothing left of these recordings to delete. Other files on the disk may be using the space."
            )
            log.error("%s", self.pause_message)
        else:
            self.recording_paused = False
            self.pause_message = None
            log.warning("deleted old segments to keep %.0f GB free", self.store.policy().min_free_gb)

    def _delete_row(self, row) -> None:
        path = Path(row["path"])
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not delete %s: %s", path.name, exc)
            return
        self.db.delete_segment(row["camera"], int(row["start_ms"]))

    def _status_loop(self) -> None:
        while not self.stop.wait(5):
            try:
                online = _go2rtc_online(self.settings.go2rtc_api)
            except Exception as exc:
                log.warning("go2rtc status failed: %s", safe_error(str(exc)))
                online = {}
            with self._lock:
                for camera in self.store.cameras():
                    self.online[camera.id] = online.get(camera.id, False)

    def _set_error(self, camera_id: str, message: str | None) -> None:
        with self._lock:
            self.errors[camera_id] = message

    def _popen(self, cmd: list[str], capture_stdout: bool) -> subprocess.Popen:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with self._lock:
            self._procs.append(proc)
        return proc

    def _release_proc(self, proc: subprocess.Popen) -> None:
        _close_pipes(proc)
        with self._lock:
            try:
                self._procs.remove(proc)
            except ValueError:
                pass


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg did not exit")
    _close_pipes(proc)


def _close_pipes(proc: subprocess.Popen) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass


def _remux(ts_path: Path, dest: Path) -> tuple[bool, str]:
    commands = [
        ["-bsf:a", "aac_adtstoasc"],
        [],
    ]
    detail = "unknown remux error"
    for extra in commands:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(ts_path),
            "-c",
            "copy",
            *extra,
            "-movflags",
            "+faststart",
            str(dest),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            dest.unlink(missing_ok=True)
            return False, "remux took too long"
        if result.returncode == 0:
            return True, ""
        detail = safe_error((result.stderr or "").strip().splitlines()[-1] if result.stderr else "ffmpeg failed")
        dest.unlink(missing_ok=True)
    return False, detail


def _duration_seconds(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _go2rtc_online(api: str) -> dict[str, bool]:
    with urllib.request.urlopen(f"{api}/api/streams", timeout=5) as response:
        payload = json.load(response)
    online: dict[str, bool] = {}
    if not isinstance(payload, dict):
        return online
    for name, info in payload.items():
        producers = (info or {}).get("producers") or []
        has_video = False
        for producer in producers:
            for receiver in producer.get("receivers") or []:
                codec = receiver.get("codec") or {}
                if codec.get("codec_type") == "video" or codec.get("codec_name") in {"h264", "hevc"}:
                    has_video = True
        online[name] = has_video
    return online


def post_webrtc(api: str, camera_id: str, offer: bytes) -> bytes:
    request = urllib.request.Request(
        f"{api}/api/webrtc?src={camera_id}",
        data=offer,
        headers={"Content-Type": "application/sdp"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode(errors="replace")
        raise RuntimeError(safe_error(detail) or f"go2rtc returned {exc.code}") from exc
