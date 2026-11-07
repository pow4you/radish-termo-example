"""
Sensor app. Run as many as you like, anywhere on the LAN:

    python -m thermo.sensor --name kitchen
    python -m thermo.sensor --name bedroom --interval 1 --period 120   # fast demo: a "day" every 2 min

Each sensor picks its own name; that name is its identity on the mesh.
"""
from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from collections import deque

from radioactor import Actor, expose

from thermo.common import HISTORY, READINGS, WHOIS
from thermo.fake import FakeReader


class TemperatureSensor(Actor):
    def __init__(self, name: str, reader, interval: float):
        # actor_id() is read inside Actor.__init__, so set the id first: the
        # name then shows up in logs and in this node's instance id.
        self.id = name
        # depends_on pre-warms wiring to whoever listens for READINGS, so the
        # first reading doesn't get sent into the void. Optional, but cheap.
        super().__init__(depends_on=[READINGS])
        self.name = name
        self.reader = reader
        self.interval = interval
        self.recent = deque(maxlen=HISTORY)

    async def on_register(self):
        self._loop_task = asyncio.ensure_future(self._loop())

    async def _loop(self):
        while True:
            reading = {
                "sensor": self.name,
                "celsius": round(self.reader.read(), 2),
                "ts": time.time(),
                "interval_s": self.interval,
            }
            self.recent.append(reading)
            # Fire and forget. No idea who (if anyone) is listening -- that
            # is the point. Late joiners backfill through whois() below.
            self.send(READINGS, reading)
            print(f"[{self.name}] {reading['celsius']:.2f} C", flush=True)
            await asyncio.sleep(self.interval)

    @expose(groups=WHOIS)
    async def whois(self, payload):
        """A new dashboard/forecaster asked who's here: replay recent readings."""
        try:
            n = max(1, min(HISTORY, int((payload or {}).get("replay", 1))))
        except (TypeError, ValueError):
            n = 1
        for reading in list(self.recent)[-n:]:
            self.send(READINGS, reading)
        return {"sensor": self.name}


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--name", default=f"sensor-{uuid.uuid4().hex[:4]}",
                    help="sensor name (default: random; pass one for a stable identity)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between readings (default 60)")
    ap.add_argument("--source", choices=["fake"], default="fake",
                    help="where readings come from; only 'fake' exists so far")
    ap.add_argument("--period", type=float, default=86400.0,
                    help="fake source: seconds per daily cycle (default 86400; shrink for demos)")
    args = ap.parse_args()

    reader = FakeReader(args.name, period_s=args.period)
    sensor = await TemperatureSensor(args.name, reader, args.interval).register()
    print(f"sensor '{args.name}' up ({sensor.instance_id()}), every {args.interval:g}s", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await sensor.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
