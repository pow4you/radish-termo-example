"""
Terminal dashboard. There is no sensor list anywhere: a row appears the first
time a sensor's name shows up in the READINGS stream, and a row whose sensor
goes quiet flips to "offline".

    python -m thermo.dashboard [--horizon 30]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from radioactor import Actor, expose
from radish import RadioDishError

from thermo.common import FORECAST, HISTORY, READINGS, ReadingBook, parse_reading, poll_for_sensors

BARS = "▁▂▃▄▅▆▇█"


def sparkline(values) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return "".join(BARS[int((v - lo) / span * (len(BARS) - 1))] for v in values)


class Dashboard(Actor):
    id = "dashboard"

    def __init__(self, horizon_min: float = 30.0):
        super().__init__()
        self.horizon = horizon_min
        self.book = ReadingBook()
        self.forecasts = {}   # sensor name -> result dict, or an error string

    async def on_register(self):
        self._tasks = [
            asyncio.ensure_future(poll_for_sensors(self)),
            asyncio.ensure_future(self._forecast_loop()),
            asyncio.ensure_future(self._render_loop()),
        ]

    @expose(groups=READINGS)
    async def on_reading(self, payload):
        reading = parse_reading(payload)
        if reading:
            self.book.add(reading)

    async def _ask(self, name: str):
        try:
            self.forecasts[name] = await self.call(
                FORECAST, {"sensor": name, "horizon_min": self.horizon}, timeout=3.0)
        except asyncio.TimeoutError:
            self.forecasts[name] = "no forecaster"
        except RadioDishError as e:          # forecaster answered with an error
            self.forecasts[name] = str(e)

    async def _forecast_loop(self):
        while True:
            names = list(self.book.sensors)
            await asyncio.gather(*(self._ask(n) for n in names))
            # poll fast until every known sensor has an answer, then relax
            pending = any(n not in self.forecasts for n in self.book.sensors)
            await asyncio.sleep(1.0 if pending or not names else 5.0)

    def render(self) -> str:
        now = time.time()
        names = sorted(self.book.sensors)
        w = HISTORY + 2
        out = [f"radiodish thermo  |  {len(names)} sensor(s)  |  forecast +{self.horizon:g} min  |  "
               f"{time.strftime('%H:%M:%S')}", ""]
        out.append(f"{'SENSOR':<16}{'NOW':>9}  {'MIN-MAX':<13}{'TREND':<{w}}{'FORECAST':<16}STATUS")
        for name in names:
            hist = self.book.sensors[name]
            last = hist[-1]
            temps = [r["celsius"] for r in hist]
            age = now - last["ts"]
            live = age < 2.5 * last["interval_s"]
            fc = self.forecasts.get(name)
            if isinstance(fc, dict):
                fc_txt = f"{fc['celsius']:.1f} ±{fc['band']:.1f} C"
            else:
                fc_txt = fc or "..."
            out.append(
                f"{name[:15]:<16}{last['celsius']:>7.1f} C  "
                f"{min(temps):>5.1f}-{max(temps):<6.1f}"
                f"{sparkline(temps):<{w}}{fc_txt[:15]:<16}"
                f"{'live' if live else f'offline ({int(age)}s ago)'}"
            )
        if not names:
            out.append("(waiting for sensors -- start one: python -m thermo.sensor --name kitchen)")
        return "\n".join(out)

    async def _render_loop(self):
        tty = sys.stdout.isatty()
        while True:
            text = self.render()
            if tty:
                sys.stdout.write("\x1b[H\x1b[2J" + text + "\n")
            else:
                sys.stdout.write(text + "\n" + "-" * 60 + "\n")
            sys.stdout.flush()
            await asyncio.sleep(1.0)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--horizon", type=float, default=30.0, help="forecast horizon in minutes")
    args = ap.parse_args()
    dash = await Dashboard(args.horizon).register()
    try:
        await asyncio.Event().wait()
    finally:
        await dash.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
