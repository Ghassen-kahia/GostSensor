"""One-shot, idempotent data build.

    python -m ghost.etl.build_all            # topology + history
    python -m ghost.etl.build_all --skip-history

Re-running never duplicates rooms, edges, sensors or measurements (upserts and
INSERT OR IGNORE keyed on stable identifiers).
"""
from __future__ import annotations

import argparse
import json
import logging
import time

import pandas as pd

from .. import db
from ..config import settings
from . import history, topology

log = logging.getLogger("ghost.etl")

FLOOR_TO_STOREY = {"Sous-sol": "S1", "RDC": "N0", "Etage 1": "N1", "Etage 2": "N2", "Etage 3": "N3",
                   "Etage 4": "N4", "Etage 5": "N5", "Terrasse": "T1"}

# Device types (MSI "Types de device") used to attach an acquisition station to a sensor.
TEMP_DEVICE_TYPES = ("#QMX37",)          # Siemens room operator unit (temperature)
CO2_DEVICE_TYPES = ("#QMX70", "#QMX37")


def load_topology(conn, topo: dict) -> None:
    b = topo["building"]
    with db.transaction(conn):
        conn.execute("INSERT INTO buildings(id,name,source) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, source=excluded.source",
                     (b["id"], b["name"], b["source"]))
        for s in topo["storeys"]:
            conn.execute("""INSERT INTO storeys(id,building_id,name,elevation_m,sort_order) VALUES (?,?,?,?,?)
                            ON CONFLICT(id) DO UPDATE SET elevation_m=excluded.elevation_m, sort_order=excluded.sort_order""",
                         (s["id"], b["id"], s["name"], s["elevation_m"], s["sort_order"]))
        stations = {}
        for d in topo["msi"]["devices"]:
            if d["room"] and d["station"]:
                stations.setdefault(d["room"], set()).add(d["station"])
        for r in topo["rooms"]:
            conn.execute("""INSERT INTO rooms(id,building_id,storey_id,long_name,ifc_global_id,area_m2,centroid_x,centroid_y,footprint,wing,stations,in_ifc,space_kind)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)
                            ON CONFLICT(id) DO UPDATE SET storey_id=excluded.storey_id, long_name=excluded.long_name,
                              ifc_global_id=excluded.ifc_global_id, area_m2=excluded.area_m2, centroid_x=excluded.centroid_x,
                              centroid_y=excluded.centroid_y, footprint=excluded.footprint, wing=excluded.wing,
                              stations=excluded.stations, in_ifc=1, space_kind=excluded.space_kind""",
                         (r["id"], b["id"], r["storey_id"], r["long_name"], r["ifc_global_id"], r["area_m2"],
                          r["centroid_x"], r["centroid_y"], json.dumps(r["footprint"]), r["wing"],
                          json.dumps(sorted(stations.get(r["id"], []))) if r["id"] in stations else None, r.get("space_kind", "ROOM")))
        conn.execute("DELETE FROM room_adjacency")
        conn.executemany(
            "INSERT INTO room_adjacency(room_a,room_b,kind,shared_length_m,gap_m,door_count,overlap_m2) VALUES (?,?,?,?,?,?,?)",
            [(e["room_a"], e["room_b"], e["kind"], e.get("shared_length_m"), e.get("gap_m"), e.get("door_count", 0), e.get("overlap_m2"))
             for e in topo["adjacency"]],
        )
        room_ids = {r["id"] for r in topo["rooms"]}
        conn.execute("DELETE FROM room_equipment")
        seen = set()
        for e in topo["msi"]["equipment"]:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            conn.execute("INSERT INTO room_equipment(id,room_id,type,bim_code,elec_feed,heat_feed,cool_feed,air_feed) VALUES (?,?,?,?,?,?,?,?)",
                         (e["id"], e["room"] if e["room"] in room_ids else None, e["type"], e["bim_code"],
                          e["elec_feed"], e["heat_feed"], e["cool_feed"], e["air_feed"]))
    log.info("topology loaded: %d rooms, %d edges, %d equipment", len(topo["rooms"]), len(topo["adjacency"]), len(seen))


def _station_for(sensor: dict, devices_by_room: dict, devices_by_name: dict) -> tuple[str | None, str | None, str | None]:
    t, room = sensor["type"], sensor["room_id"]
    types = TEMP_DEVICE_TYPES if t == "TEMPERATURE" else CO2_DEVICE_TYPES if t == "CO2" else ()
    for d in devices_by_room.get(room, []):
        if d["type"] in types and d["station"]:
            return d["station"], f"MSI Device site: {d['type']} {d['name']} in {room}", d["parent"]
    if t in ("ENERGY",):
        m = sensor["external_id"].split("/")[-1]
        num = m.split("_")[1] if m.startswith("Depart_") and "_" in m else None
        d = devices_by_name.get(f"ExtDepart_{num}") if num else None
        if d and d["station"]:
            return d["station"], f"MSI Device site: {d['name']}", d["parent"]
    hub = sensor["external_id"].strip("/").split("/")[0]
    return None, f"BMS point namespace {hub} (acquisition station not documented)", None


def load_history(conn, topo: dict) -> dict:
    devices_by_room, devices_by_name = {}, {}
    for d in topo["msi"]["devices"]:
        devices_by_name[d["name"]] = d
        if d["room"]:
            devices_by_room.setdefault(d["room"], []).append(d)
    known_rooms = {r["id"] for r in topo["rooms"]}
    stats = {}
    settings.derived_dir.mkdir(parents=True, exist_ok=True)
    for spec in history.SPECS:
        t0 = time.time()
        sensors, buckets = history.build_type(spec, settings.source_dir)
        if sensors.empty:
            continue
        with db.transaction(conn):
            for s in sensors.to_dict("records"):
                room = s["room_id"]
                if room and room not in known_rooms:
                    # Present in the BMS export but not in the IFC model: keep it, flagged.
                    conn.execute("INSERT OR IGNORE INTO rooms(id,building_id,storey_id,in_ifc) VALUES (?,?,?,0)",
                                 (room, topo["building"]["id"], FLOOR_TO_STOREY.get(str(s["floor_label"]).split(";")[0])))
                    known_rooms.add(room)
                station, station_source, controller = _station_for(s, devices_by_room, devices_by_name)
                meta = dict(s["meta"])
                if controller:
                    meta["controller"] = controller
                conn.execute("""INSERT INTO sensors(id,external_id,room_id,type,unit,display_name,floor_label,zone,station,station_source,
                                   resolution_s,stale_after_s,offline_after_s,valid_min,valid_max,meta)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT(id) DO UPDATE SET room_id=excluded.room_id, display_name=excluded.display_name,
                                   floor_label=excluded.floor_label, zone=excluded.zone, station=excluded.station,
                                   station_source=excluded.station_source, meta=excluded.meta""",
                             (s["id"], s["external_id"], room, s["type"], s["unit"], s["display_name"], s["floor_label"], s["zone"],
                              station, station_source, s["resolution_s"], s["stale_after_s"], s["offline_after_s"],
                              s["valid_min"], s["valid_max"], db.dumps(meta)))
                if room and s["zone"]:
                    conn.execute("UPDATE rooms SET zone=COALESCE(zone, ?) WHERE id=?", (s["zone"], room))
            recs = buckets.astype({"ts": "int64", "n_samples": "int64", "n_valid": "int64"})
            recs["value"] = recs["value"].astype(object).where(recs["value"].notna(), None)
            conn.executemany(
                """INSERT INTO measurements(sensor_id,ts,value,n_samples,n_valid,origin) VALUES (?,?,?,?,?,'HISTORIAN')
                   ON CONFLICT(sensor_id,ts) DO UPDATE SET value=excluded.value, n_samples=excluded.n_samples,
                   n_valid=excluded.n_valid WHERE measurements.origin='HISTORIAN'""",
                recs.itertuples(index=False, name=None),
            )
        if spec.type in ("TEMPERATURE", "CO2", "OCCUPANCY"):
            history.wide(buckets, sensors, spec.resolution_s).to_parquet(settings.derived_dir / f"{spec.type.lower()}_15min.parquet")
        stats[spec.type] = {"sensors": len(sensors), "buckets": len(buckets), "seconds": round(time.time() - t0, 1)}
        log.info("%s loaded: %s", spec.type, stats[spec.type])
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-history", action="store_true")
    ap.add_argument("--reuse-topology", action="store_true", help="reuse data/derived/topology.json if present")
    args = ap.parse_args()

    conn = db.connect()
    db.migrate(conn)
    cached = settings.derived_dir / "topology.json"
    if args.reuse_topology and cached.exists():
        topo = json.loads(cached.read_text(encoding="utf-8"))
    else:
        topo = topology.build(settings.source_dir, settings.derived_dir)
    load_topology(conn, topo)
    if not args.skip_history:
        stats = load_history(conn, topo)
        db.set_state("etl_stats", {"built_real": db.now_real(), **stats}, conn)
    conn.execute("PRAGMA optimize")
    log.info("done")


if __name__ == "__main__":
    main()
