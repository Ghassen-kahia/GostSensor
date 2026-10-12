import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowDownUp } from "lucide-react";
import { Page, qs, Sensor } from "../api";
import { Card, ChartTooltip, DataState, Drawer, Pager, ReadingValue, SensorStatusBadge, SimulatedBadge, SourceBadge, VirtualBadge } from "../components";
import { ageAt, fmtNum, fmtTime, unitLabel, useApi, useClock } from "../lib";

type SensorPage = Page<Sensor> & { statusCounts: Record<string, number>; sourceCounts: Record<string, number> };
const TYPES = ["TEMPERATURE", "CO2", "ENERGY", "OCCUPANCY", "WATER", "HUMIDITY", "LIGHTING"];
const STATUSES = ["ONLINE", "STALE", "OFFLINE", "INVALID", "NO_DATA"];

export default function Sensors() {
  const [p, setP] = useSearchParams();
  const get = (k: string, d = "") => p.get(k) ?? d;
  const set = (patch: Record<string, string>) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(patch)) v ? n.set(k, v) : n.delete(k);
    if (!("page" in patch)) n.delete("page");
    setP(n, { replace: true });
  };
  const page = Number(get("page", "1"));
  const sort = get("sort", "id"), order = get("order", "asc");
  const query = qs({ type: get("type"), status: get("status"), source: get("source"), storey: get("storey"), q: get("q"), sort, order, page, pageSize: 50 });
  const q = useApi<SensorPage>(["sensors", query], `/api/sensors${query}`, { refetchInterval: 15000, placeholderData: (prev) => prev });
  const clock = useClock();
  const [open, setOpen] = useState<string | null>(null);
  const sortBtn = (key: string, label: string) => (
    <button onClick={() => set({ sort: key, order: sort === key && order === "asc" ? "desc" : "asc" })} aria-label={`Sort by ${label}`}>
      {label}{sort === key && <ArrowDownUp size={12} />}
    </button>
  );

  return (
    <>
      <div className="page-head"><div><h1>Live sensor monitoring</h1><p>Latest reading per sensor at building time. Inferred values are marked with the ghost badge and a violet edge.</p></div></div>
      <div className="filters">
        <input className="input" placeholder="Search sensor, room..." value={get("q")} onChange={(e) => set({ q: e.target.value })} aria-label="Search sensors" />
        <select className="input" value={get("type")} onChange={(e) => set({ type: e.target.value })} aria-label="Sensor type"><option value="">All types</option>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
        <select className="input" value={get("status")} onChange={(e) => set({ status: e.target.value })} aria-label="Connectivity status"><option value="">All statuses</option>{STATUSES.map((t) => <option key={t}>{t}</option>)}<option value="STALE,OFFLINE,INVALID">Any fault</option></select>
        <select className="input" value={get("source")} onChange={(e) => set({ source: e.target.value })} aria-label="Value source"><option value="">Physical + inferred</option><option value="PHYSICAL">Physical only</option><option value="INFERRED">Inferred only</option><option value="NONE">No value</option></select>
        <select className="input" value={get("storey")} onChange={(e) => set({ storey: e.target.value })} aria-label="Storey"><option value="">All storeys</option>{["S1", "N0", "N1", "N2", "N3", "N4", "N5"].map((s) => <option key={s}>{s}</option>)}</select>
        {q.data && <span className="muted" style={{ fontSize: 12 }}>{Object.entries(q.data.statusCounts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ")}</span>}
      </div>
      <Card>
        <DataState q={q} lines={10} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr>
                    <th>{sortBtn("id", "Sensor")}</th><th>{sortBtn("room", "Room")}</th><th>{sortBtn("type", "Type")}</th><th>{sortBtn("value", "Latest value")}</th>
                    <th>Source</th><th>Measured / inferred at</th><th>{sortBtn("freshness", "Freshness")}</th><th>{sortBtn("status", "Connectivity")}</th><th>Anomaly / incident</th>
                  </tr></thead>
                  <tbody>
                    {d.items.map((s) => {
                      const r = s.reading;
                      return (
                        <tr key={s.id} className={`clickable ${r.source === "INFERRED" ? "inferred" : ""}`} onClick={() => setOpen(s.id)}>
                          <td className="mono">{s.id}</td>
                          <td>{s.roomId ? <Link to={`/twin?room=${encodeURIComponent(s.roomId)}&storey=${s.storey ?? ""}`} onClick={(e) => e.stopPropagation()}>{s.roomId}</Link> : <span className="muted">unmapped</span>}</td>
                          <td>{s.type}</td>
                          <td className="num"><ReadingValue r={r} /></td>
                          <td><SourceBadge r={r} />{r.source === "INFERRED" && r.inferenceQuality && <div className="muted" style={{ fontSize: 11.5 }}>quality {r.inferenceQuality}</div>}</td>
                          <td>{fmtTime(r.observedAt ?? r.inferredAt)}</td>
                          <td title={`last transmission ${fmtTime(r.lastTransmissionAt)}`}>{ageAt(r.lastTransmissionAt, clock.data?.now)}</td>
                          <td><SensorStatusBadge s={r.sensorStatus} />{r.simulatedFault && <> <SimulatedBadge /></>}</td>
                          <td>{r.anomaly ? <span className="badge">{r.anomaly}</span> : null} {r.incidentId && <Link to={`/incidents/${r.incidentId}`} onClick={(e) => e.stopPropagation()}>{r.incidentId}</Link>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pager page={d.page} pageSize={d.pageSize} total={d.total} onPage={(n) => set({ page: String(n) })} />
            </>
          )}
        </DataState>
      </Card>
      {open && <SensorDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

interface History {
  sensorId: string; unit: string; physical: { t: string; value: number | null; simulated: boolean }[];
  inferred: { t: string; value: number | null; lower: number | null; upper: number | null; quality: string; actual: number | null }[]; note: string;
}

export function SensorHistoryChart({ id, hours = 48 }: { id: string; hours?: number }) {
  const h = useApi<History>(["history", id, hours], `/api/sensors/${encodeURIComponent(id)}/history?hours=${hours}`, { refetchInterval: 30000 });
  return (
    <DataState q={h} lines={5}>
      {(d) => {
        const map = new Map<string, Record<string, unknown>>();
        for (const r of d.physical) map.set(r.t, { t: r.t, physical: r.value });
        for (const r of d.inferred) map.set(r.t, { ...(map.get(r.t) ?? { t: r.t }), inferred: r.value, band: r.lower != null && r.upper != null ? [r.lower, r.upper] : null, actual: r.actual });
        const rows = [...map.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
        const u = unitLabel(d.unit);
        if (!rows.length) return <div className="state">No data in this window.</div>;
        return (
          <>
            <div className="legend"><span><i style={{ background: "var(--series-1)" }} />Physical</span><span><i style={{ background: "var(--series-2)" }} />Inferred (virtual)</span><span><i className="sq" style={{ background: "var(--series-2)", opacity: 0.3 }} />90 % interval</span>{d.inferred.some((r) => r.actual != null) && <span><i style={{ background: "var(--text-primary)" }} />Hidden true value (simulated outage)</span>}</div>
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={rows} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="t" tickFormatter={(v) => fmtTime(v, false)} tick={{ fontSize: 11, fill: "var(--text-muted)" }} minTickGap={40} stroke="var(--border)" />
                <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--border)" domain={["auto", "auto"]} />
                <Tooltip content={<ChartTooltip unit={u} labelFmt={fmtTime} />} />
                <Area dataKey="band" name="Interval" stroke="none" fill="var(--series-2)" fillOpacity={0.22} isAnimationActive={false} connectNulls={false} />
                <Line dataKey="physical" name="Physical" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                <Line dataKey="inferred" name="Inferred" stroke="var(--series-2)" strokeWidth={2} dot={d.inferred.length < 4 ? { r: 4 } : false} isAnimationActive={false} connectNulls={false} />
                <Line dataKey="actual" name="True value" stroke="var(--text-primary)" strokeWidth={1} dot={{ r: 1.5 }} isAnimationActive={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>{d.note}</p>
          </>
        );
      }}
    </DataState>
  );
}

function SensorDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const s = useApi<Sensor & { room: { id: string; long_name: string | null; storey_id: string } | null; simulatedFaults: { id: number; kind: string; startAt: string; endAt: string | null }[] }>(["sensor", id], `/api/sensors/${encodeURIComponent(id)}`);
  const [hours, setHours] = useState(48);
  return (
    <Drawer title={<div className="row"><strong className="mono">{id}</strong>{s.data?.reading.source === "INFERRED" && <VirtualBadge />}</div>} onClose={onClose}>
      <DataState q={s}>
        {(d) => (
          <>
            <dl className="kv">
              <dt>Display name</dt><dd>{d.displayName ?? "-"}</dd>
              <dt>Room</dt><dd>{d.room ? <Link to={`/twin?room=${encodeURIComponent(d.room.id)}&storey=${d.room.storey_id}`}>{d.room.id}</Link> : "unmapped"} {d.room?.long_name}</dd>
              <dt>Type / unit</dt><dd>{d.type} · {unitLabel(d.unit) || "-"}</dd>
              <dt>Connectivity</dt><dd><SensorStatusBadge s={d.reading.sensorStatus} /> last transmission {fmtTime(d.reading.lastTransmissionAt)}</dd>
              <dt>Current value</dt><dd><ReadingValue r={d.reading} /> <SourceBadge r={d.reading} /></dd>
              {d.reading.source === "INFERRED" && <><dt>Model</dt><dd className="mono">{d.reading.modelVersion} · quality {d.reading.inferenceQuality}</dd>
                <dt>Interval</dt><dd>{fmtNum(d.reading.uncertainty?.lower)} - {fmtNum(d.reading.uncertainty?.upper)} ({Math.round((d.reading.uncertainty?.level ?? 0) * 100)} %, {d.reading.uncertainty?.method})</dd></>}
              {d.reading.qualityReasons && d.reading.qualityReasons.length > 0 && <><dt>Quality notes</dt><dd>{d.reading.qualityReasons.join("; ")}</dd></>}
              <dt>Last physical</dt><dd>{d.reading.lastPhysical ? `${fmtNum(d.reading.lastPhysical.value, 2)} at ${fmtTime(d.reading.lastPhysical.observedAt)}` : "never"}</dd>
              <dt>Heartbeat</dt><dd>stale after {d.staleAfterS / 60} min, offline after {d.offlineAfterS / 60} min (bucket {d.resolutionS / 60} min)</dd>
              <dt>Acquisition</dt><dd>{d.station ?? "-"} <span className="muted">({d.stationSource})</span></dd>
              <dt>BMS point</dt><dd className="mono" style={{ fontSize: 11.5 }}>{d.externalId}</dd>
            </dl>
            <div className="row"><h3>History</h3><select className="input" style={{ marginLeft: "auto" }} value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="History window"><option value={12}>12 h</option><option value={48}>48 h</option><option value={168}>7 days</option></select></div>
            <SensorHistoryChart id={id} hours={hours} />
            {d.simulatedFaults.length > 0 && <div><h3>Simulated faults</h3><ul className="list">{d.simulatedFaults.map((f) => <li key={f.id}>{f.kind} from {fmtTime(f.startAt)} to {f.endAt ? fmtTime(f.endAt) : "open"}</li>)}</ul></div>}
          </>
        )}
      </DataState>
    </Drawer>
  );
}
