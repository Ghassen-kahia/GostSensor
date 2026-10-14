import { PointerEvent as RPointerEvent, useEffect, useMemo, useRef, useState, WheelEvent as RWheelEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Maximize, Minus, Plus } from "lucide-react";
import { Incident, Reading, Sensor } from "../api";
import { Card, DataState, GhostIcon, IncidentStatusBadge, ReadingValue, Seg, SensorStatusBadge, SeverityBadge, SourceBadge } from "../components";
import { fmtNum, useApi } from "../lib";
import { t } from "../i18n";

interface TopoRoom {
  id: string; name: string | null; storey: string; areaM2: number | null; centroid: [number, number]; footprint: [number, number][];
  wing: string | null; zone: string | null; sensors: { id: string; type: string; status: string | null; anomaly: string | null; incidentId: string | null }[];
  temperature: number | null; virtualActive: boolean; virtualValue: number | null; hasFault: boolean; hasAnomaly: boolean;
}
interface Topology {
  storeys: { id: string; name: string; elevation_m: number; rooms: number }[]; rooms: TopoRoom[];
  edges: { room_a: string; room_b: string; kind: string; shared_length_m: number | null; door_count: number; overlap_m2: number | null }[]; method: string;
}
interface RoomDetail {
  room: { id: string; long_name: string | null; storey_id: string; area_m2: number | null; zone: string | null; stations: string[]; in_ifc: number };
  sensors: Sensor[]; equipment: { id: string; type: string | null }[]; incidents: Incident[];
  neighbours: { room: { id: string; long_name: string | null; storey_id: string } | null; kind: string; sharedLengthM: number | null; doorCount: number; overlapM2: number | null; readings: Reading[] }[];
}

const SEQ = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"].map((v) => `var(${v})`);

export default function DigitalTwin() {
  const [params, setParams] = useSearchParams();
  const storey = params.get("storey") ?? "N1";
  const selected = params.get("room");
  const [edgesMode, setEdgesMode] = useState<"none" | "horizontal" | "all">("horizontal");
  const [focus, setFocus] = useState<"all" | "faults">("all");
  const [search, setSearch] = useState("");
  const topo = useApi<Topology>(["topology", storey], `/api/topology?storey=${storey}`);
  const allRooms = useApi<{ items: { id: string; long_name: string | null; storey_id: string }[] }>(["rooms-all"], "/api/rooms", { refetchInterval: false });

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v === null ? next.delete(k) : next.set(k, v);
    setParams(next, { replace: true });
  };
  const goTo = (roomId: string) => {
    const r = allRooms.data?.items.find((x) => x.id.toLowerCase() === roomId.trim().toLowerCase());
    if (r) set({ storey: r.storey_id, room: r.id });
  };

  return (
    <>
      <div className="page-head">
        <div><h1>{t("Digital twin")}</h1><p>{t("Rooms from the IFC model (real footprints) and their geometry-derived adjacency. Click a room for sensors and neighbours.")}</p></div>
      </div>
      <div className="filters">
        <DataState q={topo} lines={1}>{(tp) => (
          <Seg label={t("Storey")} value={storey} onChange={(v) => set({ storey: v, room: null })}
            items={tp.storeys.filter((s) => s.rooms > 0).map((s) => ({ id: s.id, label: `${s.id} (${s.rooms})` }))} />)}
        </DataState>
        <form className="row" onSubmit={(e) => { e.preventDefault(); goTo(search); }}>
          <input className="input" list="room-ids" placeholder={t("Find room, e.g. 02-14-N")} value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t("Find room")} />
          <datalist id="room-ids">{allRooms.data?.items.map((r) => <option key={r.id} value={r.id}>{r.long_name ?? ""}</option>)}</datalist>
          <button className="btn sm" type="submit">{t("Go")}</button>
        </form>
        <Seg label={t("Adjacency edges")} value={edgesMode} onChange={setEdgesMode} items={[{ id: "none", label: t("No edges") }, { id: "horizontal", label: t("Wall/door edges") }, { id: "all", label: t("+ vertical") }]} />
        <Seg label={t("Focus")} value={focus} onChange={setFocus} items={[{ id: "all", label: t("All rooms") }, { id: "faults", label: t("Faults & virtual") }]} />
      </div>
      <div className="split" style={{ gridTemplateColumns: selected ? "1fr 380px" : "1fr" }}>
        <DataState q={topo} lines={8}>
          {(tp) => <FloorPlan topo={tp} selected={selected} onSelect={(id) => set({ room: id })} edgesMode={edgesMode} focus={focus} />}
        </DataState>
        {selected && <RoomPanel id={selected} onClose={() => set({ room: null })} onNavigate={goTo} />}
      </div>
      {topo.data && <p className="muted" style={{ fontSize: 12, margin: 0 }}>{topo.data.method} {t("Edges are never invented: each carries its shared wall length, door count or footprint overlap.")}</p>}
    </>
  );
}

function FloorPlan({ topo, selected, onSelect, edgesMode, focus }: { topo: Topology; selected: string | null; onSelect: (id: string) => void; edgesMode: string; focus: string }) {
  const rooms = topo.rooms;
  const bounds = useMemo(() => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const r of rooms) for (const [x, y] of r.footprint) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); }
    if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 100, maxY: 100 };
    return { minX, minY, maxX, maxY };
  }, [rooms]);
  const flipY = (y: number) => bounds.maxY + bounds.minY - y; // IFC Y points north (up); SVG Y points down
  const initial = useMemo(() => ({ x: bounds.minX - 3, y: bounds.minY - 3, w: bounds.maxX - bounds.minX + 6, h: bounds.maxY - bounds.minY + 6 }), [bounds]);
  const [vb, setVb] = useState(initial);
  useEffect(() => setVb(initial), [initial]);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; vb: typeof vb; moved: boolean } | null>(null);
  useEffect(() => {
    // React's wheel listener is passive; block page scrolling while zooming the plan.
    const el = svgRef.current;
    const stop = (e: WheelEvent) => e.preventDefault();
    el?.addEventListener("wheel", stop, { passive: false });
    return () => el?.removeEventListener("wheel", stop);
  }, []);

  const temps = rooms.map((r) => r.temperature).filter((v): v is number => v != null);
  const tMin = temps.length ? Math.min(...temps) : 0;
  const tMax = temps.length ? Math.max(...temps) : 1;
  const tempColor = (v: number) => SEQ[Math.min(6, Math.max(0, Math.floor(((v - tMin) / Math.max(tMax - tMin, 0.01)) * 6.999)))];
  const byId = useMemo(() => Object.fromEntries(rooms.map((r) => [r.id, r])), [rooms]);
  const neighbours = useMemo(() => new Set(topo.edges.filter((e) => e.room_a === selected || e.room_b === selected).map((e) => (e.room_a === selected ? e.room_b : e.room_a))), [topo.edges, selected]);

  const zoom = (factor: number, cx?: number, cy?: number) => setVb((v) => {
    const w = Math.min(Math.max(v.w * factor, 5), initial.w * 3);
    const h = (w / v.w) * v.h;
    const px = cx ?? v.x + v.w / 2, py = cy ?? v.y + v.h / 2;
    return { x: px - ((px - v.x) * w) / v.w, y: py - ((py - v.y) * h) / v.h, w, h };
  });
  const toSvg = (clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    const scale = Math.max(vb.w / rect.width, vb.h / rect.height);
    const offX = (rect.width * scale - vb.w) / 2, offY = (rect.height * scale - vb.h) / 2;
    return { x: vb.x - offX + (clientX - rect.left) * scale, y: vb.y - offY + (clientY - rect.top) * scale, scale };
  };
  const onWheel = (e: RWheelEvent) => { const p = toSvg(e.clientX, e.clientY); zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, p.x, p.y); };
  const onDown = (e: RPointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, vb, moved: false }; };
  const onMove = (e: RPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) d.moved = true;
    if (!d.moved) return;
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    const s = toSvg(0, 0).scale;
    setVb({ ...d.vb, x: d.vb.x - (e.clientX - d.x) * s, y: d.vb.y - (e.clientY - d.y) * s });
  };
  const onUp = () => { setTimeout(() => { drag.current = null; }, 0); };
  const labelSize = Math.max(0.6, vb.w / 90);
  const showLabels = vb.w < 140;

  const pts = (fp: [number, number][]) => fp.map(([x, y]) => `${x},${flipY(y)}`).join(" ");
  const edges = topo.edges.filter((e) => edgesMode === "all" || (edgesMode === "horizontal" && e.kind !== "VERTICAL"));

  return (
    <Card title={`${t("Storey")} ${rooms[0]?.storey ?? ""}`} sub={t("{r} rooms · {e} horizontal edges on this storey", { r: rooms.length, e: topo.edges.filter((e) => e.kind !== "VERTICAL").length })}
      note={<div className="legend">
        <span><i className="sq" style={{ background: SEQ[1] }} /><i className="sq" style={{ background: SEQ[3], marginLeft: -4 }} /><i className="sq" style={{ background: SEQ[6], marginLeft: -4 }} />{t("Temperature")} {fmtNum(tMin)}-{fmtNum(tMax)} °C ({t("physical")})</span>
        <span><i className="sq" style={{ background: "var(--ghost-soft)", outline: "2px solid var(--ghost)" }} />{t("Virtual sensor active")}</span>
        <span><i className="sq" style={{ background: "var(--surface-3)", outline: "2px solid var(--critical)" }} />{t("Sensor fault, no inferred value")}</span>
        <span><i className="sq" style={{ background: "var(--surface-1)", outline: "1px solid var(--border)" }} />{t("Not instrumented")}</span>
        <span><i style={{ background: "var(--text-muted)" }} />{t("Adjacency edge")}</span>
      </div>}>
      <div className="plan-wrap">
        <svg ref={svgRef} className="plan" viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`} onWheel={onWheel} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp}
          role="img" aria-label={t("Floor plan of the selected storey")}>
          {rooms.map((r) => {
            const sel = r.id === selected;
            const nb = neighbours.has(r.id);
            const dim = focus === "faults" && !r.hasFault && !r.virtualActive;
            const fill = r.virtualActive ? "var(--ghost-soft)" : r.hasFault ? "var(--surface-3)" : r.temperature != null ? tempColor(r.temperature) : "var(--surface-1)";
            const stroke = sel ? "var(--accent)" : r.virtualActive ? "var(--ghost)" : r.hasFault ? "var(--critical)" : nb ? "var(--text-secondary)" : "var(--border)";
            return (
              <polygon key={r.id} points={pts(r.footprint)} fill={fill} stroke={stroke} strokeWidth={sel || r.hasFault || r.virtualActive ? 0.35 : nb ? 0.3 : 0.12}
                opacity={dim ? 0.25 : 1} style={{ cursor: "pointer" }} vectorEffect="non-scaling-stroke"
                onClick={() => { if (!drag.current?.moved) onSelect(r.id); }}>
                <title>{`${r.id}${r.name ? ` - ${r.name}` : ""}${r.temperature != null ? ` · ${fmtNum(r.temperature)} °C` : ""}${r.virtualActive ? ` · ${t("virtual")} ${fmtNum(r.virtualValue)} °C` : ""}${r.hasFault ? ` · ${t("sensor fault")}` : ""}`}</title>
              </polygon>
            );
          })}
          {edges.map((e) => {
            const a = byId[e.room_a], b = byId[e.room_b];
            if (!a || !b) return null;
            const on = selected && (e.room_a === selected || e.room_b === selected);
            return <line key={`${e.room_a}-${e.room_b}-${e.kind}`} x1={a.centroid[0]} y1={flipY(a.centroid[1])} x2={b.centroid[0]} y2={flipY(b.centroid[1])}
              stroke={on ? "var(--accent)" : "var(--text-muted)"} strokeWidth={on ? 2 : 1} opacity={on ? 0.95 : 0.45} vectorEffect="non-scaling-stroke" pointerEvents="none" />;
          })}
          {rooms.filter((r) => r.virtualActive).map((r) => (
            <g key={`g-${r.id}`} transform={`translate(${r.centroid[0] - labelSize * 1.2},${flipY(r.centroid[1]) - labelSize * 1.2}) scale(${labelSize / 10})`} pointerEvents="none">
              <path d="M12 2.5a7.5 7.5 0 0 0-7.5 7.5v11.2l2.6-2.1 2.4 2.4 2.5-2.4 2.5 2.4 2.4-2.4 2.6 2.1V10A7.5 7.5 0 0 0 12 2.5z" fill="var(--ghost)" />
            </g>
          ))}
          {showLabels && rooms.map((r) => (
            <text key={`t-${r.id}`} x={r.centroid[0]} y={flipY(r.centroid[1]) + labelSize * 1.6} fontSize={labelSize} textAnchor="middle" fill="var(--text-primary)" pointerEvents="none">{r.id}</text>
          ))}
        </svg>
        <div className="plan-tools">
          <button className="btn sm" aria-label={t("Zoom in")} onClick={() => zoom(1 / 1.3)}><Plus size={14} /></button>
          <button className="btn sm" aria-label={t("Zoom out")} onClick={() => zoom(1.3)}><Minus size={14} /></button>
          <button className="btn sm" aria-label={t("Reset view")} onClick={() => setVb(initial)}><Maximize size={14} /></button>
        </div>
      </div>
    </Card>
  );
}

function RoomPanel({ id, onClose, onNavigate }: { id: string; onClose: () => void; onNavigate: (id: string) => void }) {
  const q = useApi<RoomDetail>(["room", id], `/api/rooms/${encodeURIComponent(id)}`);
  return (
    <Card title={id} right={<button className="btn sm" onClick={onClose}>{t("Close")}</button>}>
      <DataState q={q}>
        {(d) => (
          <div className="stack" style={{ gap: 14 }}>
            <dl className="kv">
              <dt>{t("Name")}</dt><dd>{d.room.long_name ?? "-"}</dd>
              <dt>{t("Storey")}</dt><dd>{d.room.storey_id}</dd>
              <dt>{t("Area")}</dt><dd>{fmtNum(d.room.area_m2)} m²</dd>
              <dt>{t("Zone")}</dt><dd>{d.room.zone ?? "-"}</dd>
              <dt>{t("Stations")}</dt><dd>{d.room.stations.length ? d.room.stations.join(", ") : "-"}</dd>
              <dt>{t("Equipment")}</dt><dd>{t("{n} documented items (MSI)", { n: d.equipment.length })}</dd>
            </dl>
            <div>
              <h3>{t("Sensors")}</h3>
              {d.sensors.length === 0 ? <p className="muted">{t("No sensor in this room.")}</p> : d.sensors.map((s) => (
                <div key={s.id} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--grid)" }}>
                  <Link to={`/sensors?q=${encodeURIComponent(s.id)}`} className="mono">{s.id}</Link>
                  <SensorStatusBadge s={s.reading.sensorStatus} /><SourceBadge r={s.reading} />
                  <span style={{ marginLeft: "auto" }}><ReadingValue r={s.reading} /></span>
                </div>
              ))}
            </div>
            <div>
              <h3>{t("Neighbours")} ({d.neighbours.length})</h3>
              <div className="table-wrap"><table className="table"><thead><tr><th>{t("Room")}</th><th>{t("Link")}</th><th>{t("Temp.")}</th></tr></thead><tbody>
                {d.neighbours.map((n) => {
                  const tr = n.readings.find((r) => r.type === "TEMPERATURE");
                  return (
                    <tr key={`${n.room?.id}-${n.kind}`} className="clickable" onClick={() => n.room && onNavigate(n.room.id)}>
                      <td>{n.room?.id}<div className="muted" style={{ fontSize: 11.5 }}>{n.room?.long_name}</div></td>
                      <td>{t(n.kind)}{n.kind === "WALL" && n.sharedLengthM ? ` ${fmtNum(n.sharedLengthM)} m` : ""}{n.doorCount ? ` · ${n.doorCount} ${t("door")}` : ""}{n.kind === "VERTICAL" ? ` ${fmtNum(n.overlapM2, 0)} m²` : ""}</td>
                      <td>{tr ? <span className="row" style={{ gap: 4 }}>{tr.source === "INFERRED" && <GhostIcon size={13} />}<ReadingValue r={tr} /></span> : <span className="muted">-</span>}</td>
                    </tr>
                  );
                })}
              </tbody></table></div>
            </div>
            {d.incidents.length > 0 && (
              <div><h3>{t("Incidents")}</h3>{d.incidents.slice(0, 6).map((i) => (
                <div key={i.id} className="row" style={{ padding: "4px 0" }}><Link to={`/incidents/${i.id}`}>{i.id}</Link><SeverityBadge s={i.severity} /><IncidentStatusBadge s={i.status} /></div>))}</div>
            )}
          </div>
        )}
      </DataState>
    </Card>
  );
}
