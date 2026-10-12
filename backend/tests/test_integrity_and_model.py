"""Scenario I (data integrity), J (model validation) and unit tests."""
from __future__ import annotations

import json

import numpy as np
import pytest


# ------------------------------------------------------------------ I: data integrity
def test_I_adjacency_is_consistent(conn):
    from ghost import db

    assert db.scalar("SELECT COUNT(*) FROM room_adjacency WHERE room_a >= room_b", conn=conn) == 0
    orphans = db.scalar("""SELECT COUNT(*) FROM room_adjacency a WHERE NOT EXISTS (SELECT 1 FROM rooms r WHERE r.id=a.room_a)
                           OR NOT EXISTS (SELECT 1 FROM rooms r WHERE r.id=a.room_b)""", conn=conn)
    assert orphans == 0
    # every edge carries its geometric justification
    assert db.scalar("SELECT COUNT(*) FROM room_adjacency WHERE kind='WALL' AND (shared_length_m IS NULL OR shared_length_m < 0.8)", conn=conn) == 0
    assert db.scalar("SELECT COUNT(*) FROM room_adjacency WHERE kind='DOOR' AND door_count < 1", conn=conn) == 0
    assert db.scalar("SELECT COUNT(*) FROM room_adjacency WHERE kind='VERTICAL' AND overlap_m2 IS NULL", conn=conn) == 0


def test_I_sensors_rooms_and_incidents_are_consistent(conn):
    from ghost import db

    assert db.scalar("SELECT COUNT(*) FROM sensors s WHERE s.room_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM rooms r WHERE r.id=s.room_id)", conn=conn) == 0
    assert db.scalar("SELECT COUNT(*) FROM sensors WHERE type='TEMPERATURE'", conn=conn) == 138
    assert db.scalar("SELECT COUNT(*) FROM incident_sensors x WHERE NOT EXISTS (SELECT 1 FROM sensors s WHERE s.id=x.sensor_id)", conn=conn) == 0
    assert db.scalar("SELECT COUNT(*) FROM anomaly_events e WHERE e.incident_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM incidents i WHERE i.id=e.incident_id)", conn=conn) == 0
    # at most one open incident per condition
    assert db.scalar("""SELECT COUNT(*) FROM (SELECT dedup_key FROM incidents WHERE status IN ('OPEN','INVESTIGATING','ACKNOWLEDGED')
                        GROUP BY dedup_key HAVING COUNT(*) > 1)""", conn=conn) == 0
    # timestamps are sane
    assert db.scalar("SELECT COUNT(*) FROM incidents WHERE onset_ts IS NOT NULL AND onset_ts > detected_ts", conn=conn) == 0
    assert db.scalar("SELECT COUNT(*) FROM incidents WHERE resolved_ts IS NOT NULL AND resolved_ts < detected_ts", conn=conn) == 0


def test_I_topology_reload_is_idempotent(conn, test_env):
    from ghost import db
    from ghost.etl.build_all import load_topology

    topo = json.loads((test_env / "derived" / "topology.json").read_text(encoding="utf-8"))
    counts = lambda: [db.scalar(f"SELECT COUNT(*) FROM {t}", conn=conn) for t in ("rooms", "room_adjacency", "room_equipment", "storeys")]
    before = counts()
    load_topology(conn, topo)
    load_topology(conn, topo)
    assert counts() == before


def test_I_api_topology_uses_real_edges_only(client, conn):
    from ghost import db

    t = client.get("/api/topology", params={"storey": "N2"}).json()
    ids = {r["id"] for r in t["rooms"]}
    assert all(e["room_a"] in ids and e["room_b"] in ids for e in t["edges"])
    n = db.scalar("""SELECT COUNT(*) FROM room_adjacency a JOIN rooms ra ON ra.id=a.room_a JOIN rooms rb ON rb.id=a.room_b
                     WHERE ra.storey_id='N2' AND rb.storey_id='N2'""", conn=conn)
    assert len(t["edges"]) == n


# ------------------------------------------------------------------ J: model validation
def test_J_offline_evaluation_beats_baselines_and_is_calibrated(client):
    v = client.get("/api/virtual-sensors/validation").json()
    ev = v["offlineEvaluation"]
    assert ev is not None, "model must be trained"
    model_mae = ev["model"]["mae"]
    assert model_mae < ev["baselines"]["last_value_persistence"]["mae"]
    assert model_mae < ev["baselines"]["neighbour_weighted_mean"]["mae"]
    assert 0.8 <= ev["interval_coverage_90"] <= 0.97
    assert ev["unseen_rooms"]["mae"] < 1.0


def test_J_independent_outage_simulation_on_held_out_month(test_env):
    """Re-run the inference path on June (held-out) data, hiding real readings, and score it."""
    import pandas as pd

    from ghost import db
    from ghost.engine import virtual
    from ghost.ml import features as F
    from ghost.ml.train import load_graph

    b = virtual.load_bundle(force=True)
    W = pd.read_parquet(test_env / "derived" / "temperature_15min.parquet")
    W = W.where((W > -20) & (W < 60))
    rooms = b.meta["rooms"]
    W = W[rooms]
    arr, tsv = W.to_numpy(float), W.index.to_numpy(np.int64)
    graph = load_graph(db.connect(), rooms)
    rng = np.random.default_rng(123)
    june = np.where(tsv >= int(pd.Timestamp("2026-06-01", tz="UTC").timestamp()))[0]
    r = rng.integers(0, len(rooms), 4000)
    t = rng.choice(june, 4000)
    t0 = t - rng.integers(4, 97, 4000)  # 1-24 h outages
    ok = ~np.isnan(arr[t, r]) & ~np.isnan(arr[t0, r])
    X = F.compute_features(arr, tsv, graph, r[ok], t[ok], t0[ok])
    col = F.FEATURE_NAMES.index("ref")
    keep = ~np.isnan(X[:, col])
    pred = X[keep, col] + b.model.predict(X[keep])
    y = arr[t[ok], r[ok]][keep]
    mae = float(np.abs(pred - y).mean())
    persist = float(np.abs(X[keep, F.FEATURE_NAMES.index("last_val")] - y).mean())
    assert mae < persist
    assert mae < 0.8


def test_J_features_never_use_the_target_value(test_env):
    import pandas as pd

    from ghost import db
    from ghost.ml import features as F
    from ghost.ml.train import load_graph

    W = pd.read_parquet(test_env / "derived" / "temperature_15min.parquet").iloc[:2000]
    rooms = list(W.columns)
    arr, tsv = W.to_numpy(float), W.index.to_numpy(np.int64)
    graph = load_graph(db.connect(), rooms)
    r, t, t0 = np.array([5]), np.array([1500]), np.array([1400])
    a = F.compute_features(arr, tsv, graph, r, t, t0)
    arr2 = arr.copy()
    arr2[1401:1501, 5] = 99.0  # corrupt the target during the outage window
    b = F.compute_features(arr2, tsv, graph, r, t, t0)
    np.testing.assert_allclose(a, b, equal_nan=True)


# ------------------------------------------------------------------ unit tests
def test_grounding_check_rejects_invented_numbers():
    from ghost.agents.documentation import ungrounded_numbers

    corpus = "E1: 31 sensors silent since 09:45. Last valid 18.14 degC."
    assert ungrounded_numbers("31 sensors silent; last value 18.14 degC.", corpus) == []
    assert ungrounded_numbers("Temperature reached 27.5 degC in 3 rooms.", corpus) == ["27.5"]


def test_llm_narrative_is_validated_before_storage(conn):
    from ghost import db
    from ghost.agents import documentation, llm
    from ghost.agents.runs import Trigger

    iid = db.scalar("SELECT id FROM incidents ORDER BY detected_ts LIMIT 1", conn=conn)
    if iid is None:
        from conftest import detect_at

        detect_at(conn, "2026-03-02T06:00Z")
        iid = db.scalar("SELECT id FROM incidents ORDER BY detected_ts LIMIT 1", conn=conn)

    class Fake(llm.LLMProvider):
        name = "fake"

        def __init__(self, payload):
            self.payload = payload

        @property
        def model(self):
            return "fake-model"

        def available(self):
            return True

        def generate_json(self, system, prompt, schema, max_tokens=4000):
            return llm.LLMResult(self.payload, "fake-model", 1)

    try:
        db.execute("DELETE FROM incident_reports WHERE incident_id=?", (iid,), conn)
        llm.set_provider(Fake({"narrative": "The sensor failed at 99.9 degC.", "citedEvidence": ["E1"]}))
        out = documentation.run(conn, iid, Trigger("local"))
        assert "rejected" in out["llm"]
        assert db.scalar("SELECT ai_narrative FROM incident_reports WHERE incident_id=? ORDER BY version DESC", (iid,), conn) is None

        db.execute("DELETE FROM incident_reports WHERE incident_id=?", (iid,), conn)
        llm.set_provider(Fake({"narrative": "The point is not delivering usable values (E1).", "citedEvidence": ["E1"]}))
        out = documentation.run(conn, iid, Trigger("local"))
        assert out["llm"] == "accepted"
        md = db.scalar("SELECT markdown FROM incident_reports WHERE incident_id=? ORDER BY version DESC", (iid,), conn)
        assert "AI narrative (generated, verify before use)" in md
    finally:
        llm.set_provider(llm.LLMProvider())


def test_zero_is_a_valid_reading_not_a_failure():
    from ghost.engine.readings import classify

    sensor = {"resolution_s": 3600, "stale_after_s": 10800, "offline_after_s": 43200}
    rec = {"last_tx_ts": 1000 * 3600, "last_valid_ts": 1000 * 3600, "last_value": 0.0}
    assert classify(sensor, rec, 1001 * 3600 + 60) == "ONLINE"


def test_fault_overlay_does_not_modify_historian(conn):
    from ghost import db
    from ghost.engine import readings

    s = db.row("SELECT * FROM sensors WHERE id='T-01-02-S'", conn=conn)
    t = 1772460000
    raw = readings.window([s], t - 7200, t, conn, faults={})
    faults = {s["id"]: [{"kind": "OFFSET", "start_ts": t - 7200, "end_ts": None, "params": {"delta": 5.0}}]}
    shifted = readings.window([s], t - 7200, t, conn, faults=faults)
    np.testing.assert_allclose(shifted["value"].to_numpy(), raw["value"].to_numpy() + 5.0)
    assert shifted["simulated"].all() and not raw["simulated"].any()
    again = readings.window([s], t - 7200, t, conn, faults={})
    np.testing.assert_allclose(again["value"].to_numpy(), raw["value"].to_numpy())
