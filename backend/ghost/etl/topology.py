"""Build the building topology from the IFC model and the MSI coordination workbook.

Adjacency is *derived from geometry* because the IFC export contains no
IfcRelSpaceBoundary entities:

* WALL      two spaces on the same storey whose footprints are separated by at
            most `WALL_GAP_M` (a wall thickness) along a shared boundary of at
            least `MIN_SHARED_M`. `door_count` counts IfcDoors on that boundary.
* DOOR      an IfcDoor touches both spaces but the wall criterion was not met
            (e.g. thick walls).
* VERTICAL  spaces on consecutive storeys whose footprints overlap.

No edge is ever invented: every edge carries the geometric measurement that
justifies it (shared length, gap, door count or overlap area).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("ghost.etl.topology")

WALL_GAP_M = 0.6
MIN_SHARED_M = 0.8
DOOR_BUFFER_M = 0.3
VERTICAL_MIN_OVERLAP_M2 = 4.0
VERTICAL_MIN_OVERLAP_FRAC = 0.2
STOREY_ORDER = ["S1", "N0", "N1", "N2", "N3", "N4", "N5", "T1"]

IFC_FILE = "V7-CAMPUS-DIJON-BATIMENT.ifc"
MSI_FILE = "20220825 (On Dijon) Coordination MSI.xlsx"


ADJACENCY_KINDS = {"ROOM", "CIRCULATION", "ROOF"}


def space_kind(name: str, long_name: str | None) -> str:
    """Classify an IfcSpace from its naming convention in this model.

    * `00-30-N`, `S1-05-S`  -> ROOM          (room codes used by the BMS)
    * `C-02-06-N`           -> CIRCULATION   (corridors, landings)
    * `R-05-01-S`           -> ROOF          (roof / terraces)
    * `Face Est`...         -> FACADE        (exterior facade zones, hundreds of metres long)
    * `26`, `41` (numeric)  -> ZONE          (aggregate occupancy areas overlapping many rooms)
    * `VB:...`, `VH:...`    -> VOID          (shafts / voids)
    """
    if (long_name or "").lower().startswith("face "):
        return "FACADE"
    if re.fullmatch(r"\d+", name):
        return "ZONE"
    if name.startswith(("VB:", "VH:")):
        return "VOID"
    if name.startswith("C-"):
        return "CIRCULATION"
    if name.startswith("R-"):
        return "ROOF"
    return "ROOM"


def _footprint(shape) -> "Polygon":
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    verts = np.array(shape.geometry.verts).reshape(-1, 3)
    faces = np.array(shape.geometry.faces).reshape(-1, 3)
    tris = [Polygon(verts[f, :2]) for f in faces]
    tris = [t for t in tris if t.is_valid and t.area > 1e-6]
    poly = unary_union(tris).buffer(0)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly, float(verts[:, 2].min()), float(verts[:, 2].max())


def extract_ifc(source_dir: Path) -> dict:
    import ifcopenshell
    import ifcopenshell.geom
    from shapely.geometry import MultiPoint
    from shapely.strtree import STRtree

    model = ifcopenshell.open(str(source_dir / IFC_FILE))
    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set("use-world-coords", True)

    building = model.by_type("IfcBuilding")[0]
    storeys = []
    for st in model.by_type("IfcBuildingStorey"):
        elevation_cm = st.Elevation or 0.0  # model length unit is centimetre
        storeys.append({"id": st.Name, "name": st.Name, "elevation_m": round(elevation_cm / 100.0, 2)})

    spaces = {}
    for sp in model.by_type("IfcSpace"):
        storey = next((r.RelatingObject.Name for r in sp.Decomposes), None)
        try:
            poly, z0, z1 = _footprint(ifcopenshell.geom.create_shape(geom_settings, sp))
        except Exception as exc:  # pragma: no cover - geometry kernel edge cases
            log.warning("no geometry for space %s: %s", sp.Name, exc)
            continue
        spaces[sp.Name] = {
            "id": sp.Name,
            "kind": space_kind(sp.Name, sp.LongName),
            "long_name": sp.LongName,
            "ifc_global_id": sp.GlobalId,
            "storey_id": storey,
            "poly": poly,
        }

    # Adjacency only between real rooms / circulation / roof spaces.
    names = [n for n in spaces if spaces[n]["kind"] in ADJACENCY_KINDS]
    geoms = [spaces[n]["poly"] for n in names]
    tree = STRtree(geoms)

    edges: dict[tuple[str, str, str], dict] = {}
    for i, a in enumerate(names):
        A = geoms[i]
        Ab = A.buffer(WALL_GAP_M)
        for j in tree.query(Ab):
            if j <= i:
                continue
            b = names[j]
            if spaces[a]["storey_id"] != spaces[b]["storey_id"]:
                continue
            B = geoms[j]
            shared = min(B.exterior.intersection(Ab).length, A.exterior.intersection(B.buffer(WALL_GAP_M)).length)
            if shared >= MIN_SHARED_M:
                key = tuple(sorted((a, b))) + ("WALL",)
                edges[key] = {"shared_length_m": round(shared, 2), "gap_m": round(A.distance(B), 2), "door_count": 0}

    door_total = 0
    for door in model.by_type("IfcDoor"):
        try:
            shape = ifcopenshell.geom.create_shape(geom_settings, door)
        except Exception:
            continue
        storey = next((r.RelatingStructure.Name for r in door.ContainedInStructure), None)
        hull = MultiPoint(np.array(shape.geometry.verts).reshape(-1, 3)[:, :2]).convex_hull.buffer(DOOR_BUFFER_M)
        hits = [names[j] for j in tree.query(hull) if geoms[j].intersects(hull) and spaces[names[j]]["storey_id"] == storey]
        door_total += 1
        for x in range(len(hits)):
            for y in range(x + 1, len(hits)):
                a, b = sorted((hits[x], hits[y]))
                if spaces[a]["poly"].distance(spaces[b]["poly"]) > 1.0:
                    continue
                wall = edges.get((a, b, "WALL"))
                if wall is not None:
                    wall["door_count"] += 1
                else:
                    e = edges.setdefault((a, b, "DOOR"), {"shared_length_m": None, "gap_m": round(spaces[a]["poly"].distance(spaces[b]["poly"]), 2), "door_count": 0})
                    e["door_count"] += 1

    order = {s: k for k, s in enumerate(STOREY_ORDER)}
    by_storey = defaultdict(list)
    for n in names:
        by_storey[spaces[n]["storey_id"]].append(n)
    for lower, upper in zip(STOREY_ORDER, STOREY_ORDER[1:]):
        for a in by_storey.get(lower, []):
            A = spaces[a]["poly"]
            for b in by_storey.get(upper, []):
                B = spaces[b]["poly"]
                if not A.intersects(B):
                    continue
                ov = A.intersection(B).area
                if ov >= VERTICAL_MIN_OVERLAP_M2 and ov >= VERTICAL_MIN_OVERLAP_FRAC * min(A.area, B.area):
                    x, y = sorted((a, b))
                    edges[(x, y, "VERTICAL")] = {"shared_length_m": None, "gap_m": None, "door_count": 0, "overlap_m2": round(ov, 1)}

    # Local frame (metres) so the dashboard does not have to handle georeferenced coordinates.
    minx = min(g.bounds[0] for g in geoms)
    miny = min(g.bounds[1] for g in geoms)
    rooms = []
    for n in spaces:
        sp = spaces[n]
        poly = sp["poly"].simplify(0.05)
        ring = [[round(x - minx, 2), round(y - miny, 2)] for x, y in poly.exterior.coords]
        c = poly.centroid
        m = re.search(r"-([A-Z])$", n)
        rooms.append({
            "id": n,
            "long_name": sp["long_name"],
            "ifc_global_id": sp["ifc_global_id"],
            "storey_id": sp["storey_id"],
            "area_m2": round(sp["poly"].area, 2),
            "centroid_x": round(c.x - minx, 2),
            "centroid_y": round(c.y - miny, 2),
            "footprint": ring,
            "wing": m.group(1) if m else None,
            "space_kind": sp["kind"],
        })

    storeys.sort(key=lambda s: order.get(s["id"], 99))
    for k, s in enumerate(storeys):
        s["sort_order"] = k
    adjacency = [
        {"room_a": a, "room_b": b, "kind": kind, "overlap_m2": None, **attrs}
        for (a, b, kind), attrs in sorted(edges.items())
    ]
    return {
        "building": {"id": "CAMPUS-DIJON", "name": building.Name or "Campus Metropolitain Dijon", "source": IFC_FILE},
        "storeys": storeys,
        "rooms": rooms,
        "adjacency": adjacency,
        "stats": {"spaces": len(rooms), "adjacency_spaces": len(names), "doors_processed": door_total, "edges": len(adjacency),
                  "by_kind": {k: sum(1 for r in rooms if r["space_kind"] == k) for k in sorted({r["space_kind"] for r in rooms})}},
        "origin": {"x": minx, "y": miny, "crs_note": "IFC world coordinates (shared coordinates, site DJN8) minus origin"},
    }


def extract_msi(source_dir: Path) -> dict:
    """Device -> acquisition station mapping and room equipment from the MSI workbook."""
    path = source_dir / MSI_FILE
    if not path.exists():
        log.warning("MSI workbook not found; station metadata unavailable")
        return {"devices": [], "equipment": []}

    def sheet(name: str) -> pd.DataFrame:
        df = pd.read_excel(path, sheet_name=name, header=3).iloc[:, 1:]
        df.columns = [str(c).strip() for c in df.columns]
        return df

    dev = sheet("Device site")
    devices = []
    for _, r in dev.iterrows():
        name = r.get("Nom device BOS")
        if not isinstance(name, str):
            continue
        devices.append({
            "name": name.strip(),
            "type": str(r.get("Ref Type Device") or "").strip(),
            "room": r.get("Espace Device") if isinstance(r.get("Espace Device"), str) else None,
            "station": r.get("Station Acquisition") if isinstance(r.get("Station Acquisition"), str) else None,
            "parent": r.get("Relation : dataFeeds parent") if isinstance(r.get("Relation : dataFeeds parent"), str) else None,
        })

    eq = sheet("Equipements site")
    cols = {c.lower(): c for c in eq.columns}

    def col(*keys):
        for k in keys:
            for lc, orig in cols.items():
                if k in lc:
                    return orig
        return None

    c_type, c_room = col("type eqpt"), col("espace equip")
    c_elec, c_heat, c_cool, c_air = col("elecfeeds"), col("heatfeeds"), col("coolfeeds"), col("airfeeds")
    equipment = []
    for _, r in eq.iterrows():
        name = r.get("Nom equip BOS")
        if not isinstance(name, str):
            continue

        def s(c):
            v = r.get(c) if c else None
            return v.strip() if isinstance(v, str) else None

        equipment.append({
            "id": name.strip(), "type": s(c_type), "room": s(c_room), "bim_code": s("[Tag] BIM_Code_Equipement"),
            "elec_feed": s(c_elec), "heat_feed": s(c_heat), "cool_feed": s(c_cool), "air_feed": s(c_air),
        })
    return {"devices": devices, "equipment": equipment}


def build(source_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    topo = extract_ifc(source_dir)
    msi = extract_msi(source_dir)
    topo["msi"] = msi
    (out_dir / "topology.json").write_text(json.dumps(topo, ensure_ascii=False), encoding="utf-8")
    log.info("topology: %s", topo["stats"])
    return topo


if __name__ == "__main__":
    from ..config import settings

    logging.basicConfig(level=logging.INFO)
    build(settings.source_dir, settings.derived_dir)
