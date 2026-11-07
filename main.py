#!/usr/bin/env python3
"""
thermo launcher: a lazygit-style terminal UI that runs the whole mesh.

    python main.py --fast        # 1 s readings, 2-min "day", 15 s forecast: visible trends at once
    python main.py               # real-time: one reading per minute
    python main.py --empty       # start with nothing, add everything yourself

Sensors and forecasters are real OS processes (python -m thermo.sensor /
thermo.forecaster) that find each other over radiodish; this window just starts
and stops them. The dashboard on the right is an in-process radioactor Actor,
the same one `python -m thermo.dashboard` runs, drawn inside the TUI.

Keys: j/k or arrows select | s add sensor | f add forecaster | x stop |
      r restart | d remove stopped | q quit. Mouse: click a key hint or a row.
Needs a Unix-like terminal (curses). On Windows: pip install windows-curses.
"""
from __future__ import annotations

import argparse
import asyncio
import curses
import locale
import os
import signal
import sys
import time
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from thermo.common import HISTORY, poll_for_sensors  # noqa: E402
from thermo.dashboard import Dashboard, sparkline      # noqa: E402


class QuietDashboard(Dashboard):
    """Same actor as thermo.dashboard, minus the stdout render loop: curses owns the screen."""

    async def on_register(self):
        self._tasks = [
            asyncio.ensure_future(poll_for_sensors(self)),
            asyncio.ensure_future(self._forecast_loop()),
        ]


class Proc:
    def __init__(self, kind, name, module, args):
        self.kind, self.name, self.module, self.args = kind, name, module, args
        self.proc = None
        self.returncode = None
        self.logs = deque(maxlen=300)

    @property
    def label(self):
        return f"{self.kind} {self.name}"

    @property
    def running(self):
        return self.proc is not None and self.returncode is None

    def state(self):
        if self.proc is None:
            return "starting"
        return f"pid {self.proc.pid}" if self.returncode is None else f"exit {self.returncode}"


class App:
    def __init__(self, args, dash):
        self.args, self.dash = args, dash
        self.procs = []
        self.sel = 0
        self.alive = True
        self.prompt = None          # None or the text typed so far
        self.flash, self.flash_until = "", 0.0
        self.zones, self.row_zones = [], []
        self.bar = [("s", "add sensor", self.act_sensor), ("f", "add forecaster", self.act_forecaster),
                    ("x", "stop", self.act_stop), ("r", "restart", self.act_restart),
                    ("d", "remove", self.act_remove), ("q", "quit", self.act_quit)]

    # ---- processes ---------------------------------------------------------

    async def _start(self, p):
        env = dict(os.environ, PYTHONUNBUFFERED="1",
                   PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        p.returncode = None
        p.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-m", p.module, *p.args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL, cwd=str(ROOT), env=env)
        p.logs.append(f"$ python -m {p.module} {' '.join(p.args)}")
        asyncio.ensure_future(self._pump(p, p.proc))

    async def _pump(self, p, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            p.logs.append(line.decode(errors="replace").rstrip())
        rc = await proc.wait()
        if p.proc is proc:
            p.returncode = rc
            p.logs.append(f"[exited with code {rc}]")

    async def _stop(self, p):
        pr = p.proc
        if pr is None or pr.returncode is not None:
            return
        pr.terminate()
        try:
            await asyncio.wait_for(pr.wait(), 2.0)
        except asyncio.TimeoutError:
            pr.kill()
            await pr.wait()

    async def _restart(self, p):
        await self._stop(p)
        await self._start(p)

    async def shutdown(self):
        await asyncio.gather(*(self._stop(p) for p in self.procs), return_exceptions=True)

    # ---- actions -----------------------------------------------------------

    def say(self, text):
        self.flash, self.flash_until = text, time.time() + 4

    def current(self):
        return self.procs[self.sel] if 0 <= self.sel < len(self.procs) else None

    def add_sensor(self, name):
        if any(p.kind == "sensor" and p.name == name for p in self.procs):
            return self.say(f"sensor '{name}' already listed (names are identity on the mesh)")
        a = self.args
        p = Proc("sensor", name, "thermo.sensor",
                 ["--name", name, "--interval", f"{a.interval:g}", "--period", f"{a.period:g}"])
        self.procs.append(p)
        self.sel = len(self.procs) - 1
        asyncio.ensure_future(self._start(p))

    def add_forecaster(self):
        n = sum(1 for p in self.procs if p.kind == "forecaster") + 1
        p = Proc("forecaster", f"#{n}", "thermo.forecaster", [])
        self.procs.append(p)
        self.sel = len(self.procs) - 1
        asyncio.ensure_future(self._start(p))

    def act_sensor(self):
        self.prompt = ""

    def act_forecaster(self):
        self.add_forecaster()

    def act_stop(self):
        p = self.current()
        if p and p.running:
            asyncio.ensure_future(self._stop(p))

    def act_restart(self):
        p = self.current()
        if p:
            asyncio.ensure_future(self._restart(p))

    def act_remove(self):
        p = self.current()
        if p and not p.running and p.proc is not None:
            self.procs.remove(p)
            self.sel = max(0, min(self.sel, len(self.procs) - 1))
        elif p:
            self.say("stop it first (x)")

    def act_quit(self):
        self.alive = False

    # ---- input -------------------------------------------------------------

    def on_key(self, ch):
        if self.prompt is not None:
            if ch in (10, 13, curses.KEY_ENTER):
                raw, self.prompt = self.prompt, None
                name = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw.strip())[:20]
                self.add_sensor(name or f"sensor-{uuid.uuid4().hex[:4]}")
            elif ch == 27:
                self.prompt = None
            elif ch in (127, 8, curses.KEY_BACKSPACE):
                self.prompt = self.prompt[:-1]
            elif 32 <= ch < 127 and len(self.prompt) < 40:
                self.prompt += chr(ch)
            return
        if ch in (ord("q"), ord("Q"), 3):
            self.act_quit()
        elif ch in (ord("j"), curses.KEY_DOWN):
            self.sel = min(self.sel + 1, len(self.procs) - 1)
        elif ch in (ord("k"), curses.KEY_UP):
            self.sel = max(self.sel - 1, 0)
        elif ch == curses.KEY_MOUSE:
            self.on_mouse()
        else:
            for key, _, action in self.bar:
                if ch == ord(key):
                    action()

    def on_mouse(self):
        try:
            _, mx, my, _, state = curses.getmouse()
        except curses.error:
            return
        if not state & curses.BUTTON1_PRESSED:
            return
        if my == self.scr.getmaxyx()[0] - 1 and self.prompt is None:
            for x0, x1, action in self.zones:
                if x0 <= mx < x1:
                    return action()
        for y, i in self.row_zones:
            if y == my:
                self.sel = i

    # ---- drawing -----------------------------------------------------------

    def setup(self, scr):
        self.scr = scr
        curses.raw()            # Ctrl-C arrives as a key, so we always shut children down cleanly
        curses.noecho()
        scr.keypad(True)
        scr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        curses.mousemask(curses.BUTTON1_PRESSED)
        self.col = {"green": 0, "red": 0, "yellow": 0, "cyan": 0}
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for i, (name, c) in enumerate([("green", curses.COLOR_GREEN), ("red", curses.COLOR_RED),
                                           ("yellow", curses.COLOR_YELLOW), ("cyan", curses.COLOR_CYAN)], 1):
                curses.init_pair(i, c, -1)
                self.col[name] = curses.color_pair(i)

    def put(self, y, x, text, attr=0):
        H, W = self.scr.getmaxyx()
        if 0 <= y < H and 0 <= x < W:
            try:
                self.scr.addstr(y, x, text[: W - x], attr)
            except curses.error:     # writing the bottom-right cell raises; harmless
                pass

    def panel(self, y, x, h, w, title):
        if h < 3 or w < 6:
            return
        t = f"─ {title} "
        self.put(y, x, "┌" + t + "─" * max(0, w - 2 - len(t)) + "┐")
        self.put(y, x + 2, f" {title} "[:w - 4], self.col["cyan"] | curses.A_BOLD)
        for r in range(1, h - 1):
            self.put(y + r, x, "│")
            self.put(y + r, x + w - 1, "│")
        self.put(y + h - 1, x, "└" + "─" * (w - 2) + "┘")

    def draw(self):
        scr = self.scr
        scr.erase()
        H, W = scr.getmaxyx()
        self.zones, self.row_zones = [], []
        if H < 10 or W < 60:
            self.put(0, 0, "terminal too small (need 60x10)")
            scr.refresh()
            return
        body = H - 1
        lw = min(40, max(30, W // 3))
        ph = max(5, body // 2)
        self.draw_procs(0, 0, ph, lw)
        self.draw_log(ph, 0, body - ph, lw)
        self.draw_dash(0, lw, body, W - lw)
        self.draw_bar(H, W)
        scr.refresh()

    def draw_procs(self, y, x, h, w):
        self.panel(y, x, h, w, "Processes")
        ih, iw = h - 2, w - 2
        if not self.procs:
            self.put(y + 1, x + 2, "none: press s or f", curses.A_DIM)
        start = max(0, self.sel - ih + 1)
        for r, i in enumerate(range(start, min(len(self.procs), start + ih))):
            p = self.procs[i]
            dot, colr = ("●", self.col["green"]) if p.running else (
                ("○", self.col["red"]) if p.proc is not None else ("◌", self.col["yellow"]))
            line = f" {dot} {p.label:<19.19} {p.state()}"
            hl = curses.A_REVERSE if i == self.sel else 0
            self.put(y + 1 + r, x + 1, line.ljust(iw)[:iw], hl)
            self.put(y + 1 + r, x + 2, dot, colr | hl)
            self.row_zones.append((y + 1 + r, i))

    def draw_log(self, y, x, h, w):
        p = self.current()
        self.panel(y, x, h, w, f"Log: {p.label}" if p else "Log")
        if not p:
            return
        ih, iw = h - 2, w - 3
        for r, line in enumerate(list(p.logs)[-ih:]):
            self.put(y + 1 + r, x + 2, line[:iw], curses.A_DIM)

    def draw_dash(self, y, x, h, w):
        d = self.dash
        names = sorted(d.book.sensors)
        self.panel(y, x, h, w, "Dashboard")
        iw = w - 4
        cx = x + 2
        self.put(y + 1, cx, f"{len(names)} sensor(s)  ·  forecast +{d.horizon:g} min"[:iw], curses.A_BOLD)
        if not names:
            self.put(y + 3, cx, "waiting for sensors… press s to add one"[:iw], curses.A_DIM)
            return
        fixed = 14 + 9 + 17 + 12
        tw = max(0, min(HISTORY, iw - fixed - 2))   # the trend column is tw + 2 wide
        if tw < 8:
            tw = 0
        cols = [("SENSOR", 14), ("NOW", 9)] + ([("TREND", tw + 2)] if tw else []) + [("FORECAST", 17), ("STATUS", 12)]
        self.put(y + 3, cx, "".join(f"{n:<{c}}" for n, c in cols)[:iw], curses.A_UNDERLINE)
        now = time.time()
        for r, name in enumerate(names[: h - 6]):
            hist = d.book.sensors[name]
            last = hist[-1]
            age = now - last["ts"]
            live = age < 2.5 * last["interval_s"]
            fc = d.forecasts.get(name)
            fc_txt = f"{fc['celsius']:.1f}±{fc['band']:.1f} °C" if isinstance(fc, dict) else (fc or "…")
            row, cxx = y + 4 + r, cx
            for text, width, attr in [
                (name, 14, 0), (f"{last['celsius']:.1f} °C", 9, curses.A_BOLD),
                *([(sparkline([v["celsius"] for v in hist][-tw:]), tw + 2, self.col["cyan"])] if tw else []),
                (fc_txt, 17, 0 if isinstance(fc, dict) else curses.A_DIM),
                ("live" if live else f"offline {int(age)}s", 12, self.col["green"] if live else self.col["yellow"]),
            ]:
                self.put(row, cxx, f"{text:<{width}.{width - 1}}", attr)
                cxx += width

    def draw_bar(self, H, W):
        if self.prompt is not None:
            self.put(H - 1, 1, f"New sensor name (enter = random, esc = cancel): {self.prompt}▏", curses.A_BOLD)
            return
        x = 1
        for key, label, action in self.bar:
            self.put(H - 1, x, key, self.col["cyan"] | curses.A_BOLD)
            self.put(H - 1, x + 2, label)
            self.zones.append((x, x + 3 + len(label), action))
            x += len(label) + 5
        if self.flash and time.time() < self.flash_until:
            self.put(H - 1, max(x, W - len(self.flash) - 2), self.flash, self.col["yellow"])

    async def run(self):
        while self.alive:
            while True:
                ch = self.scr.getch()
                if ch == -1:
                    break
                self.on_key(ch)
            self.draw()
            await asyncio.sleep(0.05)


async def amain(args):
    if args.fast:
        args.interval, args.period, args.horizon = 1.0, 120.0, 0.25
    dash = await QuietDashboard(args.horizon).register()   # before curses, so errors print normally
    app = App(args, dash)
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, app.act_quit)
    except (NotImplementedError, AttributeError):
        pass
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    scr = curses.initscr()
    try:
        app.setup(scr)
        if not args.empty:
            app.add_forecaster()
            app.add_sensor("kitchen")
        await app.run()
    finally:
        curses.endwin()
        print("stopping processes…")
        await app.shutdown()
        for t in getattr(dash, "_tasks", []):
            t.cancel()
        await dash.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="demo clock: --interval 1 --period 120 --horizon 0.25")
    ap.add_argument("--empty", action="store_true", help="don't autostart a forecaster and a sensor")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between sensor readings")
    ap.add_argument("--period", type=float, default=86400.0, help="fake source: seconds per daily cycle")
    ap.add_argument("--horizon", type=float, default=30.0, help="forecast horizon in minutes")
    try:
        asyncio.run(amain(ap.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()