import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, CircleSlash, FlaskConical, Mail, Play, RotateCcw } from "lucide-react";
import { AgentRun, api, Health } from "../api";
import { Card, ConfirmDialog, DataState, SimulatedBadge } from "../components";
import { fmtEpoch, fmtTime, useAction, useApi, useClock, useOperator } from "../lib";

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
  if (s === "NOT_CONFIGURED") return <CircleSlash size={16} color="var(--info)" aria-label="Not configured" />;
  return <AlertTriangle size={16} color={s === "DEGRADED" || s === "UNREACHABLE" ? "var(--warning)" : "var(--critical)"} aria-label={s} />;
}

const COMPONENT_LABEL: Record<string, string> = { database: "Database", engine: "Detection engine", mlModel: "ML inference", n8n: "n8n automation", llm: "LLM provider", email: "Email provider" };

export default function SystemHealth() {
  const h = useApi<Health>(["health"], "/api/health", { refetchInterval: 10000 });
  const w = useApi<Workflows>(["workflows"], "/api/system/workflows", { refetchInterval: 15000 });
  return (
    <>
      <div className="page-head"><div><h1>Workflows & system health</h1><p>Backend, ML, database, n8n orchestration, agents and notification delivery. Core monitoring continues even if n8n, the LLM or email are unavailable.</p></div></div>
      <DataState q={h} lines={3}>
        {(d) => (
          <>
            {d.status !== "OK" && <div className={`banner ${d.coreMonitoring === "OK" ? "warn" : "err"}`}><AlertTriangle size={16} />Overall status {d.status}. Core monitoring: {d.coreMonitoring}. See the components below for recovery steps.</div>}
            <div className="kpis">
              {Object.entries(d.components).map(([k, c]) => (
                <div key={k} className="card kpi">
                  <div className="label"><StatusIcon s={c.status} />{COMPONENT_LABEL[k] ?? k}</div>
                  <div className="value" style={{ fontSize: 16 }}>{c.status.replace("_", " ")}</div>
                  <div className="hint">{componentHint(k, c)}</div>
                </div>
              ))}
              <div className="card kpi"><div className="label">Last sensor update</div><div className="value" style={{ fontSize: 16 }}>{fmtTime(d.lastSensorUpdate)}</div><div className="hint">building time</div></div>
              <div className="card kpi"><div className="label">Last detection (Agent 1)</div><div className="value" style={{ fontSize: 16 }}>{d.lastDetection ? fmtEpoch(d.lastDetection.evaluatedAt) : "-"}</div>
                <div className="hint">{d.lastDetection ? `${d.lastDetection.durationMs} ms · via ${d.lastDetection.trigger}` : "not run yet"}</div></div>
            </div>
          </>
        )}
      </DataState>

      <DataState q={w} lines={6}>
        {(d) => (
          <>
            <div className="grid cols-2">
              <Card title="n8n orchestration" sub={`mode: ${d.orchestrationMode}`}>
                <dl className="kv">
                  <dt>n8n URL</dt><dd className="mono">{d.n8n.baseUrl ?? "not configured"}</dd>
                  <dt>Reachable</dt><dd>{d.n8n.reachable ? "yes" : `no${d.n8n.error ? ` (${d.n8n.error})` : ""}`}</dd>
                  <dt>Last call from n8n</dt><dd>{d.n8n.lastHeartbeat ? `${fmtTime(d.n8n.lastHeartbeat.at)} · ${d.n8n.lastHeartbeat.workflow} #${d.n8n.lastHeartbeat.executionId}` : "never (workflows not active yet?)"}</dd>
                  <dt>Pending incidents</dt><dd>{d.pendingIncidents} awaiting agents 2-4</dd>
                  <dt>Local fallback</dt><dd>after {d.localFallbackAfterS} s without n8n processing</dd>
                  <dt>Failed runs (24 h)</dt><dd>{d.failedRuns24h}</dd>
                </dl>
                {!d.n8n.reachable && <div className="banner warn" style={{ marginTop: 10 }}>n8n is not reachable from the backend. Incidents are still processed by the local fallback. Recovery: start n8n in WSL (<code>n8n start</code>) and check <code>N8N_BASE_URL</code>.</div>}
              </Card>
              <Card title="Agents" sub="all executions">
                <table className="table"><thead><tr><th>Agent</th><th className="num">Runs</th><th className="num">Failed</th><th className="num">Invalid output</th><th>Last run (real)</th></tr></thead><tbody>
                  {["DETECTION", "DOCUMENTATION", "ANALYSIS", "NOTIFICATION"].map((k) => { const a = d.agents[k]; return (
                    <tr key={k}><td>{k}</td><td className="num">{a?.total ?? 0}</td><td className="num">{a?.failed ?? 0}</td><td className="num">{a?.invalid ?? 0}</td><td>{a ? fmtEpoch(a.last_real) : "-"}</td></tr>); })}
                </tbody></table>
              </Card>
            </div>
            <Card title="n8n executions" sub="metadata only">
              {Array.isArray(d.n8nExecutions) ? (
                <table className="table"><thead><tr><th>Execution</th><th>Workflow</th><th>Status</th><th>Mode</th><th>Started</th></tr></thead><tbody>
                  {d.n8nExecutions.map((e) => <tr key={e.id}><td>{e.id}</td><td className="mono">{e.workflowId}</td><td>{e.status}</td><td>{e.mode}</td><td>{fmtTime(e.startedAt)}</td></tr>)}</tbody></table>
              ) : <p className="muted" style={{ margin: 0 }}>{d.n8nExecutions && "error" in d.n8nExecutions ? `n8n API error: ${d.n8nExecutions.error}` : d.n8nExecutionsNote}</p>}
            </Card>
            <div className="grid cols-2">
              <Card title="Recent agent runs">
                <div className="table-wrap" style={{ maxHeight: 380 }}><table className="table"><thead><tr><th>Agent</th><th>Incident</th><th>Status</th><th>Trigger</th><th>When (real)</th></tr></thead><tbody>
                  {d.recentRuns.map((r) => <tr key={r.id}><td>{r.agent}</td><td>{r.incident_id ? <Link to={`/incidents/${r.incident_id}`}>{r.incident_id}</Link> : "-"}</td>
                    <td title={r.error ?? r.summary ?? ""}>{r.status}</td><td>{r.trigger}{r.n8n_execution_id ? ` #${r.n8n_execution_id}` : ""}</td><td>{fmtTime(r.startedAt)}</td></tr>)}
                </tbody></table></div>
              </Card>
              <Card title="Workflow & error events" sub="from Workflow E and the backend">
                <div className="table-wrap" style={{ maxHeight: 380 }}><table className="table"><thead><tr><th>When</th><th>Level</th><th>Source</th><th>Event</th></tr></thead><tbody>
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
  if (k === "engine") return c.lastError ?? `last tick ${fmtTime(c.lastTickReal, false)} (real)`;
  if (k === "mlModel") return c.version ?? c.error ?? "";
  if (k === "n8n") return c.reachable ? c.baseUrl : `unreachable: ${c.error ?? c.httpStatus ?? ""}`;
  if (k === "llm") return c.configured ? `${c.provider} · ${c.model}` : "optional - agents run without it";
  if (k === "email") return c.configured ? `${c.mode} mode · ${c.recipients} recipient(s)` : `${c.mode} mode · missing ${c.missing.join(", ")}`;
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
  if (!token) return <Card title="Operator tools"><p className="muted" style={{ margin: 0 }}>Sign in as operator (top-right) to control the replay, inject simulated faults or send a test notification.</p></Card>;
  const c = clock.data;
  return (
    <div className="grid cols-2">
      <Card title="Replay clock" sub={c?.label}>
        <div className="stack">
          <div className="row"><span className="mono">{fmtEpoch(c?.now)}</span><span className="muted">{c?.paused ? "paused" : `x${c?.speed}`}</span></div>
          <div className="row">
            <button className="btn sm" onClick={() => act(() => api.post("/api/clock", { action: c?.paused ? "resume" : "pause" }), "Clock updated")}><Play size={13} /> {c?.paused ? "Resume" : "Pause"}</button>
            <select className="input" value={speed} onChange={(e) => setSpeed(e.target.value)} aria-label="Replay speed">{["1", "10", "60", "300", "900"].map((s) => <option key={s} value={s}>x{s}</option>)}</select>
            <button className="btn sm" onClick={() => act(() => api.post("/api/clock", { action: "speed", speed: Number(speed) }), `Speed x${speed}`)}>Set speed</button>
          </div>
          <div className="row">
            <input className="input" type="datetime-local" value={jump} onChange={(e) => setJump(e.target.value)} aria-label="Jump to (UTC)" />
            <button className="btn sm" disabled={!jump} onClick={() => act(() => api.post("/api/clock", { action: "jump", toTs: Math.floor(Date.parse(jump + ":00Z") / 1000) }), "Jumped forward")}>Jump forward (UTC)</button>
          </div>
          <div className="row">
            <button className="btn sm" onClick={() => act(() => api.post("/api/detection/run"), "Detection run")}>Run Agent 1 now</button>
            <button className="btn sm" onClick={() => setConfirm("email")}><Mail size={13} /> Send test notification</button>
            <button className="btn sm danger" onClick={() => setConfirm("reset")}><RotateCcw size={13} /> Reset replay</button>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>Tip: the real historian contains a building-wide outage on 2026-03-03 (~09:45-16:00 UTC) and recurring ground-floor (N0) outages in January.</p>
        </div>
      </Card>
      <Card title={<span className="row"><FlaskConical size={16} /> Simulated faults <SimulatedBadge /></span>} sub="overlay only - historian data is never modified">
        <div className="stack">
          <div className="row">
            <label className="field">Sensor id<input className="input" value={sensorId} onChange={(e) => setSensorId(e.target.value)} /></label>
            <label className="field">Fault<select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="SILENCE">Silence (stop transmitting)</option><option value="NULL_VALUES">Invalid values</option><option value="OFFSET">Offset / drift</option><option value="SPIKE">Single spike</option></select></label>
            <label className="field">Duration min (empty = until ended)<input className="input" type="number" min={15} value={duration} onChange={(e) => setDuration(e.target.value)} /></label>
            {(kind === "OFFSET" || kind === "SPIKE") && <label className="field">Delta<input className="input" type="number" value={delta} onChange={(e) => setDelta(e.target.value)} /></label>}
          </div>
          <div><button className="btn primary sm" disabled={!sensorId} onClick={() => setConfirm("inject")}>Inject fault</button></div>
          <DataState q={faults} empty={(d) => d.items.length === 0}>
            {(d) => <table className="table"><thead><tr><th>Sensor</th><th>Fault</th><th>From</th><th>To</th><th /></tr></thead><tbody>
              {d.items.slice(0, 12).map((f) => <tr key={f.id}><td className="mono">{f.sensor_id}</td><td>{f.kind}</td><td>{fmtTime(f.startAt)}</td><td>{f.endAt ? fmtTime(f.endAt) : "open"}</td>
                <td>{f.active && <button className="btn sm" onClick={() => act(() => api.del(`/api/simulation/faults/${f.id}`), "Fault ended: sensor recovers on next tick")}>End (recover)</button>}</td></tr>)}
            </tbody></table>}
          </DataState>
        </div>
      </Card>
      {confirm === "inject" && <ConfirmDialog title="Inject a simulated fault?" confirmLabel="Inject" onClose={() => setConfirm(null)}
        body={<>A <strong>{kind}</strong> fault is overlaid on <code>{sensorId}</code> from the current building time. Everything it causes is labelled SIMULATED; notifications may be produced according to policy.</>}
        onConfirm={() => act(() => api.post("/api/simulation/faults", { sensorId, kind, durationMinutes: duration ? Number(duration) : null, delta: kind === "OFFSET" || kind === "SPIKE" ? Number(delta) : null, note: "dashboard" }), "Simulated fault injected").then(() => undefined)} />}
      {confirm === "email" && <ConfirmDialog title="Send a test notification?" confirmLabel="Send test" onClose={() => setConfirm(null)}
        body="Builds an email from the latest incident. In test mode it is written to the outbox only; in SMTP mode it is really sent to ALERT_RECIPIENTS."
        onConfirm={() => act(() => api.post("/api/notifications/test", { confirm: true }), "Test notification processed - see delivery status").then(() => undefined)} />}
      {confirm === "reset" && <ConfirmDialog title="Reset the replay?" confirmLabel="Reset" danger requireText="RESET" onClose={() => setConfirm(null)}
        body="Deletes all incidents, reports, analyses, predictions, notifications, simulated faults and agent runs, then restarts the replay (paused). The historian and topology are untouched. This cannot be undone."
        onConfirm={() => act(() => api.post("/api/admin/reset", { confirm: "RESET" }), "Replay reset").then(() => undefined)} />}
    </div>
  );
}
