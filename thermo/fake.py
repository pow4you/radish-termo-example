"""
Fake temperature source, so the whole mesh is testable without hardware.

Model: per-room mean + a daily sine wave + a slow random-walk drift + sensor
noise. Everything is seeded from the sensor's name, so "kitchen" always
behaves like "kitchen" but differs from "bedroom".

`period_s` is the length of the daily cycle in seconds (default: a real day).
For demos shrink it, e.g. --interval 1 --period 120 gives a full "day" every
two minutes, so trends are visible within seconds. Timestamps stay real, so
forecast horizons stay meaningful (use a matching small --horizon).
"""
from __future__ import annotations

import math
import random
import time
import zlib
from typing import Optional


class FakeReader:
    def __init__(self, name: str, period_s: float = 86400.0, seed: Optional[int] = None,
                 t0: Optional[float] = None):
        self.rng = random.Random(seed if seed is not None else zlib.crc32(name.encode()))
        self.base = 19.0 + self.rng.random() * 4.0       # 19-23 C room mean
        self.amp = 1.0 + self.rng.random() * 1.5         # 1-2.5 C daily swing
        self.phase = self.rng.random() * 2 * math.pi
        self.drift = 0.0
        self.period_s = period_s
        self.t0 = time.time() if t0 is None else t0

    def read(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        day_frac = (now - self.t0) / self.period_s
        self.drift = 0.95 * self.drift + self.rng.gauss(0, 0.05)
        daily = self.amp * math.sin(2 * math.pi * day_frac + self.phase)
        return self.base + daily + self.drift + self.rng.gauss(0, 0.05)
