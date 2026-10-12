"""Test session: an isolated copy of the real built database + trained model.

The suite needs the data build and a trained model:
    python -m ghost.etl.build_all && python -m ghost.ml.train
Tests run against a temporary copy, so the working database is never modified.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _source_data_dir() -> Path:
    # Resolve the configured data dir without importing ghost (settings are frozen at import).
    env = os.environ.get("GHOST_DATA_DIR")
    if not env:
        for f in (BACKEND.parent / ".env", BACKEND / ".env"):
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.startswith("GHOST_DATA_DIR="):
                        env = line.split("=", 1)[1].strip()
    return Path(env) if env else BACKEND.parent / "data"


@pytest.fixture(scope="session", autouse=True)
def test_env(tmp_path_factory):
    src = _source_data_dir()
    if not (src / "ghost.db").exists() or not (src / "models" / "CURRENT").exists():
        pytest.exit(f"Built data not found in {src}. Run the ETL and training first (see README).", returncode=2)
    dst = tmp_path_factory.mktemp("ghostdata")
    # Consistent snapshot even if the dev server is running (WAL mode).
    with sqlite3.connect(src / "ghost.db") as s, sqlite3.connect(dst / "ghost.db") as d:
        s.backup(d)
    shutil.copytree(src / "models", dst / "models")
    shutil.copytree(src / "derived", dst / "derived")
    os.environ.update({
        "GHOST_DATA_DIR": str(dst),
        "GHOST_BACKGROUND_ENGINE": "false",
        "GHOST_OPERATOR_TOKEN": "test-operator-token",
        "GHOST_AUTOMATION_TOKEN": "test-automation-token",
        "GHOST_ORCHESTRATION_MODE": "n8n",
        "GHOST_LOCAL_FALLBACK_AFTER_S": "300",
        "N8N_BASE_URL": "http://127.0.0.1:9",  # nothing listens here: n8n "down"
        "N8N_TRIGGER_WEBHOOK_URL": "",
        "EMAIL_MODE": "test",
        "ALERT_RECIPIENTS": "ops@example.com",
        "LLM_PROVIDER": "none",
        "GHOST_ALLOW_ANONYMOUS_READ": "true",
    })
    yield dst


@pytest.fixture(scope="session")
def client(test_env):
    from fastapi.testclient import TestClient

    from ghost.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def conn(test_env):
    from ghost import db

    c = db.connect()
    db.migrate(c)
    return c


OP = {"Authorization": "Bearer test-operator-token", "X-Operator-Name": "pytest"}
AUTO = {"X-Ghost-Token": "test-automation-token"}


def ts(iso: str) -> int:
    import pandas as pd

    return int(pd.Timestamp(iso).timestamp())


def reset_runtime(conn, at_iso: str) -> None:
    from ghost import db
    from ghost.engine import clock

    for t in ("notification_attempts", "notifications", "recommendations", "hypotheses", "analyses", "incident_reports", "incident_timeline",
              "anomaly_events", "incident_sensors", "virtual_predictions", "incidents", "sensor_state", "fault_injections", "agent_runs", "workflow_events"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM system_state")
    clock.reset(ts(at_iso), conn)


def detect_at(conn, at_iso: str) -> dict:
    from ghost import db
    from ghost.agents.runs import Trigger
    from ghost.engine import clock, pipeline

    clock.reset(ts(at_iso), conn)
    return pipeline.detect(conn, Trigger("operator"), force=True)
