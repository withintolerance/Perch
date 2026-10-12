from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS segments (
                    camera TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY (camera, start_ms)
                );
                CREATE TABLE IF NOT EXISTS motion (
                    camera TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    PRIMARY KEY (camera, ts)
                );
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    can_live INTEGER NOT NULL DEFAULT 1,
                    can_playback INTEGER NOT NULL DEFAULT 1,
                    can_settings INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    sort_order INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS zones (
                    camera TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    w REAL NOT NULL,
                    h REAL NOT NULL,
                    PRIMARY KEY (camera, id)
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._conn.commit()
            self._upgrade_users()

    def _upgrade_users(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(users)")}
        if "role" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'owner'")
        if "can_live" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN can_live INTEGER NOT NULL DEFAULT 1")
        if "can_playback" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN can_playback INTEGER NOT NULL DEFAULT 1")
        if "can_settings" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN can_settings INTEGER NOT NULL DEFAULT 0")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_cameras (
                username TEXT NOT NULL,
                camera TEXT NOT NULL,
                PRIMARY KEY (username, camera)
            )
            """
        )
        self._conn.commit()

    def add_segment(self, camera: str, start_ms: int, end_ms: int, path: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO segments (camera, start_ms, end_ms, path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(camera, start_ms) DO UPDATE SET
                    end_ms = excluded.end_ms,
                    path = excluded.path
                """,
                (camera, start_ms, end_ms, path),
            )
            self._conn.commit()

    def segments_between(self, camera: str, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT camera, start_ms, end_ms, path
                FROM segments
                WHERE camera = ? AND end_ms > ? AND start_ms < ?
                ORDER BY start_ms
                """,
                (camera, start_ms, end_ms),
            ).fetchall()
        return rows

    def segments_ended_before(self, cutoff_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT camera, start_ms, end_ms, path FROM segments WHERE end_ms < ? ORDER BY start_ms",
                (cutoff_ms,),
            ).fetchall()

    def oldest_segments(self, limit: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT camera, start_ms, end_ms, path FROM segments ORDER BY start_ms LIMIT ?",
                (limit,),
            ).fetchall()

    def delete_segment(self, camera: str, start_ms: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM segments WHERE camera = ? AND start_ms = ?",
                (camera, start_ms),
            )
            self._conn.commit()

    def newest_end(self, camera: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(end_ms) AS end_ms FROM segments WHERE camera = ?",
                (camera,),
            ).fetchone()
        if row is None or row["end_ms"] is None:
            return None
        return int(row["end_ms"])

    def add_motion(self, camera: str, ts: int, level: int) -> None:
        if level <= 0:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO motion (camera, ts, level) VALUES (?, ?, ?)
                ON CONFLICT(camera, ts) DO UPDATE SET
                    level = MAX(level, excluded.level)
                """,
                (camera, ts, level),
            )
            self._conn.commit()

    def motion_between(self, camera: str, start_s: int, end_s: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT ts, level FROM motion
                WHERE camera = ? AND ts >= ? AND ts < ?
                ORDER BY ts
                """,
                (camera, start_s, end_s),
            ).fetchall()

    def delete_motion_before(self, cutoff_s: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM motion WHERE ts < ?", (cutoff_s,))
            self._conn.commit()

    def peak_motion(self, camera: str, start_s: int, end_s: int) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(level) AS level, COUNT(*) AS n FROM motion WHERE camera = ? AND ts >= ? AND ts < ?",
                (camera, start_s, end_s),
            ).fetchone()
        if row is None or int(row["n"]) == 0:
            return None
        return int(row["level"] or 0)

    def user_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT username, password_hash, role, can_live, can_playback, can_settings FROM users WHERE username = ?",
                (username,),
            ).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT username, role, can_live, can_playback, can_settings FROM users ORDER BY username"
            ).fetchall()

    def owner_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'owner'").fetchone()["n"])

    def create_user(
        self,
        username: str,
        password_hash: str,
        created_at: int,
        *,
        role: str = "owner",
        can_live: int = 1,
        can_playback: int = 1,
        can_settings: int = 1,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO users (username, password_hash, created_at, role, can_live, can_playback, can_settings)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, password_hash, created_at, role, can_live, can_playback, can_settings),
            )
            self._conn.commit()

    def update_user_access(self, username: str, role: str, can_live: int, can_playback: int, can_settings: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET role = ?, can_live = ?, can_playback = ?, can_settings = ? WHERE username = ?",
                (role, can_live, can_playback, can_settings, username),
            )
            self._conn.commit()

    def delete_user(self, username: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM user_cameras WHERE username = ?", (username,))
            self._conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            self._conn.execute("DELETE FROM users WHERE username = ?", (username,))
            self._conn.commit()

    def cameras_for_user(self, username: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT camera FROM user_cameras WHERE username = ? ORDER BY camera",
                (username,),
            ).fetchall()
        return [str(row["camera"]) for row in rows]

    def set_user_cameras(self, username: str, camera_ids: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM user_cameras WHERE username = ?", (username,))
            self._conn.executemany(
                "INSERT INTO user_cameras (username, camera) VALUES (?, ?)",
                [(username, camera_id) for camera_id in camera_ids],
            )
            self._conn.commit()

    def recording_path(self, camera: str, day: str, name: str) -> str | None:
        needle = f"/{day}/{name}"
        with self._lock:
            rows = self._conn.execute("SELECT path FROM segments WHERE camera = ?", (camera,)).fetchall()
        for row in rows:
            path = str(row["path"])
            if path.endswith(needle):
                return path
        return None

    def set_password(self, username: str, password_hash: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username))
            self._conn.commit()

    def save_session(self, token_hash: str, username: str, expires_at: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (expires_at - 31 * 86400,))
            self._conn.execute(
                "INSERT INTO sessions (token_hash, username, expires_at) VALUES (?, ?, ?)",
                (token_hash, username, expires_at),
            )
            self._conn.commit()

    def session_user(self, token_hash: str, now: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT username, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None or int(row["expires_at"]) < now:
            return None
        return str(row["username"])

    def delete_sessions_for(self, username: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            self._conn.commit()

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self._conn.commit()

    def list_cameras(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT id, name, url, sort_order FROM cameras ORDER BY sort_order, id").fetchall()

    def camera_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS n FROM cameras").fetchone()["n"])

    def insert_camera(self, camera_id: str, name: str, url: str, sort_order: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cameras (id, name, url, sort_order) VALUES (?, ?, ?, ?)",
                (camera_id, name, url, sort_order),
            )
            self._conn.commit()

    def update_camera(self, camera_id: str, name: str, url: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE cameras SET name = ?, url = ? WHERE id = ?", (name, url, camera_id))
            self._conn.commit()

    def delete_camera(self, camera_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM zones WHERE camera = ?", (camera_id,))
            self._conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            self._conn.commit()

    def zones_for(self, camera_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, name, x, y, w, h FROM zones WHERE camera = ? ORDER BY id",
                (camera_id,),
            ).fetchall()

    def all_zones(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT camera, id, name, x, y, w, h FROM zones ORDER BY camera, id").fetchall()

    def replace_zones(self, camera_id: str, zones: list[tuple]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM zones WHERE camera = ?", (camera_id,))
            self._conn.executemany(
                "INSERT INTO zones (camera, id, name, x, y, w, h) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(camera_id, *zone) for zone in zones],
            )
            self._conn.commit()

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()
