"""Traceable agent executions (table agent_runs)."""
from __future__ import annotations

import logging
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field

from .. import db

log = logging.getLogger("ghost.agents")


@dataclass
class Trigger:
    kind: str = "local"                 # engine | n8n | local-fallback | operator | local
    n8n_execution_id: str | None = None
    n8n_workflow: str | None = None


@dataclass
class RunHandle:
    id: int
    status: str = "SUCCESS"
    summary: str | None = None
    details: dict = field(default_factory=dict)


@contextmanager
def agent_run(conn, agent: str, incident_id: str | None, trigger: Trigger):
    started = time.time()
    cur = db.execute(
        "INSERT INTO agent_runs(agent, incident_id, status, trigger, n8n_execution_id, n8n_workflow, started_real) VALUES (?,?,?,?,?,?,?)",
        (agent, incident_id, "RUNNING", trigger.kind, trigger.n8n_execution_id, trigger.n8n_workflow, int(started)), conn)
    handle = RunHandle(cur.lastrowid)
    try:
        yield handle
    except Exception as exc:
        db.execute("UPDATE agent_runs SET status='FAILED', finished_real=?, duration_ms=?, error=? WHERE id=?",
                   (int(time.time()), int((time.time() - started) * 1000), f"{type(exc).__name__}: {exc}"[:2000], handle.id), conn)
        log.error("agent %s failed for %s: %s\n%s", agent, incident_id, exc, traceback.format_exc())
        raise
    db.execute("UPDATE agent_runs SET status=?, finished_real=?, duration_ms=?, summary=?, details=? WHERE id=?",
               (handle.status, int(time.time()), int((time.time() - started) * 1000), handle.summary,
                db.dumps(handle.details), handle.id), conn)
