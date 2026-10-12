import { useNavigate, useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { Bot, FileText, Radar, Send, UserCheck } from "lucide-react";
import { AgentRun, Analysis, Incident, NotificationRec, Page } from "../api";
import { Card, DataState, Empty, IncidentStatusBadge, SeverityBadge } from "../components";
import { fmtTime, useApi } from "../lib";

interface AgentsView {
  incident: Incident;
  agent1: { event: Record<string, any> | null; runs: AgentRun[] };
  agent2: { report: { version: number; summary: string; ai_narrative: string | null; llm_model: string | null; createdAt: string } | null; runs: AgentRun[] };
  agent3: { analysis: Analysis | null; runs: AgentRun[] };
  agent4: { notifications: NotificationRec[]; runs: AgentRun[] };
  timeline: { at: string | null; realAt: string; kind: string; actor: string; message: string }[];
}

function Runs({ runs }: { runs: AgentRun[] }) {
  if (!runs.length) return <p className="muted" style={{ fontSize: 12, margin: 0 }}>No execution recorded yet.</p>;
  return (
    <ul className="list" style={{ fontSize: 12.5 }}>
      {runs.slice(-4).map((r) => (
        <li key={r.id}><strong>{r.status}</strong> via {r.trigger}{r.n8n_execution_id ? ` (n8n execution ${r.n8n_execution_id}${r.n8n_workflow ? `, ${r.n8n_workflow}` : ""})` : ""} · {fmtTime(r.startedAt)} · {r.duration_ms ?? "-"} ms
          {r.summary && <div className="muted">{r.summary}</div>}{r.error && <div style={{ color: "var(--critical-ink)" }}>{r.error}</div>}</li>
      ))}
    </ul>
  );
}

export default function Investigation() {
  const { id } = useParams();
  const nav = useNavigate();
  const list = useApi<Page<Incident>>(["inv-list"], "/api/incidents?status=OPEN,INVESTIGATING,ACKNOWLEDGED,RESOLVED&sort=updated&pageSize=40");
  const sel = id ?? list.data?.items[0]?.id ?? null;
  const v = useApi<AgentsView>(["agents", sel], sel ? `/api/incidents/${encodeURIComponent(sel)}/agents` : null, { refetchInterval: 10000 });
  return (
    <>
      <div className="page-head"><div><h1>AI investigation center</h1><p>What each of the four agents contributed to an incident. Automated output is labelled; only a person can confirm a cause.</p></div></div>
      <div className="split">
        <Card title="Incidents" sub="recently updated">
          <DataState q={list} empty={(d) => d.items.length === 0}>
            {(d) => (
              <div className="stack" style={{ gap: 2, maxHeight: 720, overflowY: "auto" }}>
                {d.items.map((i) => (
                  <button key={i.id} className="btn ghost-btn" style={{ justifyContent: "flex-start", textAlign: "left", whiteSpace: "normal", background: i.id === sel ? "var(--surface-3)" : undefined }} onClick={() => nav(`/investigation/${i.id}`)}>
                    <div><div className="row" style={{ gap: 6 }}><span className="mono" style={{ fontSize: 12 }}>{i.id}</span><SeverityBadge s={i.severity} /></div><div style={{ fontSize: 12.5 }}>{i.title}</div></div>
                  </button>
                ))}
              </div>
            )}
          </DataState>
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          {!sel ? <Card><Empty>No incident yet.</Empty></Card> : (
            <DataState q={v} lines={8}>
              {(d) => {
                const ev = d.agent1.event;
                const a = d.agent3.analysis;
                return (
                  <>
                    <div className="row"><h2><Link to={`/incidents/${d.incident.id}`}>{d.incident.id}</Link> · {d.incident.title}</h2><SeverityBadge s={d.incident.severity} /><IncidentStatusBadge s={d.incident.status} /></div>
                    <div className="grid cols-2">
                      <Card title={<span className="row"><Radar size={16} /> Agent 1 · Detection & classification</span>} sub="deterministic rules">
                        {ev ? (
                          <dl className="kv">
                            <dt>Category</dt><dd>{ev.category}</dd><dt>Rule</dt><dd className="mono">{ev.ruleId}</dd><dt>Severity</dt><dd>{ev.severity} - {(ev.severityReasons ?? []).join("; ")}</dd>
                            <dt>Scope</dt><dd>{ev.scope} ({(ev.sensorIds ?? []).length} sensor(s))</dd><dt>Event id</dt><dd className="mono">{ev.eventId}</dd>
                            <dt>Evidence</dt><dd><ul className="list">{(ev.evidence ?? []).map((e: string, k: number) => <li key={k}>{e}</li>)}</ul></dd>
                          </dl>) : <p className="muted">No event payload.</p>}
                        <Runs runs={d.agent1.runs} />
                      </Card>
                      <Card title={<span className="row"><FileText size={16} /> Agent 2 · Documentation</span>} sub={d.agent2.report ? `report v${d.agent2.report.version}` : "pending"}>
                        {d.agent2.report ? (<>
                          <p style={{ marginTop: 0 }}>{d.agent2.report.summary}</p>
                          {d.agent2.report.ai_narrative && <div className="banner"><Bot size={14} /> <div><strong>AI narrative ({d.agent2.report.llm_model}, unverified):</strong> {d.agent2.report.ai_narrative}</div></div>}
                          <Link to={`/incidents/${d.incident.id}`}>Open full report</Link>
                        </>) : <p className="muted">Not documented yet.</p>}
                        <Runs runs={d.agent2.runs} />
                      </Card>
                      <Card title={<span className="row"><Bot size={16} /> Agent 3 · Root cause & recommendations</span>} sub={a ? `analysis v${a.version}` : "pending"}>
                        {a ? (<div className="stack">
                          {a.hypotheses.slice(0, 3).map((h) => (
                            <div key={h.id} className="row" style={{ alignItems: "flex-start" }}>
                              <span className="badge">{h.support}</span><div style={{ flex: 1 }}>{h.title}</div>
                              {h.outcome ? <span className="badge human"><UserCheck size={12} /> {h.outcome.toLowerCase()} by person</span> : <span className="badge">{h.origin === "LLM" ? "AI suggestion" : "automated, unconfirmed"}</span>}
                            </div>))}
                          <div className="muted" style={{ fontSize: 12 }}>{a.recommendations.length} recommendation(s); top: {a.recommendations[0]?.action ?? "-"}</div>
                          {a.sharedCause && <div className="banner warn">{a.sharedCause.note}</div>}
                        </div>) : <p className="muted">Not analysed yet.</p>}
                        <Runs runs={d.agent3.runs} />
                      </Card>
                      <Card title={<span className="row"><Send size={16} /> Agent 4 · Alerting</span>} sub={`${d.agent4.notifications.length} notification record(s)`}>
                        {d.agent4.notifications.length ? (
                          <ul className="list">{d.agent4.notifications.map((n) => <li key={n.id}>{n.kind} via {n.channel}: <strong>{n.status}</strong>{n.statusReason ? ` - ${n.statusReason}` : ""}</li>)}</ul>
                        ) : <p className="muted">No notification yet.</p>}
                        <Runs runs={d.agent4.runs} />
                      </Card>
                    </div>
                    <Card title="Agent and workflow timeline">
                      <ul className="timeline">{d.timeline.map((t, k) => (
                        <li key={k}><div className="when">{fmtTime(t.at)}<div>real {fmtTime(t.realAt, false)}</div></div>
                          <div><span className={`badge ${t.actor.startsWith("OPERATOR") ? "human" : t.actor.startsWith("AGENT") ? "ai" : ""}`}>{t.actor}</span> <strong>{t.kind}</strong><div className="secondary">{t.message}</div></div></li>))}</ul>
                    </Card>
                  </>
                );
              }}
            </DataState>
          )}
        </div>
      </div>
    </>
  );
}
