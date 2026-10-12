"""Incident persistence: idempotent upsert from Agent 1 events, lifecycle, timeline."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .. import db
from ..agents.schemas import SEVERITIES, AnomalyEvent

OPEN_STATUSES = ("OPEN", "INVESTIGATING", "ACKNOWLEDGED")
TRANSITIONS = {
    "OPEN": {"INVESTIGATING", "ACKNOWLEDGED", "RESOLVED"},
    "INVESTIGATING": {"ACKNOWLEDGED", "RESOLVED"},
    "ACKNOWLEDGED": {"INVESTIGATING", "RESOLVED"},
    "RESOLVED": {"CLOSED", "OPEN"},
    "CLOSED": set(),
}

TITLES = {
    "SENSOR_OFFLINE": "Sensor offline", "SENSOR_STALE": "Stale sensor data", "SENSOR_INVALID": "Sensor transmitting invalid values",
    "SENSOR_SPIKE": "Sudden spike in sensor reading", "SENSOR_DRIFT": "Suspected sensor drift",
    "SENSOR_OUT_OF_RANGE": "Reading outside physically plausible range", "ENVIRONMENTAL_ANOMALY": "Environmental anomaly",
    "ENERGY_ANOMALY": "Abnormal energy consumption", "TOPOLOGY_INCONSISTENCY": "Topology inconsistency", "UNKNOWN": "Unclassified anomaly",
}


def sev_rank(s: str) -> int:
    return SEVERITIES.index(s)


def event_id(dedup_key: str, onset: int | None) -> str:
    return "EVT-" + hashlib.sha1(f"{dedup_key}|{onset}".encode()).hexdigest()[:16]


def incident_id(dedup_key: str, onset: int | None, detected: int) -> str:
    day = datetime.fromtimestamp(detected, tz=timezone.utc).strftime("%Y%m%d")
    return f"INC-{day}-" + hashlib.sha1(f"{dedup_key}|{onset}".encode()).hexdigest()[:6].upper()


def title_for(ev: AnomalyEvent, category: str | None = None, n_sensors: int | None = None) -> str:
    base = TITLES.get(category or ev.category, category or ev.category)
    if ev.scope == "GROUP":
        return f"{base} - {n_sensors or len(ev.sensorIds)} sensors" + (f" ({', '.join(ev.stations)})" if ev.stations else "")
    where = ev.roomId or ev.sensorId
    return f"{base} - {where}" + (f" ({ev.sensorType.lower()})" if ev.sensorType else "")


def timeline(conn, incident: str, kind: str, actor: str, message: str, data=None, ts: int | None = None) -> None:
    db.execute("INSERT INTO incident_timeline(incident_id, ts, real_ts, kind, actor, message, data) VALUES (?,?,?,?,?,?,?)",
               (incident, ts, db.now_real(), kind, actor, message, db.dumps(data) if data is not None else None), conn)


def find_open(conn, dedup_key: str) -> dict | None:
    return db.row(f"SELECT * FROM incidents WHERE dedup_key=? AND status IN {OPEN_STATUSES}", (dedup_key,), conn)


def upsert(conn, ev: AnomalyEvent) -> tuple[dict, str]:
    """Persist an event; returns (incident, action) with action in created | escalated | updated | duplicate."""
    now = db.now_real()
    with db.transaction(conn):
        existing = find_open(conn, ev.dedupKey)
        if existing is None:
            iid = incident_id(ev.dedupKey, ev.onsetAt, ev.detectedAt)
            # Idempotency: the same event replayed after resolution must not create a second incident.
            if db.row("SELECT id FROM incidents WHERE id=?", (iid,), conn):
                db.execute("INSERT OR IGNORE INTO anomaly_events(id,incident_id,category,severity,rule_id,sensor_id,room_id,onset_ts,detected_ts,observed_value,payload,simulated,created_real) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (ev.eventId, iid, ev.category, ev.severity, ev.ruleId, ev.sensorId, ev.roomId, ev.onsetAt, ev.detectedAt,
                            ev.observedValue, ev.model_dump_json(), int(ev.simulated), now), conn)
                return db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn), "duplicate"
            db.execute(
                """INSERT INTO incidents(id,dedup_key,title,category,severity,status,scope,primary_sensor_id,primary_room_id,onset_ts,detected_ts,
                     last_seen_ts,simulated,summary,created_real,updated_real,version_real) VALUES (?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, ev.dedupKey, title_for(ev), ev.category, ev.severity, ev.scope, ev.sensorId, ev.roomId, ev.onsetAt,
                 ev.detectedAt, ev.detectedAt, int(ev.simulated), ev.evidence[0] if ev.evidence else None, now, now, now), conn)
            for sid in ev.sensorIds or ([ev.sensorId] if ev.sensorId else []):
                db.execute("INSERT OR IGNORE INTO incident_sensors(incident_id, sensor_id) VALUES (?,?)", (iid, sid), conn)
            _store_event(conn, ev, iid, now)
            timeline(conn, iid, "DETECTED", "AGENT_1_DETECTION", f"{ev.category} detected ({ev.severity}) by rule {ev.ruleId}",
                     {"eventId": ev.eventId, "severityReasons": ev.severityReasons}, ev.detectedAt)
            return db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn), "created"

        iid = existing["id"]
        new_event = _store_event(conn, ev, iid, now)
        added = []
        for sid in ev.sensorIds or ([ev.sensorId] if ev.sensorId else []):
            if db.execute("INSERT OR IGNORE INTO incident_sensors(incident_id, sensor_id) VALUES (?,?)", (iid, sid), conn).rowcount:
                added.append(sid)
        action = "duplicate" if not new_event and not added else "updated"
        sets = {"last_seen_ts": max(existing["last_seen_ts"], ev.detectedAt), "updated_real": now}
        if new_event:
            sets["occurrences"] = existing["occurrences"] + 1
        significant = False
        if sev_rank(ev.severity) > sev_rank(existing["severity"]):
            sets["severity"] = ev.severity
            significant = True
            timeline(conn, iid, "ESCALATED", "AGENT_1_DETECTION", f"Severity raised {existing['severity']} -> {ev.severity}",
                     {"reasons": ev.severityReasons}, ev.detectedAt)
        if ev.category != existing["category"] and ev.category == "SENSOR_OFFLINE" and existing["category"] == "SENSOR_STALE":
            sets["category"] = ev.category
            sets["title"] = title_for(ev)
            significant = True
            timeline(conn, iid, "RECLASSIFIED", "AGENT_1_DETECTION", "Stale data now exceeds the offline timeout: reclassified SENSOR_OFFLINE", None, ev.detectedAt)
        if added:
            total = db.scalar("SELECT COUNT(*) FROM incident_sensors WHERE incident_id=?", (iid,), conn)
            sets["title"] = title_for(ev, sets.get("category", existing["category"]), total) if ev.scope == "GROUP" else existing["title"]
            timeline(conn, iid, "SENSORS_ADDED", "AGENT_1_DETECTION", f"{len(added)} more sensor(s) joined this incident", {"sensors": added[:50]}, ev.detectedAt)
            significant = significant or len(added) >= 5
        if significant:
            sets["pipeline_version"] = existing["pipeline_version"] + 1
            sets["version_real"] = now
            sets["claimed_by"] = None
            sets["claimed_real"] = None
            action = "escalated"
        db.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), iid), conn)
        return db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn), action


def _store_event(conn, ev: AnomalyEvent, iid: str, now: int) -> bool:
    cur = db.execute(
        "INSERT OR IGNORE INTO anomaly_events(id,incident_id,category,severity,rule_id,sensor_id,room_id,onset_ts,detected_ts,observed_value,payload,simulated,created_real) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ev.eventId, iid, ev.category, ev.severity, ev.ruleId, ev.sensorId, ev.roomId, ev.onsetAt, ev.detectedAt,
         ev.observedValue, ev.model_dump_json(), int(ev.simulated), now), conn)
    return cur.rowcount > 0


def transition(conn, iid: str, new_status: str, actor: str, note: str | None = None, ts: int | None = None) -> dict:
    with db.transaction(conn):
        inc = db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn)
        if inc is None:
            raise KeyError(iid)
        if new_status == inc["status"]:
            return inc
        if new_status not in TRANSITIONS[inc["status"]]:
            raise ValueError(f"cannot move incident from {inc['status']} to {new_status}")
        if new_status == "OPEN" and find_open(conn, inc["dedup_key"]):
            raise ValueError("another open incident already tracks this condition")
        sets = {"status": new_status, "updated_real": db.now_real()}
        if new_status == "ACKNOWLEDGED":
            sets.update(acknowledged_ts=ts, acknowledged_by=actor)
        elif new_status == "RESOLVED":
            sets.update(resolved_ts=ts, resolved_by=actor, resolution_note=note)
        elif new_status == "CLOSED":
            sets.update(closed_ts=ts)
        if new_status in ("ACKNOWLEDGED", "RESOLVED", "OPEN"):
            # Agent 4 may need to send an acknowledgement / resolution notice.
            sets.update(pipeline_version=inc["pipeline_version"] + 1, version_real=db.now_real(), claimed_by=None, claimed_real=None)
        db.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), iid), conn)
        timeline(conn, iid, new_status, actor, f"Status {inc['status']} -> {new_status}" + (f": {note}" if note else ""), None, ts)
        return db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn)
