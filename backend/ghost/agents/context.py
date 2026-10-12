"""Evidence gathering shared by Agent 2 (documentation) and Agent 3 (analysis)."""
from __future__ import annotations

from collections import Counter

import numpy as np

from .. import db
from ..engine import clock, readings


def incident_context(conn, iid: str) -> dict:
    inc = db.row("SELECT * FROM incidents WHERE id=?", (iid,), conn)
    if inc is None:
        raise KeyError(iid)
    t_now = clock.now(conn)
    events = db.rows("SELECT id, payload, detected_ts FROM anomaly_events WHERE incident_id=? ORDER BY detected_ts", (iid,), conn)
    first = db.loads(events[0]["payload"]) if events else {}
    latest = db.loads(events[-1]["payload"]) if events else {}
    members = db.rows(
        """SELECT s.id, s.type, s.unit, s.room_id, s.station, s.station_source, s.display_name, s.stale_after_s, s.offline_after_s,
                  s.resolution_s, r.storey_id, r.long_name, r.zone, st.status, st.last_tx_ts, st.last_valid_ts, st.last_value
           FROM incident_sensors i JOIN sensors s ON s.id=i.sensor_id LEFT JOIN rooms r ON r.id=s.room_id
           LEFT JOIN sensor_state st ON st.sensor_id=s.id WHERE i.incident_id=? ORDER BY s.id""", (iid,), conn)
    member_ids = [m["id"] for m in members]

    predictions = {}
    if member_ids:
        for p in db.rows(
            f"""SELECT * FROM virtual_predictions WHERE id IN (SELECT MAX(id) FROM virtual_predictions
                 WHERE sensor_id IN ({','.join('?' * len(member_ids))}) AND target_ts >= ? GROUP BY sensor_id)""",
            (*member_ids, (inc["onset_ts"] or inc["detected_ts"]) - 900), conn):
            p["inputs"] = db.loads(p["inputs"], {})
            p["quality_reasons"] = db.loads(p["quality_reasons"], [])
            predictions[p["sensor_id"]] = p

    primary = db.row("SELECT * FROM sensors WHERE id=?", (inc["primary_sensor_id"],), conn) if inc["primary_sensor_id"] else None
    history = {}
    if primary:
        t_ref = inc["condition_cleared_ts"] or t_now
        start = (inc["onset_ts"] or inc["detected_ts"]) - 86400
        w = readings.window([primary], start, t_ref, conn)
        vals = w["value"].dropna()
        week = readings.window([primary], start - 6 * 86400, start + 86400, conn)["value"].dropna()
        history = {
            "sensorId": primary["id"], "from": start, "to": t_ref,
            "points": [[int(a), None if np.isnan(b) else round(float(b), 3)] for a, b in w[["ts", "value"]].to_numpy()][-200:],
            "baseline7d": None if week.empty else {"mean": round(float(week.mean()), 3), "min": round(float(week.min()), 3),
                                                   "max": round(float(week.max()), 3), "std": round(float(week.std()), 3), "n": int(len(week))},
            "validInWindow": int(len(vals)),
        }

    rooms = sorted({m["room_id"] for m in members if m["room_id"]})
    related = []
    if member_ids:
        related = db.rows(
            f"""SELECT DISTINCT i.id, i.title, i.category, i.severity, i.status, i.detected_ts, i.resolved_ts, i.resolution_note
                FROM incidents i JOIN incident_sensors s ON s.incident_id=i.id
                WHERE s.sensor_id IN ({','.join('?' * len(member_ids))}) AND i.id != ? ORDER BY i.detected_ts DESC LIMIT 15""",
            (*member_ids, iid), conn)
    concurrent = db.rows(
        """SELECT id, title, category, severity, scope, onset_ts, primary_room_id FROM incidents
           WHERE id != ? AND status IN ('OPEN','INVESTIGATING','ACKNOWLEDGED') AND category NOT IN ('TOPOLOGY_INCONSISTENCY')
           ORDER BY detected_ts DESC LIMIT 50""", (iid,), conn)
    timeline = db.rows("SELECT ts, real_ts, kind, actor, message FROM incident_timeline WHERE incident_id=? ORDER BY id", (iid,), conn)
    runs = db.rows("SELECT id, agent, status, trigger, n8n_execution_id, n8n_workflow, started_real, duration_ms FROM agent_runs WHERE incident_id=? ORDER BY id DESC LIMIT 20", (iid,), conn)
    equipment = db.rows(f"SELECT id, type, room_id, elec_feed, heat_feed, cool_feed, air_feed FROM room_equipment WHERE room_id IN ({','.join('?' * len(rooms))}) LIMIT 60",
                        tuple(rooms), conn) if rooms and len(rooms) <= 5 else []
    return {
        "incident": inc, "now": t_now, "clockMode": clock.state(conn).mode, "firstEvent": first, "latestEvent": latest, "eventCount": len(events),
        "members": members, "memberTypes": dict(Counter(m["type"] for m in members)), "rooms": rooms,
        "storeys": dict(Counter(m["storey_id"] for m in members if m["storey_id"])),
        "stations": dict(Counter(m["station"] for m in members if m["station"])),
        "predictions": predictions, "history": history, "related": related, "concurrent": concurrent,
        "timeline": timeline, "runs": runs, "equipment": equipment,
    }
