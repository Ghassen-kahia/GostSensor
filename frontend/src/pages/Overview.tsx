import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Building2, Radio, ShieldAlert, WifiOff } from "lucide-react";
import { Severity, Summary } from "../api";
import { Card, ChartCard, ChartTooltip, DataState, GhostIcon, Kpi, Seg, SeverityBadge } from "../components";
import { fmtTime, useApi } from "../lib";

interface Series {
  from: string; to: string;
  temperature: { t: string; mean: number | null; min: number | null; max: number | null; physical: number; inferred: number }[];
  availability: { t: string; temperature: number; co2: number }[];
  energy: { t: string; kwhPerHour: number; meters: number }[];
  anomalyFrequency: ({ day: string } & Record<string, number>)[];
  severityDistribution: Partial<Record<Severity, number>>;
  notes: Record<string, string>;
}

// Six folded groups, fixed slot order (validated palette; never cycled).
const GROUPS: { key: string; label: string; color: string; cats: string[] }[] = [
  { key: "silence", label: "Offline / stale", color: "var(--series-1)", cats: ["SENSOR_OFFLINE", "SENSOR_STALE"] },
  { key: "invalid", label: "Invalid values", color: "var(--series-2)", cats: ["SENSOR_INVALID"] },
  { key: "value", label: "Spike / drift / range", color: "var(--series-3)", cats: ["SENSOR_SPIKE", "SENSOR_DRIFT", "SENSOR_OUT_OF_RANGE"] },
  { key: "env", label: "Environmental", color: "var(--series-4)", cats: ["ENVIRONMENTAL_ANOMALY"] },
  { key: "energy", label: "Energy", color: "var(--series-5)", cats: ["ENERGY_ANOMALY"] },
  { key: "other", label: "Topology / other", color: "var(--series-6)", cats: ["TOPOLOGY_INCONSISTENCY", "UNKNOWN"] },
];
const SEV_COLOR: Record<Severity, string> = { CRITICAL: "var(--critical)", HIGH: "var(--serious)", MEDIUM: "var(--warning)", LOW: "var(--series-1)", INFO: "var(--info)" };
const tick = { fontSize: 11, fill: "var(--text-muted)" };
const tShort = (v: string) => fmtTime(v, false);

export default function Overview() {
  const [hours, setHours] = useState<"24" | "48" | "168">("48");
  const summary = useApi<Summary>(["summary"], "/api/dashboard/summary");
  const series = useApi<Series>(["timeseries", hours], `/api/dashboard/timeseries?hours=${hours}`, { refetchInterval: 30000 });

  return (
    <>
      <div className="page-head">
        <div><h1>Executive overview</h1><p>Building health, sensor availability, anomalies and agent activity at the current building time.</p></div>
      </div>
      <DataState q={summary} lines={4}>
        {(s) => {
          const bs = s.sensors.byStatus;
          return (
            <div className="kpis">
              <Kpi icon={<Building2 size={14} />} label="Rooms (IFC)" value={s.rooms.total} hint={`${s.rooms.instrumented} instrumented · ${s.rooms.otherIfcSpaces} non-room spaces excluded`} />
              <Kpi icon={<Radio size={14} />} label="Sensors" value={s.sensors.total} hint={`${bs.ONLINE ?? 0} online`} />
              <Kpi icon={<WifiOff size={14} />} label="Offline" value={bs.OFFLINE ?? 0} hint={`${bs.STALE ?? 0} stale · ${bs.INVALID ?? 0} invalid`} />
              <Kpi icon={<GhostIcon size={14} />} label="Active virtual sensors" value={s.virtualSensorsActive} hint={<Link to="/ghost">Ghost monitor</Link>} />
              <Kpi icon={<ShieldAlert size={14} />} label="Open incidents" value={s.incidents.open}
                hint={(["CRITICAL", "HIGH", "MEDIUM"] as Severity[]).map((k) => `${s.incidents.openBySeverity[k] ?? 0} ${k.toLowerCase()}`).join(" · ")} />
              <Kpi label="System health" value={s.health} hint={<Link to="/system">Workflows & health</Link>} />
            </div>
          );
        }}
      </DataState>

      <div className="row"><h3>Trends</h3><div style={{ marginLeft: "auto" }}><Seg label="Time range" value={hours} onChange={setHours} items={[{ id: "24", label: "24 h" }, { id: "48", label: "48 h" }, { id: "168", label: "7 d" }]} /></div></div>
      <DataState q={series} lines={6}>
        {(d) => {
          const temp = d.temperature.map((r) => ({ ...r, range: r.min != null && r.max != null ? [r.min, r.max] : null }));
          const freq = d.anomalyFrequency.map((r) => {
            const o: Record<string, number | string> = { day: r.day };
            for (const g of GROUPS) o[g.key] = g.cats.reduce((a, c) => a + (Number(r[c]) || 0), 0);
            return o;
          });
          const sev = (["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as Severity[]).map((k) => ({ k, n: d.severityDistribution[k] ?? 0 }));
          const maxSev = Math.max(1, ...sev.map((x) => x.n));
          return (
            <>
              <div className="grid cols-2">
                <ChartCard title="Temperature (building)" sub="°C, physical readings" legend={[{ label: "Mean", color: "var(--series-1)" }, { label: "Min-max across rooms", color: "var(--seq-2)", square: true }]}
                  chart={
                    <ResponsiveContainer width="100%" height={240}>
                      <ComposedChart data={temp} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis dataKey="t" tickFormatter={tShort} tick={tick} minTickGap={40} stroke="var(--border)" />
                        <YAxis tick={tick} stroke="var(--border)" domain={["auto", "auto"]} />
                        <Tooltip content={<ChartTooltip unit="°C" labelFmt={fmtTime} />} />
                        <Area dataKey="range" name="Min-max" stroke="none" fill="var(--seq-2)" fillOpacity={0.35} isAnimationActive={false} />
                        <Line dataKey="mean" name="Mean" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </ComposedChart>
                    </ResponsiveContainer>}
                  table={{ columns: ["Time", "Mean", "Min", "Max"], rows: temp.slice(-48).map((r) => [fmtTime(r.t), r.mean, r.min, r.max]) }} />
                <ChartCard title="Physical vs inferred readings" sub="temperature sensors per 15-min bucket"
                  legend={[{ label: "Physical", color: "var(--series-1)" }, { label: "Inferred (virtual)", color: "var(--series-2)" }]}
                  chart={
                    <ResponsiveContainer width="100%" height={240}>
                      <LineChart data={d.temperature} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis dataKey="t" tickFormatter={tShort} tick={tick} minTickGap={40} stroke="var(--border)" />
                        <YAxis tick={tick} stroke="var(--border)" allowDecimals={false} />
                        <Tooltip content={<ChartTooltip labelFmt={fmtTime} />} />
                        <Line dataKey="physical" name="Physical" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
                        <Line dataKey="inferred" name="Inferred" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>}
                  table={{ columns: ["Time", "Physical", "Inferred"], rows: d.temperature.slice(-48).map((r) => [fmtTime(r.t), r.physical, r.inferred]) }} />
                <ChartCard title="Sensor availability" sub="% of sensors whose bucket was received" note={d.notes.availability}
                  legend={[{ label: "Temperature", color: "var(--series-1)" }, { label: "CO₂", color: "var(--series-2)" }]}
                  chart={
                    <ResponsiveContainer width="100%" height={220}>
                      <LineChart data={d.availability} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis dataKey="t" tickFormatter={tShort} tick={tick} minTickGap={40} stroke="var(--border)" />
                        <YAxis tick={tick} stroke="var(--border)" domain={[0, 100]} />
                        <Tooltip content={<ChartTooltip unit="%" labelFmt={fmtTime} />} />
                        <Line dataKey="temperature" name="Temperature" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
                        <Line dataKey="co2" name="CO₂" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>}
                  table={{ columns: ["Time", "Temperature %", "CO₂ %"], rows: d.availability.slice(-48).map((r) => [fmtTime(r.t), r.temperature, r.co2]) }} />
                <ChartCard title="Electricity consumption" sub="kWh/h, sum of feeder meters" note={d.notes.energy}
                  chart={
                    <ResponsiveContainer width="100%" height={220}>
                      <LineChart data={d.energy} margin={{ top: 4, right: 8, left: -4, bottom: 0 }}>
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis dataKey="t" tickFormatter={tShort} tick={tick} minTickGap={40} stroke="var(--border)" />
                        <YAxis tick={tick} stroke="var(--border)" />
                        <Tooltip content={<ChartTooltip unit="kWh/h" labelFmt={fmtTime} />} />
                        <Line dataKey="kwhPerHour" name="Electricity" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>}
                  table={{ columns: ["Hour", "kWh/h", "Meters"], rows: d.energy.slice(-48).map((r) => [fmtTime(r.t), r.kwhPerHour, r.meters]) }} />
              </div>
              <div className="grid cols-2">
                <ChartCard title="Anomaly frequency" sub="incidents detected per day (building time)" legend={GROUPS.map((g) => ({ label: g.label, color: g.color, square: true }))}
                  chart={freq.length ? (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={freq} margin={{ top: 4, right: 8, left: -12, bottom: 0 }} barCategoryGap="20%">
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis dataKey="day" tick={tick} stroke="var(--border)" />
                        <YAxis tick={tick} stroke="var(--border)" allowDecimals={false} />
                        <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--surface-2)" }} />
                        {GROUPS.map((g, i) => <Bar key={g.key} dataKey={g.key} name={g.label} stackId="a" fill={g.color} stroke="var(--surface-1)" strokeWidth={2} radius={i === GROUPS.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />)}
                      </BarChart>
                    </ResponsiveContainer>) : <div className="state">No incidents in the last 14 days of building time.</div>}
                  table={{ columns: ["Day", ...GROUPS.map((g) => g.label)], rows: freq.map((r) => [r.day as string, ...GROUPS.map((g) => r[g.key] as number)]) }} />
                <Card title="Open incidents by severity">
                  <div className="stack" style={{ gap: 10 }}>
                    {sev.map(({ k, n }) => (
                      <div key={k} className="row" style={{ flexWrap: "nowrap" }}>
                        <div style={{ width: 110 }}><SeverityBadge s={k} /></div>
                        <div className="bar-track" style={{ flex: 1 }}><div style={{ width: `${(100 * n) / maxSev}%`, background: SEV_COLOR[k] }} /></div>
                        <strong style={{ width: 36, textAlign: "right" }}>{n}</strong>
                      </div>
                    ))}
                    <Link to="/incidents?status=OPEN,INVESTIGATING,ACKNOWLEDGED">View open incidents</Link>
                  </div>
                </Card>
              </div>
            </>
          );
        }}
      </DataState>

      <DataState q={summary}>
        {(s) => (
          <div className="grid cols-2">
            <Card title="Recent agent / workflow runs" right={<Link to="/system">All runs</Link>}>
              {s.recentRuns.length === 0 ? <div className="state">No agent runs yet.</div> : (
                <div className="table-wrap"><table className="table"><thead><tr><th>Agent</th><th>Incident</th><th>Status</th><th>Trigger</th><th>Started (real)</th></tr></thead>
                  <tbody>{s.recentRuns.map((r) => (
                    <tr key={r.id}><td>{r.agent}</td><td>{r.incident_id ? <Link to={`/incidents/${r.incident_id}`}>{r.incident_id}</Link> : "-"}</td>
                      <td>{r.status}</td><td>{r.trigger}{r.n8n_execution_id ? ` #${r.n8n_execution_id}` : ""}</td><td>{fmtTime(r.startedAt)}</td></tr>))}</tbody></table></div>)}
            </Card>
            <Card title="Recent notifications" right={<Link to="/system">Delivery log</Link>}>
              {s.recentNotifications.length === 0 ? <div className="state">No notifications yet.</div> : (
                <div className="table-wrap"><table className="table"><thead><tr><th>Incident</th><th>Kind</th><th>Channel</th><th>Status</th><th>When (real)</th></tr></thead>
                  <tbody>{s.recentNotifications.map((n) => (
                    <tr key={n.id}><td><Link to={`/incidents/${n.incident_id}`}>{n.incident_id}</Link></td><td>{n.kind} <SeverityBadge s={n.severity} /></td><td>{n.channel}</td>
                      <td title={n.status_reason ?? ""}>{n.status}</td><td>{fmtTime(n.createdAt)}</td></tr>))}</tbody></table></div>)}
            </Card>
          </div>
        )}
      </DataState>
      <p className="muted" style={{ fontSize: 12 }}>Readings are a replay of the real OnDijon BMS historian; nothing on this page is synthetic. Inferred values are labelled.</p>
    </>
  );
}
