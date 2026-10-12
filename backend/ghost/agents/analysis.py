"""Agent 3 - root-cause analysis, risk estimation and recommendations.

Separation of knowledge kinds (each is labelled in the output):
* facts               - observations straight from stored data;
* statisticalFindings - computed from the historian (recurrence, model validation);
* hypotheses          - rule-engine (or LLM) explanations, never confirmed by the agent.

Hypothesis "score" is a rule-support score in [0, 1]: the weighted share of
supporting checks that passed minus contradicting ones. It is NOT a calibrated
probability; the qualitative label (STRONG/MODERATE/WEAK) is what the UI shows.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

import numpy as np

from .. import db
from ..engine import incidents as inc_store
from ..engine import virtual
from . import llm
from .context import incident_context
from .documentation import _advance, iso, ungrounded_numbers
from .runs import Trigger, agent_run
from .schemas import LLM_ANALYSIS_SCHEMA, Analysis, Hypothesis, Recommendation

SYSTEM_PROMPT = (
    "You assist building-operations engineers with root-cause analysis. You receive facts, statistics and rule-based "
    "hypotheses. Write a short narrative (<150 words) explaining the most plausible causes and what would confirm or rule "
    "them out. Only propose additional hypotheses if the evidence supports them, and cite evidence IDs (F1.., S1..). "
    "Never claim any action has been performed and never present a hypothesis as confirmed."
)

RECURRENCE_DAYS = 60


class Rule:
    """Accumulates weighted checks for one hypothesis."""

    def __init__(self, code, title, explanation):
        self.code, self.title, self.explanation = code, title, explanation
        self.pos, self.neg, self.total = 0.0, 0.0, 0.0
        self.ev_for: list[str] = []
        self.ev_against: list[str] = []

    def check(self, cond: bool, weight: float, text: str, against_text: str | None = None):
        self.total += weight
        if cond:
            self.pos += weight
            self.ev_for.append(text)
        elif against_text:
            self.neg += weight * 0.5
            self.ev_against.append(against_text)
        return self

    def contra(self, cond: bool, weight: float, text: str):
        if cond:
            self.neg += weight
            self.ev_against.append(text)
        return self

    def build(self) -> Hypothesis | None:
        if self.total == 0 or not self.ev_for:
            return None
        score = max(0.0, min(1.0, (self.pos - self.neg) / self.total))
        support = "STRONG" if score >= 0.7 else "MODERATE" if score >= 0.4 else "WEAK"
        return Hypothesis(code=self.code, title=self.title, support=support, score=round(score, 2), explanation=self.explanation,
                          evidenceFor=self.ev_for, evidenceAgainst=self.ev_against)


# ---------------------------------------------------------------- statistics
def outage_episodes(conn, sensors: list[dict], t_end: int, days: int = RECURRENCE_DAYS) -> dict:
    """Past silence episodes of these sensors, from the historian (real data, no overlay)."""
    if not sensors:
        return {"episodes": 0}
    sample = sensors[:40]
    ids = [s["id"] for s in sample]
    thr = {s["id"]: s["stale_after_s"] + s["resolution_s"] for s in sample}
    rows = db.rows(
        f"""SELECT sensor_id, ts, ts - LAG(ts) OVER (PARTITION BY sensor_id ORDER BY ts) AS gap FROM measurements
            WHERE sensor_id IN ({','.join('?' * len(ids))}) AND ts BETWEEN ? AND ?""", (*ids, t_end - days * 86400, t_end), conn)
    starts = sorted((r["ts"] - r["gap"], r["gap"], r["sensor_id"]) for r in rows if r["gap"] and r["gap"] > thr[r["sensor_id"]])
    episodes = []
    for s, gap, sid in starts:
        if episodes and s - episodes[-1]["start"] <= 1800:
            episodes[-1]["sensors"].add(sid)
            episodes[-1]["durations"].append(gap)
        else:
            episodes.append({"start": s, "sensors": {sid}, "durations": [gap]})
    if not episodes:
        return {"episodes": 0, "days": days, "sensorsSampled": len(sample)}
    lam = len(episodes) / days
    out = {
        "episodes": len(episodes), "days": days, "sensorsSampled": len(sample),
        "medianDurationH": round(float(np.median([max(e["durations"]) for e in episodes])) / 3600, 2),
        "medianSensorsPerEpisode": float(np.median([len(e["sensors"]) for e in episodes])),
        "lastEpisodeStart": episodes[-1]["start"],
    }
    if len(episodes) >= 3:
        out["ratePerWeek"] = round(lam * 7, 2)
        out["pAtLeastOneNext7d"] = round(1 - math.exp(-lam * 7), 2)
        out["method"] = "empirical Poisson rate over the window; assumes the past rate persists"
    else:
        out["method"] = "fewer than 3 episodes: no quantitative recurrence estimate"
    return out


def energy_context(conn) -> dict:
    rows = db.rows("SELECT st.status, st.last_value FROM sensors s JOIN sensor_state st ON st.sensor_id=s.id WHERE s.type='ENERGY'", conn=conn)
    online = [r for r in rows if r["status"] == "ONLINE"]
    consuming = [r for r in online if (r["last_value"] or 0) > 0]
    return {"meters": len(rows), "online": len(online), "consuming": len(consuming)}


# ---------------------------------------------------------------- hypotheses
def _namespace_text(ns: dict, dead: list[str], alive: list[str]) -> str:
    parts = [f"{k} ({ns[k]['silent']}/{ns[k]['total']} points)" for k in dead]
    text = "BMS point namespace " + ", ".join(parts) + " is mostly silent"
    if alive:
        text += f" while {', '.join(alive)} keeps reporting: the failure sits upstream of the field devices"
    return text + "."


def hypotheses_for(ctx: dict, stats: dict) -> list[Hypothesis]:
    i = ctx["incident"]
    cat = i["category"]
    members = ctx["members"]
    n = len(members)
    silent = [m for m in members if m["status"] in ("STALE", "OFFLINE")]
    stations = ctx["stations"]
    top_station, top_count = (stations and max(stations.items(), key=lambda x: x[1])) or (None, 0)
    storeys = ctx["storeys"]
    frac_building = stats.get("fractionOfAllSensorsSilent", 0)
    ev = ctx["latestEvent"] or {}
    neighbours = ev.get("neighbours", [])
    nb_online = [x for x in neighbours if x.get("status") == "ONLINE"]
    energy = stats.get("energy", {})
    rec = stats.get("recurrence", {})
    out: list[Hypothesis | None] = []

    ns = stats.get("namespaces", {})
    dead_ns = [k for k, v in ns.items() if v["silentFraction"] >= 0.5 and v["total"] >= 20]
    alive_ns = [k for k, v in ns.items() if v["silentFraction"] <= 0.1 and v["total"] >= 20]
    tf = stats.get("typeFractions", {})
    whole_types = [k for k, v in tf.items() if v >= 0.9 and k in {m["type"] for m in members}]
    if cat in ("SENSOR_OFFLINE", "SENSOR_STALE"):
        out.append(Rule("DATA_PIPELINE", "Data pipeline / BMS acquisition outage",
                        "Upstream collection (historian, IoT hub, connector) stopped ingesting from many unrelated stations at once.")
                   .check(frac_building >= 0.5 or len(whole_types) >= 2, 2,
                          f"{', '.join(whole_types) or 'Most'} sensor types are silent almost entirely ({frac_building:.0%} of all sensors).",
                          f"Only {frac_building:.0%} of all building sensors are silent.")
                   .check(bool(dead_ns) and n >= 20, 3, _namespace_text(ns, dead_ns, alive_ns), "No BMS point namespace is mostly silent.")
                   .check(len(stations) >= 3, 2, f"Silent sensors span {len(stations)} documented acquisition stations.",
                          "Silent sensors do not span several acquisition stations.")
                   .check(len(storeys) >= 3, 1, f"Silent sensors span {len(storeys)} storeys.")
                   .build())
        out.append(Rule("GATEWAY_NETWORK", "Acquisition station / network segment failure",
                        "A gateway, switch or controller bus feeding a subset of sensors lost connectivity.")
                   .check(n >= 5, 2, f"{n} sensors went silent within the same 30-minute window.", "Only one sensor is affected.")
                   .check(top_count >= 0.8 * max(len([m for m in members if m['station']]), 1) and top_count >= 3, 3,
                          f"{top_count} of the affected sensors with documented stations report through {top_station}.",
                          "Affected sensors do not share a single documented acquisition station." if n > 1 else None)
                   .check(len(storeys) == 1 and n >= 5, 1, f"All affected rooms are on storey {next(iter(storeys), '?')}.")
                   .check(rec.get("episodes", 0) >= 3 and rec.get("medianSensorsPerEpisode", 0) >= 5, 1,
                          f"The same sensors went silent together {rec.get('episodes')} times in the last {rec.get('days')} days (historian).")
                   .contra(frac_building >= 0.5 or len(stations) >= 3, 2,
                           f"Silence spans {len(stations)} acquisition stations, which points upstream rather than to one gateway.")
                   .build())
        out.append(Rule("POWER_INTERRUPTION", "Local power interruption",
                        "Loss of supply to field devices (room controllers, gateways) in the affected area.")
                   .check(n >= 5 and len(storeys) <= 2, 1, f"Affected rooms are concentrated on {len(storeys)} storey(s).")
                   .contra(energy.get("consuming", 0) > 0, 1,
                           f"{energy.get('consuming')} energy meters still report non-zero consumption, so building supply is available.")
                   .build())
        if n == 1:
            m = members[0]
            out.append(Rule("SENSOR_HARDWARE", "Sensor or room device failure",
                            "The device itself (battery, electronics, wiring) stopped working while its environment kept communicating.")
                       .check(True, 1, f"Only {m['id']} is silent.")
                       .check(len(nb_online) > 0, 2, f"{len(nb_online)}/{len(neighbours)} adjacent sensors are still transmitting.",
                              "No adjacent instrumented sensor is transmitting either.")
                       .check(rec.get("episodes", 0) == 0, 1, "No comparable silence of this sensor in the historian window.",
                              f"This sensor had {rec.get('episodes')} silence episode(s) in the last {rec.get('days')} days.")
                       .build())
    elif cat == "SENSOR_INVALID":
        never = all(m["last_valid_ts"] is None for m in members)
        out.append(Rule("POINT_CONFIGURATION", "Point not commissioned / mis-mapped in the BMS",
                        "The BMS point exists and is polled, but no value is mapped to it.")
                   .check(never, 3, "The point has never delivered a valid value in the available history.", "The point delivered valid values before.")
                   .check(i["category"] == "SENSOR_INVALID", 1, "Transmissions arrive on schedule, so the communication path works.")
                   .build())
        out.append(Rule("SENSOR_HARDWARE", "Sensor element failure",
                        "The device communicates but its measuring element returns no reading.")
                   .check(not never, 2, "The point used to deliver valid values and stopped.", "The point never delivered values, which fits configuration better.")
                   .build())
    elif cat == "SENSOR_DRIFT":
        b = ev.get("baseline", {})
        out.append(Rule("CALIBRATION_DRIFT", "Sensor calibration drift",
                        "The sensor's offset relative to its neighbours changed without a matching change in the neighbours.")
                   .check(True, 2, f"Offset to adjacent rooms moved from {b.get('offsetToNeighboursBaseline')} to {b.get('offsetToNeighboursRecent')}.")
                   .check(len(nb_online) >= 2, 1, f"{len(nb_online)} neighbour sensors remain consistent with each other.")
                   .build())
        out.append(Rule("LOCAL_HVAC_CHANGE", "Real local change (room HVAC / usage)",
                        "A terminal unit or use of the room changed the room's temperature relative to its neighbours.")
                   .check(bool(ctx["equipment"]), 1, f"Room has {len(ctx['equipment'])} documented HVAC/electrical items (MSI).")
                   .build())
    elif cat == "SENSOR_SPIKE":
        b = ev.get("baseline", {})
        nbstep = b.get("neighbourMedianStep")
        out.append(Rule("SENSOR_GLITCH", "Transient sensor glitch",
                        "A single-bucket jump not mirrored by neighbouring rooms is typical of a measurement glitch.")
                   .check(nbstep is not None and nbstep < 0.5, 2, f"Adjacent rooms moved by only {nbstep}.")
                   .check(True, 1, f"Jump from {b.get('previous')} to {ev.get('observedValue')} in one bucket.")
                   .build())
        out.append(Rule("REAL_EVENT", "Real local event (door/window opened, equipment start)",
                        "A genuine, sudden local change in the room.")
                   .check(bool(ctx["equipment"]), 1, "Room has documented equipment that can change conditions quickly.")
                   .build())
    elif cat == "SENSOR_OUT_OF_RANGE":
        out.append(Rule("SENSOR_HARDWARE", "Sensor fault or wiring problem",
                        "Values outside the physically plausible range cannot describe the room.")
                   .check(True, 3, f"Observed {ev.get('observedValue')} outside {ev.get('thresholds')}.").build())
    elif cat == "ENVIRONMENTAL_ANOMALY":
        b = ev.get("baseline", {})
        also = b.get("neighboursAlsoDeviating", 0)
        comp = b.get("neighboursCompared", 0)
        if i["primary_sensor_id"] and i["primary_sensor_id"].startswith("CO2"):
            occ = stats.get("occupancy")
            out.append(Rule("OCCUPANCY_VENTILATION", "High occupancy with insufficient ventilation",
                            "CO2 accumulates when occupancy exceeds the fresh-air supply.")
                       .check(bool(occ and occ.get("value", 0) > 0), 2, f"Room presence sensor reports occupancy ({occ}).",
                              "Room presence sensor does not report occupancy." if occ else None)
                       .check(True, 1, f"CO2 {ev.get('observedValue')} ppm above {ev.get('thresholds', {}).get('high')} ppm.")
                       .build())
        else:
            out.append(Rule("SHARED_HVAC_ZONE", "Real environmental change affecting a zone",
                            "Neighbouring rooms deviate together: heating/cooling of the zone or weather.")
                       .check(comp > 0 and also >= max(1, comp // 2), 3, f"{also}/{comp} adjacent rooms deviate as well.",
                              f"Only {also}/{comp} adjacent rooms deviate." if comp else None).build())
            out.append(Rule("LOCAL_HVAC_FAULT", "Local HVAC terminal fault or room usage",
                            "Only this room deviates: a fan coil/radiator/valve or room usage is likely.")
                       .check(comp > 0 and also == 0, 2, "No adjacent room shows the same deviation.")
                       .check(bool(ctx["equipment"]), 1, f"Room has {len(ctx['equipment'])} documented equipment items.").build())
            out.append(Rule("SENSOR_SUSPECT", "Sensor reading suspect",
                            "If the room is not actually different, the sensor may be misreading.")
                       .check(comp > 0 and also == 0, 1, "Neighbours do not confirm the deviation.").build())
    elif cat == "ENERGY_ANOMALY":
        b = ev.get("baseline", {})
        out.append(Rule("ABNORMAL_LOAD", "Abnormal load on the feeder",
                        "Equipment left running, a control schedule change or a fault increasing consumption.")
                   .check((b.get("robustZ") or 0) > 6, 2, f"Robust z-score {b.get('robustZ')} vs same weekday/hour.")
                   .check(True, 1, f"{ev.get('observedValue')} kWh/h vs typical {b.get('sameHourMedian')} kWh/h.").build())
    elif cat == "TOPOLOGY_INCONSISTENCY":
        out.append(Rule("DATA_MAPPING", "Incomplete asset mapping between BMS and BIM",
                        "Points exported without a room reference cannot be located in the IFC model.")
                   .check(True, 2, f"{n} sensors have no room assignment.").build())
    hyps = [h for h in out if h is not None]
    hyps.sort(key=lambda h: -h.score)
    return hyps


RECS = {
    "DATA_PIPELINE": [("Check the BMS historian / IoT hub ingestion and connector logs for the outage window.", "IMMEDIATE",
                       "Restores data for all affected sensors at once.", "Data resumes for sensors across all stations; connector logs show the failure window.")],
    "GATEWAY_NETWORK": [("Verify network connectivity and status of the shared acquisition station (ping/port check, switch port, PoE).", "IMMEDIATE",
                         "Restores monitoring for every sensor behind the station.", "Station responds and the affected sensors transmit again within one heartbeat."),
                        ("Check the station's power supply / UPS and controller bus.", "WITHIN_24H",
                         "Rules out a local supply problem.", "Supply present and controller LEDs/diagnostics nominal.")],
    "POWER_INTERRUPTION": [("Check the power supply of the field devices in the affected area (electrical panel, breakers).", "WITHIN_24H",
                            "Confirms or eliminates a supply loss.", "Breaker/feeder status and panel meter readings during the outage window.")],
    "SENSOR_HARDWARE": [("Inspect the sensor on site (power, wiring, battery, LEDs); replace or reset if faulty.", "WITHIN_24H",
                         "Restores the physical measurement; the virtual sensor covers the gap meanwhile.", "Sensor transmits valid values consistent with adjacent rooms.")],
    "POINT_CONFIGURATION": [("Review the BMS point mapping / commissioning for this sensor (address, object, unit).", "PLANNED",
                             "Turns a permanently empty point into usable data.", "Point returns numeric values in the BMS supervisor.")],
    "CALIBRATION_DRIFT": [("Compare the sensor against a calibrated reference thermometer; recalibrate or replace.", "PLANNED",
                           "Removes a systematic bias from monitoring and control.", "Reference reading matches within +/-0.5 degC after recalibration."),
                          ("Compare adjacent sensor readings over the next 24 h.", "MONITOR", "Confirms whether the offset persists.", "Offset to neighbours returns to its baseline.")],
    "LOCAL_HVAC_CHANGE": [("Check the room's terminal units (fan coil, radiator valve, setpoint) and recent usage.", "PLANNED",
                           "Distinguishes a real change from a sensor issue.", "Unit status/setpoint explains the observed offset.")],
    "SENSOR_GLITCH": [("Monitor the room for the next 2 hours; no action needed if values stay consistent.", "MONITOR",
                       "Avoids an unnecessary site visit for a transient glitch.", "No further spikes; readings track neighbours.")],
    "REAL_EVENT": [("Confirm with occupants or access-control logs whether a door/window or equipment changed state.", "MONITOR",
                    "Explains the jump without maintenance.", "Event found in logs at the time of the spike.")],
    "SHARED_HVAC_ZONE": [("Check the AHU / heating-cooling schedule and setpoints for the zone serving these rooms.", "WITHIN_24H",
                          "Corrects a comfort problem affecting several rooms.", "Zone supply temperature/schedule explains the deviation.")],
    "LOCAL_HVAC_FAULT": [("Inspect the room's HVAC terminal (valve actuator, fan coil, thermostat).", "WITHIN_24H",
                          "Restores comfort in the room.", "Terminal operates and room temperature returns to its usual band.")],
    "SENSOR_SUSPECT": [("Cross-check the room temperature with a portable thermometer.", "PLANNED",
                        "Validates the sensor before acting on HVAC.", "Portable reading agrees with neighbours rather than the sensor.")],
    "OCCUPANCY_VENTILATION": [("Increase ventilation / check the air-handling unit and room damper; review occupancy.", "IMMEDIATE",
                               "Brings CO2 below 1000-1500 ppm for occupant health.", "CO2 decreases below the threshold within 30-60 min.")],
    "ABNORMAL_LOAD": [("Investigate the loads on this feeder (equipment left on, schedules, faults).", "WITHIN_24H",
                       "Avoids wasted energy and possible equipment damage.", "Consumption returns to its weekly profile after correction.")],
    "DATA_MAPPING": [("Complete the room assignment of the listed points in the BMS/BIM mapping.", "PLANNED",
                      "Makes these meters usable in the digital twin.", "Points appear with a room in the next export.")],
}


def recommendations_for(hyps: list[Hypothesis], severity: str) -> list[Recommendation]:
    out, seen = [], set()
    for h in hyps:
        if h.support == "WEAK" and out:
            continue
        for action, urgency, impact, validation in RECS.get(h.code, []):
            if action in seen:
                continue
            seen.add(action)
            if severity == "CRITICAL" and urgency in ("WITHIN_24H", "PLANNED") and h.support == "STRONG":
                urgency = "IMMEDIATE"
            out.append(Recommendation(action=action, hypothesisCode=h.code, urgency=urgency, expectedImpact=impact,
                                      validationMethod=validation, rationale=f"Addresses hypothesis '{h.title}' ({h.support} rule support)."))
    return out[:8]


def run(conn, iid: str, trigger: Trigger) -> dict:
    with agent_run(conn, "ANALYSIS", iid, trigger) as h:
        ctx = incident_context(conn, iid)
        i = ctx["incident"]
        total = db.scalar("SELECT COUNT(*) FROM sensor_state WHERE status != 'NO_DATA'", conn=conn) or 1
        silent_all = db.scalar("SELECT COUNT(*) FROM sensor_state WHERE status IN ('STALE','OFFLINE')", conn=conn) or 0
        stats = {"fractionOfAllSensorsSilent": round(silent_all / total, 3), "energy": energy_context(conn)}
        ns, tf = {}, {}
        for r in db.rows("SELECT s.external_id, s.type, st.status FROM sensors s JOIN sensor_state st ON st.sensor_id=s.id WHERE st.status != 'NO_DATA'", conn=conn):
            key = r["external_id"].strip("/").split("/")[0]
            d = ns.setdefault(key, {"total": 0, "silent": 0})
            d["total"] += 1
            d["silent"] += r["status"] in ("STALE", "OFFLINE")
            t_ = tf.setdefault(r["type"], [0, 0])
            t_[0] += 1
            t_[1] += r["status"] in ("STALE", "OFFLINE")
        for d in ns.values():
            d["silentFraction"] = round(d["silent"] / d["total"], 3)
        stats["namespaces"] = ns
        stats["typeFractions"] = {k: round(v[1] / v[0], 3) for k, v in tf.items()}
        sensors_full = db.rows(f"SELECT * FROM sensors WHERE id IN ({','.join('?' * len(ctx['members']))})",
                               tuple(m["id"] for m in ctx["members"]), conn) if ctx["members"] else []
        if i["category"] in ("SENSOR_OFFLINE", "SENSOR_STALE"):
            stats["recurrence"] = outage_episodes(conn, sensors_full, (i["onset_ts"] or i["detected_ts"]) - 1800)
        if i["primary_room_id"]:
            occ = db.row("SELECT st.status, st.last_value AS value FROM sensors s JOIN sensor_state st ON st.sensor_id=s.id WHERE s.type='OCCUPANCY' AND s.room_id=?",
                         (i["primary_room_id"],), conn)
            stats["occupancy"] = occ
        hyps = hypotheses_for(ctx, stats)
        recs = recommendations_for(hyps, i["severity"])

        facts = [f"F{k + 1}: {e}" for k, e in enumerate(dict.fromkeys((ctx["firstEvent"] or {}).get("evidence", []) + (ctx["latestEvent"] or {}).get("evidence", [])))]
        stat_lines = [f"Building-wide: {silent_all}/{total} sensors currently silent ({stats['fractionOfAllSensorsSilent']:.0%})."]
        e = stats["energy"]
        stat_lines.append(f"Energy meters: {e['online']}/{e['meters']} online, {e['consuming']} reporting non-zero consumption.")
        stat_lines.append("Silent share per BMS namespace: " + ", ".join(f"{k} {v['silent']}/{v['total']}" for k, v in sorted(stats["namespaces"].items())) + ".")
        rec = stats.get("recurrence")
        if rec:
            if rec.get("episodes"):
                line = f"Historian: {rec['episodes']} silence episode(s) for these sensors in the {rec['days']} days before onset (median duration {rec['medianDurationH']} h)."
                if "pAtLeastOneNext7d" in rec:
                    line += f" Empirical rate {rec['ratePerWeek']}/week -> P(>=1 episode in next 7 days) ~ {rec['pAtLeastOneNext7d']:.0%} ({rec['method']})."
                else:
                    line += f" {rec['method']}."
                stat_lines.append(line)
            else:
                stat_lines.append(f"Historian: no silence episode for these sensors in the {rec['days']} days before onset.")
        risk = {"recurrence": rec or {"method": "not applicable to this category"}}
        bundle = virtual.load_bundle()
        if bundle and i["category"] in ("SENSOR_OFFLINE", "SENSOR_STALE", "SENSOR_INVALID") and any(m["type"] == "TEMPERATURE" for m in ctx["members"]):
            by_gap = bundle.meta["evaluation"]["by_gap"]
            risk["virtualSensorDegradation"] = {k: v["mae"] for k, v in by_gap.items()}
            stat_lines.append("Virtual-sensor validation MAE by outage length (held-out data): "
                              + ", ".join(f"{k}: {v['mae']} degC" for k, v in by_gap.items())
                              + " - accuracy degrades as the outage lasts longer.")
        sev_rank = inc_store.sev_rank(i["severity"])
        risk["escalation"] = ("Escalation possible: CRITICAL incidents are escalated if not acknowledged in time." if sev_rank >= 4
                              else "Severity rises automatically if the outage exceeds 24 h or more sensors join.")

        shared = None
        if ctx["concurrent"]:
            same_cat = [c for c in ctx["concurrent"] if c["category"] == i["category"]]
            if same_cat:
                shared = {"concurrentIncidents": [c["id"] for c in same_cat][:20], "count": len(same_cat),
                          "note": f"{len(same_cat)} other open {i['category']} incident(s): consider a shared upstream cause."}

        model_versions = {"detection": "rules-v1 (config/detection.json)", "analysis": "rules-v1"}
        if bundle:
            model_versions["virtualSensor"] = bundle.version

        narrative, llm_model, llm_note = None, None, None
        provider = llm.get_provider()
        if provider.available():
            stat_ids = [f"S{k + 1}: {s}" for k, s in enumerate(stat_lines)]
            prompt = ("Incident: " + i["title"] + f" ({i['severity']})\nFacts:\n" + "\n".join(facts) + "\nStatistics:\n" + "\n".join(stat_ids)
                      + "\nRule-based hypotheses:\n" + "\n".join(f"- {x.title} [{x.support}]: for={x.evidenceFor}; against={x.evidenceAgainst}" for x in hyps))
            try:
                res = provider.generate_json(SYSTEM_PROMPT, prompt, LLM_ANALYSIS_SCHEMA, max_tokens=3000)
                valid_ids = {f.split(":")[0] for f in facts} | {s.split(":")[0] for s in stat_ids}
                cited = set(res.data.get("citedEvidence", []))
                bad = ungrounded_numbers(res.data.get("narrative", ""), prompt)
                if not res.data.get("narrative") or not cited <= valid_ids or bad:
                    llm_note = f"LLM output rejected (unknown citations {sorted(cited - valid_ids)}, ungrounded numbers {bad})"
                    h.status = "INVALID_OUTPUT"
                else:
                    narrative, llm_model = res.data["narrative"], res.model
                    for extra in res.data.get("additionalHypotheses", [])[:3]:
                        c = set(extra.get("citedEvidence", []))
                        if c and c <= valid_ids and not ungrounded_numbers(extra.get("explanation", ""), prompt):
                            hyps.append(Hypothesis(code="LLM_" + hashlib.sha1(extra["title"].encode()).hexdigest()[:6].upper(),
                                                   title=extra["title"][:200], support="WEAK", score=0.0, explanation=extra["explanation"][:1000],
                                                   evidenceFor=sorted(c), evidenceAgainst=[], origin="LLM"))
            except llm.LLMUnavailable as exc:
                llm_note = f"LLM unavailable: {exc}"

        analysis = Analysis(incidentId=iid, facts=facts, statisticalFindings=stat_lines, hypotheses=hyps, recommendations=recs,
                            risk=risk, sharedCause=shared, modelVersions=model_versions,
                            confidenceNote=("Hypotheses are ranked by a rule-support score (share of weighted checks satisfied, minus contradictions). "
                                            "It is not a probability. No hypothesis is confirmed until a person validates it."))
        structured = analysis.model_dump()
        content_hash = hashlib.sha256(json.dumps({"h": [(x.code, x.support) for x in hyps], "r": [r.action for r in recs],
                                                  "s": i["severity"], "c": i["category"], "n": len(ctx["members"])}, sort_keys=True).encode()).hexdigest()[:16]
        last = db.row("SELECT id, version, content_hash FROM analyses WHERE incident_id=? ORDER BY version DESC LIMIT 1", (iid,), conn)
        if last and last["content_hash"] == content_hash:
            h.status, h.summary = "SKIPPED", f"analysis v{last['version']} unchanged"
            _advance(conn, i, "ANALYZED")
            return {"incidentId": iid, "version": last["version"], "created": False, "valid": True}
        version = (last["version"] if last else 0) + 1
        with db.transaction(conn):
            aid = db.execute("""INSERT INTO analyses(incident_id,version,content_hash,structured,risk,model_versions,ai_narrative,llm_model,created_real)
                                VALUES (?,?,?,?,?,?,?,?,?)""",
                             (iid, version, content_hash, db.dumps(structured), db.dumps(risk), db.dumps(model_versions), narrative, llm_model, db.now_real()), conn).lastrowid
            for k, x in enumerate(hyps):
                db.execute("""INSERT INTO hypotheses(analysis_id,incident_id,rank,cause_code,title,support,score,explanation,evidence_for,evidence_against,origin)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (aid, iid, k + 1, x.code, x.title, x.support, x.score, x.explanation, db.dumps(x.evidenceFor), db.dumps(x.evidenceAgainst), x.origin), conn)
            for k, r in enumerate(recs):
                db.execute("""INSERT INTO recommendations(analysis_id,incident_id,rank,hypothesis_code,action,urgency,expected_impact,validation_method,rationale,origin)
                              VALUES (?,?,?,?,?,?,?,?,?,?)""",
                           (aid, iid, k + 1, r.hypothesisCode, r.action, r.urgency, r.expectedImpact, r.validationMethod, r.rationale, r.origin), conn)
            top = hyps[0] if hyps else None
            inc_store.timeline(conn, iid, "ANALYZED", "AGENT_3_ANALYSIS",
                               f"Analysis v{version}: {len(hyps)} hypothesis(es)" + (f", leading: {top.title} [{top.support}]" if top else "")
                               + f", {len(recs)} recommendation(s)" + (f" ({llm_note})" if llm_note else ""), None, ctx["now"])
        _advance(conn, i, "ANALYZED")
        h.summary = f"v{version}: " + (f"{hyps[0].code} {hyps[0].support}" if hyps else "no hypothesis") + f"; {len(recs)} recs"
        h.details = {"version": version, "hypotheses": [x.code for x in hyps], "llm": llm_note or ("accepted" if narrative else "not configured")}
        return {"incidentId": iid, "version": version, "created": True, "valid": True,
                "leadingHypothesis": hyps[0].code if hyps else None, "llm": llm_note or ("accepted" if narrative else "not configured")}
