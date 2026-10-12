from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger("nvr.ha")


class HomeAssistant:
    """Publish motion sensors over MQTT, including Home Assistant discovery."""

    def __init__(self, store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._client = None
        self._connected = False
        self._active: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._disconnect()

    def reconfigure(self) -> None:
        self._disconnect()

    def update(self, camera_id: str, level: int, zones: dict[str, int]) -> None:
        settings = self.store.mqtt()
        if not settings.get("enabled") or not self._connected:
            return
        threshold = self.store.policy().motion_percent
        hold = int(settings.get("holdSeconds") or 10)
        now = time.time()
        names = {camera.id: camera.name for camera in self.store.cameras()}
        zone_names = {zone["id"]: zone["name"] for zone in self.store.zones(camera_id)}
        self._set_sensor(settings, camera_id, "", names.get(camera_id, camera_id), level >= threshold, now, hold)
        for zone_id, zone_level in zones.items():
            self._set_sensor(
                settings,
                camera_id,
                zone_id,
                zone_names.get(zone_id, zone_id),
                zone_level >= threshold,
                now,
                hold,
            )

    def _set_sensor(self, settings: dict, camera_id: str, zone_id: str, name: str, active: bool, now: float, hold: int) -> None:
        key = f"{camera_id}/{zone_id}"
        prefix = settings["prefix"]
        topic = f"{prefix}/{camera_id}/motion" if not zone_id else f"{prefix}/{camera_id}/zone/{zone_id}"
        if active:
            self._active[key] = now
            self._publish(topic, "ON")
            return
        last = self._active.get(key)
        if last is None:
            return
        if now - last >= hold:
            self._active.pop(key, None)
            self._publish(topic, "OFF")

    def _run(self) -> None:
        while not self._stop.wait(5):
            settings = self.store.mqtt()
            if not settings.get("enabled"):
                self._disconnect()
                continue
            if self._connected:
                self._advertise()
                continue
            self._connect(settings)

    def _connect(self, settings: dict) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt is not installed, Home Assistant publishing is off")
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{settings['prefix']}-recorder")
        if settings.get("username"):
            client.username_pw_set(settings["username"], settings.get("password") or None)
        client.will_set(f"{settings['prefix']}/status", "offline", retain=True)
        try:
            client.connect(settings["host"], int(settings["port"]), 30)
            client.loop_start()
        except Exception as exc:
            log.warning("MQTT connect failed: %s", exc)
            return
        with self._lock:
            self._client = client
            self._connected = True
        self._publish(f"{settings['prefix']}/status", "online", retain=True)
        self._advertise()
        log.info("MQTT connected to %s", settings["host"])

    def _disconnect(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._connected = False
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                log.debug("MQTT disconnect failed", exc_info=True)

    def _advertise(self) -> None:
        settings = self.store.mqtt()
        if not settings.get("enabled"):
            return
        prefix = settings["prefix"]
        device = {"identifiers": [prefix], "name": "Perch", "manufacturer": "Perch"}
        for camera in self.store.cameras():
            self._discovery(settings, camera.id, "", f"{camera.name} motion", device)
            for zone in self.store.zones(camera.id):
                self._discovery(settings, camera.id, zone["id"], f"{camera.name} {zone['name']}", device)

    def _discovery(self, settings: dict, camera_id: str, zone_id: str, name: str, device: dict) -> None:
        prefix = settings["prefix"]
        slug = camera_id if not zone_id else f"{camera_id}_{zone_id}"
        state = f"{prefix}/{camera_id}/motion" if not zone_id else f"{prefix}/{camera_id}/zone/{zone_id}"
        payload = {
            "name": name,
            "unique_id": f"{prefix}_{slug}_motion",
            "state_topic": state,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "motion",
            "availability_topic": f"{prefix}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device,
        }
        topic = f"homeassistant/binary_sensor/{prefix}_{slug}/config"
        self._publish(topic, json.dumps(payload), retain=True)

    def _publish(self, topic: str, payload: str, retain: bool = True) -> None:
        with self._lock:
            client = self._client
        if client is None:
            return
        try:
            client.publish(topic, payload, retain=retain)
        except Exception:
            log.warning("MQTT publish failed for %s", topic)
