"""Read-only endpoints used by the dashboard (and by n8n for context)."""
from __future__ import annotations

import csv
import io
import json
import queue
import time
from collections import Counter, defaultdict

import httpx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from .. import db
from ..agents import llm, notification
from ..config import settings
from ..engine import clock, pipeline, readings, virtual
from .security import require_read
from .views import incident_dict, iso, latest_predictions, reading, sensor_dict

router = APIRouter(prefix="/api", dependencies=[Depends(require_read)])
REAL_SPACES = "('ROOM','CIRCULATION','ROOF')"
SEV_ORDER = "CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END"


def conn():
    return db.get_conn()


def _page(page: int, size: int) -> tuple[int, int]:
    size = max(1, min(size, 500))
    return max(page, 1), size


# ------------------------------------------------------------------ health / clock
def _n8n_status() -> dict:
    out = {"baseUrl": settings.n8n_base_url, "reachable": False}
    if settings.n8n_base_url:
        try:
            r = httpx.get(settings.n8n_base_url.rstrip("/") + "/healthz", timeout=2.5)
            out["reachable"] = r.status_code == 200
            out["httpStatus"] = r.status_code
        except httpx.HTTPError as exc:
            out["error"] = type(exc).__name__
    hb = db.get_state("n8n_heartbeat")
    out["lastHeartbeat"] = hb
    return out


@router.get("/health")
def health():
    c = conn()
    components = {}
    try:
        db.scalar("SELECT 1", conn=c)
        components["database"] = {"status": "OK", "path": str(settings.db_path)}
    except Exception as exc:  # pragma: no cover
        components["database"] = {"status": "DOWN", "error": str(exc)}
    m = virtual.model_status()
    components["mlModel"] = {**m, "status": "OK" if m["status"] == "OK" else "DEGRADED"}
    eng = pipeline.engine
    components["engine"] = {"status": "OK" if eng.running and not eng.last_error else ("DEGRADED" if eng.running else "STOPPED"),
                            "lastTickReal": iso(eng.last_tick_real), "lastError": eng.last_error}
    n8n = _n8n_status()
    components["n8n"] = {**n8n, "status": "OK" if n8n["reachable"] else "UNREACHABLE", "orchestrationMode": settings.orchestration_mode}
    lst = llm.status()
    components["llm"] = {**lst, "status": "OK" if lst["configured"] else "NOT_CONFIGURED"}
    em = notification.email_config_status()
    components["email"] = {**em, "status": "OK" if em["configured"] else "NOT_CONFIGURED"}
    last_det = db.get_state("last_detection", conn=c)
    last_sensor = db.scalar("SELECT MAX(last_tx_ts) FROM sensor_state", conn=c)
    core_ok = components["database"]["status"] == "OK" and components["engine"]["status"] != "STOPPED"
    overall = "OK" if core_ok and all(v["status"] in ("OK", "NOT_CONFIGURED") for v in components.values()) else ("DEGRADED" if core_ok else "DOWN")
    return {"status": overall, "components": components, "clock": clock.describe(c),
            "lastDetection": last_det, "lastSensorUpdate": iso(last_sensor + 900) if last_sensor else None,
            "coreMonitoring": "OK" if core_ok else "DOWN"}


@router.get("/clock")
def get_clock():
    return clock.describe(conn())


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard/summary")
def summary():
    c = conn()
    states = Counter(r["status"] for r in db.rows("SELECT status FROM sensor_state", conn=c))
    by_type = defaultdict(Counter)
    for r in db.rows("SELECT s.type, st.status FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id", conn=c):
        by_type[r["type"]][r["status"] or "NO_DATA"] += 1
    open_sev = {r["severity"]: r["n"] for r in db.rows("SELECT severity, COUNT(*) n FROM incidents WHERE status IN ('OPEN','INVESTIGATING','ACKNOWLEDGED') GROUP BY severity", conn=c)}
    status_counts = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) n FROM incidents GROUP BY status", conn=c)}
    preds = latest_predictions(c)
    active_virtual = sum(1 for s in db.rows("SELECT sensor_id, status, last_valid_ts FROM sensor_state WHERE status IN ('STALE','OFFLINE','INVALID')", conn=c)
                         if (p := preds.get(s["sensor_id"])) and p["value"] is not None and (s["last_valid_ts"] is None or p["target_ts"] >= s["last_valid_ts"]))
    runs = db.rows("SELECT id, agent, incident_id, status, trigger, n8n_execution_id, started_real, duration_ms, summary FROM agent_runs ORDER BY id DESC LIMIT 8", conn=c)
    notifs = db.rows("SELECT id, incident_id, kind, severity, channel, status, status_reason, subject, created_real FROM notifications ORDER BY id DESC LIMIT 8", conn=c)
    return {
        "clock": clock.describe(c),
        "building": db.row("SELECT * FROM buildings LIMIT 1", conn=c),
        "rooms": {"total": db.scalar(f"SELECT COUNT(*) FROM rooms WHERE space_kind IN {REAL_SPACES}", conn=c),
                  "otherIfcSpaces": db.scalar(f"SELECT COUNT(*) FROM rooms WHERE space_kind NOT IN {REAL_SPACES}", conn=c), "instrumented": db.scalar("SELECT COUNT(DISTINCT room_id) FROM sensors WHERE room_id IS NOT NULL", conn=c)},
        "sensors": {"total": db.scalar("SELECT COUNT(*) FROM sensors", conn=c), "byStatus": dict(states), "byType": {k: dict(v) for k, v in by_type.items()}},
        "incidents": {"openBySeverity": open_sev, "byStatus": status_counts,
                      "open": sum(open_sev.values())},
        "virtualSensorsActive": active_virtual,
        "recentRuns": [{**r, "startedAt": iso(r.pop("started_real"))} for r in runs],
        "recentNotifications": [{**n, "createdAt": iso(n.pop("created_real"))} for n in notifs],
        "health": health()["status"],
    }


@router.get("/dashboard/timeseries")
def timeseries(hours: int = Query(48, ge=6, le=24 * 14)):
    c = conn()
    t = clock.now(c)
    start = t - hours * 3600
    temps = readings.sensors(c, ("TEMPERATURE",))
    w = readings.window(temps, start, t, c)
    grid = range(start - start % 900, t - 899, 900)
    v = w[w["value"].notna()]
    agg = v.groupby("ts")["value"].agg(["mean", "min", "max", "count"])
    tx = w.groupby("ts")["sensor_id"].nunique()
    co2 = readings.sensors(c, ("CO2",))
    wc = readings.window(co2, start, t, c)
    txc = wc.groupby("ts")["sensor_id"].nunique()
    inferred = {r["target_ts"]: r["n"] for r in db.rows(
        "SELECT target_ts, COUNT(*) n FROM virtual_predictions WHERE value IS NOT NULL AND target_ts BETWEEN ? AND ? GROUP BY target_ts", (start, t), c)}
    temperature, availability = [], []
    for ts in grid:
        a = agg.loc[ts] if ts in agg.index else None
        temperature.append({"t": iso(ts), "mean": None if a is None else round(float(a["mean"]), 2),
                            "min": None if a is None else round(float(a["min"]), 2), "max": None if a is None else round(float(a["max"]), 2),
                            "physical": 0 if a is None else int(a["count"]), "inferred": inferred.get(ts, 0)})
        availability.append({"t": iso(ts), "temperature": round(100 * float(tx.get(ts, 0)) / max(len(temps), 1), 1),
                             "co2": round(100 * float(txc.get(ts, 0)) / max(len(co2), 1), 1)})
    energy = db.rows("""SELECT m.ts, SUM(m.value) total, COUNT(*) meters FROM measurements m JOIN sensors s ON s.id=m.sensor_id
                        WHERE s.type='ENERGY' AND json_extract(s.meta,'$.resource')='Electricity' AND m.value IS NOT NULL
                        AND m.ts BETWEEN ? AND ? GROUP BY m.ts ORDER BY m.ts""", (start, t - 3600), c)
    day0 = t - 14 * 86400
    freq = defaultdict(Counter)
    for r in db.rows("SELECT detected_ts, category FROM incidents WHERE detected_ts >= ?", (day0,), c):
        freq[iso(r["detected_ts"] - r["detected_ts"] % 86400)[:10]][r["category"]] += 1
    open_sev = {r["severity"]: r["n"] for r in db.rows("SELECT severity, COUNT(*) n FROM incidents WHERE status IN ('OPEN','INVESTIGATING','ACKNOWLEDGED') GROUP BY severity", conn=c)}
    return {"from": iso(start), "to": iso(t), "temperature": temperature, "availability": availability,
            "energy": [{"t": iso(r["ts"]), "kwhPerHour": round(r["total"], 1), "meters": r["meters"]} for r in energy],
            "anomalyFrequency": [{"day": d, **cnt} for d, cnt in sorted(freq.items())],
            "severityDistribution": open_sev,
            "notes": {"energy": "Sum of electricity feeder meters (kWh/h), historian values up to building time.",
                      "availability": "Share of sensors whose bucket was received (includes simulated faults)."}}


# ------------------------------------------------------------------ topology
@router.get("/topology")
def topology(storey: str | None = None):
    c = conn()
    storeys = db.rows("SELECT id, name, elevation_m, sort_order FROM storeys ORDER BY sort_order", conn=c)
    counts = {r["storey_id"]: r["n"] for r in db.rows(f"SELECT storey_id, COUNT(*) n FROM rooms WHERE space_kind IN {REAL_SPACES} GROUP BY storey_id", conn=c)}
    for s in storeys:
        s["rooms"] = counts.get(s["id"], 0)
    where, params = f"WHERE r.space_kind IN {REAL_SPACES}", ()
    if storey:
        where, params = where + " AND r.storey_id=?", (storey,)
    rooms = db.rows(f"SELECT r.* FROM rooms r {where}", params, c)
    ids = {r["id"] for r in rooms}
    sensors_by_room = defaultdict(list)
    for s in db.rows("SELECT s.id, s.type, s.room_id, st.status, st.last_value, st.anomaly, st.incident_id FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id", conn=c):
        if s["room_id"] in ids:
            sensors_by_room[s["room_id"]].append(s)
    preds = latest_predictions(c)
    out_rooms = []
    for r in rooms:
        ss = sensors_by_room.get(r["id"], [])
        temp = next((s for s in ss if s["type"] == "TEMPERATURE"), None)
        virtual_active = bool(temp and temp["status"] in ("STALE", "OFFLINE", "INVALID") and preds.get(temp["id"]) and preds[temp["id"]]["value"] is not None)
        has_fault = any(s["status"] in ("OFFLINE", "STALE", "INVALID") for s in ss)
        out_rooms.append({
            "id": r["id"], "name": r["long_name"], "storey": r["storey_id"], "areaM2": r["area_m2"], "centroid": [r["centroid_x"], r["centroid_y"]],
            "footprint": db.loads(r["footprint"], []), "wing": r["wing"], "zone": r["zone"], "inIfc": bool(r["in_ifc"]), "kind": r["space_kind"],
            "sensors": [{"id": s["id"], "type": s["type"], "status": s["status"], "anomaly": s["anomaly"], "incidentId": s["incident_id"]} for s in ss],
            "temperature": None if not temp else (temp["last_value"] if temp["status"] == "ONLINE" else None),
            "virtualActive": virtual_active,
            "virtualValue": preds[temp["id"]]["value"] if virtual_active else None,
            "hasFault": has_fault, "hasAnomaly": any(s["anomaly"] for s in ss),
        })
    edges = db.rows("SELECT room_a, room_b, kind, shared_length_m, door_count, overlap_m2 FROM room_adjacency", conn=c)
    if storey:
        edges = [e for e in edges if e["room_a"] in ids and e["room_b"] in ids]
    return {"storeys": storeys, "rooms": out_rooms, "edges": edges,
            "method": ("Adjacency derived from IFC space geometry (shared wall >= 0.8 m within 0.6 m gap; IfcDoor contacts; vertical footprint overlap) "
                       "between rooms, circulation and roof spaces. Facade zones, aggregate occupancy zones and voids of the IFC export are excluded.")}


@router.get("/rooms")
def rooms(storey: str | None = None, q: str | None = None, instrumented: bool | None = None, all_spaces: bool = Query(False, alias="allSpaces")):
    c = conn()
    sql = """SELECT r.id, r.long_name, r.storey_id, r.area_m2, r.zone, r.in_ifc, r.space_kind, COUNT(s.id) sensors
             FROM rooms r LEFT JOIN sensors s ON s.room_id=r.id WHERE 1=1"""
    if not all_spaces:
        sql += f" AND r.space_kind IN {REAL_SPACES}"
    params: list = []
    if storey:
        sql += " AND r.storey_id=?"; params.append(storey)
    if q:
        sql += " AND (r.id LIKE ? OR r.long_name LIKE ?)"; params += [f"%{q}%", f"%{q}%"]
    sql += " GROUP BY r.id ORDER BY r.id"
    out = db.rows(sql, params, c)
    if instrumented is not None:
        out = [r for r in out if (r["sensors"] > 0) == instrumented]
    return {"items": out, "total": len(out)}


def _neighbours(c, room_id: str) -> list[dict]:
    edges = db.rows("SELECT * FROM room_adjacency WHERE room_a=? OR room_b=?", (room_id, room_id), c)
    out = []
    preds = None
    for e in edges:
        other = e["room_b"] if e["room_a"] == room_id else e["room_a"]
        r = db.row("SELECT id, long_name, storey_id FROM rooms WHERE id=?", (other,), c)
        sens = db.rows("SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts "
                       "FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id WHERE s.room_id=?", (other,), c)
        if preds is None:
            preds = latest_predictions(c)
        out.append({"room": r, "kind": e["kind"], "sharedLengthM": e["shared_length_m"], "doorCount": e["door_count"], "overlapM2": e["overlap_m2"],
                    "readings": [reading(s, s, preds.get(s["id"])) for s in sens if s["type"] in ("TEMPERATURE", "CO2")]})
    return out


@router.get("/rooms/{room_id}")
def room(room_id: str):
    c = conn()
    r = db.row("SELECT * FROM rooms WHERE id=?", (room_id,), c)
    if not r:
        raise HTTPException(404, "room not found")
    sens = db.rows("SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts "
                   "FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id WHERE s.room_id=?", (room_id,), c)
    preds = latest_predictions(c, [s["id"] for s in sens])
    incidents = db.rows("""SELECT DISTINCT i.* FROM incidents i LEFT JOIN incident_sensors s ON s.incident_id=i.id LEFT JOIN sensors x ON x.id=s.sensor_id
                           WHERE i.primary_room_id=? OR x.room_id=? ORDER BY i.detected_ts DESC LIMIT 20""", (room_id, room_id), c)
    return {"room": {**r, "footprint": db.loads(r["footprint"], []), "stations": db.loads(r["stations"], [])},
            "sensors": [{**sensor_dict(s), "reading": reading(s, s, preds.get(s["id"]))} for s in sens],
            "neighbours": _neighbours(c, room_id),
            "equipment": db.rows("SELECT * FROM room_equipment WHERE room_id=? ORDER BY type, id", (room_id,), c),
            "incidents": [incident_dict(i) for i in incidents]}


@router.get("/rooms/{room_id}/neighbors")
def room_neighbours(room_id: str):
    c = conn()
    if not db.row("SELECT id FROM rooms WHERE id=?", (room_id,), c):
        raise HTTPException(404, "room not found")
    return {"roomId": room_id, "neighbours": _neighbours(c, room_id)}


# ------------------------------------------------------------------ sensors
@router.get("/sensors")
def sensors(type: str | None = None, status: str | None = None, source: str | None = None, q: str | None = None,
            room: str | None = None, storey: str | None = None, anomaly: bool | None = None,
            sort: str = "id", order: str = "asc", page: int = 1, page_size: int = Query(50, alias="pageSize")):
    c = conn()
    page, page_size = _page(page, page_size)
    sql = """SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts,
                    r.storey_id FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id LEFT JOIN rooms r ON r.id=s.room_id WHERE 1=1"""
    params: list = []
    if type:
        sql += " AND s.type=?"; params.append(type.upper())
    if status:
        sql += f" AND COALESCE(st.status,'NO_DATA') IN ({','.join('?' * len(status.split(',')))})"; params += status.upper().split(",")
    if q:
        sql += " AND (s.id LIKE ? OR s.room_id LIKE ? OR s.display_name LIKE ?)"; params += [f"%{q}%"] * 3
    if room:
        sql += " AND s.room_id=?"; params.append(room)
    if storey:
        sql += " AND r.storey_id=?"; params.append(storey)
    if anomaly is not None:
        sql += " AND st.anomaly IS " + ("NOT NULL" if anomaly else "NULL")
    rows_ = db.rows(sql, params, c)
    preds = latest_predictions(c, [r["id"] for r in rows_ if r["status"] in ("STALE", "OFFLINE", "INVALID")])
    items = [{**sensor_dict(r), "storey": r["storey_id"], "reading": reading(r, r, preds.get(r["id"]))} for r in rows_]
    if source:
        items = [i for i in items if i["reading"]["source"] == source.upper()]
    keyf = {
        "id": lambda i: i["id"], "room": lambda i: i["roomId"] or "", "type": lambda i: i["type"],
        "status": lambda i: i["reading"]["sensorStatus"], "value": lambda i: (i["reading"]["value"] is None, i["reading"]["value"] or 0),
        "freshness": lambda i: i["reading"]["lastTransmissionAt"] or "",
    }.get(sort, lambda i: i["id"])
    items.sort(key=keyf, reverse=order == "desc")
    total = len(items)
    return {"items": items[(page - 1) * page_size: page * page_size], "total": total, "page": page, "pageSize": page_size,
            "statusCounts": dict(Counter(i["reading"]["sensorStatus"] for i in items)),
            "sourceCounts": dict(Counter(i["reading"]["source"] for i in items))}


def _sensor_row(c, sensor_id: str) -> dict:
    s = db.row("""SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts
                  FROM sensors s LEFT JOIN sensor_state st ON st.sensor_id=s.id WHERE s.id=?""", (sensor_id,), c)
    if not s:
        raise HTTPException(404, "sensor not found")
    return s


@router.get("/sensors/{sensor_id}")
def sensor(sensor_id: str):
    c = conn()
    s = _sensor_row(c, sensor_id)
    preds = latest_predictions(c, [sensor_id])
    faults = db.rows("SELECT * FROM fault_injections WHERE sensor_id=? ORDER BY id DESC", (sensor_id,), c)
    return {**sensor_dict(s), "reading": reading(s, s, preds.get(sensor_id)),
            "room": db.row("SELECT id, long_name, storey_id FROM rooms WHERE id=?", (s["room_id"],), c) if s["room_id"] else None,
            "simulatedFaults": [{**f, "params": db.loads(f["params"], {}), "startAt": iso(f["start_ts"]), "endAt": iso(f["end_ts"])} for f in faults]}


@router.get("/sensors/{sensor_id}/current")
def sensor_current(sensor_id: str):
    c = conn()
    s = _sensor_row(c, sensor_id)
    return reading(s, s, latest_predictions(c, [sensor_id]).get(sensor_id))


@router.get("/sensors/{sensor_id}/history")
def sensor_history(sensor_id: str, hours: int = Query(48, ge=1, le=24 * 31)):
    c = conn()
    s = _sensor_row(c, sensor_id)
    t = clock.now(c)
    w = readings.window([s], t - hours * 3600, t, c)
    physical = [{"t": iso(int(r.ts) + s["resolution_s"]), "value": None if np.isnan(r.value) else round(float(r.value), 3),
                 "samples": int(r.n_samples), "valid": int(r.n_valid), "simulated": bool(r.simulated)} for r in w.itertuples()]
    inferred = [{"t": iso(p["target_ts"] + s["resolution_s"]), "value": p["value"], "lower": p["lower"], "upper": p["upper"],
                 "quality": p["quality"], "modelVersion": p["model_version"], "actual": p["actual_value"], "incidentId": p["incident_id"]}
                for p in db.rows("SELECT * FROM virtual_predictions WHERE sensor_id=? AND target_ts BETWEEN ? AND ? ORDER BY target_ts",
                                 (sensor_id, t - hours * 3600, t), c)]
    return {"sensorId": sensor_id, "unit": s["unit"], "from": iso(t - hours * 3600), "to": iso(t), "physical": physical, "inferred": inferred,
            "note": "physical = historian buckets received by building time (value null = transmitted but invalid); inferred = virtual sensor estimates"}


# ------------------------------------------------------------------ virtual sensors
@router.get("/virtual-sensors")
def virtual_sensors(include_recovered: bool = Query(True, alias="includeRecovered")):
    c = conn()
    t = clock.now(c)
    temps = db.rows("""SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts
                       FROM sensors s JOIN sensor_state st ON st.sensor_id=s.id WHERE s.type='TEMPERATURE'""", conn=c)
    preds = latest_predictions(c, [s["id"] for s in temps])
    active, recovered = [], []
    for s in temps:
        p = preds.get(s["id"])
        if not p:
            continue
        hist = db.rows("SELECT target_ts, value, lower, upper, quality, actual_value FROM virtual_predictions WHERE sensor_id=? ORDER BY target_ts DESC LIMIT 96", (s["id"],), c)
        base = db.row("SELECT AVG(value) avg, MIN(value) mn, MAX(value) mx, COUNT(value) n FROM measurements WHERE sensor_id=? AND ts BETWEEN ? AND ? AND value IS NOT NULL",
                      (s["id"], (s["last_valid_ts"] or t) - 7 * 86400, s["last_valid_ts"] or t), c)
        item = {
            "sensor": sensor_dict(s), "reading": reading(s, s, p),
            "prediction": {"targetAt": iso(p["target_ts"] + 900), "value": p["value"], "lower": p["lower"], "upper": p["upper"],
                           "intervalLevel": p["interval_level"], "quality": p["quality"], "qualityReasons": db.loads(p["quality_reasons"], []),
                           "modelVersion": p["model_version"], "inputs": db.loads(p["inputs"], {}), "gapS": p["gap_s"], "incidentId": p["incident_id"]},
            "history": [{"t": iso(h["target_ts"] + 900), "value": h["value"], "lower": h["lower"], "upper": h["upper"], "quality": h["quality"],
                         "actual": h["actual_value"]} for h in reversed(hist)],
            "baseline7d": {"mean": base["avg"], "min": base["mn"], "max": base["mx"], "n": base["n"]} if base and base["n"] else None,
            "physicalStatus": s["status"],
            "recovered": s["status"] == "ONLINE",
        }
        if s["status"] in ("STALE", "OFFLINE", "INVALID"):
            active.append(item)
        elif include_recovered and t - p["target_ts"] < 2 * 86400:
            recovered.append(item)
    active.sort(key=lambda i: (i["prediction"]["value"] is None, i["sensor"]["roomId"] or ""))
    return {"active": active, "recentlyRecovered": recovered, "model": virtual.model_status()}


@router.get("/virtual-sensors/validation")
def virtual_validation():
    c = conn()
    rows_ = db.rows("SELECT value, lower, upper, actual_value, quality, gap_s FROM virtual_predictions WHERE value IS NOT NULL AND actual_value IS NOT NULL", conn=c)
    live = None
    if rows_:
        err = np.array([r["value"] - r["actual_value"] for r in rows_])
        cov = np.mean([r["lower"] <= r["actual_value"] <= r["upper"] for r in rows_])
        live = {"n": len(rows_), "mae": round(float(np.abs(err).mean()), 3), "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
                "coverage": round(float(cov), 3),
                "note": "Computed on simulated outages only, where the true historian value is known."}
    b = virtual.load_bundle()
    usage = db.row("SELECT COUNT(*) n, COUNT(DISTINCT sensor_id) sensors, SUM(quality='GOOD') good, SUM(quality='DEGRADED') degraded, SUM(quality='INSUFFICIENT') insufficient FROM virtual_predictions", conn=c)
    return {"offlineEvaluation": b.meta["evaluation"] if b else None, "modelVersion": b.version if b else None,
            "conformal": b.meta["conformal"] if b else None, "featureImportances": b.meta["feature_importances"][:12] if b else None,
            "liveValidation": live, "usage": usage}


# ------------------------------------------------------------------ incidents
@router.get("/incidents")
def incidents(status: str | None = None, severity: str | None = None, category: str | None = None, q: str | None = None,
              scope: str | None = None, simulated: bool | None = None, page: int = 1, page_size: int = Query(25, alias="pageSize"),
              sort: str = "detected", order: str = "desc"):
    c = conn()
    page, page_size = _page(page, page_size)
    where, params = ["1=1"], []
    for col, val in (("status", status), ("severity", severity), ("category", category), ("scope", scope)):
        if val:
            vals = val.upper().split(",")
            where.append(f"{col} IN ({','.join('?' * len(vals))})"); params += vals
    if q:
        where.append("(id LIKE ? OR title LIKE ? OR primary_room_id LIKE ? OR primary_sensor_id LIKE ?)"); params += [f"%{q}%"] * 4
    if simulated is not None:
        where.append("simulated=?"); params.append(int(simulated))
    order_sql = {"detected": "detected_ts", "severity": SEV_ORDER, "updated": "updated_real"}.get(sort, "detected_ts")
    direction = "DESC" if order == "desc" else "ASC"
    if sort == "severity":
        direction = "ASC" if order == "desc" else "DESC"
    total = db.scalar(f"SELECT COUNT(*) FROM incidents WHERE {' AND '.join(where)}", params, c)
    rows_ = db.rows(f"""SELECT i.*, (SELECT COUNT(*) FROM incident_sensors s WHERE s.incident_id=i.id) sensor_count FROM incidents i
                        WHERE {' AND '.join(where)} ORDER BY {order_sql} {direction}, detected_ts DESC LIMIT ? OFFSET ?""",
                    (*params, page_size, (page - 1) * page_size), c)
    return {"items": [incident_dict(r) for r in rows_], "total": total, "page": page, "pageSize": page_size}


def _incident(c, iid: str) -> dict:
    i = db.row("SELECT * FROM incidents WHERE id=?", (iid,), c)
    if not i:
        raise HTTPException(404, "incident not found")
    return i


@router.get("/incidents/{iid}")
def incident(iid: str):
    c = conn()
    i = _incident(c, iid)
    members = db.rows("""SELECT s.*, st.status, st.last_value, st.last_valid_ts, st.last_tx_ts, st.anomaly, st.incident_id, st.simulated, st.evaluated_ts
                         FROM incident_sensors x JOIN sensors s ON s.id=x.sensor_id LEFT JOIN sensor_state st ON st.sensor_id=s.id
                         WHERE x.incident_id=? ORDER BY s.type, s.id""", (iid,), c)
    preds = latest_predictions(c, [m["id"] for m in members])
    events = db.rows("SELECT id, category, severity, rule_id, detected_ts, payload FROM anomaly_events WHERE incident_id=? ORDER BY detected_ts", (iid,), c)
    report = db.row("SELECT version, summary, ai_narrative, llm_model, created_real FROM incident_reports WHERE incident_id=? ORDER BY version DESC LIMIT 1", (iid,), c)
    return {
        "incident": incident_dict(i),
        "sensors": [{**sensor_dict(m), "reading": reading(m, m, preds.get(m["id"]))} for m in members[:300]],
        "sensorCount": len(members),
        "events": [{"id": e["id"], "category": e["category"], "severity": e["severity"], "ruleId": e["rule_id"], "detectedAt": iso(e["detected_ts"]),
                    "payload": db.loads(e["payload"])} for e in events[-20:]],
        "eventCount": len(events),
        "timeline": [{**t, "at": iso(t["ts"]), "realAt": iso(t["real_ts"]), "data": db.loads(t["data"])}
                     for t in db.rows("SELECT * FROM incident_timeline WHERE incident_id=? ORDER BY id", (iid,), c)],
        "report": {**report, "createdAt": iso(report["created_real"])} if report else None,
        "analysis": _analysis(c, iid),
        "notifications": _notifications(c, iid),
        "agentRuns": _runs(c, iid),
    }


def _analysis(c, iid: str) -> dict | None:
    a = db.row("SELECT * FROM analyses WHERE incident_id=? ORDER BY version DESC LIMIT 1", (iid,), c)
    if not a:
        return None
    hyps = db.rows("SELECT * FROM hypotheses WHERE analysis_id=? ORDER BY rank", (a["id"],), c)
    recs = db.rows("SELECT * FROM recommendations WHERE analysis_id=? ORDER BY rank", (a["id"],), c)
    st = db.loads(a["structured"], {})
    return {"version": a["version"], "createdAt": iso(a["created_real"]), "facts": st.get("facts", []), "statisticalFindings": st.get("statisticalFindings", []),
            "risk": db.loads(a["risk"], {}), "sharedCause": st.get("sharedCause"), "modelVersions": db.loads(a["model_versions"], {}),
            "confidenceNote": st.get("confidenceNote"), "aiNarrative": a["ai_narrative"], "llmModel": a["llm_model"],
            "hypotheses": [{"id": h["id"], "rank": h["rank"], "code": h["cause_code"], "title": h["title"], "support": h["support"], "score": h["score"],
                            "explanation": h["explanation"], "evidenceFor": db.loads(h["evidence_for"], []), "evidenceAgainst": db.loads(h["evidence_against"], []),
                            "origin": h["origin"], "outcome": h["outcome"], "outcomeBy": h["outcome_by"], "outcomeAt": iso(h["outcome_real"])} for h in hyps],
            "recommendations": [{"id": r["id"], "rank": r["rank"], "hypothesisCode": r["hypothesis_code"], "action": r["action"], "urgency": r["urgency"],
                                 "expectedImpact": r["expected_impact"], "validationMethod": r["validation_method"], "rationale": r["rationale"],
                                 "status": r["status"], "statusBy": r["status_by"], "statusNote": r["status_note"], "origin": r["origin"]} for r in recs]}


def _notifications(c, iid: str) -> list[dict]:
    out = []
    for n in db.rows("SELECT * FROM notifications WHERE incident_id=? ORDER BY id", (iid,), c):
        attempts = db.rows("SELECT attempt_no, at_real, success, error, provider_response FROM notification_attempts WHERE notification_id=? ORDER BY id", (n["id"],), c)
        out.append({"id": n["id"], "kind": n["kind"], "severity": n["severity"], "channel": n["channel"], "recipients": db.loads(n["recipients"], []),
                    "subject": n["subject"], "status": n["status"], "statusReason": n["status_reason"], "attempts": n["attempts"],
                    "lastError": n["last_error"], "providerResponse": n["provider_response"], "createdAt": iso(n["created_real"]),
                    "sentAt": iso(n["sent_real"]), "nextAttemptAt": iso(n["next_attempt_real"]),
                    "attemptLog": [{**a, "at": iso(a.pop("at_real"))} for a in attempts]})
    return out


def _runs(c, iid: str) -> list[dict]:
    return [{**r, "details": db.loads(r["details"]), "startedAt": iso(r["started_real"]), "finishedAt": iso(r["finished_real"])}
            for r in db.rows("SELECT * FROM agent_runs WHERE incident_id=? ORDER BY id", (iid,), c)]


@router.get("/incidents/{iid}/report")
def incident_report(iid: str, format: str = "json", version: int | None = None):
    c = conn()
    _incident(c, iid)
    sql = "SELECT * FROM incident_reports WHERE incident_id=?" + (" AND version=?" if version else "") + " ORDER BY version DESC LIMIT 1"
    r = db.row(sql, (iid, version) if version else (iid,), c)
    if not r:
        raise HTTPException(404, "no report yet (Agent 2 has not processed this incident)")
    if format == "md":
        return PlainTextResponse(r["markdown"], media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{iid}-v{r["version"]}.md"'})
    if format == "html":
        return HTMLResponse(_md_to_html(r["markdown"], f"{iid} v{r['version']}"))
    versions = db.rows("SELECT version, created_real, llm_model FROM incident_reports WHERE incident_id=? ORDER BY version", (iid,), c)
    return {"incidentId": iid, "version": r["version"], "createdAt": iso(r["created_real"]), "title": r["title"], "summary": r["summary"],
            "markdown": r["markdown"], "structured": db.loads(r["structured"]), "aiNarrative": r["ai_narrative"], "llmModel": r["llm_model"],
            "versions": [{"version": v["version"], "createdAt": iso(v["created_real"]), "llmModel": v["llm_model"]} for v in versions]}


def _md_to_html(md: str, title: str) -> str:
    """Small, dependency-free Markdown renderer for the report subset we generate (printable -> PDF)."""
    import html as h
    import re

    out, in_table, in_code, in_list = [], False, False, False

    def inline(s: str) -> str:
        s = h.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        s = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", s)
        s = s.replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
        return s

    for line in md.splitlines():
        if line.startswith("```"):
            out.append("</pre>" if in_code else "<pre>"); in_code = not in_code; continue
        if in_code:
            out.append(h.escape(line)); continue
        if line.startswith("|"):
            cells = [x.strip() for x in line.strip("|").split("|")]
            if all(set(x) <= set("-: ") for x in cells):
                continue
            if not in_table:
                out.append("<table><tr>" + "".join(f"<th>{inline(x)}</th>" for x in cells) + "</tr>"); in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{inline(x)}</td>" for x in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if line.startswith("* "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(line[2:])}</li>"); continue
        if in_list:
            out.append("</ul>"); in_list = False
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#101828;font-size:14px}"
           "table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #d0d5dd;padding:4px 8px;text-align:left;font-size:12px}"
           "th{background:#f2f4f7}pre{background:#f2f4f7;padding:8px;overflow:auto;font-size:12px}h1{font-size:22px}h2{font-size:17px;margin-top:22px;border-bottom:1px solid #eaecf0}"
           "@media print{body{margin:0}}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{h.escape(title)}</title><style>{css}</style></head><body>{''.join(out)}</body></html>"


@router.get("/incidents/{iid}/recommendations")
def incident_recommendations(iid: str):
    c = conn()
    _incident(c, iid)
    a = _analysis(c, iid)
    return {"incidentId": iid, "analysisVersion": a["version"] if a else None, "recommendations": a["recommendations"] if a else [],
            "hypotheses": a["hypotheses"] if a else [], "advisory": "Recommendations are advisory; no physical action is executed by the platform."}


@router.get("/incidents/{iid}/agents")
def incident_agents(iid: str):
    c = conn()
    i = _incident(c, iid)
    first_event = db.row("SELECT payload FROM anomaly_events WHERE incident_id=? ORDER BY detected_ts LIMIT 1", (iid,), c)
    report = db.row("SELECT version, summary, ai_narrative, llm_model, created_real FROM incident_reports WHERE incident_id=? ORDER BY version DESC LIMIT 1", (iid,), c)
    runs = _runs(c, iid)
    by_agent = defaultdict(list)
    for r in runs:
        by_agent[r["agent"]].append(r)
    det_runs = db.rows("SELECT * FROM agent_runs WHERE agent='DETECTION' AND started_real <= ? ORDER BY id DESC LIMIT 1", (i["created_real"] + 5,), c)
    return {
        "incident": incident_dict(i),
        "agent1": {"event": db.loads(first_event["payload"]) if first_event else None, "runs": [_run(r) for r in det_runs]},
        "agent2": {"report": {**report, "createdAt": iso(report["created_real"])} if report else None, "runs": by_agent.get("DOCUMENTATION", [])},
        "agent3": {"analysis": _analysis(c, iid), "runs": by_agent.get("ANALYSIS", [])},
        "agent4": {"notifications": _notifications(c, iid), "runs": by_agent.get("NOTIFICATION", [])},
        "timeline": [{**t, "at": iso(t["ts"]), "realAt": iso(t["real_ts"])} for t in db.rows("SELECT ts, real_ts, kind, actor, message FROM incident_timeline WHERE incident_id=? ORDER BY id", (iid,), c)],
    }


def _run(r: dict) -> dict:
    return {**r, "details": db.loads(r["details"]), "startedAt": iso(r["started_real"]), "finishedAt": iso(r["finished_real"])}


# ------------------------------------------------------------------ reports & analytics
@router.get("/reports")
def reports(q: str | None = None, severity: str | None = None, category: str | None = None, page: int = 1, page_size: int = Query(25, alias="pageSize")):
    c = conn()
    page, page_size = _page(page, page_size)
    where, params = ["1=1"], []
    if q:
        where.append("(i.id LIKE ? OR i.title LIKE ? OR r.summary LIKE ?)"); params += [f"%{q}%"] * 3
    if severity:
        where.append("i.severity=?"); params.append(severity.upper())
    if category:
        where.append("i.category=?"); params.append(category.upper())
    base = f"""FROM incident_reports r JOIN incidents i ON i.id=r.incident_id
               WHERE r.version=(SELECT MAX(version) FROM incident_reports x WHERE x.incident_id=r.incident_id) AND {' AND '.join(where)}"""
    total = db.scalar(f"SELECT COUNT(*) {base}", params, c)
    rows_ = db.rows(f"SELECT i.*, r.version report_version, r.summary report_summary, r.created_real report_created, r.llm_model {base} ORDER BY i.detected_ts DESC LIMIT ? OFFSET ?",
                    (*params, page_size, (page - 1) * page_size), c)
    return {"items": [{**incident_dict(r), "reportVersion": r["report_version"], "reportSummary": r["report_summary"],
                       "reportCreatedAt": iso(r["report_created"]), "llmModel": r["llm_model"]} for r in rows_], "total": total, "page": page, "pageSize": page_size}


@router.get("/reports/export.csv")
def export_csv(severity: str | None = None, status: str | None = None):
    c = conn()
    where, params = ["1=1"], []
    if severity:
        where.append("severity=?"); params.append(severity.upper())
    if status:
        where.append("status=?"); params.append(status.upper())
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "title", "category", "severity", "status", "scope", "primary_room_id", "primary_sensor_id", "onset_ts", "detected_ts",
            "acknowledged_ts", "resolved_ts", "resolved_by", "occurrences", "simulated", "summary"]
    w.writerow(["id", "title", "category", "severity", "status", "scope", "room", "sensor", "onset_utc", "detected_utc", "acknowledged_utc",
                "resolved_utc", "resolved_by", "occurrences", "simulated", "summary", "sensors_involved", "leading_hypothesis"])
    for r in db.rows(f"SELECT * FROM incidents WHERE {' AND '.join(where)} ORDER BY detected_ts DESC", params, c):
        n = db.scalar("SELECT COUNT(*) FROM incident_sensors WHERE incident_id=?", (r["id"],), c)
        h = db.row("SELECT h.title, h.support FROM hypotheses h JOIN analyses a ON a.id=h.analysis_id WHERE h.incident_id=? AND h.rank=1 ORDER BY a.version DESC LIMIT 1", (r["id"],), c)
        w.writerow([*(iso(r[k]) if k.endswith("_ts") else r[k] for k in cols), n, f"{h['title']} [{h['support']}]" if h else ""])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="ghost-sensor-incidents.csv"'})


@router.get("/analytics")
def analytics():
    c = conn()
    inc = db.rows("SELECT * FROM incidents", conn=c)
    ttd = [i["detected_ts"] - i["onset_ts"] for i in inc if i["onset_ts"] and i["detected_ts"] >= i["onset_ts"]]
    ttr = [i["resolved_ts"] - i["detected_ts"] for i in inc if i["resolved_ts"]]
    by_type = db.rows("""SELECT s.type, i.category, COUNT(DISTINCT i.id) n FROM incidents i JOIN incident_sensors x ON x.incident_id=i.id
                         JOIN sensors s ON s.id=x.sensor_id GROUP BY s.type, i.category""", conn=c)
    top_sensors = db.rows("""SELECT x.sensor_id, COUNT(*) n FROM incident_sensors x JOIN incidents i ON i.id=x.incident_id
                             WHERE i.category IN ('SENSOR_OFFLINE','SENSOR_STALE','SENSOR_INVALID') GROUP BY x.sensor_id ORDER BY n DESC LIMIT 10""", conn=c)
    outcomes = db.rows("SELECT cause_code, outcome, COUNT(*) n FROM hypotheses WHERE outcome IS NOT NULL GROUP BY cause_code, outcome", conn=c)
    rec_status = db.rows("SELECT status, COUNT(*) n FROM recommendations GROUP BY status", conn=c)
    trend = defaultdict(Counter)
    for i in inc:
        trend[iso(i["detected_ts"] - i["detected_ts"] % 86400)[:10]][i["severity"]] += 1

    def stats(xs):
        if not xs:
            return None
        a = np.array(xs, dtype=float)
        return {"n": len(xs), "meanS": round(float(a.mean())), "medianS": round(float(np.median(a))), "p90S": round(float(np.quantile(a, 0.9)))}
    return {
        "incidents": len(inc),
        "meanTimeToDetect": stats(ttd), "meanTimeToResolve": stats(ttr),
        "mttdNote": "Building time from onset (last transmission / first anomalous bucket) to detection.",
        "mttrNote": "Building time from detection to resolution (auto-recovery or operator).",
        "failuresByType": by_type, "mostAffectedSensors": top_sensors,
        "trend": [{"day": d, **cnt} for d, cnt in sorted(trend.items())],
        "hypothesisOutcomes": outcomes, "recommendationStatus": rec_status,
        "virtualSensors": virtual_validation(),
    }


# ------------------------------------------------------------------ system / workflows
@router.get("/system/workflows")
def workflows():
    c = conn()
    events = db.rows("SELECT * FROM workflow_events ORDER BY id DESC LIMIT 100", conn=c)
    runs = db.rows("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 60", conn=c)
    per_agent = {r["agent"]: r for r in db.rows("""SELECT agent, COUNT(*) total, SUM(status='FAILED') failed, SUM(status='INVALID_OUTPUT') invalid,
                                                     MAX(started_real) last_real FROM agent_runs GROUP BY agent""", conn=c)}
    executions = None
    if settings.n8n_api_key and settings.n8n_base_url:
        try:
            r = httpx.get(settings.n8n_base_url.rstrip("/") + "/api/v1/executions", params={"limit": 20},
                          headers={"X-N8N-API-KEY": settings.n8n_api_key}, timeout=5)
            if r.status_code == 200:
                executions = [{"id": e.get("id"), "workflowId": e.get("workflowId"), "status": e.get("status"), "mode": e.get("mode"),
                               "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt")} for e in r.json().get("data", [])]
            else:
                executions = {"error": f"n8n API HTTP {r.status_code}"}
        except httpx.HTTPError as exc:
            executions = {"error": type(exc).__name__}
    pend = pipeline.pending(c, 50)
    return {
        "n8n": _n8n_status(), "orchestrationMode": settings.orchestration_mode, "localFallbackAfterS": settings.local_fallback_after_s,
        "n8nExecutions": executions,
        "n8nExecutionsNote": None if executions is not None else "Set N8N_API_KEY to list n8n executions here (metadata only, no payloads).",
        "pendingIncidents": len(pend), "pending": pend[:20],
        "agents": per_agent,
        "recentRuns": [_run(r) for r in runs],
        "events": [{**e, "details": db.loads(e["details"]), "at": iso(e["real_ts"])} for e in events],
        "failedRuns24h": db.scalar("SELECT COUNT(*) FROM agent_runs WHERE status IN ('FAILED','INVALID_OUTPUT') AND started_real > ?", (db.now_real() - 86400,), c),
    }


@router.get("/notifications")
def notifications(limit: int = Query(50, le=500), status: str | None = None):
    c = conn()
    sql, params = "SELECT * FROM notifications", []
    if status:
        sql += " WHERE status=?"; params.append(status.upper())
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"items": [{"id": n["id"], "incidentId": n["incident_id"], "kind": n["kind"], "severity": n["severity"], "channel": n["channel"],
                       "status": n["status"], "statusReason": n["status_reason"], "subject": n["subject"], "attempts": n["attempts"],
                       "lastError": n["last_error"], "createdAt": iso(n["created_real"]), "sentAt": iso(n["sent_real"])} for n in db.rows(sql, params, c)],
            "email": notification.email_config_status()}


@router.get("/simulation/faults")
def list_faults(active: bool = False):
    c = conn()
    t = clock.now(c)
    rows_ = db.rows("SELECT * FROM fault_injections ORDER BY id DESC LIMIT 200", conn=c)
    out = [{**f, "params": db.loads(f["params"], {}), "startAt": iso(f["start_ts"]), "endAt": iso(f["end_ts"]),
            "active": f["start_ts"] <= t and (f["end_ts"] is None or f["end_ts"] > t)} for f in rows_]
    return {"items": [f for f in out if f["active"]] if active else out}


@router.get("/stream")
def stream():
    """Server-Sent Events: tick + incident updates. Clients fall back to polling if this drops."""
    q = pipeline.bus.subscribe()

    def gen():
        try:
            yield "retry: 5000\n\n"
            last = time.time()
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
                except queue.Empty:
                    pass
                if time.time() - last > 15:
                    yield ": keep-alive\n\n"
                    last = time.time()
        finally:
            pipeline.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
