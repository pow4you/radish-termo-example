# thermo: a room-temperature mesh on radiodish-actor

Three kinds of app, **no broker, no config files, no addresses**:

| app | what it does |
|---|---|
| `thermo.sensor` | Picks its own name, publishes a reading every `--interval` seconds (default 60). Fake source only for now. |
| `thermo.forecaster` | Listens to all readings, answers "what will sensor X read in N minutes?" |
| `thermo.dashboard` | Terminal table. A row appears when a new sensor name shows up; silent sensors flip to `offline`. |

## Run it

```bash
pip install radiodish-actor        # or: uv sync
# one terminal each, any order, any machine on the LAN:
python -m thermo.forecaster
python -m thermo.dashboard
python -m thermo.sensor --name kitchen
python -m thermo.sensor --name bedroom     # watch a new row appear
```

Real-time readings are one per minute, so the first forecast takes ~3 minutes.
For a fast demo shrink the clock (a fake "day" every 2 minutes, forecast 15 s ahead):

```bash
python -m thermo.sensor --name kitchen --interval 1 --period 120
python -m thermo.dashboard --horizon 0.25
```

Tests (pure logic, no network): `pip install pytest && pytest`

## Where radiodish's design shows up

- **Group addressing is the whole protocol** (`thermo/common.py`). Three group
  strings, namespaced with a prefix because exposed names are not actor-namespaced.
- **Fan-out for free.** A sensor does one `send("room.temperature", ...)`; the
  forecaster and the dashboard both receive it. Add a logger or an alerting app
  later without touching the sensor.
- **Dynamic discovery.** The dashboard has no sensor list. `ReadingBook.sensors`
  grows when a new name arrives; a new row is just a dict key.
- **Late joiners.** Sensors expose `whois`; a new dashboard or forecaster
  broadcasts to it and sensors replay their recent readings. Start the dashboard
  an hour in and the trend column is already full.
- **Request/response on a broadcast transport.** The dashboard `call()`s
  `room.forecast`. Run two forecasters and the first reply wins; run none and
  `call` times out and the row says `no forecaster`. Nothing crashes either way.
- **Errors cross the wire.** The forecaster `raise`s `ValueError("need at least 3
  readings")`; the dashboard catches it as `RadioDishError` and shows the text.
- **Sensors name themselves.** `self.id = name` before `super().__init__()`, so
  the name appears in the node's instance id and logs.

## Honest caveats (inherited from the transport)

- Delivery is at-least-once and lossy, so every handler is idempotent:
  `ReadingBook.add` ignores anything not strictly newer than what it holds.
- `send()` is single-shot. The first reading after startup can be lost while
  wiring converges; the `whois` replay covers that gap.
- Sensor names are identity. Two sensors with the same name merge into one row.
- A `call` to a group with several handlers keeps only the first reply. If one
  forecaster is fresh and another has the history, the fresh one may answer with
  an error. Backfill makes this rare, but it is real.
- Only `--source fake` exists. To add hardware, give any object with
  `read() -> float` to `TemperatureSensor`.

## Swapping the forecast

`thermo/forecast.py::linear_forecast` is a plain function (least-squares line
plus a 95% band). Replace it with anything smarter; nothing else changes.
