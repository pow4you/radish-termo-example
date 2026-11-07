"""
Shared vocabulary for the three apps.

The whole "protocol" between sensor, forecaster and dashboard is these three
group names plus the shape of a reading. There is no registry, no address
book and no schema server -- apps find each other because they use the same
group strings. Namespaced with a dot-prefix so we don't collide with other
actors on the same LAN (see radiodish docs: function names are NOT namespaced).
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, Dict, Optional

READINGS = "room.temperature"   # pub/sub-style: sensors send(), forecaster + dashboard listen
WHOIS = "room.sensors.whois"    # "anyone out there?" -> sensors replay recent readings
FORECAST = "room.forecast"      # request/response: call() any forecaster for a prediction

HISTORY = 30  # readings kept per sensor (sensor-side replay buffer and consumer-side window)


def parse_reading(p) -> Optional[dict]:
    """Payloads are plain JSON from the network -- never trust their shape."""
    if not isinstance(p, dict):
        return None
    try:
        return {
            "sensor": str(p["sensor"]),
            "celsius": float(p["celsius"]),
            "ts": float(p["ts"]),
            "interval_s": float(p.get("interval_s", 60.0)),
        }
    except (KeyError, TypeError, ValueError):
        return None


class ReadingBook:
    """
    Per-sensor reading history. Sensors appear in `.sensors` the first time we
    hear their name -- that dict growing is the dashboard's whole "dynamic
    discovery" story.

    The mesh is at-least-once (retries, replays), so add() drops anything that
    isn't strictly newer than what we already have for that sensor. That makes
    every handler idempotent without needing message ids.
    """

    def __init__(self, maxlen: int = HISTORY):
        self.maxlen = maxlen
        self.sensors: Dict[str, Deque[dict]] = {}

    def add(self, reading: dict) -> bool:
        hist = self.sensors.setdefault(reading["sensor"], deque(maxlen=self.maxlen))
        if hist and reading["ts"] <= hist[-1]["ts"]:
            return False
        hist.append(reading)
        return True


async def poll_for_sensors(actor, every: float = 30.0) -> None:
    """
    Late joiners ask the mesh who is already out there. Sensors answer by
    re-sending their recent readings to READINGS, so a freshly started
    dashboard or forecaster backfills instantly instead of waiting a full
    sensor interval.

    send() is single-shot and wiring takes a moment after startup, so the
    first ask is repeated a few times; after that a cheap "latest only" ask
    every `every` seconds heals any datagram UDP dropped along the way.
    """
    for delay in (0.0, 1.5, 3.0):
        await asyncio.sleep(delay)
        actor.send(WHOIS, {"replay": HISTORY})
    while True:
        await asyncio.sleep(every)
        actor.send(WHOIS, {"replay": 1})
