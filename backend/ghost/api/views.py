"""Serialisation helpers. The PHYSICAL / INFERRED distinction is enforced here."""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db


def iso(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def latest_predictions(conn, sensor_ids: list[str] | None = None) -> dict[str, dict]:
    where, params = "", ()
    if sensor_ids is not None:
        if not sensor_ids:
            return {}
        where = f"WHERE sensor_id IN ({','.join('?' * len(sensor_ids))})"
        params = tuple(sensor_ids)
    out = {}
    for p in db.rows(f"SELECT * FROM virtual_predictions WHERE id IN (SELECT MAX(id) FROM virtual_predictions {where} GROUP BY sensor_id)", params, conn):
        out[p["sensor_id"]] = p
    return out


def reading(sensor: dict, state: dict | None, pred: dict | None) -> dict:
    """Current reading object. Never presents an inferred value as physical."""
    res = sensor["resolution_s"]
    status = (state or {}).get("status", "NO_DATA")
    last_valid = (state or {}).get("last_valid_ts")
    physical = None
    if last_valid is not None and (state or {}).get("last_value") is not None:
        physical = {"value": state["last_value"], "observedAt": iso(last_valid + res)}
    base = {
        "sensorId": sensor["id"], "type": sensor["type"], "unit": sensor["unit"], "roomId": sensor["room_id"],
        "sensorStatus": status, "anomaly": (state or {}).get("anomaly"), "incidentId": (state or {}).get("incident_id"),
        "simulatedFault": bool((state or {}).get("simulated")), "lastPhysical": physical,
        "lastTransmissionAt": iso(((state or {}).get("last_tx_ts") or 0) + res) if (state or {}).get("last_tx_ts") else None,
        "evaluatedAt": iso((state or {}).get("evaluated_ts")),
    }
    if status == "ONLINE" and physical:
        return {**base, "source": "PHYSICAL", "value": physical["value"], "observedAt": physical["observedAt"],
                "inferredAt": None, "modelVersion": None, "uncertainty": None, "inferenceQuality": None}
    fresh_pred = pred if (pred and (last_valid is None or pred["target_ts"] >= last_valid)) else None
    if fresh_pred and fresh_pred["value"] is not None and status in ("STALE", "OFFLINE", "INVALID"):
        return {**base, "source": "INFERRED", "value": fresh_pred["value"], "observedAt": None,
                "inferredAt": iso(fresh_pred["target_ts"] + res), "modelVersion": fresh_pred["model_version"],
                "uncertainty": {"lower": fresh_pred["lower"], "upper": fresh_pred["upper"], "level": fresh_pred["interval_level"],
                                "method": "split-conformal prediction interval from held-out validation"},
                "inferenceQuality": fresh_pred["quality"], "qualityReasons": db.loads(fresh_pred["quality_reasons"], []),
                "incidentId": fresh_pred["incident_id"] or base["incidentId"]}
    quality = None
    reasons = []
    if fresh_pred and fresh_pred["value"] is None:
        quality, reasons = fresh_pred["quality"], db.loads(fresh_pred["quality_reasons"], [])
    return {**base, "source": "NONE", "value": None, "observedAt": None, "inferredAt": None, "modelVersion": None,
            "uncertainty": None, "inferenceQuality": quality, "qualityReasons": reasons}


def sensor_dict(s: dict) -> dict:
    return {"id": s["id"], "externalId": s["external_id"], "roomId": s["room_id"], "type": s["type"], "unit": s["unit"],
            "displayName": s["display_name"], "floor": s["floor_label"], "zone": s["zone"], "station": s["station"],
            "stationSource": s["station_source"], "resolutionS": s["resolution_s"], "staleAfterS": s["stale_after_s"],
            "offlineAfterS": s["offline_after_s"], "meta": db.loads(s["meta"], {})}


def incident_dict(i: dict) -> dict:
    keys = {"id": "id", "title": "title", "category": "category", "severity": "severity", "status": "status", "scope": "scope",
            "primary_sensor_id": "primarySensorId", "primary_room_id": "primaryRoomId", "occurrences": "occurrences",
            "acknowledged_by": "acknowledgedBy", "resolved_by": "resolvedBy", "resolution_note": "resolutionNote",
            "pipeline_stage": "pipelineStage", "pipeline_version": "pipelineVersion", "processed_version": "processedVersion",
            "claimed_by": "claimedBy", "summary": "summary", "dedup_key": "dedupKey"}
    out = {v: i.get(k) for k, v in keys.items()}
    for k, v in {"onset_ts": "onsetAt", "detected_ts": "detectedAt", "last_seen_ts": "lastSeenAt", "condition_cleared_ts": "conditionClearedAt",
                 "acknowledged_ts": "acknowledgedAt", "resolved_ts": "resolvedAt", "closed_ts": "closedAt"}.items():
        out[v] = iso(i.get(k))
    out["virtualSensorActive"] = bool(i.get("virtual_sensor_active"))
    out["simulated"] = bool(i.get("simulated"))
    out["createdAtReal"] = iso(i.get("created_real"))
    out["updatedAtReal"] = iso(i.get("updated_real"))
    if "sensor_count" in i:
        out["sensorCount"] = i["sensor_count"]
    return out
