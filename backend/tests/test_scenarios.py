"""Acceptance scenarios A-H on the real historian (tests run in file order and share state)."""
from __future__ import annotations

import pytest

from conftest import AUTO, OP, detect_at, reset_runtime, ts

SENSOR = "T-02-14-N"
T0 = "2026-03-02T06:00Z"


@pytest.fixture(scope="module", autouse=True)
def fresh(conn, client):
    reset_runtime(conn, T0)
    detect_at(conn, T0)


def test_A_normal_operation_returns_physical_reading(client, conn):
    from ghost import db

    r = client.get(f"/api/sensors/{SENSOR}/current").json()
    assert r["sensorStatus"] == "ONLINE"
    assert r["source"] == "PHYSICAL"
    assert r["inferredAt"] is None and r["modelVersion"] is None
    hist = db.row("SELECT value FROM measurements WHERE sensor_id=? AND ts<=? AND value IS NOT NULL ORDER BY ts DESC LIMIT 1",
                  (SENSOR, ts(T0) - 900), conn)
    assert r["value"] == pytest.approx(hist["value"])


def test_B_sensor_outage_detected_by_heartbeat(client, conn):
    res = client.post("/api/simulation/faults", json={"sensorId": SENSOR, "kind": "SILENCE", "note": "pytest outage"}, headers=OP)
    assert res.status_code == 200, res.text
    assert res.json()["simulated"] is True
    detect_at(conn, "2026-03-02T07:00Z")  # ~75 min of silence > 45 min stale threshold
    cur = client.get(f"/api/sensors/{SENSOR}/current").json()
    assert cur["sensorStatus"] == "STALE"
    assert cur["simulatedFault"] is True
    items = client.get("/api/incidents", params={"q": SENSOR}).json()["items"]
    inc = [i for i in items if i["primarySensorId"] == SENSOR]
    assert len(inc) == 1 and inc[0]["category"] == "SENSOR_STALE" and inc[0]["simulated"]
    detect_at(conn, "2026-03-02T08:30Z")  # > 2 h -> offline, reclassified on the same incident
    inc2 = client.get(f"/api/incidents/{inc[0]['id']}").json()
    assert inc2["incident"]["category"] == "SENSOR_OFFLINE"
    assert any(t["kind"] == "RECLASSIFIED" for t in inc2["timeline"])


def test_C_virtual_sensor_activation_with_provenance(client):
    cur = client.get(f"/api/sensors/{SENSOR}/current").json()
    assert cur["source"] == "INFERRED"
    assert cur["observedAt"] is None, "an inferred value must never carry a physical observation time"
    assert cur["modelVersion"].startswith("temp-rf-")
    u = cur["uncertainty"]
    assert u["lower"] < cur["value"] < u["upper"] and u["level"] == pytest.approx(0.9)
    assert cur["inferenceQuality"] in ("GOOD", "DEGRADED")
    assert cur["lastPhysical"]["value"] is not None
    vs = client.get("/api/virtual-sensors").json()
    item = next(a for a in vs["active"] if a["sensor"]["id"] == SENSOR)
    inputs = item["prediction"]["inputs"]
    assert inputs["neighbourCountAvailable"] >= 1
    assert all(n["sensorId"] != SENSOR for n in inputs["neighbours"])
    # Simulated outage: the true value is known, so live validation is possible.
    actual = item["history"][-1]["actual"]
    assert actual is not None and abs(actual - cur["value"]) < 3.0


def test_D_agent_handoff_through_automation_api(client, conn):
    claim = client.post("/api/automation/claim", json={"executionId": "pytest-1", "limit": 20}, headers=AUTO).json()
    ids = [c["incidentId"] for c in claim["items"]]
    target = next(i["id"] for i in client.get("/api/incidents", params={"q": SENSOR}).json()["items"])
    assert target in ids
    body = {"incidentId": target, "executionId": "pytest-1", "workflow": "pytest"}
    d = client.post("/api/agents/documentation", json=body, headers=AUTO).json()
    assert d["valid"] and d["version"] >= 1
    a = client.post("/api/agents/analysis", json=body, headers=AUTO).json()
    assert a["valid"] and a["leadingHypothesis"] == "SENSOR_HARDWARE"
    n = client.post("/api/agents/notification", json=body, headers=AUTO).json()
    assert n["valid"] and "policy" in n
    view = client.get(f"/api/incidents/{target}/agents").json()
    assert view["agent1"]["event"]["category"] in ("SENSOR_STALE", "SENSOR_OFFLINE")
    assert view["agent2"]["report"]["version"] >= 1
    hyps = view["agent3"]["analysis"]["hypotheses"]
    assert hyps and all(h["origin"] == "RULE_ENGINE" and h["outcome"] is None for h in hyps)
    assert all(r["trigger"] == "n8n" and r["n8n_execution_id"] == "pytest-1" for r in view["agent2"]["runs"])
    report_md = client.get(f"/api/incidents/{target}/report", params={"format": "md"}).text
    assert "Virtual sensor" in report_md and "not physical measurements" in report_md
    inc = client.get(f"/api/incidents/{target}").json()["incident"]
    assert inc["processedVersion"] == inc["pipelineVersion"] and inc["pipelineStage"] == "NOTIFIED"


def test_E_email_in_test_mode_is_never_reported_as_sent(client, conn, test_env):
    from ghost import db

    detect_at(conn, "2026-03-03T12:00Z")  # real building-wide outage in the historian
    grp = db.row("SELECT id, severity FROM incidents WHERE scope='GROUP' AND category='SENSOR_OFFLINE' AND status!='RESOLVED'", conn=conn)
    assert grp and grp["severity"] == "CRITICAL"
    body = {"incidentId": grp["id"], "executionId": "pytest-2"}
    for agent in ("documentation", "analysis", "notification"):
        assert client.post(f"/api/agents/{agent}", json=body, headers=AUTO).status_code == 200
    notes = client.get(f"/api/incidents/{grp['id']}").json()["notifications"]
    email = [n for n in notes if n["channel"] == "EMAIL" and n["kind"] == "ALERT"]
    assert len(email) == 1 and email[0]["status"] == "TEST_MODE" and email[0]["sentAt"] is None
    assert email[0]["recipients"] == ["op***@example.com"], "recipients are masked in storage/API"
    eml = list((test_env / "outbox").glob(f"*{grp['id']}*.eml"))
    assert eml and "Inferred values" in eml[0].read_text(encoding="utf-8", errors="replace")
    analysis = client.get(f"/api/incidents/{grp['id']}/recommendations").json()
    assert analysis["hypotheses"][0]["code"] == "DATA_PIPELINE"


def test_E2_smtp_failure_is_recorded_and_retried(client, conn):
    from ghost import db
    from ghost.agents import notification
    from ghost.config import load_policy, settings

    inc = db.row("SELECT * FROM incidents WHERE scope='GROUP' AND category='SENSOR_OFFLINE'", conn=conn)
    object.__setattr__(settings, "email_mode", "smtp")
    object.__setattr__(settings, "smtp_host", "127.0.0.1")
    object.__setattr__(settings, "smtp_port", 9)  # closed port -> connection refused (transient)
    object.__setattr__(settings, "email_from", "ghost@example.com")
    try:
        policy = {**load_policy("notification"), "storm": {"max_emails_per_hour": 100, "min_interval_same_incident_s": 0}}
        res = notification._create_and_send(conn, inc, "ESCALATION", ["ops@example.com"], policy)
        n = db.row("SELECT * FROM notifications WHERE id=?", (res["id"],), conn)
        assert n["status"] == "PENDING" and n["attempts"] == 1 and n["next_attempt_real"] is not None and n["sent_real"] is None
        att = db.rows("SELECT * FROM notification_attempts WHERE notification_id=?", (n["id"],), conn)
        assert len(att) == 1 and att[0]["success"] == 0 and att[0]["error"]
        for _ in range(5):
            db.execute("UPDATE notifications SET next_attempt_real=0 WHERE id=?", (n["id"],), conn)
            notification.process_retries(conn)
        n = db.row("SELECT * FROM notifications WHERE id=?", (n["id"],), conn)
        assert n["status"] == "FAILED" and n["attempts"] == 4, "bounded retries"
    finally:
        object.__setattr__(settings, "email_mode", "test")


def test_F_duplicate_events_do_not_duplicate_incidents_or_emails(client, conn):
    from ghost import db

    before_inc = db.scalar("SELECT COUNT(*) FROM incidents", conn=conn)
    before_notif = db.scalar("SELECT COUNT(*) FROM notifications", conn=conn)
    detect_at(conn, "2026-03-03T12:00Z")
    detect_at(conn, "2026-03-03T12:00Z")
    assert db.scalar("SELECT COUNT(*) FROM incidents", conn=conn) == before_inc
    grp = db.row("SELECT id FROM incidents WHERE scope='GROUP' AND category='SENSOR_OFFLINE'", conn=conn)
    for _ in range(2):
        client.post("/api/agents/notification", json={"incidentId": grp["id"]}, headers=AUTO)
    assert db.scalar("SELECT COUNT(*) FROM notifications", conn=conn) == before_notif
    reports = db.scalar("SELECT COUNT(*) FROM incident_reports WHERE incident_id=?", (grp["id"],), conn)
    client.post("/api/agents/documentation", json={"incidentId": grp["id"]}, headers=AUTO)
    assert db.scalar("SELECT COUNT(*) FROM incident_reports WHERE incident_id=?", (grp["id"],), conn) == reports


def test_G_service_outages_preserve_core_monitoring(client, conn):
    from ghost import db
    from ghost.agents import llm
    from ghost.engine import virtual

    h = client.get("/api/health").json()
    assert h["components"]["n8n"]["status"] == "UNREACHABLE"
    assert h["components"]["database"]["status"] == "OK"
    assert h["components"]["mlModel"]["status"] == "OK"

    class Down(llm.LLMProvider):
        name = "stub"

        def available(self):
            return True

        def generate_json(self, *a, **k):
            raise llm.LLMUnavailable("timeout")

    llm.set_provider(Down())
    try:
        iid = db.scalar("SELECT id FROM incidents WHERE primary_sensor_id=?", ("T-02-14-N",), conn)
        db.execute("DELETE FROM incident_reports WHERE incident_id=?", (iid,), conn)
        out = client.post("/api/agents/documentation", json={"incidentId": iid}, headers=AUTO).json()
        assert out["valid"] and "LLM unavailable" in out["llm"]
    finally:
        llm.set_provider(llm.LLMProvider())

    # ML model unavailable: detection still runs, virtual sensor reports INSUFFICIENT instead of a value.
    saved = virtual._bundle
    virtual._bundle, virtual._bundle_error = None, "model unavailable (test)"
    original = virtual.load_bundle
    virtual.load_bundle = lambda force=False: None
    try:
        s = detect_at(conn, "2026-03-02T09:00Z")
        assert s["statusCounts"].get("ONLINE", 0) > 100
        p = db.row("SELECT * FROM virtual_predictions WHERE sensor_id=? ORDER BY id DESC LIMIT 1", ("T-02-14-N",), conn)
        assert p["value"] is None and p["quality"] == "INSUFFICIENT"
        cur = client.get("/api/sensors/T-02-14-N/current").json()
        assert cur["source"] == "NONE" and cur["value"] is None
    finally:
        virtual.load_bundle = original
        virtual._bundle = saved


def test_H_physical_recovery_switches_back_and_keeps_history(client, conn):
    from ghost import db

    fault = client.get("/api/simulation/faults", params={"active": "true"}).json()["items"]
    fid = next(f["id"] for f in fault if f["sensor_id"] == SENSOR)
    assert client.delete(f"/api/simulation/faults/{fid}", headers=OP).status_code == 200
    detect_at(conn, "2026-03-02T10:00Z")
    cur = client.get(f"/api/sensors/{SENSOR}/current").json()
    assert cur["source"] == "PHYSICAL" and cur["sensorStatus"] == "ONLINE"
    inc = next(i for i in client.get("/api/incidents", params={"q": SENSOR}).json()["items"] if i["primarySensorId"] == SENSOR)
    assert inc["status"] == "RESOLVED" and inc["resolvedBy"] == "SYSTEM:auto-recovery"
    hist = client.get(f"/api/sensors/{SENSOR}/history", params={"hours": 6}).json()
    assert len(hist["inferred"]) >= 3, "inference history is preserved after recovery"
    assert db.scalar("SELECT COUNT(*) FROM incident_reports WHERE incident_id=?", (inc["id"],), conn) >= 1


def test_operator_lifecycle_and_auth(client):
    inc = client.get("/api/incidents", params={"status": "OPEN,INVESTIGATING"}).json()["items"][0]
    assert client.post(f"/api/incidents/{inc['id']}/status", json={"status": "ACKNOWLEDGED"}).status_code == 401
    r = client.post(f"/api/incidents/{inc['id']}/status", json={"status": "ACKNOWLEDGED", "note": "on it"}, headers=OP)
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    bad = client.post(f"/api/incidents/{inc['id']}/status", json={"status": "CLOSED"}, headers=OP)
    assert bad.status_code == 409, "ACKNOWLEDGED -> CLOSED must go through RESOLVED"
    assert client.post("/api/automation/claim", json={}, headers={"X-Ghost-Token": "wrong"}).status_code == 401
    assert client.post("/api/incidents/x/status", json={"status": "ACKNOWLEDGED", "evil": 1}, headers=OP).status_code == 422


def test_local_fallback_processes_incidents_n8n_never_claimed(conn):
    """Scenario G (n8n down): pending work ages from its version, not from per-tick updates."""
    from ghost import db
    from ghost.engine import pipeline

    detect_at(conn, "2026-03-02T10:30Z")
    pend = pipeline.pending(conn, 100)
    assert pend, "expected unprocessed incident versions"
    target = pend[0]["id"]
    now = db.now_real()
    # Re-detected every tick (updated_real is fresh) but the version was created 10 minutes ago.
    db.execute("UPDATE incidents SET updated_real=?, version_real=?, claimed_by=NULL, claimed_real=NULL WHERE id=?", (now, now - 600, target), conn)
    db.execute("UPDATE incidents SET version_real=? WHERE id!=?", (now, target), conn)
    done = pipeline.local_fallback(conn)
    assert target in done
    inc = db.row("SELECT processed_version, pipeline_version FROM incidents WHERE id=?", (target,), conn)
    assert inc["processed_version"] == inc["pipeline_version"]
    assert db.scalar("SELECT COUNT(*) FROM agent_runs WHERE incident_id=? AND trigger='local-fallback'", (target,), conn) == 3
    assert db.scalar("SELECT COUNT(*) FROM workflow_events WHERE kind='LOCAL_FALLBACK'", conn=conn) >= 1
