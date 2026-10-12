"""Feature construction for the temperature virtual sensor.

A *single* vectorised function, `compute_features`, is used both for training
(millions of simulated outages) and for online inference (one row), so the
model never sees features computed differently at serving time.

Scenario being modelled: room `r` has had no valid physical reading since
`t0` (its last valid bucket) and we want its temperature at `t >= t0`.

Only *physical* readings enter the features. Virtual (inferred) values are
never fed back, which prevents recursive inference from propagating through
the building.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STEP_S = 900                      # 15-minute buckets
OFFSET_WINDOW = 7 * 96            # 7 days of buckets for the room/neighbour offset
OFFSET_MIN_PERIODS = 96
WINGS = {"N": 0, "S": 1}
ZONES = {"ESEO": 0, "ESTP": 1, "ESPACES COMMUNS": 2, "LOGISTIQUE COMMUNE": 3}

FEATURE_NAMES = [
    "nb_mean", "nb_wmean", "nb_min", "nb_max", "nb_std", "nb_count",
    "vert_mean", "vert_count", "floor_mean", "bldg_mean",
    "last_val", "gap_h", "offset_7d", "nb_delta", "persist_adj", "nb_plus_offset",
    "hour_sin", "hour_cos", "dow", "weekend",
    "area_m2", "storey_idx", "wing", "zone",
    "ref", "ref_kind", "nbo_minus_ref", "nb_minus_ref",
]
REF_KINDS = ["persistence_adjusted", "neighbours_plus_offset", "neighbour_mean", "floor_mean", "building_mean", "none"]


@dataclass
class Graph:
    """Room ordering plus neighbour weight matrices (rooms with a sensor only)."""
    rooms: list[str]
    horiz: np.ndarray             # R x R weights (0 = not adjacent)
    vert: np.ndarray              # R x R 0/1
    floor: np.ndarray             # R x R 0/1 same storey (excluding self)
    static: np.ndarray            # R x 4: area, storey_idx, wing, zone

    @property
    def index(self) -> dict[str, int]:
        return {r: i for i, r in enumerate(self.rooms)}


def build_graph(rooms: list[str], room_meta: dict[str, dict], edges: list[dict], storey_order: dict[str, int]) -> Graph:
    idx = {r: i for i, r in enumerate(rooms)}
    n = len(rooms)
    horiz = np.zeros((n, n))
    vert = np.zeros((n, n))
    for e in edges:
        a, b = idx.get(e["room_a"]), idx.get(e["room_b"])
        if a is None or b is None:
            continue
        if e["kind"] == "VERTICAL":
            vert[a, b] = vert[b, a] = 1.0
        else:
            # weight: shared wall length (m), +2 m equivalent per door; DOOR-only edges count 1 m.
            w = (e.get("shared_length_m") or 1.0) + 2.0 * (e.get("door_count") or 0)
            horiz[a, b] = horiz[b, a] = max(horiz[a, b], w)
    storeys = np.array([storey_order.get(room_meta.get(r, {}).get("storey_id"), -1) for r in rooms])
    floor = (storeys[:, None] == storeys[None, :]).astype(float)
    np.fill_diagonal(floor, 0.0)
    static = np.array([
        [
            room_meta.get(r, {}).get("area_m2") or np.nan,
            storey_order.get(room_meta.get(r, {}).get("storey_id"), -1),
            WINGS.get(room_meta.get(r, {}).get("wing"), 2),
            ZONES.get(room_meta.get(r, {}).get("zone"), 4),
        ]
        for r in rooms
    ], dtype=float)
    return Graph(rooms, horiz, vert, floor, static)


def _masked_stats(values: np.ndarray, weights: np.ndarray):
    """values: S x R (NaN = unavailable); weights: S x R (0 = not a neighbour)."""
    avail = (~np.isnan(values)) & (weights > 0)
    v = np.where(avail, values, 0.0)
    w = np.where(avail, weights, 0.0)
    cnt = avail.sum(axis=1).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(cnt > 0, np.where(avail, v, 0).sum(1) / cnt, np.nan)
        wsum = w.sum(1)
        wmean = np.where(wsum > 0, (v * w).sum(1) / wsum, np.nan)
        vmin = np.where(cnt > 0, np.where(avail, values, np.inf).min(1), np.nan)
        vmax = np.where(cnt > 0, np.where(avail, values, -np.inf).max(1), np.nan)
        sq = np.where(avail, (values - mean[:, None]) ** 2, 0.0).sum(1)
        std = np.where(cnt > 1, np.sqrt(sq / np.maximum(cnt - 1, 1)), np.nan)
    return mean, wmean, vmin, vmax, std, cnt


def neighbour_mean_matrix(W: np.ndarray, graph: Graph) -> np.ndarray:
    """T x R matrix: unweighted mean of available horizontal neighbours at each step."""
    A = (graph.horiz > 0).astype(float)
    avail = ~np.isnan(W)
    num = np.nan_to_num(W) @ A.T
    den = avail.astype(float) @ A.T
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def rolling_offset(W: np.ndarray, nb_mean: np.ndarray) -> np.ndarray:
    """T x R trailing mean of (room - neighbour mean) over OFFSET_WINDOW, NaN-aware."""
    diff = W - nb_mean
    ok = ~np.isnan(diff)
    c = np.cumsum(np.vstack([np.zeros((1, diff.shape[1])), ok]), axis=0)
    s = np.cumsum(np.vstack([np.zeros((1, diff.shape[1])), np.where(ok, diff, 0.0)]), axis=0)
    T = diff.shape[0]
    hi = np.arange(1, T + 1)
    lo = np.maximum(hi - OFFSET_WINDOW, 0)
    cnt = c[hi] - c[lo]
    tot = s[hi] - s[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt >= OFFSET_MIN_PERIODS, tot / np.maximum(cnt, 1), np.nan)


def compute_features(
    W: np.ndarray,
    ts: np.ndarray,
    graph: Graph,
    r_idx: np.ndarray,
    t_idx: np.ndarray,
    t0_idx: np.ndarray,
    nb_mean_T: np.ndarray | None = None,
    offset_T: np.ndarray | None = None,
    hide_own_history: np.ndarray | None = None,
) -> np.ndarray:
    """Return an S x len(FEATURE_NAMES) matrix (column `ref` is the residual anchor).

    W        T x R physical values (NaN = no valid reading)
    ts       T epoch seconds of each row of W
    r_idx    target room column per sample
    t_idx    prediction time row per sample
    t0_idx   row of the last valid reading of the target before the outage (-1 = none)
    """
    if nb_mean_T is None:
        nb_mean_T = neighbour_mean_matrix(W, graph)
    if offset_T is None:
        offset_T = rolling_offset(W, nb_mean_T)
    S = len(r_idx)
    rows_t = W[t_idx].copy()
    # The target itself is unknown at prediction time.
    rows_t[np.arange(S), r_idx] = np.nan

    nb_mean, nb_wmean, nb_min, nb_max, nb_std, nb_cnt = _masked_stats(rows_t, graph.horiz[r_idx])
    vert_mean, _, _, _, _, vert_cnt = _masked_stats(rows_t, graph.vert[r_idx])
    floor_mean, *_ = _masked_stats(rows_t, graph.floor[r_idx])
    cnt_all = (~np.isnan(rows_t)).sum(axis=1)
    bldg_mean = np.where(cnt_all > 0, np.nansum(rows_t, axis=1) / np.maximum(cnt_all, 1), np.nan)

    has_t0 = t0_idx >= 0
    safe_t0 = np.where(has_t0, t0_idx, 0)
    last_val = np.where(has_t0, W[safe_t0, r_idx], np.nan)
    gap_h = np.where(has_t0, (ts[t_idx] - ts[safe_t0]) / 3600.0, np.nan)
    offset = np.where(has_t0, offset_T[safe_t0, r_idx], np.nan)
    # Neighbour change since the outage began (neighbour sets at t and t0 may differ slightly).
    nb_t0 = np.where(has_t0, nb_mean_T[safe_t0, r_idx], np.nan)
    nb_delta = nb_mean - nb_t0
    if hide_own_history is not None:
        for arr in (last_val, offset, nb_delta):
            arr[hide_own_history] = np.nan
    persist_adj = last_val + np.nan_to_num(nb_delta)
    persist_adj = np.where(np.isnan(last_val), np.nan, persist_adj)
    nb_plus_offset = nb_wmean + np.nan_to_num(offset)

    # Reference value: the model learns a *correction* to it (residual learning),
    # which keeps predictions anchored when temperatures leave the training range.
    candidates = [persist_adj, nb_plus_offset, nb_wmean, floor_mean, bldg_mean]
    ref = np.full(S, np.nan)
    ref_kind = np.full(S, float(len(REF_KINDS) - 1))
    for k in range(len(candidates) - 1, -1, -1):
        ok = ~np.isnan(candidates[k])
        ref = np.where(ok, candidates[k], ref)
        ref_kind = np.where(ok, float(k), ref_kind)

    t_sec = ts[t_idx]
    hour = (t_sec % 86400) / 3600.0
    dow = ((t_sec // 86400) + 3) % 7  # 1970-01-01 was a Thursday -> Monday = 0
    st = graph.static[r_idx]
    X = np.column_stack([
        nb_mean, nb_wmean, nb_min, nb_max, nb_std, nb_cnt,
        vert_mean, vert_cnt, floor_mean, bldg_mean,
        last_val, gap_h, offset, nb_delta, persist_adj, nb_plus_offset,
        np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24), dow, (dow >= 5).astype(float),
        st[:, 0], st[:, 1], st[:, 2], st[:, 3],
        ref, ref_kind, nb_plus_offset - ref, nb_wmean - ref,
    ])
    return X.astype(np.float32)
