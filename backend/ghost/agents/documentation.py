"""Agent 2 - technical documentation and incident reporting.

The report is assembled deterministically from stored evidence. An optional
LLM narrative is accepted only if it (a) matches the JSON schema, (b) cites
existing evidence IDs and (c) contains no number absent from the evidence.
"""
from __future__ import annotations

import hashlib
import json
import re

import pandas as pd

from .. import db

from ..engine import incidents as inc_store
from . import llm
from .context import incident_context
from .runs import Trigger, agent_run
from .schemas import LLM_REPORT_SCHEMA, IncidentReport

SYSTEM_PROMPT = (
    "You write incident summaries for building-operations engineers. Use ONLY the evidence provided. "
    "Do not invent measurements, tests, events, causes or remediation actions. Do not state a root cause; "
    "describe what was observed. Cite evidence by its ID (E1, E2...). Keep it under 120 words."
)


def iso(ts) -> str:
    return "n/a" if ts is None else pd.Timestamp(int(ts), unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


_NUM = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)?")


def ungrounded_numbers(text: str, corpus: str) -> list[str]:
    """Numbers in `text` that never appear in `corpus` (ignores single digits used for counting)."""
    have = {n.replace(",", ".").rstrip("0").rstrip(".") for n in _NUM.findall(corpus)}
    bad = []
    for n in _NUM.findall(text):
        norm = n.replace(",", ".").rstrip("0").rstrip(".") if "." in n or "," in n else n
        if len(norm.lstrip("-")) == 1:
            continue
        if norm not in have:
            bad.append(n)
    return bad


def build_report(ctx: dict) -> tuple[IncidentReport, list[str]]:
    i = ctx["incident"]
    ev = ctx["latestEvent"] or {}
    evidence = list(dict.fromkeys((ctx["firstEvent"] or {}).get("evidence", []) + ev.get("evidence", [])))
    members = ctx["members"]
    preds = ctx["predictions"]

    statuses = {m["status"] for m in members}
    if i["category"] in ("SENSOR_OFFLINE", "SENSOR_STALE"):
        transmission = ("Affected sensor(s) are not transmitting" if statuses & {"OFFLINE", "STALE"}
                        else "Affected sensor(s) have resumed transmitting")
    elif i["category"] == "SENSOR_INVALID":
        transmission = "Sensor transmits, but values are empty/invalid"
    else:
        transmission = "Sensor is transmitting valid values; the anomaly concerns the values themselves"
    online = sum(1 for m in members if m["status"] == "ONLINE")
    transmission += f" (currently {online}/{len(members)} ONLINE)."

    active = [p for p in preds.values() if p["value"] is not None]
    virtual = {
        "active": bool(i["virtual_sensor_active"]),
        "count": len(active),
        "modelVersions": sorted({p["model_version"] for p in preds.values()}),
        "items": [{"sensorId": p["sensor_id"], "at": p["target_ts"], "value": p["value"], "lower": p["lower"], "upper": p["upper"],
                   "intervalLevel": p["interval_level"], "quality": p["quality"], "reasons": p["quality_reasons"]}
                  for p in list(preds.values())[:20]],
        "note": "Inferred values are model estimates, not physical measurements. Interval = validation-derived 90 % prediction interval.",
    }
    observations = []
    for m in members[:25]:
        observations.append({"sensorId": m["id"], "type": m["type"], "room": m["room_id"], "status": m["status"],
                             "lastValid": m["last_value"], "lastValidAt": m["last_valid_ts"], "lastTxAt": m["last_tx_ts"],
                             "station": m["station"]})
    notes = []
    if i["scope"] == "GROUP":
        notes.append(f"{len(members)} sensors share this condition; storeys {ctx['storeys']}; documented stations {ctx['stations'] or 'unknown'}.")
    if ctx["concurrent"]:
        notes.append(f"{len(ctx['concurrent'])} other incident(s) are open at the same time (see Agent 3 for shared-cause analysis).")
    if ctx["related"]:
        notes.append(f"{len(ctx['related'])} previous incident(s) involved the same sensor(s).")
    if i["simulated"]:
        notes.append("This incident involves an operator-injected (simulated) fault.")
    notes.append("Root-cause hypotheses are produced separately by Agent 3 and remain unconfirmed until validated by a person.")

    report = IncidentReport(
        incidentId=i["id"], title=i["title"],
        summary=(f"{i['title']}. {evidence[0] if evidence else ''} " + transmission).strip(),
        category=i["category"], severity=i["severity"], status=i["status"], building="CAMPUS-DIJON",
        rooms=ctx["rooms"], sensors=[m["id"] for m in members][:400], detectedAt=i["detected_ts"], onsetAt=i["onset_ts"],
        transmission=transmission, virtualSensor=virtual, observations=observations,
        baseline={**(ev.get("baseline") or {}), **({"history7d": ctx["history"].get("baseline7d")} if ctx["history"] else {})},
        neighbours=ev.get("neighbours", [])[:20], evidence=evidence, thresholds=ev.get("thresholds", {}),
        investigationNotes=notes,
        relatedIncidents=[{"id": r["id"], "title": r["title"], "status": r["status"], "detectedAt": r["detected_ts"]} for r in ctx["related"]],
        workflowRuns=[{"agent": r["agent"], "status": r["status"], "trigger": r["trigger"], "n8nExecutionId": r["n8n_execution_id"],
                       "workflow": r["n8n_workflow"], "at": r["started_real"]} for r in ctx["runs"]],
        timeline=[{"ts": t["ts"], "kind": t["kind"], "actor": t["actor"], "message": t["message"]} for t in ctx["timeline"]],
        dataProvenance=("Physical readings: OnDijon BMS historian export (15-min buckets). "
                        + ("Replay mode: building time is a replay of Jan-Jun 2026 data. " if ctx["clockMode"] == "REPLAY" else "")
                        + "Topology: IFC model V7-CAMPUS-DIJON-BATIMENT (geometry-derived adjacency)."),
    )
    return report, evidence


def render_markdown(r: IncidentReport, narrative: str | None, llm_model: str | None) -> str:
    L = [f"# {r.incidentId} - {r.title}", "",
         f"**Severity:** {r.severity} | **Category:** {r.category} | **Status:** {r.status}", "",
         f"**Detected:** {iso(r.detectedAt)} | **Onset:** {iso(r.onsetAt)} | **Building:** {r.building}", "",
         "## Summary", r.summary, ""]
    if narrative:
        L += ["## AI narrative (generated, verify before use)", f"_{narrative}_", f"<sub>Model: {llm_model}. Grounding check passed.</sub>", ""]
    L += ["## Affected scope", f"* Rooms ({len(r.rooms)}): {', '.join(r.rooms[:40]) or 'n/a'}{' ...' if len(r.rooms) > 40 else ''}",
          f"* Sensors ({len(r.sensors)}): {', '.join(r.sensors[:30])}{' ...' if len(r.sensors) > 30 else ''}",
          f"* Transmission: {r.transmission}", "",
          "## Evidence (detection)"]
    L += [f"* **E{k + 1}** {e}" for k, e in enumerate(r.evidence)]
    if r.thresholds:
        L += ["", "## Thresholds applied", *[f"* `{k}`: {v}" for k, v in r.thresholds.items()]]
    if r.baseline:
        L += ["", "## Baseline", "```json", json.dumps(r.baseline, indent=2, default=str), "```"]
    L += ["", "## Observed sensor state", "| Sensor | Type | Room | Status | Last valid | Last valid at | Station |", "|---|---|---|---|---|---|---|"]
    for o in r.observations:
        L.append(f"| {o['sensorId']} | {o['type']} | {o['room']} | {o['status']} | {o['lastValid'] if o['lastValid'] is not None else '-'} | {iso(o['lastValidAt'])} | {o['station'] or '-'} |")
    if r.neighbours:
        L += ["", "## Adjacent rooms", "| Room | Sensor | Status | Value | Adjacency |", "|---|---|---|---|---|"]
        for n in r.neighbours:
            L.append(f"| {n.get('room')} | {n.get('sensorId')} | {n.get('status')} | {n.get('value') if n.get('value') is not None else '-'} | {n.get('kind')} |")
    v = r.virtualSensor
    L += ["", "## Virtual sensor (Ghost Sensor)", f"* Active: **{'yes' if v['active'] else 'no'}** ({v['count']} inferred value(s)); models: {', '.join(v['modelVersions']) or 'n/a'}"]
    for it in v["items"][:10]:
        if it["value"] is not None:
            L.append(f"* {it['sensorId']} @ {iso(it['at'])}: **{it['value']:.2f}** [{it['lower']:.2f}, {it['upper']:.2f}] ({int((it['intervalLevel'] or 0) * 100)} % interval) - quality {it['quality']}")
        else:
            L.append(f"* {it['sensorId']}: no inferred value ({'; '.join(it['reasons'])})")
    L.append(f"* _{v['note']}_")
    L += ["", "## Initial investigation notes", *[f"* {n}" for n in r.investigationNotes]]
    if r.relatedIncidents:
        L += ["", "## Related incidents", *[f"* {x['id']} - {x['title']} ({x['status']}, {iso(x['detectedAt'])})" for x in r.relatedIncidents]]
    L += ["", "## Timeline", *[f"* {iso(t['ts'])} - **{t['kind']}** ({t['actor']}): {t['message']}" for t in r.timeline]]
    if r.workflowRuns:
        L += ["", "## Agent / workflow executions", *[f"* {w['agent']} {w['status']} via {w['trigger']}" + (f" (n8n execution {w['n8nExecutionId']})" if w['n8nExecutionId'] else "") for w in r.workflowRuns]]
    L += ["", "## Data provenance", r.dataProvenance, ""]
    return "\n".join(L)


def run(conn, iid: str, trigger: Trigger) -> dict:
    with agent_run(conn, "DOCUMENTATION", iid, trigger) as h:
        ctx = incident_context(conn, iid)
        report, evidence = build_report(ctx)
        structured = report.model_dump()
        # Hash excludes volatile fields so re-running on an unchanged incident is a no-op.
        stable = {k: v for k, v in structured.items() if k not in ("timeline", "workflowRuns", "observations", "status", "transmission", "summary")}
        stable["virtualSensor"] = {"active": structured["virtualSensor"]["active"], "count": structured["virtualSensor"]["count"]}
        content_hash = hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]
        last = db.row("SELECT version, content_hash FROM incident_reports WHERE incident_id=? ORDER BY version DESC LIMIT 1", (iid,), conn)
        if last and last["content_hash"] == content_hash:
            h.status, h.summary = "SKIPPED", f"report v{last['version']} already up to date"
            _advance(conn, ctx["incident"], "DOCUMENTED")
            return {"incidentId": iid, "version": last["version"], "created": False, "valid": True}

        narrative, model, llm_note = None, None, None
        provider = llm.get_provider()
        if provider.available():
            ev_lines = "\n".join(f"E{k + 1}: {e}" for k, e in enumerate(evidence))
            prompt = f"Incident: {report.title}\nSeverity: {report.severity}\nTransmission: {report.transmission}\nEvidence:\n{ev_lines}\n"
            try:
                res = provider.generate_json(SYSTEM_PROMPT, prompt, LLM_REPORT_SCHEMA, max_tokens=2000)
                cited = set(res.data.get("citedEvidence", []))
                valid_ids = {f"E{k + 1}" for k in range(len(evidence))}
                bad_nums = ungrounded_numbers(res.data.get("narrative", ""), prompt)
                if not res.data.get("narrative") or not cited or not cited <= valid_ids or bad_nums:
                    llm_note = f"LLM narrative rejected (citations {sorted(cited - valid_ids) or 'missing'}, ungrounded numbers {bad_nums})"
                    h.status = "INVALID_OUTPUT"
                else:
                    narrative, model = res.data["narrative"], res.model
            except llm.LLMUnavailable as exc:
                llm_note = f"LLM unavailable: {exc}"
        markdown = render_markdown(report, narrative, model)
        version = (last["version"] if last else 0) + 1
        db.execute("""INSERT INTO incident_reports(incident_id,version,content_hash,title,summary,markdown,structured,ai_narrative,llm_model,created_real)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (iid, version, content_hash, report.title, report.summary, markdown, db.dumps(structured), narrative, model, db.now_real()), conn)
        db.execute("UPDATE incidents SET summary=?, updated_real=? WHERE id=?", (report.summary[:500], db.now_real(), iid), conn)
        inc_store.timeline(conn, iid, "REPORTED", "AGENT_2_DOCUMENTATION", f"Technical report v{version} generated"
                           + (" with AI narrative" if narrative else "") + (f" ({llm_note})" if llm_note else ""), None, ctx["now"])
        if ctx["incident"]["status"] == "OPEN":
            inc_store.transition(conn, iid, "INVESTIGATING", "AGENT_2_DOCUMENTATION", "automated investigation started", ts=ctx["now"])
        _advance(conn, ctx["incident"], "DOCUMENTED")
        h.summary = f"report v{version}" + (" + AI narrative" if narrative else "") + (f"; {llm_note}" if llm_note else "")
        h.details = {"version": version, "evidenceCount": len(evidence), "llm": llm_note or (model and "accepted")}
        return {"incidentId": iid, "version": version, "created": True, "valid": True, "llm": llm_note or ("accepted" if narrative else "not configured")}


def _advance(conn, incident: dict, stage: str) -> None:
    order = ["DETECTED", "DOCUMENTED", "ANALYZED", "NOTIFIED"]
    current = db.scalar("SELECT pipeline_stage FROM incidents WHERE id=?", (incident["id"],), conn)
    if order.index(stage) > order.index(current) or current == "NOTIFIED":
        db.execute("UPDATE incidents SET pipeline_stage=? WHERE id=?", (stage, incident["id"]), conn)
