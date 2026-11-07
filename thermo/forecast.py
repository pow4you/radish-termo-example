"""Pure forecasting maths, no networking, so it's trivially unit-testable."""
from __future__ import annotations

import math
from typing import Sequence


def linear_forecast(readings: Sequence[dict], horizon_min: float, window: int = 20) -> dict:
    """
    Least-squares line through the last `window` readings, extrapolated
    `horizon_min` minutes past the newest one, with a 95% prediction band.

    Deliberately simple: swap this one function for something smarter
    (Holt-Winters, a seasonal model) and nothing else in the mesh changes.
    Raises ValueError when there isn't enough data -- that message travels
    back over the wire as the call()'s error.
    """
    pts = list(readings)[-window:]
    n = len(pts)
    if n < 3:
        raise ValueError(f"need at least 3 readings, have {n}")

    t_last = pts[-1]["ts"]
    xs = [(p["ts"] - t_last) / 60.0 for p in pts]       # minutes, newest = 0
    ys = [p["celsius"] for p in pts]
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("readings all share one timestamp")
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    intercept = ybar - slope * xbar

    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    sigma = math.sqrt(sum(r * r for r in resid) / (n - 2))
    pred = intercept + slope * horizon_min
    band = 1.96 * sigma * math.sqrt(1 + 1 / n + (horizon_min - xbar) ** 2 / sxx)

    return {
        "celsius": round(pred, 2),
        "band": round(band, 2),
        "slope_per_hour": round(slope * 60.0, 2),
        "n": n,
        "horizon_min": horizon_min,
    }
