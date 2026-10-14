import { useState } from "react";
import { Link } from "react-router-dom";
import { Reading, Sensor } from "../api";
import { Card, DataState, Empty, GhostIcon, Seg, SensorStatusBadge, SimulatedBadge, VirtualBadge } from "../components";
import { ageAt, fmtNum, fmtTime, useApi, useClock } from "../lib";
import { SensorHistoryChart } from "./Sensors";
import { t } from "../i18n";

interface VItem {
  sensor: Sensor; reading: Reading; physicalStatus: string; recovered: boolean;
  prediction: { targetAt: string; value: number | null; lower: number | null; upper: number | null; intervalLevel: number | null; quality: string;
    qualityReasons: string[]; modelVersion: string; gapS: number | null; incidentId: string | null;
    inputs: { neighbours?: { room: string; sensorId: string | null; value: number | null; weight: number }[]; neighbourCountAvailable?: number;
      lastValidTs?: number | null; lastValidValue?: number | null; reference?: { kind: string; value: number | null }; floorMean?: number | null; buildingMean?: number | null; roomOffset7d?: number | null } };
  history: { t: string; value: number | null; lower: number | null; upper: number | null; quality: string; actual: number | null }[];
  baseline7d: { mean: number; min: number; max: number; n: number } | null;
}
interface VList { active: VItem[]; recentlyRecovered: VItem[]; model: { status: string; version?: string; testMae?: number; testRmse?: number; intervalCoverage90?: number; error?: string } }
interface Validation {
  offlineEvaluation: { model: { mae: number; rmse: number; n: number }; baselines: Record<string, { mae: number; rmse: number }>; by_gap: Record<string, { n: number; mae: number; rmse: number; coverage_90: number; persistence_mae: number }>;
    interval_coverage_90: number; mean_interval_halfwidth: number; unseen_rooms: { mae: number; rmse: number; rooms_held_out: number }; test_period: string } | null;
  modelVersion: string | null; liveValidation: { n: number; mae: number; rmse: number; coverage: number; note: string } | null;
  usage: { n: number; sensors: number; good: number; degraded: number; insufficient: number };
  featureImportances: [string, number][] | null;
}

export default function GhostMonitor() {
  const q = useApi<VList>(["virtual"], "/api/virtual-sensors", { refetchInterval: 15000 });
  const [tab, setTab] = useState<"active" | "recovered">("active");
  return (
    <>
      <div className="page-head">
        <div><h1 className="row"><GhostIcon size={22} /> {t("Ghost sensor monitor")}</h1>
          <p>{t("Virtual temperature sensors replacing silent or invalid physical sensors. Values are model estimates from physical neighbour readings and the room's own history - never physical measurements.")}</p></div>
      </div>
      <DataState q={q}>
        {(d) => (
          <>
            <div className="kpis">
              <div className="card kpi"><div className="label"><GhostIcon size={14} />{t("Active virtual sensors")}</div><div className="value">{d.active.filter((a) => a.prediction.value != null).length}</div><div className="hint">{d.active.filter((a) => a.prediction.value == null).length} {t("without a reliable estimate")}</div></div>
              <div className="card kpi"><div className="label">{t("Model")}</div><div className="value" style={{ fontSize: 15 }}>{d.model.status === "OK" ? <span className="mono">{d.model.version}</span> : d.model.error}</div></div>
              <div className="card kpi"><div className="label">{t("Held-out MAE / RMSE")}</div><div className="value">{fmtNum(d.model.testMae, 2)} / {fmtNum(d.model.testRmse, 2)} °C</div><div className="hint">{t("May-June 2026, simulated outages on real data")}</div></div>
              <div className="card kpi"><div className="label">{t("90 % interval coverage")}</div><div className="value">{d.model.intervalCoverage90 != null ? `${Math.round(d.model.intervalCoverage90 * 100)} %` : "-"}</div><div className="hint">{t("empirical, on held-out data")}</div></div>
            </div>
            <Seg label={t("Virtual sensor list")} value={tab} onChange={setTab} items={[{ id: "active", label: `${t("Active")} (${d.active.length})` }, { id: "recovered", label: `${t("Recently recovered")} (${d.recentlyRecovered.length})` }]} />
            {(tab === "active" ? d.active : d.recentlyRecovered).length === 0
              ? <Card><Empty>{tab === "active" ? t("No physical sensor needs a replacement right now. Inject a simulated outage from Workflows & health to see one.") : t("No recovery in the last 2 days of building time.")}</Empty></Card>
              : <div className="grid cols-2">{(tab === "active" ? d.active : d.recentlyRecovered).map((v) => <VirtualCard key={v.sensor.id} v={v} />)}</div>}
          </>
        )}
      </DataState>
      <ValidationPanel />
    </>
  );
}

function VirtualCard({ v }: { v: VItem }) {
  const clock = useClock();
  const p = v.prediction;
  const inp = p.inputs;
  return (
    <section className={`card ${v.recovered ? "" : "ghost-card"}`}>
      <div className="card-head">
        <h2 className="mono">{v.sensor.id}</h2>
        {v.recovered ? <span className="badge physical">{t("Physical sensor recovered")}</span> : p.value != null ? <VirtualBadge /> : <span className="badge">{t("No reliable estimate")}</span>}
        {v.reading.simulatedFault && <SimulatedBadge />}
        <div className="right"><Link to={`/twin?room=${encodeURIComponent(v.sensor.roomId ?? "")}`}>{v.sensor.roomId}</Link></div>
      </div>
      <div className="card-body stack" style={{ gap: 12 }}>
        <div className="row" style={{ alignItems: "flex-end", gap: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: 12 }}>{v.recovered ? t("Last inferred value") : t("Predicted replacement value")}</div>
            <div className="ghost-value" style={{ color: "var(--ghost)" }}>{p.value != null ? `${fmtNum(p.value)} °C` : "-"}</div>
            {p.lower != null && p.upper != null && <div className="secondary">{t("90 % interval")} {fmtNum(p.lower)} - {fmtNum(p.upper)} °C</div>}
          </div>
          <dl className="kv" style={{ marginLeft: "auto" }}>
            <dt>{t("Physical sensor")}</dt><dd><SensorStatusBadge s={v.physicalStatus} /></dd>
            <dt>{t("Quality")}</dt><dd>{t(p.quality)}</dd>
            <dt>{t("Inferred at")}</dt><dd>{fmtTime(p.targetAt)}</dd>
            <dt>{t("Outage so far")}</dt><dd>{p.gapS != null ? `${(p.gapS / 3600).toFixed(1)} h` : inp.lastValidTs == null ? t("no valid history") : "-"}</dd>
          </dl>
        </div>
        {p.qualityReasons.length > 0 && <div className="banner warn">{p.qualityReasons.join(" · ")}</div>}
        <div className="grid cols-2" style={{ gap: 12 }}>
          <div>
            <h3>{t("Neighbour inputs ({n} reporting)", { n: inp.neighbourCountAvailable ?? 0 })}</h3>
            <table className="table"><tbody>
              {(inp.neighbours ?? []).slice(0, 8).map((n) => <tr key={n.room}><td>{n.room}</td><td className="num">{n.value != null ? `${fmtNum(n.value)} °C` : <span className="muted">{t("not reporting")}</span>}</td><td className="num muted" title={t("adjacency weight (shared wall m + doors)")}>w {fmtNum(n.weight)}</td></tr>)}
              {(inp.neighbours ?? []).length === 0 && <tr><td className="muted">{t("No instrumented neighbour")}</td></tr>}
            </tbody></table>
          </div>
          <dl className="kv">
            <dt>{t("Reference")}</dt><dd>{inp.reference?.kind?.replace(/_/g, " ")} {fmtNum(inp.reference?.value)} °C</dd>
            <dt>{t("Last physical")}</dt><dd>{inp.lastValidValue != null ? `${fmtNum(inp.lastValidValue)} °C` : "-"}</dd>
            <dt>{t("7-day baseline")}</dt><dd>{v.baseline7d ? `${fmtNum(v.baseline7d.mean)} °C (${fmtNum(v.baseline7d.min)}-${fmtNum(v.baseline7d.max)})` : "-"}</dd>
            <dt>{t("Room offset")}</dt><dd>{inp.roomOffset7d != null ? `${fmtNum(inp.roomOffset7d, 2)} °C ${t("vs neighbours")}` : "-"}</dd>
            <dt>{t("Floor / building")}</dt><dd>{fmtNum(inp.floorMean)} / {fmtNum(inp.buildingMean)} °C</dd>
            <dt>{t("Model")}</dt><dd className="mono" style={{ fontSize: 11.5 }}>{p.modelVersion}</dd>
            <dt>{t("Incident")}</dt><dd>{p.incidentId ? <Link to={`/incidents/${p.incidentId}`}>{p.incidentId}</Link> : "-"}</dd>
            <dt>{t("Last transmission")}</dt><dd>{t("{d} ago", { d: ageAt(v.reading.lastTransmissionAt, clock.data?.now) })}</dd>
          </dl>
        </div>
        <SensorHistoryChart id={v.sensor.id} hours={24} />
      </div>
    </section>
  );
}

function ValidationPanel() {
  const q = useApi<Validation>(["validation"], "/api/virtual-sensors/validation", { refetchInterval: 60000 });
  return (
    <Card title={t("Model validation")} sub={t("Random Forest on residuals; outages simulated on real held-out data")}>
      <DataState q={q}>
        {(d) => !d.offlineEvaluation ? <Empty>{t("Model not trained.")}</Empty> : (
          <div className="grid cols-2">
            <div>
              <h3>{t("Held-out test")} ({d.offlineEvaluation.test_period})</h3>
              <table className="table"><thead><tr><th>{t("Method")}</th><th className="num">MAE °C</th><th className="num">RMSE °C</th></tr></thead><tbody>
                <tr><td><strong>{t("Ghost Sensor model")}</strong></td><td className="num"><strong>{d.offlineEvaluation.model.mae}</strong></td><td className="num"><strong>{d.offlineEvaluation.model.rmse}</strong></td></tr>
                {Object.entries(d.offlineEvaluation.baselines).map(([k, m]) => <tr key={k}><td className="secondary">{k.replace(/_/g, " ")}</td><td className="num">{m.mae}</td><td className="num">{m.rmse}</td></tr>)}
                <tr><td className="secondary">{t("Model on {n} unseen rooms", { n: d.offlineEvaluation.unseen_rooms.rooms_held_out })}</td><td className="num">{d.offlineEvaluation.unseen_rooms.mae}</td><td className="num">{d.offlineEvaluation.unseen_rooms.rmse}</td></tr>
              </tbody></table>
            </div>
            <div>
              <h3>{t("By outage length")}</h3>
              <table className="table"><thead><tr><th>{t("Gap")}</th><th className="num">n</th><th className="num">MAE</th><th className="num">{t("Persistence MAE")}</th><th className="num">{t("Coverage")}</th></tr></thead><tbody>
                {Object.entries(d.offlineEvaluation.by_gap).map(([k, m]) => <tr key={k}><td>{k}</td><td className="num">{m.n}</td><td className="num">{m.mae}</td><td className="num">{m.persistence_mae}</td><td className="num">{Math.round(m.coverage_90 * 100)} %</td></tr>)}
              </tbody></table>
              <p className="muted" style={{ fontSize: 12 }}>
                {t("Interval: split-conformal, 90 % nominal; empirical coverage {c} %, mean half-width {w} °C.", { c: Math.round(d.offlineEvaluation.interval_coverage_90 * 100), w: d.offlineEvaluation.mean_interval_halfwidth })}{" "}
                {t("It is derived from validation residuals and is not the probability that a single estimate is correct.")}
              </p>
              <p className="secondary" style={{ fontSize: 12.5 }}>
                {t("Live validation (simulated outages, true value known)")}: {d.liveValidation ? `n=${d.liveValidation.n}, MAE ${d.liveValidation.mae} °C, ${t("coverage")} ${Math.round(d.liveValidation.coverage * 100)} %` : t("no simulated outage evaluated yet")}.{" "}
                {t("Usage: {n} inferred readings for {s} sensors ({g} good, {dg} degraded, {i} insufficient).", { n: d.usage.n, s: d.usage.sensors, g: d.usage.good ?? 0, dg: d.usage.degraded ?? 0, i: d.usage.insufficient ?? 0 })}
              </p>
            </div>
          </div>
        )}
      </DataState>
    </Card>
  );
}
