"""Hardware-first location arbitration shared by cloud and Raspberry Pi.

All timestamps are monotonic (not wall clock). Coordinates are decimal WGS84.
Hardware fix expires after 5 s; phone fix expires after 30 s.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

HARDWARE_TTL = 5.0
PHONE_TTL = 30.0


def valid_coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    try:
        lat, lon = float(latitude), float(longitude)
    except (ValueError, TypeError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Original project uses (0, 0) to mean "no GPS fix".
    if lat == 0 and lon == 0:
        return None
    return lat, lon


class PositionManager:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._fixes: dict[str, dict] = {}

    def submit(self, latitude: Any, longitude: Any, source: str, *, origin: str = "") -> bool:
        if source not in ("hardware", "phone"):
            return False
        coordinates = valid_coordinates(latitude, longitude)
        if coordinates is None:
            return False
        lat, lon = coordinates
        with self._lock:
            self._fixes[source] = {
                "latitude": lat, "longitude": lon,
                "time": self._clock(), "origin": origin,
            }
        return True

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            detail = {}
            for source, ttl in (("hardware", HARDWARE_TTL), ("phone", PHONE_TTL)):
                fix = self._fixes.get(source)
                age = max(0.0, now - fix["time"]) if fix else None
                detail[source] = {
                    "available": bool(fix is not None and age <= ttl),
                    "latitude": fix["latitude"] if fix else None,
                    "longitude": fix["longitude"] if fix else None,
                    "age_seconds": round(age, 1) if age is not None else None,
                    "origin": fix["origin"] if fix else "",
                    "expires_after_seconds": ttl,
                }
            winner = next((source for source in ("hardware", "phone") if detail[source]["available"]), None)
            result = {
                "source": winner or "none",
                "source_label": {"hardware": "硬件 GPS 模块", "phone": "手机节点定位", "none": "无可用定位"}[winner or "none"],
                "latitude": detail[winner]["latitude"] if winner else None,
                "longitude": detail[winner]["longitude"] if winner else None,
                "hardware": detail["hardware"],
                "phone": detail["phone"],
                "priority": ["hardware", "phone"],
            }
            return result
