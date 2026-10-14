import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download, FileDown } from "lucide-react";
import { Incident, Page, qs, Severity } from "../api";
import { Card, ChartCard, ChartTooltip, DataState, Kpi, Pager, SeverityBadge } from "../components";
import { fmtDuration, fmtTime, useApi } from "../lib";
import { t } from "../i18n";

type ReportRow = Incident & { reportVersion: number; reportSummary: string; reportCreatedAt: string; llmModel: string | null };
interface Analytics {
  incidents: number; meanTimeToDetect: { n: number; meanS: number; medianS: number; p90S: number } | null; meanTimeToResolve: { n: number; meanS: number; medianS: number; p90S: number } | null;
  mttdNote: string; mttrNote: string; failuresByType: { type: string; category: string; n: number }[]; mostAffectedSensors: { sensor_id: string; n: number }[];
  trend: ({ day: string } & Partial<Record<Severity, number>>)[]; hypothesisOutcomes: { cause_code: string; outcome: string; n: number }[];
  recommendationStatus: { status: string; n: number }[];
  virtualSensors: { usage: { n: number; sensors: number; good: number; degraded: number; insufficient: number }; liveValidation: { n: number; mae: number; coverage: number } | null; offlineEvaluation: { model: { mae: number } } | null };
}
const SEVS: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const SEV_COLOR: Record<Severity, string> = { CRITICAL: "var(--critical)", HIGH: "var(--serious)", MEDIUM: "var(--warning)", LOW: "var(--series-1)", INFO: "var(--info)" };

export default function Reports() {
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);
  const query = qs({ q, severity, page, pageSize: 20 });
  const reports = useApi<Page<ReportRow>>(["reports", query], `/api/reports${query}`, { placeholderData: (p) => p });
  const an = useApi<Analytics>(["analytics"], "/api/analytics", { refetchInterval: 30000 });

  return (
    <>
      <div className="page-head">
        <div><h1>{t("Reports & analytics")}</h1><p>{t("Technical reports produced by Agent 2 and operational statistics over the incidents recorded so far.")}</p></div>
        <div className="actions"><a className="btn" href={`/api/reports/export.csv${qs({ severity })}`} download><FileDown size={14} /> {t("Export incidents CSV")}</a></div>
      </div>
      <DataState q={an} lines={3}>
        {(a) => (
          <>
            <div className="kpis">
              <Kpi label={t("Incidents recorded")} value={a.incidents} />
              <Kpi label={t("Mean time to detect")} value={fmtDuration(a.meanTimeToDetect?.meanS)} hint={a.meanTimeToDetect ? `${t("median")} ${fmtDuration(a.meanTimeToDetect.medianS)} · p90 ${fmtDuration(a.meanTimeToDetect.p90S)} · n=${a.meanTimeToDetect.n}` : t("not measurable yet")} />
              <Kpi label={t("Mean time to resolve")} value={fmtDuration(a.meanTimeToResolve?.meanS)} hint={a.meanTimeToResolve ? `${t("median")} ${fmtDuration(a.meanTimeToResolve.medianS)} · n=${a.meanTimeToResolve.n}` : t("no resolved incident yet")} />
              <Kpi label={t("Virtual readings produced")} value={a.virtualSensors.usage.n ?? 0} hint={t("{a} sensors · {b} insufficient", { a: a.virtualSensors.usage.sensors ?? 0, b: a.virtualSensors.usage.insufficient ?? 0 })} />
              <Kpi label={t("Live virtual-sensor MAE")} value={a.virtualSensors.liveValidation ? `${a.virtualSensors.liveValidation.mae} °C` : "-"} hint={a.virtualSensors.liveValidation ? t("n={n} simulated outages", { n: a.virtualSensors.liveValidation.n }) : t("offline test MAE {v} °C", { v: a.virtualSensors.offlineEvaluation?.model.mae ?? "-" })} />
            </div>
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>{a.mttdNote} {a.mttrNote}</p>
            <div className="grid cols-2">
              <ChartCard title={t("Incident trend by severity")} sub={t("per day of building time")} legend={SEVS.map((s) => ({ label: t(s), color: SEV_COLOR[s], square: true }))}
                chart={a.trend.length ? (
                  <ResponsiveContainer width="100%" height={230}>
                    <BarChart data={a.trend} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid stroke="var(--grid)" vertical={false} />
                      <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--border)" />
                      <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--border)" />
                      <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--surface-2)" }} />
                      {SEVS.map((s, i) => <Bar key={s} dataKey={s} name={t(s)} stackId="s" fill={SEV_COLOR[s]} stroke="var(--surface-1)" strokeWidth={2} radius={i === SEVS.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />)}
                    </BarChart>
                  </ResponsiveContainer>) : <div className="state">{t("No incidents yet.")}</div>}
                table={{ columns: [t("Day"), ...SEVS.map((s) => t(s))], rows: a.trend.map((r) => [r.day, ...SEVS.map((s) => r[s] ?? 0)]) }} />
              <Card title={t("Sensor failure statistics")}>
                <div className="grid cols-2" style={{ gap: 12 }}>
                  <div><h3>{t("By sensor type & category")}</h3><table className="table"><tbody>{a.failuresByType.map((f) => <tr key={`${f.type}-${f.category}`}><td>{t(f.type)}</td><td className="secondary">{t(f.category)}</td><td className="num">{f.n}</td></tr>)}</tbody></table></div>
                  <div><h3>{t("Most affected sensors")}</h3><table className="table"><tbody>{a.mostAffectedSensors.map((s) => <tr key={s.sensor_id}><td className="mono">{s.sensor_id}</td><td className="num">{s.n}</td></tr>)}</tbody></table></div>
                </div>
              </Card>
              <Card title={t("Root-cause hypothesis outcomes")} sub={t("confirmed / rejected by people")}>
                {a.hypothesisOutcomes.length ? <table className="table"><tbody>{a.hypothesisOutcomes.map((h) => <tr key={`${h.cause_code}-${h.outcome}`}><td>{h.cause_code}</td><td>{t(h.outcome)}</td><td className="num">{h.n}</td></tr>)}</tbody></table>
                  : <p className="muted">{t("No hypothesis has been reviewed yet. Confirm or reject hypotheses on an incident's “Root cause & actions” tab.")}</p>}
              </Card>
              <Card title={t("Recommendation follow-up")}>
                <table className="table"><tbody>{a.recommendationStatus.map((r) => <tr key={r.status}><td>{t(r.status)}</td><td className="num">{r.n}</td></tr>)}</tbody></table>
              </Card>
            </div>
          </>
        )}
      </DataState>
      <Card title={t("Incident reports")}>
        <div className="filters" style={{ marginBottom: 10 }}>
          <input className="input" placeholder={t("Search reports")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("Search reports")} />
          <select className="input" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} aria-label={t("Severity")}><option value="">{t("All severities")}</option>{SEVS.map((s) => <option key={s} value={s}>{t(s)}</option>)}</select>
        </div>
        <DataState q={reports} empty={(d) => d.items.length === 0}>
          {(d) => (<>
            <div className="table-wrap"><table className="table"><thead><tr><th>{t("Incident")}</th><th>{t("Severity")}</th><th>{t("Status")}</th><th>{t("Summary")}</th><th>{t("Report")}</th><th>{t("Download")}</th></tr></thead><tbody>
              {d.items.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/incidents/${r.id}`} className="mono">{r.id}</Link><div className="muted" style={{ fontSize: 12 }}>{fmtTime(r.detectedAt)}</div></td>
                  <td><SeverityBadge s={r.severity} /></td><td>{t(r.status)}</td>
                  <td style={{ maxWidth: 480 }}>{r.reportSummary}</td>
                  <td>v{r.reportVersion}{r.llmModel ? ` · ${t("AI narrative")}` : ""}<div className="muted" style={{ fontSize: 12 }}>{fmtTime(r.reportCreatedAt)} ({t("real")})</div></td>
                  <td><div className="row" style={{ gap: 4 }}>
                    <a className="btn sm" href={`/api/incidents/${r.id}/report?format=md`} download><Download size={12} /> MD</a>
                    <a className="btn sm" href={`/api/incidents/${r.id}/report?format=html`} target="_blank" rel="noreferrer">HTML / PDF</a>
                  </div></td>
                </tr>))}
            </tbody></table></div>
            <Pager page={d.page} pageSize={d.pageSize} total={d.total} onPage={setPage} />
          </>)}
        </DataState>
      </Card>
    </>
  );
}
