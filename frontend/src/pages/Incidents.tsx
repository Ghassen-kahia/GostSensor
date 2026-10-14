import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Incident, Page, qs } from "../api";
import { Card, DataState, GhostIcon, IncidentStatusBadge, Pager, SeverityBadge, SimulatedBadge } from "../components";
import { fmtTime, useApi } from "../lib";
import { t } from "../i18n";

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
      <div className="page-head"><div><h1>{t("Incident management")}</h1><p>{t("Incidents opened by Agent 1, documented, analysed and notified by Agents 2-4. Acknowledge and resolve from the incident page.")}</p></div></div>
      <div className="filters">
        <input className="input" placeholder={t("Search id, title, room, sensor")} value={get("q")} onChange={(e) => set({ q: e.target.value })} aria-label={t("Search incidents")} />
        <select className="input" value={get("status")} onChange={(e) => set({ status: e.target.value })} aria-label={t("Status")}>
          <option value="">{t("All statuses")}</option><option value="OPEN,INVESTIGATING,ACKNOWLEDGED">{t("Active (open/investigating/ack.)")}</option>
          {["OPEN", "INVESTIGATING", "ACKNOWLEDGED", "RESOLVED", "CLOSED"].map((s) => <option key={s} value={s}>{t(s)}</option>)}
        </select>
        <select className="input" value={get("severity")} onChange={(e) => set({ severity: e.target.value })} aria-label={t("Severity")}>
          <option value="">{t("All severities")}</option><option value="CRITICAL,HIGH">{t("Critical + high")}</option>{["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map((s) => <option key={s} value={s}>{t(s)}</option>)}
        </select>
        <select className="input" value={get("category")} onChange={(e) => set({ category: e.target.value })} aria-label={t("Category")}><option value="">{t("All categories")}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{t(c)}</option>)}</select>
        <select className="input" value={get("scope")} onChange={(e) => set({ scope: e.target.value })} aria-label={t("Scope")}><option value="">{t("Sensor + group")}</option><option value="SENSOR">{t("Single sensor")}</option><option value="GROUP">{t("Group")}</option></select>
        <select className="input" value={get("sort", "detected")} onChange={(e) => set({ sort: e.target.value })} aria-label={t("Sort")}><option value="detected">{t("Newest first")}</option><option value="severity">{t("Most severe first")}</option><option value="updated">{t("Recently updated")}</option></select>
      </div>
      <Card>
        <DataState q={q} lines={8} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>{t("Incident")}</th><th>{t("Severity")}</th><th>{t("Status")}</th><th>{t("Category")}</th><th>{t("Scope")}</th><th>{t("Detected (building)")}</th><th>{t("Pipeline")}</th></tr></thead>
                <tbody>{d.items.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => nav(`/incidents/${i.id}`)}>
                    <td><Link to={`/incidents/${i.id}`} className="mono" onClick={(e) => e.stopPropagation()}>{i.id}</Link><div>{i.title}</div>
                      <div className="row" style={{ gap: 4, marginTop: 2 }}>{i.virtualSensorActive && <span className="badge virtual"><GhostIcon size={12} /> {t("virtual")}</span>}{i.simulated && <SimulatedBadge />}</div></td>
                    <td><SeverityBadge s={i.severity} /></td><td><IncidentStatusBadge s={i.status} /></td><td>{t(i.category)}</td>
                    <td>{i.scope === "GROUP" ? t("{n} sensors", { n: i.sensorCount }) : i.primaryRoomId ?? i.primarySensorId}</td>
                    <td>{fmtTime(i.detectedAt)}</td>
                    <td className="secondary">{i.pipelineStage}{i.processedVersion < i.pipelineVersion ? ` · ${t("pending")}` : ""}</td>
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
