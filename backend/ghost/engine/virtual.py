"""Ghost Sensor inference: virtual temperature readings for silent/invalid sensors.

Safety rules
* Inputs are physical readings only (inferred values are never inputs), so an
  outage cannot propagate recursively through the building.
* No value is produced when no reference is available (no own history and no
  physical neighbour/floor/building reading): quality INSUFFICIENT.
* The interval is the split-conformal 90 % interval from validation, chosen by
  (outage length x neighbour availability). It is not a probability.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from .. import db
from ..config import settings
from ..ml import features as F
from . import readings

log = logging.getLogger("ghost.virtual")
MAX_OWN_HISTORY_GAP_S = 144 * 3600
GOOD_MAX_GAP_H = 12
_lock = threading.Lock()


@dataclass
class Bundle:
    version: str
    model: object
    meta: dict


_bundle: Bundle | None = None
_bundle_error: str | None = None


def load_bundle(force: bool = False) -> Bundle | None:
    global _bundle, _bundle_error
    with _lock:
        if _bundle is not None and not force:
            return _bundle
        try:
            version = (settings.models_dir / "CURRENT").read_text(encoding="utf-8").strip()
            meta = json.loads((settings.models_dir / f"{version}.json").read_text(encoding="utf-8"))
            model = joblib.load(settings.models_dir / f"{version}.joblib")
            _bundle, _bundle_error = Bundle(version, model, meta), None
        except FileNotFoundError:
            _bundle, _bundle_error = None, "model not trained (run: python -m ghost.ml.train)"
        except Exception as exc:  # corrupted file, version mismatch...
            _bundle, _bundle_error = None, f"model failed to load: {exc}"
        return _bundle


def model_status() -> dict:
    b = load_bundle()
    if b is None:
        return {"status": "UNAVAILABLE", "error": _bundle_error}
    ev = b.meta.get("evaluation", {})
    return {"status": "OK", "version": b.version, "trainedAt": b.meta.get("trained_at"),
            "testMae": ev.get("model", {}).get("mae"), "testRmse": ev.get("model", {}).get("rmse"),
            "intervalCoverage90": ev.get("interval_coverage_90")}


def _graph(rooms: list[str], conn) -> F.Graph:
    from ..ml.train import load_graph

    return load_graph(conn, rooms)


def infer(targets: list[dict], t: int, conn=None, incident_of: dict | None = None) -> list[dict]:
    """targets: TEMPERATURE sensor rows with `last_valid_ts` (may be None). Returns stored prediction dicts."""
    if not targets:
        return []
    bundle = load_bundle()
    target_ts = (t // F.STEP_S) * F.STEP_S - F.STEP_S  # last bucket that would be complete now
    results = []
    if bundle is None:
        for s in targets:
            results.append(_store(conn, s, target_ts, None, None, None, "unavailable", "INSUFFICIENT",
                                  [_bundle_error or "model unavailable"], {}, None, None, incident_of))
        return results

    col = {n: i for i, n in enumerate(F.FEATURE_NAMES)}
    temp_sensors = readings.sensors(conn, ("TEMPERATURE",))
    # Features carry no room identity, so every instrumented room can be served - including rooms
    # whose sensor never produced a valid value (absent from the training matrix).
    rooms = sorted(set(bundle.meta["rooms"]) | {s["room_id"] for s in temp_sensors if s["room_id"]})
    room_sensor = {s["room_id"]: s["id"] for s in temp_sensors}
    max_gap = max((target_ts - (s.get("last_valid_ts") or target_ts) for s in targets), default=0)
    span = F.OFFSET_WINDOW * F.STEP_S + min(max_gap, MAX_OWN_HISTORY_GAP_S) + 2 * F.STEP_S
    t_from = target_ts - span
    long = readings.window([s for s in temp_sensors if s["room_id"] in set(rooms)], t_from, t, conn)
    long = long[long["value"].between(-20, 60)]
    sensor_room = {s["id"]: s["room_id"] for s in temp_sensors}
    long["room"] = long["sensor_id"].map(sensor_room)
    grid = np.arange(t_from - t_from % F.STEP_S, target_ts + F.STEP_S, F.STEP_S, dtype=np.int64)
    Wdf = long.pivot_table(index="ts", columns="room", values="value", aggfunc="mean").reindex(index=grid, columns=rooms)
    W = Wdf.to_numpy(dtype=float)
    graph = _graph(rooms, conn)
    ridx = {r: i for i, r in enumerate(rooms)}
    T = len(grid)

    batch, meta_rows = [], []
    for s in targets:
        room = s["room_id"]
        if room not in ridx:
            results.append(_store(conn, s, target_ts, None, None, None, bundle.version, "INSUFFICIENT",
                                  ["room not covered by the model"], {}, None, None, incident_of))
            continue
        reasons = []
        lv = s.get("last_valid_ts")
        t0 = -1
        if lv is None:
            reasons.append("sensor never produced a valid reading: no room-specific history")
        elif target_ts - lv > MAX_OWN_HISTORY_GAP_S:
            reasons.append("outage longer than 144 h: own history ignored")
        else:
            t0 = int((lv - grid[0]) // F.STEP_S)
            if t0 < 0 or t0 >= T or np.isnan(W[t0, ridx[room]]):
                t0 = -1
                reasons.append("last valid reading not available in the inference window")
        batch.append((ridx[room], T - 1, t0))
        meta_rows.append((s, reasons, lv))

    if batch:
        r_idx = np.array([b[0] for b in batch]); t_idx = np.array([b[1] for b in batch]); t0_idx = np.array([b[2] for b in batch])
        X = F.compute_features(W, grid, graph, r_idx, t_idx, t0_idx)
        ref = X[:, col["ref"]].astype(float)
        ok = ~np.isnan(ref)
        pred = np.full(len(batch), np.nan)
        if ok.any():
            pred[ok] = ref[ok] + bundle.model.predict(X[ok])
        conf = bundle.meta["conformal"]
        from ..ml.train import bin_keys

        keys = bin_keys(X[:, col["gap_h"]], X[:, col["nb_count"]])
        for k, ((s, reasons, lv), (ri, _, t0)) in enumerate(zip(meta_rows, batch)):
            nb_cnt = int(X[k, col["nb_count"]])
            gap_h = float(X[k, col["gap_h"]]) if not np.isnan(X[k, col["gap_h"]]) else None
            neighbours = []
            for j in np.nonzero(graph.horiz[ri])[0]:
                v = W[T - 1, j]
                neighbours.append({"room": rooms[j], "sensorId": room_sensor.get(rooms[j]),
                                   "value": None if np.isnan(v) else round(float(v), 2), "weight": round(float(graph.horiz[ri, j]), 2)})
            inputs = {
                "neighbours": neighbours,
                "neighbourCountAvailable": nb_cnt,
                "lastValidTs": lv, "lastValidValue": None if t0 < 0 else round(float(W[t0, ri]), 2),
                "reference": {"kind": F.REF_KINDS[int(X[k, col["ref_kind"]])], "value": None if np.isnan(ref[k]) else round(float(ref[k]), 2)},
                "floorMean": None if np.isnan(X[k, col["floor_mean"]]) else round(float(X[k, col["floor_mean"]]), 2),
                "buildingMean": None if np.isnan(X[k, col["bldg_mean"]]) else round(float(X[k, col["bldg_mean"]]), 2),
                "roomOffset7d": None if np.isnan(X[k, col["offset_7d"]]) else round(float(X[k, col["offset_7d"]]), 2),
            }
            if np.isnan(pred[k]):
                results.append(_store(conn, s, target_ts, None, None, None, bundle.version, "INSUFFICIENT",
                                      reasons + ["no physical reference available (no own history, neighbours, floor or building readings)"],
                                      inputs, gap_h, None, incident_of))
                continue
            q = conf["bins"][str(int(keys[k]))]["q"]
            if nb_cnt == 0:
                reasons.append("no instrumented neighbour currently reporting")
            quality = "GOOD" if (not reasons and gap_h is not None and gap_h <= GOOD_MAX_GAP_H) else "DEGRADED"
            if quality == "DEGRADED" and gap_h is not None and gap_h > GOOD_MAX_GAP_H:
                reasons.append(f"outage of {gap_h:.1f} h exceeds {GOOD_MAX_GAP_H} h")
            v = float(pred[k])
            results.append(_store(conn, s, target_ts, v, v - q, v + q, bundle.version, quality, reasons, inputs, gap_h,
                                  conf["level"], incident_of))
    return results


def _store(conn, s, target_ts, value, lower, upper, version, quality, reasons, inputs, gap_h, level, incident_of) -> dict:
    actual = None
    if s.get("simulated"):
        # The physical outage is simulated: the true historian value exists and is kept for validation.
        actual = readings.raw_value(s["id"], target_ts, conn)
    rec = {
        "sensor_id": s["id"], "target_ts": int(target_ts),
        "value": None if value is None else round(value, 3),
        "lower": None if lower is None else round(lower, 3), "upper": None if upper is None else round(upper, 3),
        "interval_level": level, "model_version": version, "quality": quality, "quality_reasons": db.dumps(reasons),
        "inputs": db.dumps(inputs), "input_freshness_s": F.STEP_S,
        "gap_s": None if gap_h is None else int(gap_h * 3600),
        "incident_id": (incident_of or {}).get(s["id"]), "actual_value": actual, "created_real": db.now_real(),
    }
    db.execute(
        f"""INSERT INTO virtual_predictions({','.join(rec)}) VALUES ({','.join('?' * len(rec))})
            ON CONFLICT(sensor_id, target_ts) DO UPDATE SET value=excluded.value, lower=excluded.lower, upper=excluded.upper,
              quality=excluded.quality, quality_reasons=excluded.quality_reasons, inputs=excluded.inputs,
              model_version=excluded.model_version, incident_id=COALESCE(excluded.incident_id, virtual_predictions.incident_id),
              actual_value=COALESCE(excluded.actual_value, virtual_predictions.actual_value)""",
        tuple(rec.values()), conn,
    )
    rec["quality_reasons"] = reasons
    rec["inputs"] = inputs
    return rec
