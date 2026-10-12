"""Physical readings as seen at a given building time.

* A bucket is "received" once building time passes its end (ts + resolution).
* Simulated faults (table `fault_injections`) are applied as an overlay; the
  historian rows are never modified, and every reading touched by an overlay
  is flagged `simulated`.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from .. import db

LOOKBACK_S = 3 * 86400


def sensors(conn=None, types: tuple[str, ...] | None = None) -> list[dict]:
    sql = "SELECT * FROM sensors"
    params: tuple = ()
    if types:
        sql += f" WHERE type IN ({','.join('?' * len(types))})"
        params = types
    return db.rows(sql, params, conn)


def active_faults(t: int, conn=None) -> dict[str, list[dict]]:
    """Faults that have started by building time t (ended ones still matter for history windows)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for f in db.rows("SELECT * FROM fault_injections WHERE start_ts <= ?", (t,), conn):
        f["params"] = db.loads(f["params"], {})
        out[f["sensor_id"]].append(f)
    return out


def _overlay(rows: list[tuple], faults: list[dict], resolution: int) -> tuple[list[tuple], bool]:
    """rows: (ts, value, n_samples, n_valid). Returns transformed rows and whether anything changed."""
    if not faults:
        return rows, False
    touched = False
    out = []
    for ts, value, n_samples, n_valid in rows:
        drop = False
        for f in faults:
            kind = f["kind"]
            if kind == "SPIKE":  # affects exactly one bucket
                if ts == f["start_ts"] // resolution * resolution and value is not None:
                    value = value + float(f["params"].get("delta", 10))
                    touched = True
                continue
            end = f["end_ts"] if f["end_ts"] is not None else float("inf")
            if not (f["start_ts"] <= ts < end):
                continue
            if kind == "SILENCE":
                drop = True
            elif kind == "NULL_VALUES":
                value, n_valid = None, 0
            elif kind == "OFFSET" and value is not None:
                p = f["params"]
                value = value + float(p.get("delta", 0)) + float(p.get("rate_per_day", 0)) * (ts - f["start_ts"]) / 86400.0
            touched = True
        if not drop:
            out.append((ts, value, n_samples, n_valid))
    return out, touched


def window(sensor_rows: list[dict], t_from: int, t: int, conn=None, faults: dict | None = None) -> pd.DataFrame:
    """Long DataFrame [sensor_id, ts, value, n_samples, n_valid, simulated] of received buckets in [t_from, t]."""
    faults = active_faults(t, conn) if faults is None else faults
    frames = []
    by_res: dict[int, list[str]] = defaultdict(list)
    for s in sensor_rows:
        by_res[s["resolution_s"]].append(s["id"])
    for res, ids in by_res.items():
        hi = t - res
        for chunk in range(0, len(ids), 400):
            part = ids[chunk:chunk + 400]
            q = db.rows(
                f"SELECT sensor_id, ts, value, n_samples, n_valid FROM measurements WHERE sensor_id IN ({','.join('?' * len(part))}) AND ts BETWEEN ? AND ?",
                (*part, t_from, hi), conn,
            )
            df = pd.DataFrame(q, columns=["sensor_id", "ts", "value", "n_samples", "n_valid"])
            df["simulated"] = False
            for sid in set(part) & set(faults):
                sub = df[df.sensor_id == sid]
                rows, touched = _overlay(list(sub[["ts", "value", "n_samples", "n_valid"]].itertuples(index=False, name=None)),
                                         faults[sid], res)
                rows = [(ts, None if v is None or pd.isna(v) else v, a, b) for ts, v, a, b in rows]
                repl = pd.DataFrame(rows, columns=["ts", "value", "n_samples", "n_valid"])
                repl["sensor_id"] = sid
                repl["simulated"] = touched
                df = pd.concat([df[df.sensor_id != sid], repl], ignore_index=True)
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["sensor_id", "ts", "value", "n_samples", "n_valid", "simulated"])
    out = pd.concat(frames, ignore_index=True)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.sort_values(["sensor_id", "ts"]).reset_index(drop=True)


def latest(sensor_rows: list[dict], t: int, conn=None) -> dict[str, dict]:
    """Per sensor: last transmission, last valid reading and last value as of building time t."""
    faults = active_faults(t, conn)
    df = window(sensor_rows, t - LOOKBACK_S, t, conn, faults)
    out: dict[str, dict] = {}
    for sid, g in df.groupby("sensor_id", sort=False):
        valid = g[g["value"].notna()]
        last = g.iloc[-1]
        out[sid] = {
            "last_tx_ts": int(last["ts"]),
            "last_row_value": None if pd.isna(last["value"]) else float(last["value"]),
            "last_valid_ts": int(valid.iloc[-1]["ts"]) if len(valid) else None,
            "last_value": float(valid.iloc[-1]["value"]) if len(valid) else None,
            "simulated": bool(g["simulated"].any()),
        }
    # Sensors silent for longer than the lookback window: per-sensor indexed lookups.
    for s in sensor_rows:
        sid = s["id"]
        hi = t - s["resolution_s"]
        rec = out.get(sid)
        sim = sid in faults
        if rec is None:
            if sim:
                g = window([s], 0, t, conn, faults)
                rows_ = g[["ts", "value"]].to_numpy() if len(g) else []
                tx = int(rows_[-1][0]) if len(rows_) else None
                vv = [r for r in rows_ if not pd.isna(r[1])]
                out[sid] = {"last_tx_ts": tx, "last_row_value": None, "last_valid_ts": int(vv[-1][0]) if vv else None,
                            "last_value": float(vv[-1][1]) if vv else None, "simulated": True}
                continue
            r = db.row("SELECT ts, value FROM measurements WHERE sensor_id=? AND ts<=? ORDER BY ts DESC LIMIT 1", (sid, hi), conn)
            rv = db.row("SELECT ts, value FROM measurements WHERE sensor_id=? AND ts<=? AND value IS NOT NULL ORDER BY ts DESC LIMIT 1", (sid, hi), conn)
            out[sid] = {"last_tx_ts": r["ts"] if r else None, "last_row_value": r["value"] if r else None,
                        "last_valid_ts": rv["ts"] if rv else None, "last_value": rv["value"] if rv else None, "simulated": False}
        elif rec["last_valid_ts"] is None and not sim:
            rv = db.row("SELECT ts, value FROM measurements WHERE sensor_id=? AND ts<=? AND value IS NOT NULL ORDER BY ts DESC LIMIT 1", (sid, hi), conn)
            if rv:
                rec["last_valid_ts"], rec["last_value"] = rv["ts"], rv["value"]
    return out


def classify(sensor: dict, rec: dict | None, t: int) -> str:
    """Connectivity status from heartbeat ages (configurable per sensor)."""
    if not rec or rec.get("last_tx_ts") is None:
        return "NO_DATA"
    res = sensor["resolution_s"]
    age_tx = t - (rec["last_tx_ts"] + res)
    if age_tx > sensor["offline_after_s"]:
        return "OFFLINE"
    if age_tx > sensor["stale_after_s"]:
        return "STALE"
    if rec.get("last_valid_ts") is None:
        return "INVALID"
    age_valid = t - (rec["last_valid_ts"] + res)
    if age_valid > sensor["stale_after_s"]:
        return "INVALID"  # still transmitting, but only empty/invalid values
    return "ONLINE"


def raw_value(sensor_id: str, ts: int, conn=None) -> float | None:
    """Historian value without any simulated overlay (used to validate predictions)."""
    return db.scalar("SELECT value FROM measurements WHERE sensor_id=? AND ts=?", (sensor_id, ts), conn)
