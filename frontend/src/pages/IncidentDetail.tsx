import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bot, CheckCircle2, Download, ExternalLink, RefreshCw, ThumbsDown, ThumbsUp, UserCheck } from "lucide-react";
import { api, Hypothesis, IncidentDetail as Detail, Recommendation } from "../api";
import { Card, ConfirmDialog, DataState, GhostIcon, IncidentStatusBadge, ReadingValue, SensorStatusBadge, SeverityBadge, SimulatedBadge, SourceBadge, Tabs } from "../components";
import { fmtNum, fmtTime, useAction, useApi, useOperator } from "../lib";

const NEXT: Record<string, { to: string; label: string; danger?: boolean }[]> = {
  OPEN: [{ to: "ACKNOWLEDGED", label: "Acknowledge" }, { to: "INVESTIGATING", label: "Mark investigating" }, { to: "RESOLVED", label: "Resolve" }],
  INVESTIGATING: [{ to: "ACKNOWLEDGED", label: "Acknowledge" }, { to: "RESOLVED", label: "Resolve" }],
  ACKNOWLEDGED: [{ to: "INVESTIGATING", label: "Back to investigating" }, { to: "RESOLVED", label: "Resolve" }],
  RESOLVED: [{ to: "CLOSED", label: "Close" }, { to: "OPEN", label: "Reopen", danger: true }],
  CLOSED: [],
};

type TabId = "overview" | "report" | "analysis" | "notifications" | "timeline";

export default function IncidentDetail() {
  const { id = "" } = useParams();
  const q = useApi<Detail>(["incident", id], `/api/incidents/${encodeURIComponent(id)}`, { refetchInterval: 10000 });
  const [tab, setTab] = useState<TabId>("overview");
  const [confirm, setConfirm] = useState<{ to: string; label: string; danger?: boolean } | null>(null);
  const act = useAction();
  const { token } = useOperator();

  return (
    <DataState q={q} lines={10}>
      {(d) => {
        const i = d.incident;
        return (
          <>
            <div className="page-head">
              <div>
                <div className="row" style={{ gap: 6 }}><Link to="/incidents">Incidents</Link><span className="muted">/</span><span className="mono">{i.id}</span></div>
                <h1>{i.title}</h1>
                <div className="row" style={{ marginTop: 6 }}>
                  <SeverityBadge s={i.severity} /><IncidentStatusBadge s={i.status} /><span className="badge">{i.category}</span>
                  {i.virtualSensorActive && <span className="badge virtual"><GhostIcon size={13} /> virtual sensor active</span>}
                  {i.simulated && <SimulatedBadge />}
                  <span className="muted" style={{ fontSize: 12 }}>pipeline: {i.pipelineStage}{i.processedVersion < i.pipelineVersion ? " (update pending)" : ""}</span>
                </div>
              </div>
              <div className="actions">
                {NEXT[i.status]?.map((n) => <button key={n.to} className={`btn ${n.danger ? "danger" : n.to === "RESOLVED" ? "primary" : ""}`} disabled={!token} title={token ? "" : "Operator sign-in required"} onClick={() => setConfirm(n)}>{n.label}</button>)}
                <button className="btn" disabled={!token} title="Re-run agents 2-4 now" onClick={() => act(() => api.post(`/api/incidents/${i.id}/reprocess`), "Agents re-run")}><RefreshCw size={14} /> Re-run agents</button>
                <Link className="btn" to={`/investigation/${i.id}`}><Bot size={14} /> AI investigation</Link>
              </div>
            </div>
            {confirm && (
              <ConfirmDialog title={`${confirm.label} ${i.id}?`} confirmLabel={confirm.label} danger={confirm.danger} withNote onClose={() => setConfirm(null)}
                body={<>The status changes from <strong>{i.status}</strong> to <strong>{confirm.to}</strong>. This is recorded in the timeline with your name{confirm.to === "RESOLVED" || confirm.to === "ACKNOWLEDGED" ? " and may trigger a notification by Agent 4" : ""}.</>}
                onConfirm={(note) => act(() => api.post(`/api/incidents/${i.id}/status`, { status: confirm.to, note: note || null }), `Incident ${confirm.to.toLowerCase()}`).then(() => undefined)} />
            )}
            <Tabs value={tab} onChange={setTab} items={[
              { id: "overview", label: "Overview & evidence" }, { id: "report", label: `Technical report${d.report ? ` v${d.report.version}` : ""}` },
              { id: "analysis", label: `Root cause & actions${d.analysis ? ` v${d.analysis.version}` : ""}` }, { id: "notifications", label: `Notifications (${d.notifications.length})` },
              { id: "timeline", label: `Timeline (${d.timeline.length})` },
            ]} />
            {tab === "overview" && <OverviewTab d={d} />}
            {tab === "report" && <ReportTab id={i.id} has={!!d.report} />}
            {tab === "analysis" && <AnalysisTab d={d} />}
            {tab === "notifications" && <NotificationsTab d={d} />}
            {tab === "timeline" && <Card title="Incident lifecycle"><Timeline d={d} /></Card>}
          </>
        );
      }}
    </DataState>
  );
}

function OverviewTab({ d }: { d: Detail }) {
  const i = d.incident;
  const ev = d.events[0]?.payload ?? {};
  return (
    <div className="grid cols-2">
      <Card title="Summary">
        <p style={{ marginTop: 0 }}>{d.report?.summary ?? i.summary ?? "Awaiting Agent 2 report."}</p>
        <dl className="kv">
          <dt>Onset (building)</dt><dd>{fmtTime(i.onsetAt)}</dd>
          <dt>Detected (building)</dt><dd>{fmtTime(i.detectedAt)}</dd>
          <dt>Last seen</dt><dd>{fmtTime(i.lastSeenAt)}</dd>
          {i.conditionClearedAt && <><dt>Condition cleared</dt><dd>{fmtTime(i.conditionClearedAt)}</dd></>}
          {i.acknowledgedBy && <><dt>Acknowledged</dt><dd>{i.acknowledgedBy} at {fmtTime(i.acknowledgedAt)}</dd></>}
          {i.resolvedBy && <><dt>Resolved</dt><dd>{i.resolvedBy} at {fmtTime(i.resolvedAt)}{i.resolutionNote ? ` - ${i.resolutionNote}` : ""}</dd></>}
          <dt>Occurrences</dt><dd>{i.occurrences} detection event(s)</dd>
          <dt>Scope</dt><dd>{i.scope === "GROUP" ? `${d.sensorCount} sensors` : <>{i.primarySensorId} in <Link to={`/twin?room=${i.primaryRoomId ?? ""}`}>{i.primaryRoomId}</Link></>}</dd>
        </dl>
      </Card>
      <Card title="Detection evidence (Agent 1)" sub={`rule ${d.events[0]?.ruleId ?? "-"}`}>
        <ul className="list">{(ev.evidence as string[] | undefined)?.map((e, k) => <li key={k}>{e}</li>)}</ul>
        {ev.severityReasons?.length > 0 && <p className="secondary" style={{ fontSize: 12.5 }}>Severity: {(ev.severityReasons as string[]).join("; ")}</p>}
        {ev.thresholds && Object.keys(ev.thresholds).length > 0 && <p className="muted" style={{ fontSize: 12 }}>Thresholds: {Object.entries(ev.thresholds).map(([k, v]) => `${k}=${v}`).join(", ")}</p>}
      </Card>
      <Card title={`Affected sensors (${d.sensorCount})`} className="" sub={d.sensorCount > d.sensors.length ? `showing ${d.sensors.length}` : undefined}>
        <div className="table-wrap" style={{ maxHeight: 360 }}><table className="table"><thead><tr><th>Sensor</th><th>Room</th><th>Status</th><th>Value</th><th>Source</th></tr></thead><tbody>
          {d.sensors.map((s) => <tr key={s.id}><td className="mono">{s.id}</td><td>{s.roomId}</td><td><SensorStatusBadge s={s.reading.sensorStatus} /></td><td><ReadingValue r={s.reading} /></td><td><SourceBadge r={s.reading} /></td></tr>)}
        </tbody></table></div>
      </Card>
      <Card title="Adjacent rooms at detection">
        {(ev.neighbours as { room: string; sensorId: string; value: number | null; status: string; kind: string }[] | undefined)?.length ? (
          <table className="table"><thead><tr><th>Room</th><th>Sensor</th><th>Status</th><th className="num">Value</th><th>Link</th></tr></thead><tbody>
            {(ev.neighbours as { room: string; sensorId: string; value: number | null; status: string; kind: string }[]).map((n) => <tr key={n.sensorId}><td>{n.room}</td><td className="mono">{n.sensorId}</td><td>{n.status}</td><td className="num">{n.value != null ? fmtNum(n.value) : "-"}</td><td>{n.kind}</td></tr>)}
          </tbody></table>) : <p className="muted">No same-type sensor in adjacent rooms, or group incident.</p>}
      </Card>
    </div>
  );
}

function ReportTab({ id, has }: { id: string; has: boolean }) {
  if (!has) return <Card><div className="state">Agent 2 has not documented this incident yet (n8n Workflow B or local fallback).</div></Card>;
  const base = `/api/incidents/${encodeURIComponent(id)}/report`;
  return (
    <Card title="Technical report" right={<div className="row">
      <a className="btn sm" href={`${base}?format=md`} download><Download size={14} /> Markdown</a>
      <a className="btn sm" href={`${base}?format=html`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Printable HTML / PDF</a>
    </div>} note="Use your browser's Print -> Save as PDF on the printable page for a PDF export.">
      <iframe className="report-frame" title={`Report ${id}`} src={`${base}?format=html`} sandbox="" />
    </Card>
  );
}

function OriginBadge({ origin, outcome }: { origin: string; outcome?: string | null }) {
  if (outcome) return <span className="badge human"><UserCheck size={12} /> Human: {outcome.toLowerCase()}</span>;
  return origin === "LLM" ? <span className="badge ai"><Bot size={12} /> AI-generated, unverified</span> : <span className="badge"><Bot size={12} /> Automated (rules), unconfirmed</span>;
}

function HypothesisCard({ h }: { h: Hypothesis }) {
  const act = useAction();
  const { token } = useOperator();
  return (
    <div className="hyp">
      <div className="row"><strong>#{h.rank} {h.title}</strong><span className="badge">{h.support} rule support</span><span className="muted mono" style={{ fontSize: 11.5 }}>score {h.score.toFixed(2)}</span><span style={{ marginLeft: "auto" }}><OriginBadge origin={h.origin} outcome={h.outcome} /></span></div>
      <div className="secondary">{h.explanation}</div>
      {h.evidenceFor.length > 0 && <div><span className="muted" style={{ fontSize: 12 }}>Supporting evidence</span><ul className="list">{h.evidenceFor.map((e, k) => <li key={k}>{e}</li>)}</ul></div>}
      {h.evidenceAgainst.length > 0 && <div><span className="muted" style={{ fontSize: 12 }}>Contradicting evidence</span><ul className="list">{h.evidenceAgainst.map((e, k) => <li key={k}>{e}</li>)}</ul></div>}
      <div className="row">
        <button className="btn sm" disabled={!token} onClick={() => act(() => api.post(`/api/hypotheses/${h.id}/outcome`, { outcome: "CONFIRMED" }), "Hypothesis confirmed")}><ThumbsUp size={13} /> Confirm cause</button>
        <button className="btn sm" disabled={!token} onClick={() => act(() => api.post(`/api/hypotheses/${h.id}/outcome`, { outcome: "REJECTED" }), "Hypothesis rejected")}><ThumbsDown size={13} /> Reject</button>
        {h.outcome && <button className="btn sm ghost-btn" disabled={!token} onClick={() => act(() => api.post(`/api/hypotheses/${h.id}/outcome`, { outcome: null }), "Outcome cleared")}>Clear</button>}
      </div>
    </div>
  );
}

function RecommendationRow({ r }: { r: Recommendation }) {
  const act = useAction();
  const { token } = useOperator();
  const set = (status: string) => act(() => api.post(`/api/recommendations/${r.id}/status`, { status }), `Recommendation ${status.toLowerCase()}`);
  return (
    <tr>
      <td><strong>{r.action}</strong><div className="muted" style={{ fontSize: 12 }}>{r.rationale}</div></td>
      <td><span className="badge">{r.urgency.replace("_", " ")}</span></td>
      <td className="secondary">{r.expectedImpact}</td>
      <td className="secondary">{r.validationMethod}</td>
      <td>{r.status}{r.statusBy && <div className="muted" style={{ fontSize: 11.5 }}>{r.statusBy}</div>}
        <div className="row" style={{ marginTop: 4 }}>
          {["ACCEPTED", "DONE", "DISMISSED"].filter((s) => s !== r.status).map((s) => <button key={s} className="btn sm" disabled={!token} onClick={() => set(s)}>{s === "DONE" ? <CheckCircle2 size={12} /> : null}{s.toLowerCase()}</button>)}
        </div>
      </td>
    </tr>
  );
}

function AnalysisTab({ d }: { d: Detail }) {
  const a = d.analysis;
  if (!a) return <Card><div className="state">Agent 3 has not analysed this incident yet.</div></Card>;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="banner">{a.confidenceNote}</div>
      {a.aiNarrative && <Card title="AI narrative" right={<span className="badge ai"><Bot size={12} /> {a.llmModel} · unverified</span>}><p style={{ margin: 0 }}>{a.aiNarrative}</p></Card>}
      <div className="grid cols-2">
        <Card title="Observed facts"><ul className="list">{a.facts.map((f, k) => <li key={k}>{f}</li>)}</ul></Card>
        <Card title="Statistical findings"><ul className="list">{a.statisticalFindings.map((f, k) => <li key={k}>{f}</li>)}</ul>
          {a.sharedCause && <div className="banner warn" style={{ marginTop: 10 }}>{a.sharedCause.note}</div>}</Card>
      </div>
      <Card title="Root-cause hypotheses" sub="ranked by rule-support score">
        <div className="stack" style={{ gap: 10 }}>{a.hypotheses.length ? a.hypotheses.map((h) => <HypothesisCard key={h.id} h={h} />) : <p className="muted">No hypothesis met its evidence threshold.</p>}</div>
      </Card>
      <Card title="Recommended next steps" sub="advisory - nothing is executed on building systems" note={`Model versions: ${Object.entries(a.modelVersions).map(([k, v]) => `${k} ${v}`).join(" · ")}`}>
        <div className="table-wrap"><table className="table"><thead><tr><th>Action</th><th>Urgency</th><th>Expected impact</th><th>How to validate</th><th>Status</th></tr></thead>
          <tbody>{a.recommendations.map((r) => <RecommendationRow key={r.id} r={r} />)}</tbody></table></div>
      </Card>
    </div>
  );
}

function NotificationsTab({ d }: { d: Detail }) {
  if (!d.notifications.length) return <Card><div className="state">No notification recorded (Agent 4 not run yet, or policy did not require one).</div></Card>;
  return (
    <Card title="Notification history (Agent 4)" note="TEST_MODE = written to the outbox, not sent. SENT = accepted by the SMTP server. Recipients are masked.">
      <div className="table-wrap"><table className="table"><thead><tr><th>Kind</th><th>Channel</th><th>Status</th><th>Recipients</th><th>Attempts</th><th>Created (real)</th><th>Sent</th></tr></thead><tbody>
        {d.notifications.map((n) => (
          <tr key={n.id}><td>{n.kind} <SeverityBadge s={n.severity} /></td><td>{n.channel}</td>
            <td><strong>{n.status}</strong>{n.statusReason && <div className="muted" style={{ fontSize: 12 }}>{n.statusReason}</div>}{n.lastError && <div className="muted" style={{ fontSize: 12, color: "var(--critical-ink)" }}>{n.lastError}</div>}</td>
            <td className="mono" style={{ fontSize: 12 }}>{n.recipients.join(", ") || "-"}</td><td className="num">{n.attempts}</td><td>{fmtTime(n.createdAt)}</td><td>{fmtTime(n.sentAt)}</td></tr>))}
      </tbody></table></div>
    </Card>
  );
}

export function Timeline({ d }: { d: Pick<Detail, "timeline"> }) {
  return (
    <ul className="timeline">
      {d.timeline.map((t, k) => (
        <li key={k}><div className="when">{t.at ? fmtTime(t.at) : "-"}<div>real {fmtTime(t.realAt, false)}</div></div>
          <div><strong>{t.kind}</strong> <span className="badge" style={{ fontSize: 11 }}>{t.actor}</span><div className="secondary">{t.message}</div></div></li>
      ))}
    </ul>
  );
}
