import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, CircleSlash, FlaskConical, Mail, Play, RotateCcw } from "lucide-react";
import { AgentRun, api, Health } from "../api";
import { Card, ConfirmDialog, DataState, SimulatedBadge } from "../components";
import { fmtEpoch, fmtTime, useAction, useApi, useClock, useOperator } from "../lib";
import { t } from "../i18n";

interface Workflows {
  n8n: { baseUrl: string | null; reachable: boolean; httpStatus?: number; error?: string; lastHeartbeat: { at: string; workflow: string; executionId: string } | null };
  orchestrationMode: string; localFallbackAfterS: number; n8nExecutions: { id: string; workflowId: string; status: string; mode: string; startedAt: string; stoppedAt: string | null }[] | { error: string } | null;
  n8nExecutionsNote: string | null; pendingIncidents: number; pending: { id: string; pipeline_version: number; processed_version: number; severity: string; claimed_by: string | null }[];
  agents: Record<string, { agent: string; total: number; failed: number; invalid: number; last_real: number }>;
  recentRuns: AgentRun[]; events: { id: number; source: string; workflow: string | null; execution_id: string | null; node: string | null; level: string; kind: string; message: string; at: string }[];
  failedRuns24h: number;
}
interface Fault { id: number; sensor_id: string; kind: string; startAt: string; endAt: string | null; active: boolean; note: string | null; created_by: string }

function StatusIcon({ s }: { s: string }) {
  if (s === "OK") return <CheckCircle2 size={16} color="var(--good)" aria-label="OK" />;
  if (s === "NOT_CONFIGURED") return <CircleSlash size={16} color="var(--info)" aria-label={t("Not configured")} />;
  return <AlertTriangle size={16} color={s === "DEGRADED" || s === "UNREACHABLE" ? "var(--warning)" : "var(--critical)"} aria-label={s} />;
}

const COMPONENT_LABEL: Record<string, string> = { database: "Database", engine: "Detection engine", mlModel: "ML inference", n8n: "n8n automation", llm: "LLM provider", email: "Email provider" };

export default function SystemHealth() {
  const h = useApi<Health>(["health"], "/api/health", { refetchInterval: 10000 });
  const w = useApi<Workflows>(["workflows"], "/api/system/workflows", { refetchInterval: 15000 });
  return (
    <>
      <div className="page-head"><div><h1>{t("Workflows & system health")}</h1><p>{t("Backend, ML, database, n8n orchestration, agents and notification delivery. Core monitoring continues even if n8n, the LLM or email are unavailable.")}</p></div></div>
      <DataState q={h} lines={3}>
        {(d) => (
          <>
            {d.status !== "OK" && <div className={`banner ${d.coreMonitoring === "OK" ? "warn" : "err"}`}><AlertTriangle size={16} />{t("Overall status {s}. Core monitoring: {c}. See the components below for recovery steps.", { s: t(d.status), c: t(d.coreMonitoring) })}</div>}
            <div className="kpis">
              {Object.entries(d.components).map(([k, c]) => (
                <div key={k} className="card kpi">
                  <div className="label"><StatusIcon s={c.status} />{t(COMPONENT_LABEL[k] ?? k)}</div>
                  <div className="value" style={{ fontSize: 16 }}>{t(c.status)}</div>
                  <div className="hint">{componentHint(k, c)}</div>
                </div>
              ))}
              <div className="card kpi"><div className="label">{t("Last sensor update")}</div><div className="value" style={{ fontSize: 16 }}>{fmtTime(d.lastSensorUpdate)}</div><div className="hint">{t("building time")}</div></div>
              <div className="card kpi"><div className="label">{t("Last detection (Agent 1)")}</div><div className="value" style={{ fontSize: 16 }}>{d.lastDetection ? fmtEpoch(d.lastDetection.evaluatedAt) : "-"}</div>
                <div className="hint">{d.lastDetection ? `${d.lastDetection.durationMs} ms · ${t("via")} ${d.lastDetection.trigger}` : t("not run yet")}</div></div>
            </div>
          </>
        )}
      </DataState>

      <DataState q={w} lines={6}>
        {(d) => (
          <>
            <div className="grid cols-2">
              <Card title={t("n8n orchestration")} sub={`${t("mode")}: ${d.orchestrationMode}`}>
                <dl className="kv">
                  <dt>{t("n8n URL")}</dt><dd className="mono">{d.n8n.baseUrl ?? t("not configured")}</dd>
                  <dt>{t("Reachable")}</dt><dd>{d.n8n.reachable ? t("yes") : `${t("no")}${d.n8n.error ? ` (${d.n8n.error})` : ""}`}</dd>
                  <dt>{t("Last call from n8n")}</dt><dd>{d.n8n.lastHeartbeat ? `${fmtTime(d.n8n.lastHeartbeat.at)} · ${d.n8n.lastHeartbeat.workflow} #${d.n8n.lastHeartbeat.executionId}` : t("never (workflows not active yet?)")}</dd>
                  <dt>{t("Pending incidents")}</dt><dd>{t("{n} awaiting agents 2-4", { n: d.pendingIncidents })}</dd>
                  <dt>{t("Local fallback")}</dt><dd>{t("after {n} s without n8n processing", { n: d.localFallbackAfterS })}</dd>
                  <dt>{t("Failed runs (24 h)")}</dt><dd>{d.failedRuns24h}</dd>
                </dl>
                {!d.n8n.reachable && <div className="banner warn" style={{ marginTop: 10 }}>{t("n8n is not reachable from the backend. Incidents are still processed by the local fallback. Recovery: start n8n in WSL")} (<code>n8n start</code>) {t("and check")} <code>N8N_BASE_URL</code>.</div>}
              </Card>
              <Card title={t("Agents")} sub={t("all executions")}>
                <table className="table"><thead><tr><th>{t("Agent")}</th><th className="num">{t("Runs")}</th><th className="num">{t("Failed")}</th><th className="num">{t("Invalid output")}</th><th>{t("Last run (real)")}</th></tr></thead><tbody>
                  {["DETECTION", "DOCUMENTATION", "ANALYSIS", "NOTIFICATION"].map((k) => { const a = d.agents[k]; return (
                    <tr key={k}><td>{t(k)}</td><td className="num">{a?.total ?? 0}</td><td className="num">{a?.failed ?? 0}</td><td className="num">{a?.invalid ?? 0}</td><td>{a ? fmtEpoch(a.last_real) : "-"}</td></tr>); })}
                </tbody></table>
              </Card>
            </div>
            <Card title={t("n8n executions")} sub={t("metadata only")}>
              {Array.isArray(d.n8nExecutions) ? (
                <table className="table"><thead><tr><th>{t("Execution")}</th><th>{t("Workflow")}</th><th>{t("Status")}</th><th>{t("Mode")}</th><th>{t("Started")}</th></tr></thead><tbody>
                  {d.n8nExecutions.map((e) => <tr key={e.id}><td>{e.id}</td><td className="mono">{e.workflowId}</td><td>{e.status}</td><td>{e.mode}</td><td>{fmtTime(e.startedAt)}</td></tr>)}</tbody></table>
              ) : <p className="muted" style={{ margin: 0 }}>{d.n8nExecutions && "error" in d.n8nExecutions ? `${t("n8n API error")}: ${d.n8nExecutions.error}` : d.n8nExecutionsNote}</p>}
            </Card>
            <div className="grid cols-2">
              <Card title={t("Recent agent runs")}>
                <div className="table-wrap" style={{ maxHeight: 380 }}><table className="table"><thead><tr><th>{t("Agent")}</th><th>{t("Incident")}</th><th>{t("Status")}</th><th>{t("Trigger")}</th><th>{t("When (real)")}</th></tr></thead><tbody>
                  {d.recentRuns.map((r) => <tr key={r.id}><td>{t(r.agent)}</td><td>{r.incident_id ? <Link to={`/incidents/${r.incident_id}`}>{r.incident_id}</Link> : "-"}</td>
                    <td title={r.error ?? r.summary ?? ""}>{t(r.status)}</td><td>{r.trigger}{r.n8n_execution_id ? ` #${r.n8n_execution_id}` : ""}</td><td>{fmtTime(r.startedAt)}</td></tr>)}
                </tbody></table></div>
              </Card>
              <Card title={t("Workflow & error events")} sub={t("from Workflow E and the backend")}>
                <div className="table-wrap" style={{ maxHeight: 380 }}><table className="table"><thead><tr><th>{t("When")}</th><th>{t("Level")}</th><th>{t("Source")}</th><th>{t("Event")}</th></tr></thead><tbody>
                  {d.events.map((e) => <tr key={e.id}><td>{fmtTime(e.at)}</td><td style={{ color: e.level === "ERROR" ? "var(--critical-ink)" : undefined }}>{e.level}</td><td>{e.source}{e.workflow ? ` · ${e.workflow}` : ""}</td><td><strong>{e.kind}</strong> <span className="secondary">{e.message}</span></td></tr>)}
                </tbody></table></div>
              </Card>
            </div>
          </>
        )}
      </DataState>
      <OperatorTools />
    </>
  );
}

function componentHint(k: string, c: Record<string, any>): string {
  if (k === "database") return c.path ?? "";
  if (k === "engine") return c.lastError ?? `${t("last tick")} ${fmtTime(c.lastTickReal, false)} (${t("real")})`;
  if (k === "mlModel") return c.version ?? c.error ?? "";
  if (k === "n8n") return c.reachable ? c.baseUrl : `${t("unreachable")}: ${c.error ?? c.httpStatus ?? ""}`;
  if (k === "llm") return c.configured ? `${c.provider} · ${c.model}` : t("optional - agents run without it");
  if (k === "email") return c.configured ? t("{m} mode · {n} recipient(s)", { m: c.mode, n: c.recipients }) : t("{m} mode · missing {x}", { m: c.mode, x: c.missing.join(", ") });
  return "";
}

function OperatorTools() {
  const { token } = useOperator();
  const clock = useClock();
  const act = useAction();
  const faults = useApi<{ items: Fault[] }>(["faults"], "/api/simulation/faults");
  const [sensorId, setSensorId] = useState("T-02-14-N");
  const [kind, setKind] = useState("SILENCE");
  const [duration, setDuration] = useState("");
  const [delta, setDelta] = useState("5");
  const [speed, setSpeed] = useState("60");
  const [jump, setJump] = useState("");
  const [confirm, setConfirm] = useState<null | "reset" | "inject" | "email">(null);
  if (!token) return <Card title={t("Operator tools")}><p className="muted" style={{ margin: 0 }}>{t("Sign in as operator (top-right) to control the replay, inject simulated faults or send a test notification.")}</p></Card>;
  const c = clock.data;
  return (
    <div className="grid cols-2">
      <Card title={t("Replay clock")} sub={c?.label}>
        <div className="stack">
          <div className="row"><span className="mono">{fmtEpoch(c?.now)}</span><span className="muted">{c?.paused ? t("paused") : `x${c?.speed}`}</span></div>
          <div className="row">
            <button className="btn sm" onClick={() => act(() => api.post("/api/clock", { action: c?.paused ? "resume" : "pause" }), t("Clock updated"))}><Play size={13} /> {c?.paused ? t("Resume") : t("Pause")}</button>
            <select className="input" value={speed} onChange={(e) => setSpeed(e.target.value)} aria-label={t("Replay speed")}>{["1", "10", "60", "300", "900"].map((s) => <option key={s} value={s}>x{s}</option>)}</select>
            <button className="btn sm" onClick={() => act(() => api.post("/api/clock", { action: "speed", speed: Number(speed) }), `${t("Speed")} x${speed}`)}>{t("Set speed")}</button>
          </div>
          <div className="row">
            <input className="input" type="datetime-local" value={jump} onChange={(e) => setJump(e.target.value)} aria-label={t("Jump to (UTC)")} />
            <button className="btn sm" disabled={!jump} onClick={() => act(() => api.post("/api/clock", { action: "jump", toTs: Math.floor(Date.parse(jump + ":00Z") / 1000) }), t("Jumped forward"))}>{t("Jump forward (UTC)")}</button>
          </div>
          <div className="row">
            <button className="btn sm" onClick={() => act(() => api.post("/api/detection/run"), t("Detection run"))}>{t("Run Agent 1 now")}</button>
            <button className="btn sm" onClick={() => setConfirm("email")}><Mail size={13} /> {t("Send test notification")}</button>
            <button className="btn sm danger" onClick={() => setConfirm("reset")}><RotateCcw size={13} /> {t("Reset replay")}</button>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>{t("Tip: the real historian contains a building-wide outage on 2026-03-03 (~09:45-16:00 UTC) and recurring ground-floor (N0) outages in January.")}</p>
        </div>
      </Card>
      <Card title={<span className="row"><FlaskConical size={16} /> {t("Simulated faults")} <SimulatedBadge /></span>} sub={t("overlay only - historian data is never modified")}>
        <div className="stack">
          <div className="row">
            <label className="field">{t("Sensor id")}<input className="input" value={sensorId} onChange={(e) => setSensorId(e.target.value)} /></label>
            <label className="field">{t("Fault")}<select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="SILENCE">{t("Silence (stop transmitting)")}</option><option value="NULL_VALUES">{t("Invalid values")}</option><option value="OFFSET">{t("Offset / drift")}</option><option value="SPIKE">{t("Single spike")}</option></select></label>
            <label className="field">{t("Duration min (empty = until ended)")}<input className="input" type="number" min={15} value={duration} onChange={(e) => setDuration(e.target.value)} /></label>
            {(kind === "OFFSET" || kind === "SPIKE") && <label className="field">{t("Delta")}<input className="input" type="number" value={delta} onChange={(e) => setDelta(e.target.value)} /></label>}
          </div>
          <div><button className="btn primary sm" disabled={!sensorId} onClick={() => setConfirm("inject")}>{t("Inject fault")}</button></div>
          <DataState q={faults} empty={(d) => d.items.length === 0}>
            {(d) => <table className="table"><thead><tr><th>{t("Sensor")}</th><th>{t("Fault")}</th><th>{t("From")}</th><th>{t("To")}</th><th /></tr></thead><tbody>
              {d.items.slice(0, 12).map((f) => <tr key={f.id}><td className="mono">{f.sensor_id}</td><td>{t(f.kind)}</td><td>{fmtTime(f.startAt)}</td><td>{f.endAt ? fmtTime(f.endAt) : t("open")}</td>
                <td>{f.active && <button className="btn sm" onClick={() => act(() => api.del(`/api/simulation/faults/${f.id}`), t("Fault ended: sensor recovers on next tick"))}>{t("End (recover)")}</button>}</td></tr>)}
            </tbody></table>}
          </DataState>
        </div>
      </Card>
      {confirm === "inject" && <ConfirmDialog title={t("Inject a simulated fault?")} confirmLabel={t("Inject")} onClose={() => setConfirm(null)}
        body={<>{t("A fault of type")} <strong>{t(kind)}</strong> {t("is overlaid on")} <code>{sensorId}</code> {t("from the current building time. Everything it causes is labelled SIMULATED; notifications may be produced according to policy.")}</>}
        onConfirm={() => act(() => api.post("/api/simulation/faults", { sensorId, kind, durationMinutes: duration ? Number(duration) : null, delta: kind === "OFFSET" || kind === "SPIKE" ? Number(delta) : null, note: "dashboard" }), t("Simulated fault injected")).then(() => undefined)} />}
      {confirm === "email" && <ConfirmDialog title={t("Send a test notification?")} confirmLabel={t("Send test")} onClose={() => setConfirm(null)}
        body={t("Builds an email from the latest incident. In test mode it is written to the outbox only; in SMTP mode it is really sent to ALERT_RECIPIENTS.")}
        onConfirm={() => act(() => api.post("/api/notifications/test", { confirm: true }), t("Test notification processed - see delivery status")).then(() => undefined)} />}
      {confirm === "reset" && <ConfirmDialog title={t("Reset the replay?")} confirmLabel={t("Reset")} danger requireText="RESET" onClose={() => setConfirm(null)}
        body={t("Deletes all incidents, reports, analyses, predictions, notifications, simulated faults and agent runs, then restarts the replay (paused). The historian and topology are untouched. This cannot be undone.")}
        onConfirm={() => act(() => api.post("/api/admin/reset", { confirm: "RESET" }), t("Replay reset")).then(() => undefined)} />}
    </div>
  );
}
