from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.config import Settings, load_settings
from app.db import Database
from app.engine import Engine, post_webrtc
from app.ha import HomeAssistant
from app.passwords import hash_password, hash_token, new_session_token, verify_password
from app.paths import media_file
from app.relay import delete_stream, put_stream, snapshot, sync_streams
from app.store import Store, valid_username

log = logging.getLogger("nvr.api")
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_RANGE_MS = 2 * 86_400_000
COOKIE = "nvr_session"
SESSION_SECONDS = 30 * 24 * 3600
OPEN_PATHS = {"/", "/app.js", "/app.css", "/settings.js", "/api/session", "/api/login", "/api/setup"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.data_dir / "nvr.db")
    store = Store(db, settings)
    engine = Engine(settings, db, store)
    ha = HomeAssistant(store)
    limiter = _LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.on_levels = ha.update
        engine.start()
        try:
            sync_streams(settings, store.cameras())
        except Exception:
            log.warning("could not sync cameras to the relay", exc_info=True)
        ha.start()
        try:
            yield
        finally:
            ha.stop()
            engine.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.store = store
    app.state.engine = engine
    app.state.ha = ha

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        if _user(request, db) is None:
            return JSONResponse({"detail": "Sign in required."}, status_code=401)
        if request.method in {"POST", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and not origin.startswith(f"{request.url.scheme}://{request.headers.get('host', '')}"):
                return JSONResponse({"detail": "This request came from another site."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/app.js")
    def script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/app.css")
    def style():
        return FileResponse(STATIC / "app.css", media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/settings.js")
    def settings_script():
        return FileResponse(STATIC / "settings.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/api/session")
    def session(request: Request):
        principal = _principal(request, store)
        if principal is None:
            return {"authenticated": False, "needsSetup": db.user_count() == 0, "username": None}
        return {
            "authenticated": True,
            "needsSetup": False,
            "username": principal.username,
            "owner": principal.is_owner,
            "canLive": principal.can_live,
            "canPlayback": principal.can_playback,
            "canSettings": principal.can_settings,
        }

    @app.post("/api/setup")
    async def setup(request: Request):
        if db.user_count():
            raise HTTPException(status_code=409, detail="An owner account already exists.")
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not valid_username(username):
            raise HTTPException(status_code=400, detail="Use a username of letters, numbers, _, . or -.")
        if len(password) < 10:
            raise HTTPException(status_code=400, detail="Use a password of at least 10 characters.")
        db.create_user(username, hash_password(password), int(time.time()))
        return _login_response(db, username)

    @app.post("/api/login")
    async def login(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait a few minutes.")
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        row = db.get_user(username)
        if row is None or not verify_password(password, str(row["password_hash"])):
            limiter.fail(ip)
            raise HTTPException(status_code=401, detail="Wrong username or password.")
        limiter.clear(ip)
        return _login_response(db, username)

    @app.post("/api/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token:
            db.delete_session(hash_token(token))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.post("/api/account/password")
    async def change_password(request: Request):
        principal = _require_user(request, store)
        username = principal.username
        body = await request.json()
        row = db.get_user(username)
        if row is None or not verify_password(str(body.get("current") or ""), str(row["password_hash"])):
            raise HTTPException(status_code=400, detail="The current password is wrong.")
        new_password = str(body.get("new") or "")
        if len(new_password) < 10:
            raise HTTPException(status_code=400, detail="Use a password of at least 10 characters.")
        db.set_password(username, hash_password(new_password))
        return {"ok": True}

    @app.get("/api/status")
    def status(request: Request):
        principal = _require_user(request, store)
        policy = store.policy()
        return {
            "ok": not engine.recording_paused,
            "diskFreeGb": round(engine.disk_free_gb(), 1),
            "recordingPaused": engine.recording_paused,
            "message": engine.pause_message,
            "timezone": store.timezone(),
            "retainDays": policy.retain_days,
            "segmentSeconds": settings.segment_seconds,
            "cameras": [camera for camera in engine.status_cameras() if principal.can_camera(camera["id"])],
        }

    @app.get("/api/settings")
    def settings_view(request: Request):
        principal = _require_settings(request, store)
        payload = {
            "timezone": store.timezone(),
            "recordingsDir": str(store.recordings_dir()),
            "recording": store.policy().as_dict(),
            "mqtt": store.mqtt_public(),
            "cameras": [
                {**_camera_admin(camera), "zones": store.zones(camera.id)}
                for camera in store.cameras()
            ],
        }
        if principal.is_owner:
            payload["users"] = store.list_users()
        return payload

    @app.put("/api/settings/storage")
    async def save_storage(request: Request):
        _require_settings(request, store)
        body = await request.json()
        try:
            path = store.set_recordings_dir(str(body.get("recordingsDir") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.restart_all_workers()
        return {"recordingsDir": str(path)}

    @app.get("/api/settings/users")
    def list_accounts(request: Request):
        _require_owner(request, store)
        return {"users": store.list_users()}

    @app.post("/api/settings/users")
    async def add_account(request: Request):
        _require_owner(request, store)
        try:
            return store.create_account(await request.json())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/settings/users/{username}")
    async def edit_account(username: str, request: Request):
        actor = _require_owner(request, store)
        try:
            return store.update_account(actor.username, username, await request.json())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/settings/users/{username}")
    def remove_account(username: str, request: Request):
        actor = _require_owner(request, store)
        try:
            store.delete_account(actor.username, username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.put("/api/settings/recording")
    async def save_recording(request: Request):
        _require_settings(request, store)
        try:
            policy = store.save_policy(await request.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return policy.as_dict()

    @app.put("/api/settings/timezone")
    async def save_timezone(request: Request):
        _require_settings(request, store)
        body = await request.json()
        try:
            value = store.set_timezone(str(body.get("timezone") or "").strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"timezone": value}

    @app.put("/api/settings/mqtt")
    async def save_mqtt(request: Request):
        _require_settings(request, store)
        try:
            saved = store.save_mqtt(await request.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ha.reconfigure()
        return saved

    @app.post("/api/settings/cameras")
    async def add_camera(request: Request):
        _require_settings(request, store)
        body = await request.json()
        try:
            camera = store.create_camera(str(body.get("name") or ""), str(body.get("url") or ""), body.get("id"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        warning = _relay_put(settings, camera.id, camera.url)
        _write_relay(settings, store)
        engine.apply_cameras(store.cameras())
        ha.reconfigure()
        return {**_camera_admin(camera), "warning": warning}

    @app.put("/api/settings/cameras/{camera_id}")
    async def edit_camera(camera_id: str, request: Request):
        _require_settings(request, store)
        body = await request.json()
        try:
            camera, url_changed = store.update_camera(camera_id, str(body.get("name") or ""), str(body.get("url") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        warning = _relay_put(settings, camera.id, camera.url)
        _write_relay(settings, store)
        if url_changed:
            engine.restart_worker(camera.id)
        ha.reconfigure()
        return {**_camera_admin(camera), "warning": warning}

    @app.delete("/api/settings/cameras/{camera_id}")
    def remove_camera(camera_id: str, request: Request):
        _require_settings(request, store)
        try:
            store.remove_camera(camera_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            delete_stream(settings.go2rtc_api, camera_id)
        except Exception as exc:
            log.warning("relay did not drop %s: %s", camera_id, exc)
        _write_relay(settings, store)
        engine.apply_cameras(store.cameras())
        ha.reconfigure()
        return {"ok": True}

    @app.put("/api/settings/cameras/{camera_id}/zones")
    async def save_zones(camera_id: str, request: Request):
        _require_settings(request, store)
        body = await request.json()
        try:
            zones = store.save_zones(camera_id, body.get("zones") or [])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ha.reconfigure()
        return {"zones": zones}

    @app.get("/api/cameras/{camera_id}/snapshot.jpg")
    def camera_snapshot(camera_id: str, request: Request):
        _require_settings(request, store)
        if store.camera(camera_id) is None:
            raise HTTPException(status_code=404, detail="Unknown camera.")
        try:
            data, kind = snapshot(settings.go2rtc_api, camera_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="No snapshot yet. The live stream may still be starting.") from exc
        return Response(content=data, media_type=kind, headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{camera_id}/timeline")
    def timeline(camera_id: str, start: int, end: int, request: Request):
        _require_camera(request, store, camera_id, playback=True)
        if end <= start or end - start > MAX_RANGE_MS:
            raise HTTPException(status_code=400, detail="Pick a range of two days or less.")
        segments = []
        for row in db.segments_between(camera_id, start, end):
            path = Path(row["path"])
            segments.append(
                {
                    "startMs": int(row["start_ms"]),
                    "endMs": int(row["end_ms"]),
                    "ready": True,
                    "url": f"/media/{camera_id}/{path.parent.name}/{path.name}",
                }
            )
        open_segment = engine.open_segment(camera_id)
        if open_segment is not None and open_segment["startMs"] < end:
            segments.append(open_segment)
        motion = [
            {"t": int(row["ts"]), "level": int(row["level"])}
            for row in db.motion_between(camera_id, start // 1000, end // 1000 + 1)
        ]
        return {"segments": segments, "motion": motion}

    @app.post("/api/cameras/{camera_id}/webrtc")
    async def webrtc(camera_id: str, request: Request):
        _require_camera(request, store, camera_id, live=True)
        offer = await request.body()
        if not offer or len(offer) > 65536:
            raise HTTPException(status_code=400, detail="The live-video offer was empty or too large.")
        try:
            answer = await asyncio.to_thread(post_webrtc, settings.go2rtc_api, camera_id, offer)
        except Exception as exc:
            log.warning("live view for %s failed: %s", camera_id, exc)
            raise HTTPException(status_code=502, detail="This camera's live view is not ready yet.") from exc
        return Response(content=answer, media_type="application/sdp")

    @app.get("/media/{camera_id}/{day}/{name}")
    def media(camera_id: str, day: str, name: str, request: Request):
        _require_camera(request, store, camera_id, playback=True)
        try:
            media_file(settings.recordings_dir, camera_id, day, name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Recording not found.") from exc
        stored = db.recording_path(camera_id, day, name)
        path = Path(stored).resolve() if stored else None
        if path is None or not path.is_file() or path.name != name or path.parent.name != day:
            raise HTTPException(status_code=404, detail="That segment is not ready yet.")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    return app


def _user(request: Request, db: Database) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return db.session_user(hash_token(token), int(time.time()))


def _principal(request: Request, store: Store):
    username = _user(request, store.db)
    if username is None:
        return None
    return store.principal(username)


def _require_user(request: Request, store: Store):
    principal = _principal(request, store)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return principal


def _require_settings(request: Request, store: Store):
    principal = _require_user(request, store)
    if not principal.can_settings:
        raise HTTPException(status_code=403, detail="This account cannot change settings.")
    return principal


def _require_owner(request: Request, store: Store):
    principal = _require_user(request, store)
    if not principal.is_owner:
        raise HTTPException(status_code=403, detail="Only an owner can manage users.")
    return principal


def _require_camera(request: Request, store: Store, camera_id: str, *, live: bool = False, playback: bool = False) -> None:
    principal = _require_user(request, store)
    if store.camera(camera_id) is None or not principal.can_camera(camera_id):
        raise HTTPException(status_code=404, detail="Unknown camera.")
    if live and not principal.can_live:
        raise HTTPException(status_code=403, detail="Live view is turned off for this account.")
    if playback and not principal.can_playback:
        raise HTTPException(status_code=403, detail="Playback is turned off for this account.")


def _login_response(db: Database, username: str) -> JSONResponse:
    token = new_session_token()
    db.save_session(hash_token(token), username, int(time.time()) + SESSION_SECONDS)
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_SECONDS, path="/")
    return response


def _camera_admin(camera) -> dict:
    return {"id": camera.id, "name": camera.name, "url": camera.url}


def _relay_put(settings: Settings, camera_id: str, url: str) -> str | None:
    try:
        put_stream(settings.go2rtc_api, camera_id, url)
    except Exception as exc:
        log.warning("relay update failed for %s: %s", camera_id, exc)
        return "Saved here, but the live relay did not accept the stream yet."
    return None


def _write_relay(settings: Settings, store: Store) -> None:
    try:
        from app.relay import write_go2rtc_file

        write_go2rtc_file(settings, store.cameras())
    except OSError as exc:
        log.warning("could not rewrite the relay file: %s", exc)


class _LoginLimiter:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.time()
        recent = [stamp for stamp in self._fails.get(ip, []) if now - stamp < 900]
        self._fails[ip] = recent
        return len(recent) < 8

    def fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def clear(self, ip: str) -> None:
        self._fails.pop(ip, None)
