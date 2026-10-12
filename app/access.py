from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    can_live: bool
    can_playback: bool
    can_settings: bool
    cameras: frozenset[str]

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def can_camera(self, camera_id: str) -> bool:
        return self.is_owner or camera_id in self.cameras
