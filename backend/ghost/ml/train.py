"""Train and evaluate the temperature virtual-sensor model.

    python -m ghost.ml.train                 # train, evaluate, write model + model card
    python -m ghost.ml.train --quick         # smaller sample for CI / tests

Evaluation protocol (all on *real* historian data):
* Outages are simulated by hiding a room's real readings between t0 and t;
  the hidden value at t is the ground truth.
* Time-based split: train t < 2026-04-15, calibration [2026-04-15, 2026-05-01),
  test t >= 2026-05-01 (May-June, never seen during training).
* Unseen-room check: a model trained without 20 % of the rooms is evaluated on
  those rooms over the test period.
* Uncertainty: split-conformal 90 % intervals, calibrated per (gap length x
  neighbour availability) bin on the calibration period, then checked for
  empirical coverage on the test period. This is a validation-derived
  prediction interval, not a probability that a single prediction is right.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from .. import db
from ..config import REPO_DIR, settings
from . import features as F

log = logging.getLogger("ghost.ml.train")

TRAIN_END = pd.Timestamp("2026-04-15", tz="UTC").value // 10**9
CALIB_END = pd.Timestamp("2026-05-01", tz="UTC").value // 10**9
GAP_BINS_H = [0, 2, 12, 72, 10**6]
GAP_LABELS = ["<2h", "2-12h", "12-72h", ">72h"]
NB_BINS = [(0, 0, "0 nb"), (1, 2, "1-2 nb"), (3, 10**6, "3+ nb")]
ALPHA = 0.10


def load_graph(conn, rooms: list[str]) -> F.Graph:
    meta = {r["id"]: r for r in db.rows("SELECT id, storey_id, area_m2, wing, zone FROM rooms", conn=conn)}
    storeys = {r["id"]: r["sort_order"] for r in db.rows("SELECT id, sort_order FROM storeys", conn=conn)}
    edges = db.rows("SELECT room_a, room_b, kind, shared_length_m, door_count FROM room_adjacency", conn=conn)
    return F.build_graph(rooms, meta, edges, storeys)


def load_wide() -> pd.DataFrame:
    W = pd.read_parquet(settings.derived_dir / "temperature_15min.parquet")
    # Physically implausible values are treated as missing for training.
    return W.where((W > -20) & (W < 60))


def sample(W: np.ndarray, ts: np.ndarray, rng: np.random.Generator, n: int, t_lo: int, t_hi: int, rooms_mask=None):
    valid = ~np.isnan(W)
    T, R = W.shape
    rows_ok = np.where((ts >= t_lo) & (ts < t_hi))[0]
    out_r, out_t, out_t0 = [], [], []
    need = n
    while need > 0:
        k = need * 3
        t = rng.choice(rows_ok, k)
        r = rng.integers(0, R, k)
        u = rng.random(k)
        gap = np.where(u < 0.30, rng.integers(1, 9, k), np.where(u < 0.65, rng.integers(9, 49, k), rng.integers(49, 289, k)))
        t0 = t - gap
        ok = (t0 >= 0) & valid[t, r] & valid[np.maximum(t0, 0), r]
        if rooms_mask is not None:
            ok &= rooms_mask[r]
        out_r.append(r[ok]); out_t.append(t[ok]); out_t0.append(t0[ok])
        need -= int(ok.sum())
    return np.concatenate(out_r)[:n], np.concatenate(out_t)[:n], np.concatenate(out_t0)[:n]


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    ok = ~np.isnan(p)
    e = p[ok] - y[ok]
    return {"n": int(ok.sum()), "mae": round(float(np.abs(e).mean()), 4), "rmse": round(float(np.sqrt((e ** 2).mean())), 4),
            "p90_abs_err": round(float(np.quantile(np.abs(e), 0.9)), 4), "bias": round(float(e.mean()), 4)}


def bin_keys(gap_h: np.ndarray, nb_cnt: np.ndarray) -> np.ndarray:
    g = np.digitize(np.nan_to_num(gap_h, nan=10**5), GAP_BINS_H[1:-1])
    n = np.select([nb_cnt == 0, nb_cnt <= 2], [0, 1], 2)
    return g * 3 + n


def conformal_table(abs_res: np.ndarray, keys: np.ndarray) -> dict:
    table = {}
    overall = float(np.quantile(abs_res, min(1.0, np.ceil((len(abs_res) + 1) * (1 - ALPHA)) / len(abs_res))))
    for k in range(len(GAP_LABELS) * 3):
        r = abs_res[keys == k]
        if len(r) >= 50:
            q = float(np.quantile(r, min(1.0, np.ceil((len(r) + 1) * (1 - ALPHA)) / len(r))))
            table[str(k)] = {"q": round(q, 4), "n": int(len(r))}
        else:
            table[str(k)] = {"q": round(overall, 4), "n": int(len(r)), "fallback": "overall"}
    return {"alpha": ALPHA, "level": 1 - ALPHA, "overall_q": round(overall, 4), "bins": table,
            "gap_bins_h": GAP_BINS_H, "gap_labels": GAP_LABELS, "nb_labels": [b[2] for b in NB_BINS]}


def new_model(n_estimators: int) -> RandomForestRegressor:
    return RandomForestRegressor(n_estimators=n_estimators, max_depth=22, min_samples_leaf=15, max_features=0.5,
                                 n_jobs=-1, random_state=7)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    n_train, n_cal, n_test, n_trees = (40_000, 15_000, 20_000, 40) if args.quick else (250_000, 60_000, 120_000, 80)

    conn = db.connect()
    Wdf = load_wide()
    rooms = list(Wdf.columns)
    graph = load_graph(conn, rooms)
    W = Wdf.to_numpy(dtype=float)
    ts = Wdf.index.to_numpy(dtype=np.int64)
    nbT = F.neighbour_mean_matrix(W, graph)
    offT = F.rolling_offset(W, nbT)
    rng = np.random.default_rng(42)

    def build(n, lo, hi, mask=None):
        r, t, t0 = sample(W, ts, rng, n, lo, hi, mask)
        hide = rng.random(len(r)) < 0.05  # rooms with no usable own history (e.g. never-valid sensors)
        X = F.compute_features(W, ts, graph, r, t, t0, nbT, offT, hide_own_history=hide)
        return X, W[t, r], r

    t_start = time.time()
    Xtr, ytr, _ = build(n_train, int(ts[0]), TRAIN_END)
    Xca, yca, _ = build(n_cal, TRAIN_END, CALIB_END)
    Xte, yte, rte = build(n_test, CALIB_END, int(ts[-1]) + 1)
    log.info("samples built in %.1fs", time.time() - t_start)

    col = {n: i for i, n in enumerate(F.FEATURE_NAMES)}
    R = col["ref"]
    keep = ~np.isnan(Xtr[:, R])
    model = new_model(n_trees)
    t_fit = time.time()
    model.fit(Xtr[keep], ytr[keep] - Xtr[keep, R])  # residual learning
    log.info("fit in %.1fs", time.time() - t_fit)

    def predict(m, X):
        return X[:, R] + m.predict(X)

    pca = predict(model, Xca)
    cal_keys = bin_keys(Xca[:, col["gap_h"]], Xca[:, col["nb_count"]])
    conformal = conformal_table(np.abs(pca - yca), cal_keys)

    pte = predict(model, Xte)
    te_keys = bin_keys(Xte[:, col["gap_h"]], Xte[:, col["nb_count"]])
    q = np.array([conformal["bins"][str(k)]["q"] for k in te_keys])
    covered = np.abs(pte - yte) <= q

    def baseline(name):
        return Xte[:, col[name]]

    report = {
        "test_period": "2026-05-01 .. 2026-06-30 (held out)",
        "model": metrics(yte, pte),
        "baselines": {
            "last_value_persistence": metrics(yte, baseline("last_val")),
            "persistence_plus_neighbour_delta": metrics(yte, baseline("persist_adj")),
            "neighbour_weighted_mean": metrics(yte, baseline("nb_wmean")),
            "neighbour_mean_plus_room_offset": metrics(yte, baseline("nb_plus_offset")),
        },
        "by_gap": {}, "by_neighbours": {},
        "interval_coverage_90": round(float(covered.mean()), 4),
        "mean_interval_halfwidth": round(float(q.mean()), 4),
    }
    gap_bin = np.digitize(Xte[:, col["gap_h"]], GAP_BINS_H[1:-1])
    for i, lab in enumerate(GAP_LABELS):
        m = gap_bin == i
        if m.sum():
            report["by_gap"][lab] = {**metrics(yte[m], pte[m]), "coverage_90": round(float(covered[m].mean()), 4),
                                     "persistence_mae": metrics(yte[m], Xte[m, col["last_val"]])["mae"]}
    nbc = Xte[:, col["nb_count"]]
    for lo, hi, lab in NB_BINS:
        m = (nbc >= lo) & (nbc <= hi)
        if m.sum():
            report["by_neighbours"][lab] = {**metrics(yte[m], pte[m]), "coverage_90": round(float(covered[m].mean()), 4)}

    # Unseen-room generalisation (smaller forest, same protocol).
    holdout = rng.random(len(rooms)) < 0.2
    Xa, ya, _ = build(n_train // 2, int(ts[0]), TRAIN_END, ~holdout)
    Xb, yb, _ = build(n_test // 3, CALIB_END, int(ts[-1]) + 1, holdout)
    ka = ~np.isnan(Xa[:, R])
    m2 = new_model(max(20, n_trees // 2)).fit(Xa[ka], ya[ka] - Xa[ka, R])
    report["unseen_rooms"] = {"rooms_held_out": int(holdout.sum()), **metrics(yb, predict(m2, Xb)),
                              "baseline_persistence_plus_neighbour_delta_mae": metrics(yb, Xb[:, col["persist_adj"]])["mae"]}

    importances = sorted(zip(F.FEATURE_NAMES, model.feature_importances_.round(4).tolist()), key=lambda x: -x[1])
    data_hash = hashlib.sha256(pd.util.hash_pandas_object(Wdf.fillna(-999), index=True).values.tobytes()).hexdigest()[:10]
    trained = datetime.now(timezone.utc)
    version = f"temp-rf-{trained:%Y%m%d}-{data_hash}{'-quick' if args.quick else ''}"
    meta = {
        "version": version, "sensor_type": "TEMPERATURE", "algorithm": "sklearn RandomForestRegressor (residual on reference value)",
        "target": "y - ref", "ref_kinds": F.REF_KINDS,
        "params": model.get_params(), "trained_at": trained.isoformat(), "features": F.FEATURE_NAMES,
        "rooms": rooms, "data_range": [int(ts[0]), int(ts[-1])], "train_end": TRAIN_END, "calib_end": CALIB_END,
        "n_train": len(ytr), "n_calibration": len(yca), "n_test": len(yte), "conformal": conformal,
        "evaluation": report, "feature_importances": importances,
        "limits": {"max_gap_h_good": 12, "max_gap_h_any": 72 * 2},
    }
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, settings.models_dir / f"{version}.joblib", compress=3)
    (settings.models_dir / f"{version}.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    (settings.models_dir / "CURRENT").write_text(version, encoding="utf-8")
    write_model_card(meta)
    log.info("model %s written; test MAE %.3f RMSE %.3f coverage %.3f", version, report["model"]["mae"],
             report["model"]["rmse"], report["interval_coverage_90"])
    print(json.dumps(report, indent=2))


def write_model_card(meta: dict) -> None:
    ev = meta["evaluation"]
    lines = [
        f"# Model card - {meta['version']}", "",
        "Temperature virtual sensor (Ghost Sensor). Generated by `python -m ghost.ml.train`.", "",
        "## Protocol",
        "* Data: real BMS historian, 15-min buckets, Jan-Jun 2026, 138 rooms with a temperature sensor.",
        "* Outages are **simulated on real data**: a room's readings are hidden from `t0` to `t`; the hidden real value at `t` is the target.",
        "* Outage lengths sampled from 15 min to 72 h (30 % < 2 h, 35 % 2-12 h, 35 % 12-72 h); 5 % of samples also hide the room's own history.",
        "* Split by time: train < 2026-04-15, calibration 2026-04-15..2026-05-01, **test 2026-05-01..2026-06-30**.",
        f"* Samples: train {meta['n_train']:,}, calibration {meta['n_calibration']:,}, test {meta['n_test']:,}.", "",
        "## Results on the held-out test period", "",
        "| Method | MAE (degC) | RMSE (degC) | P90 abs err |", "|---|---|---|---|",
        f"| **Random Forest (this model)** | **{ev['model']['mae']}** | **{ev['model']['rmse']}** | {ev['model']['p90_abs_err']} |",
    ]
    for name, m in ev["baselines"].items():
        lines.append(f"| {name.replace('_', ' ')} | {m['mae']} | {m['rmse']} | {m['p90_abs_err']} |")
    lines += ["", "### By outage length", "", "| Gap | n | MAE | RMSE | persistence MAE | 90 % interval coverage |", "|---|---|---|---|---|---|"]
    for k, m in ev["by_gap"].items():
        lines.append(f"| {k} | {m['n']} | {m['mae']} | {m['rmse']} | {m['persistence_mae']} | {m['coverage_90']:.1%} |")
    lines += ["", "### By number of available neighbour sensors", "", "| Neighbours | n | MAE | RMSE | coverage |", "|---|---|---|---|---|"]
    for k, m in ev["by_neighbours"].items():
        lines.append(f"| {k} | {m['n']} | {m['mae']} | {m['rmse']} | {m['coverage_90']:.1%} |")
    u = ev["unseen_rooms"]
    lines += [
        "", f"**Unseen rooms** ({u['rooms_held_out']} rooms excluded from training): MAE {u['mae']} degC, RMSE {u['rmse']} degC.", "",
        "## Uncertainty",
        f"Split-conformal intervals at {int(meta['conformal']['level']*100)} % nominal level, calibrated per (outage length x neighbour availability) bin. "
        f"Empirical coverage on the test period: **{ev['interval_coverage_90']:.1%}**, mean half-width {ev['mean_interval_halfwidth']} degC. "
        "The interval is derived from validation residuals; it is *not* a probability that an individual prediction is correct.", "",
        "## Top features", "",
    ]
    lines += [f"* `{n}`: {v}" for n, v in meta["feature_importances"][:10]]
    worse = [k for k, m in ev["by_gap"].items() if m["persistence_mae"] < m["mae"]]
    lines += [
        "", "## Limitations",
        *([f"* For outages of {', '.join(worse)}, plain last-value persistence had a lower MAE than the model on the test period."] if worse else []),
        "* Trained on winter/spring data (Jan-Jun); summer cooling behaviour is out of distribution.",
        "* Neighbour features assume the IFC-derived adjacency reflects thermal coupling; rooms with no instrumented neighbour rely on own history and floor/building means.",
        "* Simultaneous outage of a room and all its neighbours (e.g. a gateway failure) degrades accuracy: see the `0 nb` row.",
        "* Only physical readings are used as inputs; virtual values are never chained.",
        "* Three rooms (00-02a-S, 01-49-N, 01-51-N) never produced a valid temperature in the export; their predictions have no room-specific history.",
    ]
    out = REPO_DIR / "docs" / "MODEL_CARD.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
