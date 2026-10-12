"""Mutating endpoints: operator actions (Bearer token) and n8n automation (X-Ghost-Token)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..agents import analysis, documentation, notification
from ..agents.runs import Trigger
from ..config import settings
from ..engine import clock, incidents as inc_store, pipeline
from .security import limiter, require_automation, require_operator
from .views import iso


def rate_limited(request: Request):
    limiter.check(request)


ops = APIRouter(prefix="/api", dependencies=[Depends(rate_limited)])
auto = APIRouter(prefix="/api", dependencies=[Depends(rate_limited), Depends(require_automation)])


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ operator
class StatusChange(Body):
    status: Literal["INVESTIGATING", "ACKNOWLEDGED", "RESOLVED", "CLOSED", "OPEN"]
    note: str | None = Field(default=None, max_length=1000)


@ops.post("/incidents/{iid}/status")
def change_status(iid: str, body: StatusChange, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    try:
        inc = inc_store.transition(conn, iid, body.status, actor, body.note, ts=clock.now(conn))
    except KeyError:
        raise HTTPException(404, "incident not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    pipeline.bus.publish("incident.updated", {"id": iid})
    return {"id": iid, "status": inc["status"]}


class Outcome(Body):
    outcome: Literal["CONFIRMED", "REJECTED"] | None
    note: str | None = Field(default=None, max_length=1000)


@ops.post("/hypotheses/{hid}/outcome")
def hypothesis_outcome(hid: int, body: Outcome, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    h = db.row("SELECT * FROM hypotheses WHERE id=?", (hid,), conn)
    if not h:
        raise HTTPException(404, "hypothesis not found")
    db.execute("UPDATE hypotheses SET outcome=?, outcome_by=?, outcome_real=? WHERE id=?",
               (body.outcome, actor if body.outcome else None, db.now_real() if body.outcome else None, hid), conn)
    inc_store.timeline(conn, h["incident_id"], "HYPOTHESIS_OUTCOME", actor,
                       f"Hypothesis '{h['title']}' marked {body.outcome or 'unreviewed'} by a person" + (f": {body.note}" if body.note else ""), None, clock.now(conn))
    return {"id": hid, "outcome": body.outcome}


class RecStatus(Body):
    status: Literal["PROPOSED", "ACCEPTED", "DONE", "DISMISSED"]
    note: str | None = Field(default=None, max_length=1000)


@ops.post("/recommendations/{rid}/status")
def recommendation_status(rid: int, body: RecStatus, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    r = db.row("SELECT * FROM recommendations WHERE id=?", (rid,), conn)
    if not r:
        raise HTTPException(404, "recommendation not found")
    db.execute("UPDATE recommendations SET status=?, status_by=?, status_note=?, status_real=? WHERE id=?",
               (body.status, actor, body.note, db.now_real(), rid), conn)
    inc_store.timeline(conn, r["incident_id"], "RECOMMENDATION", actor, f"Recommendation marked {body.status} by a person: {r['action'][:120]}"
                       + (f" ({body.note})" if body.note else ""), None, clock.now(conn))
    return {"id": rid, "status": body.status}


@ops.post("/incidents/{iid}/reprocess")
def reprocess(iid: str, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    if not db.row("SELECT id FROM incidents WHERE id=?", (iid,), conn):
        raise HTTPException(404, "incident not found")
    return pipeline.run_agents(conn, iid, Trigger("operator"))


class ClockBody(Body):
    action: Literal["pause", "resume", "speed", "jump"]
    speed: float | None = Field(default=None, gt=0, le=3600)
    toTs: int | None = None


@ops.post("/clock")
def clock_control(body: ClockBody, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    try:
        clock.control(body.action, speed=body.speed, to_ts=body.toTs, conn=conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    pipeline.log_workflow_event(conn, "backend", "INFO", "CLOCK", f"{actor} clock {body.action}", body.model_dump())
    return clock.describe(conn)


class FaultBody(Body):
    sensorId: str
    kind: Literal["SILENCE", "NULL_VALUES", "OFFSET", "SPIKE"]
    durationMinutes: int | None = Field(default=None, ge=15, le=60 * 24 * 14)
    delta: float | None = Field(default=None, ge=-50, le=50)
    ratePerDay: float | None = Field(default=None, ge=-20, le=20)
    note: str | None = Field(default=None, max_length=500)


@ops.post("/simulation/faults")
def inject_fault(body: FaultBody, actor: str = Depends(require_operator)):
    """Simulated fault overlay (labelled SIMULATED everywhere). Physical data is never modified."""
    conn = db.get_conn()
    s = db.row("SELECT id, resolution_s FROM sensors WHERE id=?", (body.sensorId,), conn)
    if not s:
        raise HTTPException(404, "sensor not found")
    t = clock.now(conn)
    start = t - t % s["resolution_s"] - s["resolution_s"]  # starts with the bucket being received now
    end = start + body.durationMinutes * 60 if body.durationMinutes else None
    params = {k: v for k, v in {"delta": body.delta, "rate_per_day": body.ratePerDay}.items() if v is not None}
    cur = db.execute("INSERT INTO fault_injections(sensor_id,kind,start_ts,end_ts,params,note,created_by,created_real) VALUES (?,?,?,?,?,?,?,?)",
                     (body.sensorId, body.kind, start, end, db.dumps(params), body.note, actor, db.now_real()), conn)
    pipeline.log_workflow_event(conn, "backend", "INFO", "FAULT_INJECTED", f"{actor} injected {body.kind} on {body.sensorId}", {"faultId": cur.lastrowid})
    db.set_state("last_detection_bucket", None, conn)  # re-evaluate on the next tick
    return {"id": cur.lastrowid, "sensorId": body.sensorId, "kind": body.kind, "startAt": iso(start), "endAt": iso(end), "simulated": True}


@ops.delete("/simulation/faults/{fid}")
def end_fault(fid: int, actor: str = Depends(require_operator)):
    conn = db.get_conn()
    f = db.row("SELECT * FROM fault_injections WHERE id=?", (fid,), conn)
    if not f:
        raise HTTPException(404, "fault not found")
    t = clock.now(conn)
    end = max(t - t % 900 - 900, f["start_ts"])
    db.execute("UPDATE fault_injections SET end_ts=? WHERE id=? AND (end_ts IS NULL OR end_ts > ?)", (end, fid, end), conn)
    db.set_state("last_detection_bucket", None, conn)
    pipeline.log_workflow_event(conn, "backend", "INFO", "FAULT_ENDED", f"{actor} ended simulated fault {fid}")
    return {"id": fid, "endAt": iso(end)}


class TestEmail(Body):
    confirm: bool


@ops.post("/notifications/test")
def test_email(body: TestEmail, actor: str = Depends(require_operator)):
    if not body.confirm:
        raise HTTPException(400, "set confirm=true to send a test notification")
    conn = db.get_conn()
    inc = db.row("SELECT * FROM incidents ORDER BY detected_ts DESC LIMIT 1", conn=conn)
    if not inc:
        raise HTTPException(409, "no incident available to build a test message from")
    import time as _t

    from email.message import EmailMessage

    subject, text, html_body = notification.compose(conn, inc, "ALERT")
    subject = "[TEST] " + subject
    cur = db.execute("""INSERT INTO notifications(incident_id,dedup_key,kind,severity,channel,recipients,subject,status,status_reason,created_real)
                        VALUES (?,?,'ALERT',?,'EMAIL',?,?,'PENDING','operator test',?)""",
                     (inc["id"], f"TEST:{inc['id']}:{_t.time_ns()}", inc["severity"], db.dumps([notification._mask(r) for r in settings.alert_recipients]),
                      subject, db.now_real()), conn)
    n = db.row("SELECT * FROM notifications WHERE id=?", (cur.lastrowid,), conn)
    if not settings.alert_recipients:
        db.execute("UPDATE notifications SET status='SUPPRESSED', status_reason='no recipients configured' WHERE id=?", (n["id"],), conn)
    else:
        notification._deliver(conn, n, subject, text, html_body, settings.alert_recipients)
    res = db.row("SELECT id, status, status_reason, artifact_path, last_error FROM notifications WHERE id=?", (n["id"],), conn)
    return {**res, "mode": settings.email_mode}


class ResetBody(Body):
    confirm: Literal["RESET"]
    startAt: str | None = None


@ops.post("/admin/reset")
def reset(body: ResetBody, actor: str = Depends(require_operator)):
    """Destructive: wipes runtime state (incidents, predictions, notifications, faults) and restarts the replay."""
    conn = db.get_conn()
    if clock.state(conn).mode != "REPLAY":
        raise HTTPException(409, "reset is only available in REPLAY mode")
    start = clock._parse_iso(body.startAt or settings.replay_start)
    with db.transaction(conn):
        for t in ("notification_attempts", "notifications", "recommendations", "hypotheses", "analyses", "incident_reports", "incident_timeline",
                  "anomaly_events", "incident_sensors", "virtual_predictions", "incidents", "sensor_state", "fault_injections", "agent_runs"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM system_state WHERE key IN ('last_detection_bucket','last_detection')")
    clock.reset(start, conn)
    pipeline.log_workflow_event(conn, "backend", "WARN", "RESET", f"{actor} reset runtime state; replay restarts at {iso(start)} (paused)")
    return clock.describe(conn)


@ops.post("/detection/run")
def run_detection(actor: str = Depends(require_operator)):
    conn = db.get_conn()
    return pipeline.detect(conn, Trigger("operator"), force=True)


# ------------------------------------------------------------------ automation (n8n)
class AutoBody(Body):
    executionId: str | None = Field(default=None, max_length=64)
    workflow: str | None = Field(default=None, max_length=100)


class DetectBody(AutoBody):
    force: bool = False


@auto.post("/automation/detect")
def auto_detect(body: DetectBody):
    """Workflow A: run Agent 1 now (no-op if this bucket was already evaluated) and report pending work."""
    conn = db.get_conn()
    trig = Trigger("n8n", body.executionId, body.workflow or "A-sensor-monitoring")
    summary = pipeline.detect(conn, trig, force=body.force)
    db.set_state("n8n_heartbeat", {"at": iso(db.now_real()), "workflow": trig.n8n_workflow, "executionId": body.executionId}, conn)
    return {"evaluated": summary is not None, "summary": summary, "pending": len(pipeline.pending(conn, 100)), "clock": clock.describe(conn)}


class ClaimBody(AutoBody):
    limit: int = Field(default=5, ge=1, le=20)


@auto.post("/automation/claim")
def auto_claim(body: ClaimBody):
    conn = db.get_conn()
    owner = f"n8n:{body.executionId or 'unknown'}"
    items = pipeline.claim(conn, owner, body.limit, body.executionId)
    return {"items": items, "count": len(items)}


@auto.get("/automation/incidents/{iid}/context")
def auto_context(iid: str):
    from ..agents.context import incident_context

    conn = db.get_conn()
    try:
        ctx = incident_context(conn, iid)
    except KeyError:
        raise HTTPException(404, "incident not found")
    ctx["members"] = ctx["members"][:100]
    return ctx


class AgentBody(AutoBody):
    incidentId: str = Field(max_length=40)
    version: int | None = None


def _agent(fn, body: AgentBody, default_wf: str):
    conn = db.get_conn()
    inc = db.row("SELECT id, pipeline_version, processed_version FROM incidents WHERE id=?", (body.incidentId,), conn)
    if not inc:
        raise HTTPException(404, "incident not found")
    if body.version is not None and body.version < inc["processed_version"]:
        return {"incidentId": body.incidentId, "skipped": True, "reason": "a newer version was already processed", "valid": True}
    try:
        out = fn(conn, body.incidentId, Trigger("n8n", body.executionId, body.workflow or default_wf))
    except Exception as exc:
        pipeline.log_workflow_event(conn, "backend", "ERROR", "AGENT_FAILURE", f"{default_wf} failed for {body.incidentId}",
                                    {"error": f"{type(exc).__name__}: {exc}"[:500]}, body.workflow, body.executionId)
        raise HTTPException(500, f"agent failed: {type(exc).__name__}")
    pipeline.bus.publish("incident.updated", {"id": body.incidentId})
    return out


@auto.post("/agents/documentation")
def agent_documentation(body: AgentBody):
    return _agent(documentation.run, body, "B-incident-documentation")


@auto.post("/agents/analysis")
def agent_analysis(body: AgentBody):
    return _agent(analysis.run, body, "C-root-cause-analysis")


@auto.post("/agents/notification")
def agent_notification(body: AgentBody):
    return _agent(notification.run, body, "D-alerting")


class WorkflowEvent(AutoBody):
    level: Literal["INFO", "WARN", "ERROR"] = "ERROR"
    kind: str = Field(max_length=60)
    message: str = Field(max_length=1000)
    node: str | None = Field(default=None, max_length=120)
    details: dict | None = None


SECRET_HINTS = ("password", "token", "secret", "apikey", "api_key", "authorization", "cookie")


def _scrub(obj, depth=0):
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return {k: ("[redacted]" if any(h in k.lower() for h in SECRET_HINTS) else _scrub(v, depth + 1)) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [_scrub(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, str):
        return obj[:500]
    return obj


@auto.post("/automation/events")
def workflow_event(body: WorkflowEvent):
    """Workflow E (error handler) and any workflow can log execution events here (secrets scrubbed)."""
    conn = db.get_conn()
    pipeline.log_workflow_event(conn, "n8n", body.level, body.kind, body.message, _scrub(body.details or {}), body.workflow, body.executionId, body.node)
    return {"recorded": True}


class Measurement(Body):
    sensorId: str
    ts: int
    value: float | None


class IngestBody(Body):
    measurements: list[Measurement] = Field(max_length=5000)


@auto.post("/ingest/measurements")
def ingest(body: IngestBody):
    """LIVE mode ingestion: readings are bucketed like the historian (origin=INGEST)."""
    conn = db.get_conn()
    sensors = {s["id"]: s for s in db.rows("SELECT id, resolution_s, valid_min, valid_max FROM sensors", conn=conn)}
    accepted, rejected = 0, []
    with db.transaction(conn):
        for m in body.measurements:
            s = sensors.get(m.sensorId)
            if not s:
                rejected.append({"sensorId": m.sensorId, "reason": "unknown sensor"}); continue
            b = m.ts - m.ts % s["resolution_s"]
            valid = m.value is not None
            db.execute("""INSERT INTO measurements(sensor_id,ts,value,n_samples,n_valid,origin) VALUES (?,?,?,1,?,'INGEST')
                          ON CONFLICT(sensor_id,ts) DO UPDATE SET n_samples=n_samples+1, n_valid=n_valid+excluded.n_valid,
                          value=CASE WHEN excluded.value IS NULL THEN value WHEN value IS NULL THEN excluded.value
                                     ELSE (value*n_valid + excluded.value)/(n_valid+1) END""",
                       (m.sensorId, b, m.value, int(valid)), conn)
            accepted += 1
    return {"accepted": accepted, "rejected": rejected}
