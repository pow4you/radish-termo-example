"""Pure unit tests: no network, no event loop."""
import pytest

from thermo.common import ReadingBook, parse_reading
from thermo.fake import FakeReader
from thermo.forecast import linear_forecast


def line(n, slope_per_min, start=20.0, step_s=60.0, t0=1_000_000.0):
    return [{"sensor": "x", "celsius": start + slope_per_min * (i * step_s / 60),
             "ts": t0 + i * step_s, "interval_s": step_s} for i in range(n)]


def test_forecast_extrapolates_a_perfect_line():
    r = linear_forecast(line(10, 0.1), horizon_min=30)
    assert r["celsius"] == pytest.approx(20.9 + 3.0, abs=0.01)   # last point 20.9, +0.1 C/min * 30
    assert r["slope_per_hour"] == pytest.approx(6.0, abs=0.01)
    assert r["band"] == pytest.approx(0.0, abs=0.01)


def test_forecast_needs_three_points():
    with pytest.raises(ValueError, match="at least 3"):
        linear_forecast(line(2, 0.1), horizon_min=10)


def test_reading_book_drops_duplicates_and_stale_replays():
    book = ReadingBook()
    rs = line(3, 0.0)
    assert all(book.add(r) for r in rs)
    assert not book.add(rs[1])          # replay of an old reading
    assert not book.add(rs[2])          # exact duplicate (at-least-once retry)
    assert len(book.sensors["x"]) == 3


def test_new_sensor_name_creates_a_row():
    book = ReadingBook()
    book.add({"sensor": "kitchen", "celsius": 21, "ts": 1, "interval_s": 60})
    book.add({"sensor": "bedroom", "celsius": 19, "ts": 1, "interval_s": 60})
    assert sorted(book.sensors) == ["bedroom", "kitchen"]


@pytest.mark.parametrize("bad", [None, 5, {}, {"sensor": "a"}, {"sensor": "a", "celsius": "hot", "ts": 1}])
def test_parse_reading_rejects_garbage(bad):
    assert parse_reading(bad) is None


def test_fake_reader_is_deterministic_per_name_and_plausible():
    a1 = [FakeReader("kitchen", t0=0).read(now=60 * i) for i in range(5)]
    a2 = [FakeReader("kitchen", t0=0).read(now=60 * i) for i in range(5)]
    b = [FakeReader("bedroom", t0=0).read(now=60 * i) for i in range(5)]
    assert a1 == a2 and a1 != b
    assert all(10 < t < 30 for t in a1 + b)
