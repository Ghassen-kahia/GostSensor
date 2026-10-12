"""Building clock.

REPLAY mode (default): the historian is real but ends on 2026-06-30, so the
platform replays it. Building time advances at `speed` x wall-clock from an
anchor; readings become "received" only once building time passes them.
Everything shown under REPLAY is labelled as a replay of historical data.

LIVE mode: building time is wall-clock time and readings arrive through
POST /api/ingest/measurements.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass

from .. import db
from ..config import settings

_lock = threading.Lock()
_range_cache: tuple[int, int] | None = None


@dataclass
class ClockState:
    mode: str
    anchor_ts: int
    anchor_real: float
    speed: float
    paused: bool


def data_range(conn=None) -> tuple[int, int]:
    global _range_cache
    if _range_cache is None:
        lo = db.scalar("SELECT MIN(ts) FROM measurements", conn=conn)
        hi = db.scalar("SELECT MAX(ts) FROM measurements", conn=conn)
        if lo is None:
            return (0, 0)
        _range_cache = (int(lo), int(hi) + 3600)
    return _range_cache


def _parse_iso(value: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _load(conn=None) -> ClockState:
    raw = db.get_state("clock", conn=conn)
    if raw:
        return ClockState(**raw)
    if settings.data_mode == "LIVE":
        state = ClockState("LIVE", int(time.time()), time.time(), 1.0, False)
    else:
        state = ClockState("REPLAY", _parse_iso(settings.replay_start), time.time(), settings.replay_speed, False)
    db.set_state("clock", asdict(state), conn=conn)
    return state


def state(conn=None) -> ClockState:
    return _load(conn)


def now(conn=None) -> int:
    s = _load(conn)
    if s.mode == "LIVE":
        return int(time.time())
    if s.paused:
        t = s.anchor_ts
    else:
        t = s.anchor_ts + (time.time() - s.anchor_real) * s.speed
    lo, hi = data_range(conn)
    return int(min(max(t, lo), hi)) if hi else int(t)


def _save(s: ClockState, conn=None) -> None:
    db.set_state("clock", asdict(s), conn=conn)


def control(action: str, *, speed: float | None = None, to_ts: int | None = None, conn=None) -> ClockState:
    """pause | resume | speed | jump (forward only; see reset for going back)."""
    with _lock:
        s = _load(conn)
        if s.mode == "LIVE":
            raise ValueError("clock controls are only available in REPLAY mode")
        current = now(conn)
        if action == "pause":
            s = ClockState(s.mode, current, time.time(), s.speed, True)
        elif action == "resume":
            s = ClockState(s.mode, current, time.time(), s.speed, False)
        elif action == "speed":
            if speed is None or not (0 < speed <= 3600):
                raise ValueError("speed must be in (0, 3600]")
            s = ClockState(s.mode, current, time.time(), float(speed), s.paused)
        elif action == "jump":
            lo, hi = data_range(conn)
            if to_ts is None or to_ts < current:
                raise ValueError("jumps must move forward in time; use the reset endpoint to restart the replay")
            if to_ts > hi:
                raise ValueError("target is after the end of the historian")
            s = ClockState(s.mode, int(to_ts), time.time(), s.speed, s.paused)
        else:
            raise ValueError(f"unknown clock action {action}")
        _save(s, conn)
        return s


def reset(to_ts: int, conn=None) -> ClockState:
    with _lock:
        s = _load(conn)
        s = ClockState(s.mode, int(to_ts), time.time(), s.speed, True)
        _save(s, conn)
        return s


def describe(conn=None) -> dict:
    s = _load(conn)
    lo, hi = data_range(conn)
    return {"mode": s.mode, "now": now(conn), "speed": s.speed, "paused": s.paused,
            "dataStart": lo, "dataEnd": hi, "atEnd": s.mode == "REPLAY" and now(conn) >= hi,
            "label": "Replay of historical BMS data (Jan-Jun 2026)" if s.mode == "REPLAY" else "Live"}
