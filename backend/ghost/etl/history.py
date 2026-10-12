"""Turn the raw BMS CSV exports into bucketed historian series.

Observed properties of the source data (Jan-Jun 2026 export):
* timestamps are UTC (no DST gap on 2026-03-29);
* every row is duplicated in the export -> rows are de-duplicated;
* an empty `value` means the point transmitted but had no valid value;
* energy / water points are cumulative indexes (kWh, m3).

Bucketing rules (per sensor type) are in `SPECS`. For each bucket in which a
sensor transmitted we keep `n_samples` (rows received) and `n_valid` (rows with
a numeric value) so that "silent" and "transmitting invalid values" remain
distinguishable downstream.
"""
from __future__ import annotations

import glob
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("ghost.etl.history")


@dataclass(frozen=True)
class Spec:
    folder: str
    type: str
    prefix: str
    unit: str
    resolution_s: int
    stale_after_s: int
    offline_after_s: int
    valid_min: float | None
    valid_max: float | None
    agg: str  # mean | bool_mean | counter_rate


SPECS = [
    Spec("temperature_csv", "TEMPERATURE", "T", "degC", 900, 2700, 7200, -20.0, 60.0, "mean"),
    Spec("co2_csv", "CO2", "CO2", "ppm", 900, 2700, 7200, 250.0, 5000.0, "mean"),
    Spec("humidity_csv", "HUMIDITY", "H", "%RH", 900, 2700, 7200, 0.0, 100.0, "mean"),
    Spec("lighting_csv", "LIGHTING", "L", "lux", 900, 2700, 7200, 0.0, 100000.0, "mean"),
    Spec("occupancy_csv", "OCCUPANCY", "OCC", "ratio", 900, 3 * 3600, 12 * 3600, 0.0, 1.0, "bool_mean"),
    Spec("energy_csv", "ENERGY", "E", "kWh/h", 3600, 3 * 3600, 12 * 3600, 0.0, None, "counter_rate"),
    Spec("water_csv", "WATER", "W", "m3/h", 3600, 3 * 3600, 12 * 3600, 0.0, None, "counter_rate"),
]


def sensor_short_id(spec: Spec, external_id: str, room: str | None, per_room_count: int) -> str:
    point = external_id.rstrip("/").split("/")[-1]
    if spec.type in ("ENERGY", "WATER"):
        return f"{spec.prefix}-{point}"
    if room and per_room_count == 1:
        return f"{spec.prefix}-{room}"
    return f"{spec.prefix}-{re.sub(r'[^A-Za-z0-9_-]', '', point)[:40]}"


def _read(spec: Spec, source_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(source_dir / spec.folder / "*.csv")))
    frames = []
    for f in files:
        df = pd.read_csv(f, sep=";", dtype={"value": "string", "room": "string", "floor": "string", "zone": "string"},
                         encoding="utf-8", encoding_errors="replace")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    before = len(df)
    df = df.drop_duplicates(subset=["sensor id", "timestamp", "value"])
    log.info("%s: %d rows (%d duplicates removed)", spec.type, len(df), before - len(df))
    return df


def _numeric(series: pd.Series, agg: str) -> pd.Series:
    if agg == "bool_mean":
        return series.map({"True": 1.0, "False": 0.0, "true": 1.0, "false": 0.0}).astype("float64")
    return pd.to_numeric(series, errors="coerce").astype("float64")


def build_type(spec: Spec, source_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (sensors, buckets) for one sensor type."""
    raw = _read(spec, source_dir)
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    meta_cols = [c for c in ["display name", "room", "floor", "zone", "resource", "re2020Usage"] if c in raw.columns]
    meta = raw.groupby("sensor id")[meta_cols].agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None)
    per_room = meta.groupby("room").size().to_dict()

    raw["v"] = _numeric(raw["value"], spec.agg)
    raw["bucket"] = raw["timestamp"].dt.floor(f"{spec.resolution_s}s")
    g = raw.groupby(["sensor id", "bucket"])

    if spec.agg in ("mean", "bool_mean"):
        b = g.agg(value=("v", "mean"), n_samples=("v", "size"), n_valid=("v", "count")).reset_index()
        series_kind = {sid: "instantaneous" for sid in meta.index}
    else:
        # Cumulative index -> average rate since the previous valid bucket.
        last = raw.dropna(subset=["v"]).sort_values("timestamp").groupby(["sensor id", "bucket"])["v"].last()
        counts = g.agg(n_samples=("v", "size"), n_valid=("v", "count"))
        b = counts.join(last.rename("index"), how="left").reset_index().sort_values(["sensor id", "bucket"])
        series_kind = {}
        rates = []
        for sid, s in b.groupby("sensor id", sort=False):
            idx = s["index"]
            valid = idx.notna()
            d = idx[valid].diff()
            dt_h = s.loc[valid, "bucket"].diff().dt.total_seconds() / 3600.0
            decreasing = float((d < -1e-6).mean()) if len(d) > 1 else 0.0
            if decreasing > 0.05:
                # Not a monotonic counter: keep instantaneous mean semantics.
                series_kind[sid] = "instantaneous"
                rate = idx
            else:
                series_kind[sid] = "cumulative_counter"
                rate = pd.Series(np.nan, index=s.index)
                r = d / dt_h
                r[d < -1e-6] = np.nan  # counter reset / rollback -> invalid bucket
                rate.loc[r.index] = r
            rates.append(rate)
        b["value"] = pd.concat(rates)
        reset = b["index"].notna() & b["value"].isna()
        b.loc[reset, "n_valid"] = 0
        b = b.drop(columns=["index"])

    sensors = []
    for sid, m in meta.iterrows():
        room = m.get("room")
        if isinstance(room, str) and ";" in room:
            room = room.split(";")[0]
        extra = {k: m.get(k) for k in ("resource", "re2020Usage") if k in m and m.get(k) is not None}
        extra["series_kind"] = series_kind.get(sid)
        sensors.append({
            "id": sensor_short_id(spec, sid, room, per_room.get(m.get("room"), 0)),
            "external_id": sid,
            "room_id": room if isinstance(room, str) else None,
            "type": spec.type,
            "unit": spec.unit,
            "display_name": m.get("display name"),
            "floor_label": m.get("floor"),
            "zone": m.get("zone"),
            "resolution_s": spec.resolution_s,
            "stale_after_s": spec.stale_after_s,
            "offline_after_s": spec.offline_after_s,
            "valid_min": spec.valid_min,
            "valid_max": spec.valid_max,
            "meta": extra,
        })
    sensors_df = pd.DataFrame(sensors)
    id_map = dict(zip(sensors_df["external_id"], sensors_df["id"]))
    b["sensor_id"] = b["sensor id"].map(id_map)
    b["ts"] = (b["bucket"].astype("int64") // 10**9).astype("int64")
    # A bucket whose first value comes after a long gap: the first diff of a counter is undefined.
    b.loc[b["value"].isna(), "n_valid"] = 0
    return sensors_df, b[["sensor_id", "ts", "value", "n_samples", "n_valid"]]


def wide(buckets: pd.DataFrame, sensors: pd.DataFrame, resolution_s: int) -> pd.DataFrame:
    """Wide matrix (time x room) of bucket values; NaN where missing or invalid."""
    room_of = dict(zip(sensors["id"], sensors["room_id"]))
    w = buckets.pivot_table(index="ts", columns="sensor_id", values="value", aggfunc="mean")
    full = pd.RangeIndex(int(w.index.min()), int(w.index.max()) + resolution_s, resolution_s)
    w = w.reindex(full)
    w.columns = [room_of[c] for c in w.columns]
    w.index.name = "ts"
    return w
