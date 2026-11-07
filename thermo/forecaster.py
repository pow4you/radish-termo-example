"""
Forecasting service. Listens to every sensor's readings, answers forecast
requests. Start two of them and calls just get answered by whichever replies
first; start none and the dashboard shows "no forecaster" and carries on.

    python -m thermo.forecaster
"""
from __future__ import annotations

import asyncio

from radioactor import Actor, expose

from thermo.common import FORECAST, READINGS, ReadingBook, parse_reading, poll_for_sensors
from thermo.forecast import linear_forecast


class Forecaster(Actor):
    id = "forecaster"

    def __init__(self):
        super().__init__()
        self.book = ReadingBook()

    async def on_register(self):
        self._poll_task = asyncio.ensure_future(poll_for_sensors(self))

    @expose(groups=READINGS)
    async def on_reading(self, payload):
        reading = parse_reading(payload)
        if reading:
            self.book.add(reading)

    @expose(groups=FORECAST)
    async def forecast(self, payload):
        """payload: {"sensor": "kitchen", "horizon_min": 30}
        Raising is fine: Actor turns the exception into an error reply and the
        caller gets it as a RadioDishError."""
        payload = payload or {}
        name = payload.get("sensor")
        hist = self.book.sensors.get(name)
        if not hist:
            raise LookupError(f"no readings for {name!r}")
        horizon = max(0.05, min(240.0, float(payload.get("horizon_min", 30))))
        result = linear_forecast(hist, horizon)
        result["sensor"] = name
        return result


async def main():
    fc = await Forecaster().register()
    print(f"forecaster up ({fc.instance_id()}), waiting for sensors...", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await fc.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
