"""Data-quality checker for uploaded sensor CSV files.

Accepted layouts (delimiter `;`, `,` or tab is sniffed):
* BMS export  - `timestamp;display name;sensor id;value;room;floor;zone[...]` (as in temperature_csv/ ...)
* long        - a timestamp column, a series/sensor id column and a value column
* wide        - a timestamp column and one numeric column per series

Every check is explicit and reported per series; nothing is modified or imported
into the platform. Naive timestamps are assumed UTC (stated in the report).
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import db

log = logging.getLogger("ghost.datacheck")

TYPE_KEYWORDS = [
    ("TEMPERATURE", r"temp|tempé|°c|degc"),
    ("HUMIDITY", r"humid|hygro|%rh|\brh\b"),
    ("CO2", r"co2|carbon"),
    ("ENERGY", r"energ|kwh|électr|electr|puissance|power"),
    ("WATER", r"water|\beau\b|m3"),
    ("LIGHTING", r"lumin|light|lux|éclair|eclair"),
    ("OCCUPANCY", r"occup|presence|présence"),
]
TYPE_SPEC = {
    # plausible range, minimum spike step, unit, check flat-lines?
    "TEMPERATURE": {"range": (-10.0, 50.0), "step": 3.0, "unit": "°C", "flat": True},
    "HUMIDITY": {"range": (0.0, 100.0), "step": 15.0, "unit": "%RH", "flat": True},
    "CO2": {"range": (300.0, 5000.0), "step": 800.0, "unit": "ppm", "flat": True},
    "LIGHTING": {"range": (0.0, 100000.0), "step": None, "unit": "lux", "flat": False},
    "OCCUPANCY": {"range": (0.0, 1.0), "step": None, "unit": "", "flat": False},
    "ENERGY": {"range": (0.0, None), "step": None, "unit": "", "flat": False},
    "WATER": {"range": (0.0, None), "step": None, "unit": "", "flat": False},
    "UNKNOWN": {"range": (None, None), "step": None, "unit": "", "flat": False},
}
FLAT_HOURS = 24
MIN_GAP_S = 1800


def _sniff(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (";", ",", "\t")}
        return max(counts, key=counts.get)


def _find(cols: list[str], *patterns: str) -> str | None:
    for p in patterns:
        for c in cols:
            if re.search(p, c.strip().lower()):
                return c
    return None


def _to_number(s: pd.Series, decimal_comma: bool) -> pd.Series:
    t = s.astype("string").str.strip()
    t = t.replace({"True": "1", "False": "0", "true": "1", "false": "0", "TRUE": "1", "FALSE": "0"})
    if decimal_comma:
        t = t.str.replace(",", ".", regex=False)
    return pd.to_numeric(t, errors="coerce").astype("float64")


def guess_type(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for kind, pat in TYPE_KEYWORDS:
        if re.search(pat, blob):
            return kind
    return "UNKNOWN"


def load(path: Path, filename: str) -> tuple[pd.DataFrame, dict]:
    """Normalise any accepted layout to long form: ts, series, value, raw_missing, room, display."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        sample = fh.read(65536)
    delim = _sniff(sample)
    df = pd.read_csv(path, sep=delim, dtype=str, encoding="utf-8", encoding_errors="replace", on_bad_lines="skip", engine="c",
                     skip_blank_lines=True)
    with open(path, "rb") as fh:
        data_lines = sum(1 for line in fh if line.strip()) - 1  # minus header
    bad_lines = max(0, data_lines - len(df))
    cols = list(df.columns)
    info = {"delimiter": {";": "semicolon", ",": "comma", "\t": "tab"}.get(delim, delim), "columns": cols[:40], "rawRows": int(len(df)),
            "badLinesSkipped": bad_lines, "timezone": "Timestamps without offset are assumed UTC (as in the BMS export)."}
    ts_col = _find(cols, r"^timestamp$", r"time|date|horodat|^ts$")
    if ts_col is None:
        for c in cols:
            if pd.to_datetime(df[c].head(50), errors="coerce", utc=True, format="mixed").notna().mean() > 0.8:
                ts_col = c
                break
    if ts_col is None:
        raise ValueError("No timestamp column found (expected a column named timestamp/time/date).")
    decimal_comma = delim == ";"
    ts = pd.to_datetime(df[ts_col], errors="coerce", utc=True, format="mixed")
    info["unparseableTimestamps"] = int(ts.isna().sum())

    sensor_col = _find(cols, r"^sensor id$", r"sensor|capteur|point|^id$|series|name")
    value_col = _find(cols, r"^value$", r"value|valeur|mesure|reading")
    room_col = _find(cols, r"^room$", r"room|salle|pi[eè]ce|local|space")
    display_col = _find(cols, r"display name", r"display|label|libell")
    if sensor_col and value_col and sensor_col != value_col:
        layout = "BMS export" if {"timestamp", "sensor id", "value"} <= {c.strip().lower() for c in cols} else "long"
        raw = df[value_col]
        out = pd.DataFrame({
            "ts": ts, "series": df[sensor_col].fillna("(no id)"),
            "value": _to_number(raw, decimal_comma),
            "raw_missing": raw.isna() | (raw.astype("string").str.strip() == ""),
            "room": df[room_col] if room_col else None,
            "display": df[display_col] if display_col else None,
        })
    else:
        layout = "wide"
        value_cols = [c for c in cols if c != ts_col and _to_number(df[c].head(200), decimal_comma).notna().mean() > 0.5]
        if not value_cols:
            raise ValueError("No numeric value column found.")
        parts = []
        for c in value_cols[:500]:
            raw = df[c]
            parts.append(pd.DataFrame({"ts": ts, "series": c, "value": _to_number(raw, decimal_comma),
                                       "raw_missing": raw.isna() | (raw.astype("string").str.strip() == ""), "room": None, "display": c}))
        out = pd.concat(parts, ignore_index=True)
    info["layout"] = layout
    info["filenameTypeHint"] = guess_type(filename)
    out = out[out["ts"].notna()].sort_values(["series", "ts"]).reset_index(drop=True)
    return out, info


def _gaps(ts: pd.Series, median_s: float) -> dict:
    d = ts.diff().dt.total_seconds().iloc[1:]
    thr = max(3 * median_s, MIN_GAP_S)
    big = d[d > thr]
    longest = None
    if len(big):
        i = big.idxmax()
        longest = {"from": ts.loc[i - 1].isoformat() if (i - 1) in ts.index else None, "to": ts.loc[i].isoformat(), "hours": round(float(big.max()) / 3600, 2)}
    return {"count": int(len(big)), "totalHours": round(float(big.sum()) / 3600, 2), "thresholdS": int(thr), "longest": longest}


def _flat_run_hours(ts: pd.Series, v: pd.Series) -> float:
    if len(v) < 3:
        return 0.0
    change = v.ne(v.shift()).cumsum()
    grp = pd.DataFrame({"ts": ts, "g": change}).groupby("g")["ts"].agg(["min", "max"])
    return round(float((grp["max"] - grp["min"]).dt.total_seconds().max()) / 3600, 2)


def analyse_series(g: pd.DataFrame, kind: str, platform_sensor: dict | None, known_rooms: set[str], conn) -> dict:
    spec = TYPE_SPEC[kind]
    rows = len(g)
    dup_exact = int(g.duplicated(subset=["ts", "value"]).sum())
    g = g.drop_duplicates(subset=["ts", "value"])
    conflicts = int(g.duplicated(subset=["ts"]).sum())
    g = g.drop_duplicates(subset=["ts"])
    ts, v = g["ts"], g["value"]
    invalid = int(v.isna().sum())
    empty = int(g["raw_missing"].sum())
    valid = g[v.notna()]
    vv = valid["value"]
    span_s = (ts.iloc[-1] - ts.iloc[0]).total_seconds() if len(ts) > 1 else 0
    med = float(ts.diff().dt.total_seconds().median()) if len(ts) > 1 else 0.0
    expected = span_s / med + 1 if med > 0 else len(ts)
    out = {
        "rows": rows, "uniqueTimestamps": int(len(ts)), "exactDuplicates": dup_exact, "conflictingDuplicates": conflicts,
        "emptyValues": empty, "nonNumericValues": max(invalid - empty, 0), "validValues": int(len(vv)),
        "first": ts.iloc[0].isoformat() if len(ts) else None, "last": ts.iloc[-1].isoformat() if len(ts) else None,
        "medianIntervalS": round(med, 1), "completenessPct": round(100 * len(ts) / expected, 1) if expected else None,
        "gaps": _gaps(ts.reset_index(drop=True), med) if len(ts) > 2 else {"count": 0, "totalHours": 0, "thresholdS": MIN_GAP_S, "longest": None},
    }
    if len(vv):
        out["stats"] = {"min": round(float(vv.min()), 3), "max": round(float(vv.max()), 3), "mean": round(float(vv.mean()), 3),
                        "std": round(float(vv.std()), 3) if len(vv) > 1 else 0.0, "p05": round(float(vv.quantile(0.05)), 3),
                        "p95": round(float(vv.quantile(0.95)), 3), "zeros": int((vv == 0).sum())}
    lo, hi = spec["range"]
    mask = pd.Series(False, index=vv.index)
    if lo is not None:
        mask |= vv < lo
    if hi is not None:
        mask |= vv > hi
    oor = valid[mask]
    out["outOfRange"] = {"count": int(len(oor)), "range": [lo, hi], "examples": [{"ts": r.ts.isoformat(), "value": r.value} for r in oor.head(5).itertuples()]}
    spikes = 0
    if len(vv) > 3:
        d = vv.diff().abs().iloc[1:]
        mad = float((d - d.median()).abs().median())
        thr = max(8 * 1.4826 * mad, spec["step"] or 0) if spec["step"] else None
        if thr:
            spikes = int((d > thr).sum())
            out["spikeThreshold"] = round(thr, 3)
    out["spikes"] = spikes
    out["longestFlatHours"] = _flat_run_hours(valid["ts"], vv) if spec["flat"] and len(vv) else None
    if kind in ("ENERGY", "WATER") and len(vv) > 3:
        dec = int((vv.diff() < -1e-6).sum())
        out["counter"] = {"decreases": dec, "looksCumulative": bool(dec <= 0.05 * len(vv) and vv.iloc[-1] >= vv.iloc[0])}
    room = next((r for r in g["room"].dropna().unique()), None) if g["room"].notna().any() else None
    out["room"] = room
    out["roomInDigitalTwin"] = (room in known_rooms) if room else None
    out["platformSensor"] = platform_sensor["id"] if platform_sensor else None
    if platform_sensor and len(vv):
        res = platform_sensor["resolution_s"]
        b = valid.assign(bucket=(valid["ts"].astype("int64") // 10**9) // res * res).groupby("bucket")["value"].mean()
        hist = pd.Series({r["ts"]: r["value"] for r in db.rows(
            "SELECT ts, value FROM measurements WHERE sensor_id=? AND ts BETWEEN ? AND ? AND value IS NOT NULL",
            (platform_sensor["id"], int(b.index.min()), int(b.index.max())), conn)}, dtype="float64")
        both = pd.concat([b.rename("upload"), hist.rename("historian")], axis=1).dropna()
        if kind in ("ENERGY", "WATER"):
            out["historianComparison"] = {"overlapBuckets": int(len(both)), "note": "historian stores consumption rates; uploaded counters are not compared value by value"}
        elif len(both):
            diff = both["upload"] - both["historian"]
            out["historianComparison"] = {"overlapBuckets": int(len(both)), "mae": round(float(diff.abs().mean()), 4),
                                          "maxAbsDiff": round(float(diff.abs().max()), 4),
                                          "correlation": round(float(both.corr().iloc[0, 1]), 4) if len(both) > 2 else None}
        else:
            out["historianComparison"] = {"overlapBuckets": 0}
    out["issues"] = issues_for(out, kind)
    return out


def issues_for(m: dict, kind: str) -> list[dict]:
    I = []

    def add(level, code, text):
        I.append({"level": level, "code": code, "text": text})

    if m["validValues"] == 0:
        add("error", "NO_VALID_VALUES", "No valid numeric value in this series.")
    if m["emptyValues"]:
        pct = 100 * m["emptyValues"] / max(m["rows"], 1)
        add("error" if pct > 20 else "warn", "EMPTY_VALUES", f"{m['emptyValues']} empty values ({pct:.1f} %).")
    if m["nonNumericValues"]:
        add("warn", "NON_NUMERIC", f"{m['nonNumericValues']} values could not be read as numbers.")
    if m["exactDuplicates"]:
        add("info", "DUPLICATE_ROWS", f"{m['exactDuplicates']} exact duplicate rows (same timestamp and value).")
    if m["conflictingDuplicates"]:
        add("warn", "CONFLICTING_DUPLICATES", f"{m['conflictingDuplicates']} timestamps appear with different values.")
    gp = m["gaps"]
    if gp["count"]:
        lg = gp["longest"]
        add("warn" if (lg and lg["hours"] < 24) else "error", "TRANSMISSION_GAPS",
            f"{gp['count']} gaps longer than {gp['thresholdS'] // 60} min (total {gp['totalHours']} h"
            + (f"; longest {lg['hours']} h ending {lg['to'][:16]}" if lg else "") + ").")
    if m["completenessPct"] is not None and m["completenessPct"] < 90:
        add("warn", "LOW_COMPLETENESS", f"Only {m['completenessPct']} % of the expected samples at the median interval.")
    if m["outOfRange"]["count"]:
        lo, hi = m["outOfRange"]["range"]
        add("error", "OUT_OF_RANGE", f"{m['outOfRange']['count']} values outside the plausible range [{lo}, {hi}] for {kind.lower()}.")
    if m["spikes"]:
        add("warn", "SPIKES", f"{m['spikes']} sudden steps larger than {m.get('spikeThreshold')}.")
    if m.get("longestFlatHours") and m["longestFlatHours"] >= FLAT_HOURS:
        add("warn", "FLATLINE", f"Value unchanged for {m['longestFlatHours']} h (possible frozen sensor).")
    c = m.get("counter")
    if c and c["decreases"]:
        add("warn" if c["looksCumulative"] else "info", "COUNTER_DECREASES", f"Counter decreased {c['decreases']} times (reset or rollback).")
    if m.get("roomInDigitalTwin") is False:
        add("info", "UNKNOWN_ROOM", f"Room {m['room']} is not in the digital twin.")
    hc = m.get("historianComparison")
    if hc and hc.get("mae") is not None and hc["mae"] > (0.5 if kind == "TEMPERATURE" else 0):
        add("warn" if kind == "TEMPERATURE" else "info", "DIFFERS_FROM_HISTORIAN", f"Differs from the platform historian: MAE {hc['mae']} over {hc['overlapBuckets']} buckets.")
    return I


def analyse(path: Path, filename: str, conn) -> tuple[dict, dict, pd.DataFrame]:
    long, info = load(path, filename)
    if long.empty:
        raise ValueError("The file contains no rows with a readable timestamp.")
    sensors = {s["external_id"]: s for s in db.rows("SELECT * FROM sensors", conn=conn)}
    by_id = {s["id"]: s for s in sensors.values()}
    known_rooms = {r["id"] for r in db.rows("SELECT id FROM rooms", conn=conn)}
    series_out = {}
    for sid, g in long.groupby("series", sort=True):
        disp = next((d for d in g["display"].dropna().unique()), None) if g["display"].notna().any() else None
        kind = guess_type(disp, sid, filename)
        plat = sensors.get(sid) or by_id.get(sid)
        if plat and kind == "UNKNOWN":
            kind = plat["type"] if plat["type"] in TYPE_SPEC else "UNKNOWN"
        m = analyse_series(g, kind, plat, known_rooms, conn)
        m.update({"series": sid, "displayName": disp, "type": kind, "unit": TYPE_SPEC[kind]["unit"]})
        series_out[sid] = m
    checks = {}
    for m in series_out.values():
        for i in m["issues"]:
            c = checks.setdefault(i["code"], {"code": i["code"], "level": i["level"], "series": 0})
            c["series"] += 1
            if i["level"] == "error":
                c["level"] = "error"
            elif i["level"] == "warn" and c["level"] == "info":
                c["level"] = "warn"
    types = {}
    for m in series_out.values():
        types[m["type"]] = types.get(m["type"], 0) + 1
    summary = {
        **info, "rows": int(len(long)), "series": len(series_out), "types": types,
        "first": long["ts"].min().isoformat(), "last": long["ts"].max().isoformat(),
        "exactDuplicates": sum(m["exactDuplicates"] for m in series_out.values()),
        "emptyValues": sum(m["emptyValues"] for m in series_out.values()),
        "seriesWithErrors": sum(1 for m in series_out.values() if any(i["level"] == "error" for i in m["issues"])),
        "seriesWithWarnings": sum(1 for m in series_out.values() if any(i["level"] == "warn" for i in m["issues"])),
        "seriesMatchedToPlatform": sum(1 for m in series_out.values() if m["platformSensor"]),
        "checks": sorted(checks.values(), key=lambda c: ({"error": 0, "warn": 1, "info": 2}[c["level"]], -c["series"])),
    }
    return summary, series_out, long


def series_points(long_path: Path, series: str, max_points: int = 1500) -> dict:
    """Resampled mean/min/max of one uploaded series (from the normalised parquet)."""
    df = pd.read_parquet(long_path, filters=[("series", "==", series)])
    if df.empty:
        return {"series": series, "points": [], "resolution": None}
    df = df.drop_duplicates(subset=["ts"]).set_index("ts").sort_index()
    span = (df.index[-1] - df.index[0]).total_seconds()
    step = max(60, int(np.ceil(span / max_points / 60) * 60))
    r = df["value"].resample(f"{step}s")
    agg = pd.DataFrame({"mean": r.mean(), "min": r.min(), "max": r.max(), "n": r.count()})
    pts = [{"t": t.isoformat(), "mean": None if np.isnan(a) else round(float(a), 3),
            "min": None if np.isnan(b) else round(float(b), 3), "max": None if np.isnan(c) else round(float(c), 3), "n": int(n)}
           for t, a, b, c, n in agg.itertuples()]
    return {"series": series, "resolutionS": step, "points": pts}
