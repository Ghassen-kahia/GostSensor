import { FormEvent, Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Bot, Send, Trash2, Wrench, X } from "lucide-react";
import { api, ChatReply, ChatTurn } from "./api";
import { useApi, useOperator } from "./lib";

const STORE = "ghost.chat";
const SUGGESTIONS = [
  "How is the building doing right now?",
  "Which sensors are offline?",
  "Explain the most severe open incident",
  "How accurate is the ghost sensor model?",
  "Summarise the last uploaded CSV",
];

function load(): ChatTurn[] {
  try { return JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch { return []; }
}

/** Minimal, safe Markdown: paragraphs, bullet lists, **bold**, `code`; incident and room ids become links. */
export function Markdown({ text }: { text: string }) {
  const inline = (s: string, key: string): ReactNode[] => {
    const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`|\bINC-\d{8}-[0-9A-F]{6}\b|\b(?:S1|\d{2})-\d{2}[a-z]?-[NSEW0]\b)/g);
    return parts.map((p, i) => {
      const k = `${key}-${i}`;
      if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={k}>{p.slice(2, -2)}</strong>;
      if (/^`[^`]+`$/.test(p)) return <code key={k}>{p.slice(1, -1)}</code>;
      if (/^INC-\d{8}-[0-9A-F]{6}$/.test(p)) return <Link key={k} to={`/incidents/${p}`}>{p}</Link>;
      if (/^(?:S1|\d{2})-\d{2}[a-z]?-[NSEW0]$/.test(p)) return <Link key={k} to={`/twin?room=${p}`}>{p}</Link>;
      return <Fragment key={k}>{p}</Fragment>;
    });
  };
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = (k: number) => {
    if (list.length) blocks.push(<ul key={`l${k}`} className="list">{list.map((l, i) => <li key={i}>{inline(l, `li${k}-${i}`)}</li>)}</ul>);
    list = [];
  };
  text.split("\n").forEach((line, k) => {
    const m = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (m) { list.push(m[1]); return; }
    flush(k);
    const h = line.match(/^#{1,4}\s+(.*)$/);
    if (h) blocks.push(<strong key={k} style={{ display: "block", marginTop: 6 }}>{inline(h[1], `h${k}`)}</strong>);
    else if (line.trim()) blocks.push(<p key={k} style={{ margin: "4px 0" }}>{inline(line, `p${k}`)}</p>);
  });
  flush(-1);
  return <>{blocks}</>;
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>(load);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { token } = useOperator();
  const loc = useLocation();
  const status = useApi<{ mode: string; provider: string; model: string | null }>(["chat-status"], open ? "/api/chat/status" : null, { refetchInterval: false });
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { try { sessionStorage.setItem(STORE, JSON.stringify(turns.slice(-40))); } catch { /* ignore */ } }, [turns]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [turns, busy, open]);

  const ask = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const next: ChatTurn[] = [...turns, { role: "user", content: question }];
    setTurns(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<ChatReply>("/api/chat", { messages: next.slice(-20).map(({ role, content }) => ({ role, content })), page: loc.pathname });
      setTurns([...next, { role: "assistant", content: r.answer, toolCalls: r.toolCalls, mode: r.mode, model: r.model, notice: r.notice }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); ask(input); };

  if (!open) {
    return <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Open the assistant"><Bot size={22} /></button>;
  }
  return (
    <section className="chat-panel" role="dialog" aria-label="Ghost Sensor assistant">
      <header className="chat-head">
        <Bot size={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong>Assistant</strong>
          <div className="muted" style={{ fontSize: 11.5 }}>
            {status.data ? (status.data.mode === "llm" ? `AI · ${status.data.model}` : "Built-in mode (no AI model configured)") : "…"} · answers from platform data
          </div>
        </div>
        <button className="btn ghost-btn sm" onClick={() => setTurns([])} aria-label="Clear conversation" title="Clear conversation"><Trash2 size={15} /></button>
        <button className="btn ghost-btn sm" onClick={() => setOpen(false)} aria-label="Close assistant"><X size={16} /></button>
      </header>
      <div className="chat-body">
        {!token && <div className="banner warn">Sign in as operator (top-right) to use the assistant.</div>}
        {turns.length === 0 && (
          <div className="stack">
            <p className="secondary" style={{ margin: 0 }}>Ask about rooms, sensors, readings, ghost sensors, incidents, the model or uploaded CSV files.</p>
            {SUGGESTIONS.map((s) => <button key={s} className="chat-suggestion" disabled={!token || busy} onClick={() => ask(s)}>{s}</button>)}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`chat-msg ${t.role}`}>
            {t.role === "user" ? t.content : (
              <>
                <Markdown text={t.content} />
                {t.notice && <div className="muted" style={{ fontSize: 11.5 }}>{t.notice}</div>}
                {t.toolCalls && t.toolCalls.length > 0 && (
                  <div className="chat-tools" title="Read-only lookups the assistant made">
                    <Wrench size={11} /> {t.toolCalls.map((c) => c.name.replace(/_/g, " ")).join(" · ")}
                  </div>
                )}
                <div className="muted" style={{ fontSize: 10.5 }}>{t.mode === "llm" ? `AI-generated (${t.model}) - verify important facts` : "Built-in answer"}</div>
              </>
            )}
          </div>
        ))}
        {busy && <div className="chat-msg assistant"><span className="muted">Looking it up…</span></div>}
        {error && <div className="banner err">{error}</div>}
        <div ref={endRef} />
      </div>
      <form className="chat-input" onSubmit={submit}>
        <input className="input" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a question…" maxLength={2000}
          disabled={!token || busy} aria-label="Question" />
        <button className="btn primary sm" type="submit" disabled={!token || busy || !input.trim()} aria-label="Send"><Send size={14} /></button>
      </form>
    </section>
  );
}
