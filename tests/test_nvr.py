import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import load_settings, public_camera
from app.go2rtc_config import live_source, render_go2rtc
from app.paths import media_file, start_ms_from_segment
from app.passwords import hash_password, verify_password
from app.policy import RecordingPolicy, allows_recording
from app.score import analyze_motion, motion_percent
from datetime import datetime
from app.timeline import Segment, describe, next_ready, segment_at

WIDTH = 160
HEIGHT = 90


def frame(value: int = 0) -> bytearray:
    return bytearray([value]) * (WIDTH * HEIGHT)


def fill_cells(base: bytes, cells: list[tuple[int, int]], value: int) -> bytes:
    buf = bytearray(base)
    cell_w = WIDTH // 16
    cell_h = HEIGHT // 9
    for cx, cy in cells:
        for y in range(cy * cell_h, (cy + 1) * cell_h):
            row = y * WIDTH
            for x in range(cx * cell_w, (cx + 1) * cell_w):
                buf[row + x] = value
    return bytes(buf)


class ScoreTests(unittest.TestCase):
    def test_identical_frames_are_still(self):
        still = bytes(frame(20))
        self.assertEqual(motion_percent(still, still, WIDTH, HEIGHT, noise=2, saturate=28), 0)

    def test_one_hot_cell_is_ignored(self):
        prev = bytes(frame(0))
        curr = fill_cells(prev, [(0, 0)], 255)
        self.assertEqual(motion_percent(prev, curr, WIDTH, HEIGHT, noise=2, saturate=28), 0)

    def test_a_person_sized_change_is_strong(self):
        prev = bytes(frame(0))
        curr = fill_cells(prev, [(2, 3), (3, 3), (4, 3), (2, 4), (3, 4), (4, 4)], 255)
        self.assertEqual(motion_percent(prev, curr, WIDTH, HEIGHT, noise=2, saturate=28), 100)

    def test_moderate_change_is_not_maxed(self):
        prev = bytes(frame(0))
        curr = fill_cells(prev, [(1, 1), (2, 1), (3, 1), (4, 1)], 16)
        level = motion_percent(prev, curr, WIDTH, HEIGHT, noise=2, saturate=28)
        self.assertGreater(level, 20)
        self.assertLess(level, 80)


class AccountTests(unittest.TestCase):
    def test_last_owner_cannot_be_removed(self):
        from app.db import Database
        from app.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "cameras.yaml"
            config.write_text("timezone: UTC\ndata_dir: /data\ncameras: []\n")
            settings = load_settings(config)
            store = Store(Database(root / "nvr.db"), settings)
            store.db.create_user("owner", "hash", 1, role="owner")
            with self.assertRaises(ValueError):
                store.delete_account("someone-else", "owner")

    def test_sign_in_stays_off_until_an_account_exists_or_it_is_turned_on(self):
        from app.db import Database
        from app.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "cameras.yaml"
            config.write_text("timezone: UTC\ndata_dir: /data\ncameras: []\n")
            settings = load_settings(config)
            store = Store(Database(root / "nvr.db"), settings)
            self.assertFalse(store.auth_required())
            store.db.create_user("owner", "hash", 1, role="owner")
            self.assertTrue(store.auth_required())
            store.set_auth_required(False)
            self.assertFalse(store.auth_required())
            store.set_auth_required(True)
            self.assertTrue(store.auth_required())

    def test_password_roundtrip(self):
        stored = hash_password("correct horse")
        self.assertTrue(verify_password("correct horse", stored))
        self.assertFalse(verify_password("wrong horse", stored))


class PolicyTests(unittest.TestCase):
    def test_overnight_window(self):
        policy = RecordingPolicy(True, False, (0, 1, 2, 3, 4, 5, 6), "22:00", "06:00", 12, 7, 40)
        self.assertTrue(allows_recording(policy, datetime(2026, 10, 12, 23, 0)))
        self.assertTrue(allows_recording(policy, datetime(2026, 10, 12, 5, 0)))
        self.assertFalse(allows_recording(policy, datetime(2026, 10, 12, 12, 0)))

    def test_all_day_ignores_the_clock(self):
        policy = RecordingPolicy(False, True, (), "08:00", "18:00", 12, 7, 40)
        self.assertTrue(allows_recording(policy, datetime(2026, 10, 12, 3, 0)))


class ZoneTests(unittest.TestCase):
    def test_motion_outside_a_zone_does_not_count(self):
        prev = bytes(frame(0))
        curr = fill_cells(prev, [(0, 0)], 255)
        level, zones = analyze_motion(
            prev, curr, WIDTH, HEIGHT, noise=2, saturate=28,
            zones=[{"id": "door", "x": 0.7, "y": 0.7, "w": 0.2, "h": 0.2}],
        )
        self.assertEqual(level, 0)
        self.assertEqual(zones["door"], 0)

    def test_motion_inside_a_zone_counts(self):
        prev = bytes(frame(0))
        curr = fill_cells(prev, [(14, 7)], 255)
        level, zones = analyze_motion(
            prev, curr, WIDTH, HEIGHT, noise=2, saturate=28,
            zones=[{"id": "door", "x": 0.75, "y": 0.7, "w": 0.25, "h": 0.3}],
        )
        self.assertGreater(level, 50)
        self.assertGreater(zones["door"], 50)


class TimelineTests(unittest.TestCase):
    def test_open_segment_covers_now_but_not_the_future(self):
        segments = [Segment(start_ms=1_000, end_ms=None, ready=False)]
        self.assertEqual(describe(1_500, segments, None, now_ms=2_000), "saving")
        self.assertEqual(describe(5_000, segments, None, now_ms=2_000), "gap")

    def test_ready_segment_reports_motion_and_still(self):
        segments = [Segment(start_ms=0, end_ms=10_000, ready=True, url="/media/a")]
        self.assertEqual(describe(1_000, segments, 40, now_ms=20_000), "motion")
        self.assertEqual(describe(1_000, segments, 0, now_ms=20_000), "still")
        self.assertEqual(describe(1_000, segments, None, now_ms=20_000), "still")

    def test_next_segment_rejects_a_gap(self):
        segments = [
            Segment(0, 10_000, True),
            Segment(20_000, 30_000, True),
        ]
        self.assertIsNone(next_ready(segments, 10_000))
        self.assertEqual(next_ready(segments, 10_000, gap_ms=15_000).start_ms, 20_000)

    def test_segment_at_prefers_a_ready_file(self):
        segments = [
            Segment(0, None, False),
            Segment(0, 5_000, True, url="/ready"),
        ]
        found = segment_at(segments, 1_000, now_ms=4_000)
        self.assertTrue(found.ready)


class PathTests(unittest.TestCase):
    def test_accepts_a_normal_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = media_file(root, "living_room", "2026-10-12", "17-01-02.mp4")
            self.assertEqual(path, (root / "living_room" / "2026-10-12" / "17-01-02.mp4").resolve())

    def test_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                media_file(root, "..", "2026-10-12", "17-01-02.mp4")
            with self.assertRaises(ValueError):
                media_file(root, "living_room", "2026-10-12", "../17-01-02.mp4")

    def test_filename_clock_is_utc(self):
        path = Path("/data/recordings/kitchen/2026-10-12/17-01-02.mp4")
        expected = int(datetime(2026, 10, 12, 17, 1, 2, tzinfo=timezone.utc).timestamp()) * 1000
        self.assertEqual(start_ms_from_segment(path), expected)


class ConfigTests(unittest.TestCase):
    def test_public_camera_hides_the_stream_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cameras.yaml"
            path.write_text(
                """
timezone: America/New_York
data_dir: /data
advertise_host: 10.1.1.10
cameras:
  - id: front_door
    name: Front door
    url: rtsp://user:password@10.0.0.20:554/stream1
"""
            )
            settings = load_settings(path)
            rendered = render_go2rtc(settings)
            self.assertIn("10.1.1.10:8555", rendered)
            self.assertIn("front_door", rendered)
            self.assertNotIn("#video=copy", rendered)
            self.assertEqual(
                live_source("ffmpeg:rtsp://camera/stream#video=copy#raw=-rtsp_transport tcp"),
                "rtsp://camera/stream",
            )
            from app.relay import stream_query

            query = stream_query("front_door", settings.cameras[0].url)
            self.assertIn("name=front_door", query)
            self.assertNotIn("dst=", query)
            self.assertNotIn("url", public_camera(settings.cameras[0]))
            self.assertNotIn("rtsp", str(public_camera(settings.cameras[0])))

    def test_bad_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cameras.yaml"
            path.write_text(
                """
cameras:
  - id: "../etc"
    name: Bad
    url: rtsp://example/stream
"""
            )
            with self.assertRaises(ValueError):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
