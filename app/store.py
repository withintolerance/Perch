from __future__ import annotations

import json
import re
import time

from app.access import Principal
from app.config import SAFE_ID, Camera, Settings
from app.db import Database
from app.passwords import hash_password
from app.policy import RecordingPolicy, default_policy, parse_policy

_USER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MQTT_DEFAULT = {
    "enabled": False,
    "host": "",
    "port": 1883,
    "username": "",
    "password": "",
    "prefix": "nvr",
    "holdSeconds": 10,
}


class Store:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._import_cameras()
        self._seed_policy()

    def cameras(self) -> list[Camera]:
        return [
            Camera(str(row["id"]), str(row["name"]), str(row["url"]), str(row["url"]))
            for row in self.db.list_cameras()
        ]

    def camera(self, camera_id: str) -> Camera | None:
        for item in self.cameras():
            if item.id == camera_id:
                return item
        return None

    def zones(self, camera_id: str) -> list[dict]:
        return [_zone_dict(row) for row in self.db.zones_for(camera_id)]

    def policy(self) -> RecordingPolicy:
        raw = self.db.get_setting("recording")
        fallback = default_policy(self.settings.retain_days, self.settings.min_free_gb)
        if not raw:
            return fallback
        return parse_policy(json.loads(raw), fallback)

    def save_policy(self, raw: dict) -> RecordingPolicy:
        policy = parse_policy(raw, self.policy())
        if policy.limit_hours and not policy.days:
            raise ValueError("Pick at least one day, or record all the time.")
        self.db.set_setting("recording", json.dumps(policy.as_dict()))
        return policy

    def mqtt(self) -> dict:
        stored = self.db.get_setting("mqtt")
        data = dict(_MQTT_DEFAULT)
        if stored:
            loaded = json.loads(stored)
            if isinstance(loaded, dict):
                data.update(loaded)
        data["port"] = int(data.get("port") or 1883)
        data["holdSeconds"] = int(data.get("holdSeconds") or 10)
        data["prefix"] = str(data.get("prefix") or "nvr").strip() or "nvr"
        return data

    def mqtt_public(self) -> dict:
        data = self.mqtt()
        data["passwordSet"] = bool(data.get("password"))
        data["password"] = ""
        return data

    def save_mqtt(self, raw: dict) -> dict:
        current = self.mqtt()
        password = str(raw.get("password") or "")
        if not password:
            password = str(current.get("password") or "")
        prefix = str(raw.get("prefix") or "nvr").strip() or "nvr"
        if not SAFE_ID.match(prefix):
            raise ValueError("The Home Assistant topic prefix can use letters, numbers, _ and -.")
        host = str(raw.get("host") or "").strip()
        port = int(raw.get("port") or 1883)
        hold = int(raw.get("holdSeconds") or 10)
        enabled = bool(raw.get("enabled"))
        if enabled and not host:
            raise ValueError("Home Assistant needs the MQTT broker address.")
        if not 1 <= port <= 65535:
            raise ValueError("MQTT port must be from 1 to 65535.")
        if not 1 <= hold <= 300:
            raise ValueError("Motion hold time must be from 1 to 300 seconds.")
        saved = {
            "enabled": enabled,
            "host": host,
            "port": port,
            "username": str(raw.get("username") or ""),
            "password": password,
            "prefix": prefix,
            "holdSeconds": hold,
        }
        self.db.set_setting("mqtt", json.dumps(saved))
        return self.mqtt_public()

    def recordings_dir(self) -> Path:
        raw = self.db.get_setting("recordings_dir")
        if raw:
            return Path(raw)
        return self.settings.recordings_dir

    def set_recordings_dir(self, raw: str) -> Path:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Choose a folder for recordings.")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError("Use a full path, such as /mnt/recordings.")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".nvr-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"That folder cannot be used for recordings: {exc.strerror or exc}") from exc
        if not path.is_dir():
            raise ValueError("That path is not a folder.")
        self.db.set_setting("recordings_dir", str(path.resolve()))
        return path.resolve()

    def principal(self, username: str) -> Principal | None:
        row = self.db.get_user(username)
        if row is None:
            return None
        role = str(row["role"] or "user")
        owner = role == "owner"
        return Principal(
            username=str(row["username"]),
            role=role,
            can_live=True if owner else bool(row["can_live"]),
            can_playback=True if owner else bool(row["can_playback"]),
            can_settings=True if owner else bool(row["can_settings"]),
            cameras=frozenset() if owner else frozenset(self.db.cameras_for_user(username)),
        )

    def list_users(self) -> list[dict]:
        users = []
        for row in self.db.list_users():
            owner = str(row["role"]) == "owner"
            users.append(
                {
                    "username": str(row["username"]),
                    "role": str(row["role"]),
                    "canLive": True if owner else bool(row["can_live"]),
                    "canPlayback": True if owner else bool(row["can_playback"]),
                    "canSettings": True if owner else bool(row["can_settings"]),
                    "cameras": [] if owner else self.db.cameras_for_user(str(row["username"])),
                }
            )
        return users

    def create_account(self, raw: dict) -> dict:
        username = str(raw.get("username") or "").strip()
        password = str(raw.get("password") or "")
        if not valid_username(username):
            raise ValueError("Use a username of letters, numbers, _, . or -.")
        if self.db.get_user(username):
            raise ValueError("That username is already taken.")
        if len(password) < 10:
            raise ValueError("Use a password of at least 10 characters.")
        role = str(raw.get("role") or "user")
        if role not in {"owner", "user"}:
            raise ValueError("Role must be owner or user.")
        can_live, can_playback, can_settings, cameras = self._account_flags(role, raw)
        self.db.create_user(
            username,
            hash_password(password),
            int(time.time()),
            role=role,
            can_live=int(can_live),
            can_playback=int(can_playback),
            can_settings=int(can_settings),
        )
        if role != "owner":
            self.db.set_user_cameras(username, cameras)
        return next(item for item in self.list_users() if item["username"] == username)

    def update_account(self, actor_name: str, username: str, raw: dict) -> dict:
        target = self.db.get_user(username)
        if target is None:
            raise ValueError("That user does not exist.")
        role = str(raw.get("role") or target["role"])
        if role not in {"owner", "user"}:
            raise ValueError("Role must be owner or user.")
        if str(target["role"]) == "owner" and role != "owner" and self.db.owner_count() <= 1:
            raise ValueError("The last owner has to stay an owner.")
        can_live, can_playback, can_settings, cameras = self._account_flags(role, raw)
        self.db.update_user_access(username, role, int(can_live), int(can_playback), int(can_settings))
        if role == "owner":
            self.db.set_user_cameras(username, [])
        elif "cameras" in raw:
            self.db.set_user_cameras(username, cameras)
        password = str(raw.get("password") or "")
        if password:
            if len(password) < 10:
                raise ValueError("Use a password of at least 10 characters.")
            self.db.set_password(username, hash_password(password))
            self.db.delete_sessions_for(username)
        if username == actor_name and role != "owner" and str(target["role"]) == "owner":
            pass
        return next(item for item in self.list_users() if item["username"] == username)

    def delete_account(self, actor_name: str, username: str) -> None:
        if username == actor_name:
            raise ValueError("You cannot delete the account you are signed in with.")
        target = self.db.get_user(username)
        if target is None:
            raise ValueError("That user does not exist.")
        if str(target["role"]) == "owner" and self.db.owner_count() <= 1:
            raise ValueError("The last owner cannot be deleted.")
        self.db.delete_user(username)

    def _account_flags(self, role: str, raw: dict) -> tuple[bool, bool, bool, list[str]]:
        if role == "owner":
            return True, True, True, []
        can_live = bool(raw.get("canLive", True))
        can_playback = bool(raw.get("canPlayback", True))
        can_settings = bool(raw.get("canSettings", False))
        known = {camera.id for camera in self.cameras()}
        cameras = []
        for camera_id in raw.get("cameras") or []:
            camera_id = str(camera_id)
            if camera_id not in known:
                raise ValueError(f"Unknown camera {camera_id}.")
            if camera_id not in cameras:
                cameras.append(camera_id)
        return can_live, can_playback, can_settings, cameras

    def timezone(self) -> str:
        return self.db.get_setting("timezone") or self.settings.timezone

    def set_timezone(self, value: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError("That timezone is not recognized.") from exc
        self.db.set_setting("timezone", value)
        return value

    def create_camera(self, name: str, url: str, camera_id: str | None = None) -> Camera:
        name = name.strip()
        url = url.strip()
        if not name:
            raise ValueError("A camera needs a name.")
        if not url.startswith("rtsp://"):
            raise ValueError("The stream address has to start with rtsp://")
        camera_id = (camera_id or _slug(name)).strip()
        if not SAFE_ID.match(camera_id):
            raise ValueError("The id can use letters, numbers, _ and -.")
        if self.camera(camera_id) is not None:
            raise ValueError("A camera with that id already exists.")
        order = self.db.camera_count()
        self.db.insert_camera(camera_id, name, url, order)
        camera = self.camera(camera_id)
        assert camera is not None
        return camera

    def update_camera(self, camera_id: str, name: str, url: str) -> tuple[Camera, bool]:
        current = self.camera(camera_id)
        if current is None:
            raise ValueError("That camera does not exist.")
        name = name.strip()
        url = url.strip()
        if not name:
            raise ValueError("A camera needs a name.")
        if not url.startswith("rtsp://"):
            raise ValueError("The stream address has to start with rtsp://")
        self.db.update_camera(camera_id, name, url)
        updated = self.camera(camera_id)
        assert updated is not None
        return updated, updated.url != current.url

    def remove_camera(self, camera_id: str) -> None:
        if self.camera(camera_id) is None:
            raise ValueError("That camera does not exist.")
        self.db.delete_camera(camera_id)

    def save_zones(self, camera_id: str, zones: list[dict]) -> list[dict]:
        if self.camera(camera_id) is None:
            raise ValueError("That camera does not exist.")
        if len(zones) > 8:
            raise ValueError("A camera can have up to 8 zones.")
        cleaned: list[tuple] = []
        seen: set[str] = set()
        for zone in zones:
            zone_id = str(zone.get("id") or "").strip()
            zone_name = str(zone.get("name") or "").strip()
            if not SAFE_ID.match(zone_id):
                raise ValueError("Zone ids can use letters, numbers, _ and -.")
            if zone_id in seen:
                raise ValueError(f"Zone {zone_id} is listed twice.")
            if not zone_name:
                raise ValueError(f"Zone {zone_id} needs a name.")
            x, y, w, h = (float(zone[key]) for key in ("x", "y", "w", "h"))
            if min(x, y, w, h) < 0 or x + w > 1.001 or y + h > 1.001 or w <= 0 or h <= 0:
                raise ValueError(f"Zone {zone_name} has to sit inside the picture.")
            seen.add(zone_id)
            cleaned.append((zone_id, zone_name, x, y, w, h))
        self.db.replace_zones(camera_id, cleaned)
        return self.zones(camera_id)

    def _import_cameras(self) -> None:
        if self.db.camera_count():
            return
        now = int(time.time())
        for index, camera in enumerate(self.settings.cameras):
            self.db.insert_camera(camera.id, camera.name, camera.url, index)
        if self.settings.cameras:
            self.db.set_setting("imported_at", str(now))

    def _seed_policy(self) -> None:
        if self.db.get_setting("recording"):
            return
        self.db.set_setting("recording", json.dumps(default_policy(self.settings.retain_days, self.settings.min_free_gb).as_dict()))


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return (slug or "camera")[:40]


def _zone_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "x": float(row["x"]),
        "y": float(row["y"]),
        "w": float(row["w"]),
        "h": float(row["h"]),
    }


def valid_username(username: str) -> bool:
    return bool(_USER.match(username))
