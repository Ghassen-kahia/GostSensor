"""Engine loop and agent orchestration.

Responsibility split
* The backend owns authoritative state: detection (Agent 1) runs here on every
  new bucket of building time, so monitoring continues without n8n or an LLM.
* n8n orchestrates Agents 2 -> 3 -> 4 by claiming pending incidents
  (POST /api/automation/claim) and calling the agent endpoints.
* Local fallback: if an incident version stays unclaimed (or its claim goes
  stale) for GHOST_LOCAL_FALLBACK_AFTER_S, the backend runs the agents itself.
  With GHOST_ORCHESTRATION_MODE=local it does so immediately.

Loop safety: agent endpoints never trigger n8n; the only backend -> n8n call is
an optional fire-and-forget webhook "new incidents available" after detection.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import httpx

from .. import db
from ..agents import analysis, documentation, notification
from ..agents.runs import Trigger, agent_run
from ..config import settings
from . import clock, detection

log = logging.getLogger("ghost.pipeline")


# ------------------------------------------------------------------ event bus (SSE)
class EventBus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, data: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait({"type": kind, "data": data})
            except queue.Full:
                pass


bus = EventBus()


# ------------------------------------------------------------------ agents 2-4
def run_agents(conn, iid: str, trigger: Trigger) -> dict:
    """Run documentation -> analysis -> notification for one incident (local orchestration)."""
    out = {"incidentId": iid}
    try:
        out["documentation"] = documentation.run(conn, iid, trigger)
        out["analysis"] = analysis.run(conn, iid, trigger)
        out["notification"] = notification.run(conn, iid, trigger)
    except Exception as exc:  # recorded in agent_runs by the context manager
        out["error"] = f"{type(exc).__name__}: {exc}"
        log_workflow_event(conn, "backend", "ERROR", "AGENT_FAILURE", f"Local agent chain failed for {iid}", {"error": out["error"]})
        db.execute("UPDATE incidents SET claimed_by=NULL, claimed_real=NULL WHERE id=?", (iid,), conn)
    bus.publish("incident.updated", {"id": iid})
    return out


def pending(conn, limit: int = 20) -> list[dict]:
    return db.rows("""SELECT id, pipeline_version, processed_version, severity, status, claimed_by, claimed_real, version_real
                      FROM incidents WHERE processed_version < pipeline_version ORDER BY
                      CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END, detected_ts
                      LIMIT ?""", (limit,), conn)


def claim(conn, owner: str, limit: int = 5, execution_id: str | None = None) -> list[dict]:
    """Atomically claim unprocessed incident versions (idempotent for the same owner)."""
    now = db.now_real()
    stale = now - 2 * settings.local_fallback_after_s
    claimed = []
    with db.transaction(conn):
        for r in pending(conn, 100):
            if len(claimed) >= limit:
                break
            if r["claimed_by"] and r["claimed_by"] != owner and (r["claimed_real"] or 0) > stale:
                continue
            db.execute("UPDATE incidents SET claimed_by=?, claimed_real=? WHERE id=?", (owner, now, r["id"]), conn)
            claimed.append({"incidentId": r["id"], "version": r["pipeline_version"], "severity": r["severity"], "status": r["status"]})
    if claimed:
        log_workflow_event(conn, "n8n" if owner.startswith("n8n") else "backend", "INFO", "CLAIM",
                           f"{owner} claimed {len(claimed)} incident(s)", {"executionId": execution_id, "incidents": [c["incidentId"] for c in claimed]})
    return claimed


def log_workflow_event(conn, source, level, kind, message, details=None, workflow=None, execution_id=None, node=None) -> None:
    db.execute("INSERT INTO workflow_events(source,workflow,execution_id,node,level,kind,message,details,real_ts) VALUES (?,?,?,?,?,?,?,?,?)",
               (source, workflow, execution_id, node, level, kind, message[:1000], db.dumps(details) if details else None, db.now_real()), conn)


def local_fallback(conn) -> list[str]:
    now = db.now_real()
    done = []
    for r in pending(conn, 10):
        age = now - (r["claimed_real"] or r["version_real"] or now)
        if settings.orchestration_mode == "local" or (age >= settings.local_fallback_after_s and
                                                      (not r["claimed_by"] or age >= 2 * settings.local_fallback_after_s)):
            owner = "local"
            db.execute("UPDATE incidents SET claimed_by=?, claimed_real=? WHERE id=?", (owner, now, r["id"]), conn)
            kind = "local" if settings.orchestration_mode == "local" else "local-fallback"
            if kind == "local-fallback":
                log_workflow_event(conn, "backend", "WARN", "LOCAL_FALLBACK",
                                   f"{r['id']} v{r['pipeline_version']} not processed by n8n within {settings.local_fallback_after_s}s; running agents locally")
            run_agents(conn, r["id"], Trigger(kind))
            done.append(r["id"])
    return done


def notify_n8n(conn, incident_ids: list[str]) -> None:
    """Optional push to an n8n webhook so it does not have to wait for its next poll."""
    if not settings.n8n_trigger_webhook or not incident_ids:
        return
    try:
        httpx.post(settings.n8n_trigger_webhook, json={"incidentIds": incident_ids, "source": "ghost-backend"},
                   headers={"X-Ghost-Token": settings.automation_token or ""}, timeout=5)
    except httpx.HTTPError as exc:
        log_workflow_event(conn, "backend", "WARN", "N8N_WEBHOOK_FAILED", f"Could not reach n8n webhook: {type(exc).__name__}")


# ------------------------------------------------------------------ detection tick
def detect(conn, trigger: Trigger, force: bool = False) -> dict | None:
    t = clock.now(conn)
    bucket = t - t % 900
    last = db.get_state("last_detection_bucket", conn=conn)
    if not force and last == bucket:
        return None
    detector = detection.Detector(conn)
    summary = detector.evaluate(t)
    db.set_state("last_detection_bucket", bucket, conn)
    db.set_state("last_detection", {**summary, "real": db.now_real(), "trigger": trigger.kind}, conn)
    changed = summary["created"] or summary["updated"] or summary["cleared"]
    if changed or trigger.kind != "engine":
        with agent_run(conn, "DETECTION", None, trigger) as h:
            h.summary = (f"{len(summary['created'])} new, {len(summary['updated'])} updated, {len(summary['cleared'])} cleared; "
                         f"{summary['virtualPredictions']} virtual readings")
            h.details = summary
    bus.publish("tick", {"now": t, "statusCounts": summary["statusCounts"]})
    for iid in summary["created"] + summary["updated"] + summary["cleared"]:
        bus.publish("incident.updated", {"id": iid})
    if summary["created"] or summary["updated"]:
        notify_n8n(conn, summary["created"] + summary["updated"])
    return summary


class Engine:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_tick_real: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ghost-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        conn = db.connect()
        while not self._stop.is_set():
            try:
                detect(conn, Trigger("engine"))
                notification.process_retries(conn)
                notification.check_escalations(conn)
                local_fallback(conn)
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("engine tick failed")
                try:
                    log_workflow_event(conn, "backend", "ERROR", "ENGINE_TICK", self.last_error)
                except Exception:
                    pass
            self.last_tick_real = int(time.time())
            self._stop.wait(settings.tick_seconds)
        conn.close()


engine = Engine()
