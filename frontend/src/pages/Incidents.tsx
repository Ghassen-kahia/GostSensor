import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Incident, Page, qs } from "../api";
import { Card, DataState, GhostIcon, IncidentStatusBadge, Pager, SeverityBadge, SimulatedBadge } from "../components";
import { fmtTime, useApi } from "../lib";

const CATEGORIES = ["SENSOR_OFFLINE", "SENSOR_STALE", "SENSOR_INVALID", "SENSOR_SPIKE", "SENSOR_DRIFT", "SENSOR_OUT_OF_RANGE", "ENVIRONMENTAL_ANOMALY", "ENERGY_ANOMALY", "TOPOLOGY_INCONSISTENCY"];

export default function Incidents() {
  const [p, setP] = useSearchParams();
  const nav = useNavigate();
  const get = (k: string, d = "") => p.get(k) ?? d;
  const set = (patch: Record<string, string>) => {
    const n = new URLSearchParams(p);
    for (const [k, v] of Object.entries(patch)) v ? n.set(k, v) : n.delete(k);
    if (!("page" in patch)) n.delete("page");
    setP(n, { replace: true });
  };
  const query = qs({ status: get("status"), severity: get("severity"), category: get("category"), q: get("q"), scope: get("scope"), sort: get("sort", "detected"), page: get("page", "1"), pageSize: 25 });
  const q = useApi<Page<Incident>>(["incidents", query], `/api/incidents${query}`, { refetchInterval: 15000, placeholderData: (prev) => prev });
  return (
    <>
      <div className="page-head"><div><h1>Incident management</h1><p>Incidents opened by Agent 1, documented, analysed and notified by Agents 2-4. Acknowledge and resolve from the incident page.</p></div></div>
      <div className="filters">
        <input className="input" placeholder="Search id, title, room, sensor" value={get("q")} onChange={(e) => set({ q: e.target.value })} aria-label="Search incidents" />
        <select className="input" value={get("status")} onChange={(e) => set({ status: e.target.value })} aria-label="Status">
          <option value="">All statuses</option><option value="OPEN,INVESTIGATING,ACKNOWLEDGED">Active (open/investigating/ack.)</option>
          {["OPEN", "INVESTIGATING", "ACKNOWLEDGED", "RESOLVED", "CLOSED"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="input" value={get("severity")} onChange={(e) => set({ severity: e.target.value })} aria-label="Severity">
          <option value="">All severities</option><option value="CRITICAL,HIGH">Critical + high</option>{["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="input" value={get("category")} onChange={(e) => set({ category: e.target.value })} aria-label="Category"><option value="">All categories</option>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={get("scope")} onChange={(e) => set({ scope: e.target.value })} aria-label="Scope"><option value="">Sensor + group</option><option value="SENSOR">Single sensor</option><option value="GROUP">Group</option></select>
        <select className="input" value={get("sort", "detected")} onChange={(e) => set({ sort: e.target.value })} aria-label="Sort"><option value="detected">Newest first</option><option value="severity">Most severe first</option><option value="updated">Recently updated</option></select>
      </div>
      <Card>
        <DataState q={q} lines={8} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Category</th><th>Scope</th><th>Detected (building)</th><th>Pipeline</th></tr></thead>
                <tbody>{d.items.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => nav(`/incidents/${i.id}`)}>
                    <td><Link to={`/incidents/${i.id}`} className="mono" onClick={(e) => e.stopPropagation()}>{i.id}</Link><div>{i.title}</div>
                      <div className="row" style={{ gap: 4, marginTop: 2 }}>{i.virtualSensorActive && <span className="badge virtual"><GhostIcon size={12} /> virtual</span>}{i.simulated && <SimulatedBadge />}</div></td>
                    <td><SeverityBadge s={i.severity} /></td><td><IncidentStatusBadge s={i.status} /></td><td>{i.category}</td>
                    <td>{i.scope === "GROUP" ? `${i.sensorCount} sensors` : i.primaryRoomId ?? i.primarySensorId}</td>
                    <td>{fmtTime(i.detectedAt)}</td>
                    <td className="secondary">{i.pipelineStage}{i.processedVersion < i.pipelineVersion ? " · pending" : ""}</td>
                  </tr>))}</tbody>
              </table></div>
              <Pager page={d.page} pageSize={d.pageSize} total={d.total} onPage={(n) => set({ page: String(n) })} />
            </>
          )}
        </DataState>
      </Card>
    </>
  );
}
