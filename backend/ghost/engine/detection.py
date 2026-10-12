"""Agent 1 - anomaly detection and classification (deterministic).

Runs at each new 15-minute bucket of building time (and on demand from n8n).
No LLM is involved: connectivity is decided from heartbeat ages, values from
explicit statistical rules configured in config/detection.json.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from .. import db
from ..agents.schemas import SEVERITIES, AnomalyEvent
from ..config import load_policy
from . import incidents as inc
from . import readings, virtual

log = logging.getLogger("ghost.detection")
SILENT = ("STALE", "OFFLINE")


def _bump(sev: str, steps: int = 1, cap: str = "CRITICAL") -> str:
    cur = SEVERITIES.index(sev)
    return SEVERITIES[max(cur, min(cur + steps, SEVERITIES.index(cap)))]


def _cap(sev: str, cap: str | None) -> str:
    if cap and SEVERITIES.index(sev) > SEVERITIES.index(cap):
        return cap
    return sev


def neighbour_map(conn) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in db.rows("SELECT room_a, room_b, kind FROM room_adjacency", conn=conn):
        out[e["room_a"]].append((e["room_b"], e["kind"]))
        out[e["room_b"]].append((e["room_a"], e["kind"]))
    return out


class Detector:
    def __init__(self, conn):
        self.conn = conn
        self.cfg = load_policy("detection")
        self.sensors = readings.sensors(conn)
        self.by_id = {s["id"]: s for s in self.sensors}
        self.nbrs = neighbour_map(conn)
        self.by_room_type = {(s["room_id"], s["type"]): s for s in self.sensors if s["room_id"]}

    # ------------------------------------------------------------------ helpers
    def _neighbours(self, sensor: dict, recs: dict, status: dict) -> list[dict]:
        out = []
        for room, kind in self.nbrs.get(sensor["room_id"], []):
            if kind == "VERTICAL":
                continue
            s = self.by_room_type.get((room, sensor["type"]))
            if s is None:
                continue
            r = recs.get(s["id"]) or {}
            out.append({"room": room, "sensorId": s["id"], "value": r.get("last_value") if status.get(s["id"]) == "ONLINE" else None,
                        "status": status.get(s["id"]), "kind": kind})
        return out

    def _event(self, t, dedup, category, severity, rule, scope, *, sensor=None, sensor_ids=(), evidence=(), onset=None,
               observed=None, baseline=None, thresholds=None, connectivity=None, neighbours=(), reasons=(), simulated=False, stations=()):
        sensor_ids = list(sensor_ids) or ([sensor["id"]] if sensor else [])
        rooms = sorted({self.by_id[s]["room_id"] for s in sensor_ids if self.by_id[s]["room_id"]})
        cap = self.cfg["type_severity_cap"].get(sensor["type"]) if sensor else None
        return AnomalyEvent(
            eventId=inc.event_id(dedup, onset), dedupKey=dedup, category=category, severity=_cap(severity, cap), ruleId=rule,
            scope=scope, detectedAt=t, onsetAt=onset, sensorId=sensor["id"] if sensor else None,
            sensorType=sensor["type"] if sensor else None, roomId=sensor["room_id"] if sensor else None,
            sensorIds=sensor_ids, roomIds=rooms, stations=sorted(set(stations)), observedValue=observed,
            unit=sensor["unit"] if sensor else None, baseline=baseline or {}, thresholds=thresholds or {},
            connectivity=connectivity, neighbours=list(neighbours), evidence=list(evidence),
            severityReasons=list(reasons), simulated=simulated,
        )

    # ------------------------------------------------------------------ main
    def evaluate(self, t: int) -> dict:
        started = time.time()
        recs = readings.latest(self.sensors, t, self.conn)
        status = {s["id"]: readings.classify(s, recs.get(s["id"]), t) for s in self.sensors}
        events: list[AnomalyEvent] = []

        # Virtual sensors first: severity depends on whether a replacement value exists.
        silent_temp = [dict(s, last_valid_ts=(recs.get(s["id"]) or {}).get("last_valid_ts"),
                            simulated=(recs.get(s["id"]) or {}).get("simulated", False))
                       for s in self.sensors if s["type"] == "TEMPERATURE" and status[s["id"]] in (*SILENT, "INVALID")]
        open_by_sensor = {r["sensor_id"]: r["incident_id"] for r in db.rows(
            f"SELECT s.sensor_id, s.incident_id FROM incident_sensors s JOIN incidents i ON i.id=s.incident_id WHERE i.status IN {inc.OPEN_STATUSES}",
            conn=self.conn)}
        predictions = {p["sensor_id"]: p for p in virtual.infer(silent_temp, t, self.conn, open_by_sensor)}

        events += self._silence_events(t, recs, status, predictions)
        events += self._invalid_events(t, recs, status, predictions)
        events += self._value_events(t, recs, status)
        events += self._topology_events(t)

        active_keys = {e.dedupKey for e in events}
        created, updated = [], []
        for ev in events:
            incident, action = inc.upsert(self.conn, ev)
            if action == "created":
                created.append(incident["id"])
            elif action in ("updated", "escalated"):
                updated.append(incident["id"])
        cleared = self._clear(t, active_keys, status)

        # Virtual sensor flags and per-sensor state.
        open_by_sensor = {r["sensor_id"]: r["incident_id"] for r in db.rows(
            f"SELECT s.sensor_id, s.incident_id FROM incident_sensors s JOIN incidents i ON i.id=s.incident_id WHERE i.status IN {inc.OPEN_STATUSES}",
            conn=self.conn)}
        with db.transaction(self.conn):
            db.execute(f"UPDATE incidents SET virtual_sensor_active=0 WHERE status IN {inc.OPEN_STATUSES}", conn=self.conn)
            for sid, p in predictions.items():
                if p["value"] is not None and sid in open_by_sensor:
                    db.execute("UPDATE incidents SET virtual_sensor_active=1 WHERE id=?", (open_by_sensor[sid],), self.conn)
                    db.execute("UPDATE virtual_predictions SET incident_id=? WHERE sensor_id=? AND target_ts=?",
                               (open_by_sensor[sid], sid, p["target_ts"]), self.conn)
            anomaly_of = {}
            for ev in events:
                for sid in ev.sensorIds:
                    anomaly_of.setdefault(sid, ev.category)
            for s in self.sensors:
                r = recs.get(s["id"]) or {}
                db.execute("""INSERT INTO sensor_state(sensor_id,status,last_tx_ts,last_valid_ts,last_value,anomaly,incident_id,simulated,evaluated_ts)
                              VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(sensor_id) DO UPDATE SET status=excluded.status,
                              last_tx_ts=excluded.last_tx_ts, last_valid_ts=excluded.last_valid_ts, last_value=excluded.last_value,
                              anomaly=excluded.anomaly, incident_id=excluded.incident_id, simulated=excluded.simulated, evaluated_ts=excluded.evaluated_ts""",
                           (s["id"], status[s["id"]], r.get("last_tx_ts"), r.get("last_valid_ts"), r.get("last_value"),
                            anomaly_of.get(s["id"]), open_by_sensor.get(s["id"]), int(bool(r.get("simulated"))), t), self.conn)
        counts = defaultdict(int)
        for v in status.values():
            counts[v] += 1
        return {"evaluatedAt": t, "statusCounts": dict(counts), "events": len(events), "created": created,
                "updated": sorted(set(updated)), "cleared": cleared,
                "virtualPredictions": sum(1 for p in predictions.values() if p["value"] is not None),
                "durationMs": int((time.time() - started) * 1000)}

    # ------------------------------------------------------------------ silence
    def _silence_events(self, t, recs, status, predictions) -> list[AnomalyEvent]:
        esc = self.cfg["escalation"]
        base = self.cfg["base_severity"]
        silent = [s for s in self.sensors if status[s["id"]] in SILENT]
        if not silent:
            return []
        onset = {s["id"]: recs[s["id"]]["last_tx_ts"] + s["resolution_s"] for s in silent}
        silent.sort(key=lambda s: onset[s["id"]])

        # Existing open group incidents absorb sensors whose silence began in the same window.
        groups = db.rows("SELECT id, dedup_key, onset_ts FROM incidents WHERE scope='GROUP' AND category IN ('SENSOR_OFFLINE','SENSOR_STALE') "
                         f"AND status IN {inc.OPEN_STATUSES}", conn=self.conn)
        clusters: list[dict] = []
        for g in groups:
            members = [r["sensor_id"] for r in db.rows("SELECT sensor_id FROM incident_sensors WHERE incident_id=?", (g["id"],), self.conn)]
            clusters.append({"key": g["dedup_key"], "onset": g["onset_ts"], "members": [m for m in members if status.get(m) in SILENT], "existing": True})
        free = []
        for s in silent:
            c = next((c for c in clusters if c["existing"] and abs(onset[s["id"]] - c["onset"]) <= esc["group_window_s"]), None)
            if c is not None:
                if s["id"] not in c["members"]:
                    c["members"].append(s["id"])
            else:
                free.append(s)
        i = 0
        while i < len(free):
            j = i
            while j + 1 < len(free) and onset[free[j + 1]["id"]] - onset[free[i]["id"]] <= esc["group_window_s"]:
                j += 1
            chunk = free[i:j + 1]
            if len(chunk) >= esc["group_min_sensors"]:
                o = onset[chunk[0]["id"]]
                clusters.append({"key": f"SILENCE_GROUP:{o - o % esc['group_window_s']}", "onset": o, "members": [s["id"] for s in chunk], "existing": False})
            else:
                for s in chunk:
                    clusters.append({"key": f"SILENCE:{s['id']}", "onset": onset[s["id"]], "members": [s["id"]], "existing": False, "single": True})
            i = j + 1

        transmitting_by_type = defaultdict(int)
        for s in self.sensors:
            if status[s["id"]] != "NO_DATA":
                transmitting_by_type[s["type"]] += 1
        events = []
        for c in clusters:
            members = [m for m in c["members"] if m in onset]
            if not members:
                continue
            any_offline = any(status[m] == "OFFLINE" for m in members)
            category = "SENSOR_OFFLINE" if any_offline else "SENSOR_STALE"
            sev = base[category]
            reasons = [f"base severity for {category}: {sev}"]
            simulated = any((recs.get(m) or {}).get("simulated") for m in members)
            stations = [self.by_id[m]["station"] for m in members if self.by_id[m]["station"]]
            longest = max(t - onset[m] for m in members)
            if c.get("single"):
                s = self.by_id[members[0]]
                r = recs[s["id"]]
                if longest >= esc["long_outage_s"]:
                    sev = _bump(sev, 1, "HIGH"); reasons.append(f"silent for {longest/3600:.1f} h (>= {esc['long_outage_s']/3600:.0f} h)")
                p = predictions.get(s["id"])
                if s["type"] == "TEMPERATURE" and esc["no_virtual_sensor_bump"] and (p is None or p["value"] is None):
                    sev = _bump(sev, 1, "HIGH"); reasons.append("no virtual sensor value available")
                ev_text = [f"No transmission from {s['id']} since {_iso(onset[s['id']])} ({longest/60:.0f} min).",
                           f"Heartbeat thresholds: stale after {s['stale_after_s']//60} min, offline after {s['offline_after_s']//60} min."]
                if r.get("last_value") is not None:
                    ev_text.append(f"Last valid reading {r['last_value']:.2f} {s['unit']} at {_iso(r['last_valid_ts'])}.")
                nbs = self._neighbours(s, recs, status)
                if nbs:
                    online = sum(1 for n in nbs if n["status"] == "ONLINE")
                    ev_text.append(f"{online}/{len(nbs)} adjacent same-type sensors still transmitting.")
                if p and p["value"] is not None:
                    ev_text.append(f"Virtual sensor active: {p['value']:.2f} degC [{p['lower']:.2f}, {p['upper']:.2f}] (90 % interval), quality {p['quality']}.")
                if simulated:
                    ev_text.append("SIMULATED: this outage was injected by an operator for testing.")
                events.append(self._event(
                    t, c["key"], category, sev, "HEARTBEAT_TIMEOUT", "SENSOR", sensor=s, evidence=ev_text, onset=c["onset"],
                    observed=None, baseline={"lastValidValue": r.get("last_value"), "lastValidAt": r.get("last_valid_ts")},
                    thresholds={"staleAfterS": s["stale_after_s"], "offlineAfterS": s["offline_after_s"]},
                    connectivity={"status": status[s["id"]], "lastTxAt": r["last_tx_ts"], "lastValidAt": r.get("last_valid_ts"), "silentForS": longest},
                    neighbours=nbs, reasons=reasons, simulated=simulated, stations=stations))
            else:
                n = len(members)
                types = defaultdict(int)
                for m in members:
                    types[self.by_id[m]["type"]] += 1
                frac = max(types[ty] / max(transmitting_by_type[ty], 1) for ty in types)
                if n >= esc["group_high_at"]:
                    sev = "HIGH"; reasons.append(f"{n} sensors silent simultaneously (>= {esc['group_high_at']})")
                if frac >= esc["group_critical_fraction"]:
                    sev = "CRITICAL"; reasons.append(f"{frac:.0%} of a sensor type silent (>= {esc['group_critical_fraction']:.0%})")
                rooms = sorted({self.by_id[m]["room_id"] for m in members if self.by_id[m]["room_id"]})
                storeys = defaultdict(int)
                for r_ in db.rows(f"SELECT storey_id FROM rooms WHERE id IN ({','.join('?'*len(rooms))})", tuple(rooms), self.conn) if rooms else []:
                    storeys[r_["storey_id"]] += 1
                st_count = defaultdict(int)
                for s_ in stations:
                    st_count[s_] += 1
                ev_text = [f"{n} sensors stopped transmitting within {self.cfg['escalation']['group_window_s']//60} min of {_iso(c['onset'])}.",
                           "By type: " + ", ".join(f"{k} {v}" for k, v in sorted(types.items())) + ".",
                           f"Rooms affected: {len(rooms)}; storeys: " + ", ".join(f"{k} ({v})" for k, v in sorted(storeys.items(), key=lambda x: str(x[0]))) + "."]
                if st_count:
                    ev_text.append("Documented acquisition stations of affected sensors: " + ", ".join(f"{k} ({v})" for k, v in sorted(st_count.items())) + ".")
                vcount = sum(1 for m in members if predictions.get(m, {}).get("value") is not None)
                if vcount:
                    ev_text.append(f"Virtual sensors active for {vcount} temperature sensor(s).")
                if simulated:
                    ev_text.append("SIMULATED: at least one member outage was injected by an operator.")
                events.append(self._event(
                    t, c["key"], category, sev, "HEARTBEAT_TIMEOUT_GROUP", "GROUP", sensor_ids=members, evidence=ev_text,
                    onset=c["onset"], thresholds={"groupWindowS": esc["group_window_s"], "groupMinSensors": esc["group_min_sensors"]},
                    connectivity={"status": "OFFLINE" if any_offline else "STALE", "silentForS": longest},
                    reasons=reasons, simulated=simulated, stations=stations,
                    baseline={"silentByType": dict(types), "storeys": dict(storeys), "fractionOfType": round(frac, 3)}))
        return events

    def _invalid_events(self, t, recs, status, predictions) -> list[AnomalyEvent]:
        out = []
        for s in self.sensors:
            if status[s["id"]] != "INVALID":
                continue
            r = recs[s["id"]]
            never = r.get("last_valid_ts") is None
            sev = self.cfg["base_severity"]["SENSOR_INVALID"]
            reasons = []
            p = predictions.get(s["id"])
            if s["type"] == "TEMPERATURE" and (p is None or p["value"] is None):
                sev = _bump(sev, 1, "HIGH"); reasons.append("no virtual sensor value available")
            ev = [f"{s['id']} is transmitting (last bucket {_iso(r['last_tx_ts'])}) but the values are empty/non-numeric."]
            ev.append("No valid value in the historian up to now." if never else f"Last valid value {r['last_value']} at {_iso(r['last_valid_ts'])}.")
            if p and p["value"] is not None:
                ev.append(f"Virtual sensor active: {p['value']:.2f} degC (quality {p['quality']}).")
            onset = None if never else r["last_valid_ts"] + s["resolution_s"]
            out.append(self._event(t, f"INVALID:{s['id']}", "SENSOR_INVALID", sev, "TRANSMITTING_INVALID_VALUES", "SENSOR",
                                   sensor=s, evidence=ev, onset=onset,
                                   connectivity={"status": "INVALID", "lastTxAt": r["last_tx_ts"], "lastValidAt": r.get("last_valid_ts")},
                                   reasons=reasons, simulated=bool(r.get("simulated"))))
        return out

    # ------------------------------------------------------------------ value rules
    def _value_events(self, t, recs, status) -> list[AnomalyEvent]:
        cfg = self.cfg
        out: list[AnomalyEvent] = []
        online = [s for s in self.sensors if status[s["id"]] == "ONLINE"]
        tc = [s for s in online if s["type"] in ("TEMPERATURE", "CO2")]
        all_tc = [s for s in self.sensors if s["type"] in ("TEMPERATURE", "CO2")]
        if tc:
            days = cfg["environmental"]["TEMPERATURE"]["baseline_days"] + 1
            win = readings.window(all_tc, t - days * 86400, t, self.conn)
            win = win[win["value"].notna()]
            wide = {ty: win[win.sensor_id.isin([s["id"] for s in all_tc if s["type"] == ty])]
                    .pivot_table(index="ts", columns="sensor_id", values="value") for ty in ("TEMPERATURE", "CO2")}
            for s in tc:
                W = wide[s["type"]]
                if s["id"] not in W.columns or W[s["id"]].dropna().empty:
                    continue
                out += self._rules_for(t, s, W, recs, status)
        energy = [s for s in online if s["type"] == "ENERGY"]
        if energy:
            ec = cfg["energy"]
            win = readings.window(energy, t - (ec["baseline_weeks"] * 7 + 1) * 86400, t, self.conn)
            for s in energy:
                g = win[(win.sensor_id == s["id"]) & win["value"].notna()]
                if g.empty:
                    continue
                last = g.iloc[-1]
                ts_last = int(last["ts"])
                same = g[(g.ts < ts_last) & (((ts_last - g.ts) % (7 * 86400)) == 0)]["value"].to_numpy()
                if len(same) < ec["min_history_points"]:
                    continue
                med = float(np.median(same)); mad = float(np.median(np.abs(same - med)))
                v = float(last["value"]); excess = v - med
                z = excess / (1.4826 * mad + 0.05)
                if excess > max(ec["min_abs_excess"], ec["min_rel_excess"] * abs(med)) and z > ec["robust_z"]:
                    meta = db.loads(s["meta"], {})
                    out.append(self._event(
                        t, f"ENERGY:{s['id']}", "ENERGY_ANOMALY", cfg["base_severity"]["ENERGY_ANOMALY"], "ENERGY_WEEKLY_PROFILE", "SENSOR",
                        sensor=s, onset=ts_last, observed=round(v, 3),
                        baseline={"sameHourMedian": round(med, 3), "mad": round(mad, 3), "points": len(same), "robustZ": round(z, 1)},
                        thresholds={"robustZ": ec["robust_z"], "minAbsExcess": ec["min_abs_excess"], "minRelExcess": ec["min_rel_excess"]},
                        evidence=[f"Consumption {v:.2f} kWh/h at {_iso(ts_last)} vs median {med:.2f} kWh/h for the same weekday/hour over {len(same)} previous weeks (robust z={z:.1f}).",
                                  f"Feeder: {s['display_name']}; usage: {meta.get('re2020Usage')}."],
                        simulated=bool((recs.get(s["id"]) or {}).get("simulated"))))
        return out

    def _rules_for(self, t, s, W: pd.DataFrame, recs, status) -> list[AnomalyEvent]:
        cfg = self.cfg
        out = []
        x = W[s["id"]].dropna()
        v, ts_last = float(x.iloc[-1]), int(x.index[-1])
        sim = bool((recs.get(s["id"]) or {}).get("simulated"))
        lo, hi = cfg["plausible_range"].get(s["type"], [None, None])
        nbs = self._neighbours(s, recs, status)
        nb_ids = [n["sensorId"] for n in nbs if n["sensorId"] in W.columns]

        if lo is not None and not (lo <= v <= hi):
            out.append(self._event(t, f"RANGE:{s['id']}", "SENSOR_OUT_OF_RANGE", cfg["base_severity"]["SENSOR_OUT_OF_RANGE"], "PLAUSIBLE_RANGE",
                                   "SENSOR", sensor=s, onset=ts_last, observed=round(v, 2), thresholds={"min": lo, "max": hi},
                                   evidence=[f"Reading {v:.2f} {s['unit']} is outside the physically plausible range [{lo}, {hi}]."],
                                   neighbours=nbs, simulated=sim))
            return out

        sp = cfg["spike"].get(s["type"])
        if sp and len(x) >= 2 and x.index[-2] == ts_last - s["resolution_s"]:
            step = v - float(x.iloc[-2])
            nb_steps = []
            for n in nb_ids:
                col = W[n]
                if ts_last in col.index and (ts_last - s["resolution_s"]) in col.index:
                    a, b = col.get(ts_last), col.get(ts_last - s["resolution_s"])
                    if pd.notna(a) and pd.notna(b):
                        nb_steps.append(abs(a - b))
            nb_step = float(np.median(nb_steps)) if nb_steps else None
            if abs(step) > sp["max_step"] and (nb_step is None or nb_step < sp["max_neighbour_step"]):
                out.append(self._event(t, f"SPIKE:{s['id']}", "SENSOR_SPIKE", cfg["base_severity"]["SENSOR_SPIKE"], "STEP_CHANGE", "SENSOR",
                                       sensor=s, onset=ts_last, observed=round(v, 2), baseline={"previous": round(v - step, 2), "neighbourMedianStep": nb_step},
                                       thresholds=sp, neighbours=nbs, simulated=sim,
                                       evidence=[f"Change of {step:+.2f} {s['unit']} in one {s['resolution_s']//60}-min bucket (threshold {sp['max_step']}).",
                                                 "Adjacent sensors moved by a median of " + (f"{nb_step:.2f}" if nb_step is not None else "n/a (no instrumented neighbour)") + "."]))

        dr = cfg["drift"].get(s["type"])
        if dr and nb_ids:
            nbm = W[nb_ids].mean(axis=1)
            d = (W[s["id"]] - nbm).dropna()
            recent = d[d.index > t - dr["recent_window_h"] * 3600]
            base = d[(d.index <= t - dr["recent_window_h"] * 3600) & (d.index > t - dr["baseline_window_h"] * 3600)]
            need_recent = dr["recent_window_h"] * 3600 / s["resolution_s"] * dr["min_valid_fraction"]
            if len(recent) >= need_recent and len(base) >= need_recent:
                shift = float(recent.mean() - base.mean())
                if abs(shift) > dr["threshold"]:
                    out.append(self._event(t, f"DRIFT:{s['id']}", "SENSOR_DRIFT", cfg["base_severity"]["SENSOR_DRIFT"], "NEIGHBOUR_OFFSET_SHIFT",
                                           "SENSOR", sensor=s, onset=int(recent.index[0]), observed=round(v, 2),
                                           baseline={"offsetToNeighboursBaseline": round(float(base.mean()), 2), "offsetToNeighboursRecent": round(float(recent.mean()), 2)},
                                           thresholds={"shift": dr["threshold"], "recentWindowH": dr["recent_window_h"]}, neighbours=nbs, simulated=sim,
                                           evidence=[f"Offset to adjacent rooms moved by {shift:+.2f} {s['unit']} over the last {dr['recent_window_h']} h "
                                                     f"(baseline {base.mean():+.2f}, recent {recent.mean():+.2f}; {len(nb_ids)} neighbour sensors)."]))

        env = cfg["environmental"].get(s["type"])
        if env and s["type"] == "CO2":
            last2 = x[x.index > t - (env["persist_buckets"] + 1) * s["resolution_s"]]
            if len(last2) >= env["persist_buckets"] and float(last2.min()) > env["high"]:
                sev = "HIGH" if float(last2.min()) > env["critical"] else cfg["base_severity"]["ENVIRONMENTAL_ANOMALY"]
                out.append(self._event(t, f"CO2HIGH:{s['id']}", "ENVIRONMENTAL_ANOMALY", sev, "CO2_THRESHOLD", "SENSOR", sensor=s,
                                       onset=int(last2.index[0]), observed=round(v, 1), thresholds={"high": env["high"], "critical": env["critical"]},
                                       neighbours=nbs, simulated=sim,
                                       evidence=[f"CO2 above {env['high']:.0f} ppm for {len(last2)} consecutive buckets (latest {v:.0f} ppm)."]))
        elif env and s["type"] == "TEMPERATURE":
            hod = (x.index % 86400) // s["resolution_s"]
            cur_hod = (ts_last % 86400) // s["resolution_s"]
            per_day = 86400 // s["resolution_s"]
            mask = ((hod - cur_hod) % per_day <= 1) | ((cur_hod - hod) % per_day <= 1)
            hist = x[mask & (x.index < ts_last - 3600) & (x.index >= t - env["baseline_days"] * 86400)]
            if len(hist) >= 20:
                med = float(hist.median()); mad = float((hist - med).abs().median())
                recent = x[x.index > t - (env["persist_buckets"] + 1) * s["resolution_s"]]
                z = (recent - med) / (1.4826 * mad + 0.1)
                if len(recent) >= env["persist_buckets"] and (z.abs() > env["robust_z"]).all() and ((recent - med).abs() > env["min_abs_dev"]).all():
                    nb_dev = []
                    for n in nb_ids:
                        c = W[n].dropna()
                        if len(c) and c.index[-1] == ts_last:
                            ch = c[c.index.isin(hist.index)]
                            if len(ch) >= 10:
                                nb_dev.append(abs(float(c.iloc[-1]) - float(ch.median())) > env["min_abs_dev"])
                    shared = f"{sum(nb_dev)}/{len(nb_dev)} adjacent rooms also deviate by > {env['min_abs_dev']} degC" if nb_dev else "no comparable neighbour data"
                    out.append(self._event(t, f"ENV:{s['id']}", "ENVIRONMENTAL_ANOMALY", cfg["base_severity"]["ENVIRONMENTAL_ANOMALY"], "SAME_HOUR_ROBUST_Z",
                                           "SENSOR", sensor=s, onset=int(recent.index[0]), observed=round(v, 2),
                                           baseline={"sameHourMedian": round(med, 2), "mad": round(mad, 2), "days": env["baseline_days"],
                                                     "neighboursAlsoDeviating": int(sum(nb_dev)), "neighboursCompared": len(nb_dev)},
                                           thresholds={"robustZ": env["robust_z"], "minAbsDev": env["min_abs_dev"]}, neighbours=nbs, simulated=sim,
                                           evidence=[f"{v:.2f} degC vs same-hour median {med:.2f} degC over {env['baseline_days']} days (robust z={float(z.iloc[-1]):.1f}).",
                                                     f"Spatial context: {shared}."]))
        return out

    def _topology_events(self, t) -> list[AnomalyEvent]:
        unmapped = [s for s in self.sensors if not s["room_id"]]
        if not unmapped:
            return []
        return [self._event(t, "TOPOLOGY:unmapped-sensors", "TOPOLOGY_INCONSISTENCY", "INFO", "SENSOR_ROOM_MAPPING", "GROUP",
                            sensor_ids=[s["id"] for s in unmapped], onset=None,
                            evidence=[f"{len(unmapped)} sensors in the BMS export have no room assignment, so they cannot be placed in the digital twin.",
                                      "Examples: " + ", ".join(s["id"] for s in unmapped[:5]) + "."])]

    # ------------------------------------------------------------------ clearing
    def _clear(self, t, active_keys: set[str], status: dict) -> list[str]:
        cleared = []
        for i in db.rows(f"SELECT * FROM incidents WHERE status IN {inc.OPEN_STATUSES}", conn=self.conn):
            if i["dedup_key"] in active_keys:
                continue
            members = [r["sensor_id"] for r in db.rows("SELECT sensor_id FROM incident_sensors WHERE incident_id=?", (i["id"],), self.conn)]
            kind = i["dedup_key"].split(":")[0]
            if kind in ("SILENCE", "SILENCE_GROUP"):
                # Recovered = transmitting again (an INVALID payload is tracked by its own incident).
                if any(status.get(m) in (*SILENT, "NO_DATA") for m in members):
                    continue
            elif kind != "TOPOLOGY":
                # Value / validity rules can only be judged on a fresh valid physical reading; a sensor
                # that went silent in the meantime keeps its incident open.
                if any(status.get(m) != "ONLINE" for m in members):
                    continue
            recovered = [m for m in members if status.get(m) == "ONLINE"]
            note = (f"Condition no longer detected at {_iso(t)}; "
                    + (f"{len(recovered)}/{len(members)} sensor(s) back online with valid physical readings." if members else "rule no longer triggers."))
            db.execute("UPDATE incidents SET condition_cleared_ts=? WHERE id=?", (t, i["id"]), self.conn)
            inc.transition(self.conn, i["id"], "RESOLVED", "SYSTEM:auto-recovery", note, ts=t)
            cleared.append(i["id"])
        return cleared


def _iso(ts: int | None) -> str:
    if ts is None:
        return "n/a"
    return pd.Timestamp(ts, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
