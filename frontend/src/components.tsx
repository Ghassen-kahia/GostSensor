import { ReactNode, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, CircleSlash, Info, OctagonAlert, TriangleAlert, WifiOff, X } from "lucide-react";
import { Reading, Severity, SensorStatus } from "./api";
import { fmtNum, unitLabel } from "./lib";

// ------------------------------------------------------------------ ghost icon
export function GhostIcon({ size = 16, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={title ?? "virtual sensor"} style={{ flex: "none" }}>
      <path d="M12 2.5a7.5 7.5 0 0 0-7.5 7.5v11.2l2.6-2.1 2.4 2.4 2.5-2.4 2.5 2.4 2.4-2.4 2.6 2.1V10A7.5 7.5 0 0 0 12 2.5z" fill="var(--ghost)" />
      <circle cx="9.2" cy="10.2" r="1.5" fill="var(--surface-1)" />
      <circle cx="14.8" cy="10.2" r="1.5" fill="var(--surface-1)" />
    </svg>
  );
}

export function VirtualBadge() {
  return <span className="badge virtual" title="Value inferred by the Ghost Sensor model - not a physical measurement"><GhostIcon size={14} /> Capteur Virtuel Actif</span>;
}

// ------------------------------------------------------------------ badges (status colour + icon + label, never colour alone)
const SEV: Record<Severity, { color: string; Icon: typeof Info }> = {
  CRITICAL: { color: "var(--critical)", Icon: OctagonAlert },
  HIGH: { color: "var(--serious)", Icon: TriangleAlert },
  MEDIUM: { color: "var(--warning)", Icon: AlertTriangle },
  LOW: { color: "var(--series-1)", Icon: Info },
  INFO: { color: "var(--info)", Icon: Info },
};

export function SeverityBadge({ s }: { s: Severity }) {
  const { color, Icon } = SEV[s] ?? SEV.INFO;
  return <span className="badge"><Icon size={13} color={color} aria-hidden /> {s}</span>;
}

const STATUS: Record<string, { color: string; Icon: typeof Info; label: string }> = {
  ONLINE: { color: "var(--good)", Icon: CheckCircle2, label: "Online" },
  STALE: { color: "var(--warning)", Icon: CircleDashed, label: "Stale" },
  OFFLINE: { color: "var(--critical)", Icon: WifiOff, label: "Offline" },
  INVALID: { color: "var(--serious)", Icon: CircleSlash, label: "Invalid values" },
  NO_DATA: { color: "var(--info)", Icon: CircleDashed, label: "No data" },
};

export function SensorStatusBadge({ s }: { s: SensorStatus | string }) {
  const m = STATUS[s] ?? STATUS.NO_DATA;
  return <span className="badge"><m.Icon size={13} color={m.color} aria-hidden /> {m.label}</span>;
}

const INC_STATUS: Record<string, string> = {
  OPEN: "var(--critical)", INVESTIGATING: "var(--warning)", ACKNOWLEDGED: "var(--series-1)", RESOLVED: "var(--good)", CLOSED: "var(--info)",
};
export function IncidentStatusBadge({ s }: { s: string }) {
  return <span className="badge"><span className="dot" style={{ background: INC_STATUS[s] ?? "var(--info)" }} />{s}</span>;
}

export function SourceBadge({ r }: { r: Reading }) {
  if (r.source === "INFERRED") return <VirtualBadge />;
  if (r.source === "PHYSICAL") return <span className="badge physical" title="Physical measurement from the sensor">Physical</span>;
  return <span className="badge" title="No usable value">No value</span>;
}

export function SimulatedBadge() {
  return <span className="badge simulated" title="Injected by an operator for testing - not a real event">Simulated</span>;
}

export function ReadingValue({ r }: { r: Reading }) {
  if (r.value == null) return <span className="muted">-</span>;
  const u = unitLabel(r.unit);
  if (r.source === "INFERRED" && r.uncertainty) {
    return (
      <span title={`90 % interval ${fmtNum(r.uncertainty.lower)}-${fmtNum(r.uncertainty.upper)} ${u}`}>
        <span style={{ color: "var(--ghost)", fontWeight: 650 }}>{fmtNum(r.value)}</span> {u}
        <span className="muted"> ±{fmtNum((r.uncertainty.upper - r.uncertainty.lower) / 2)}</span>
      </span>
    );
  }
  return <span>{fmtNum(r.value, r.type === "ENERGY" ? 2 : 1)} {u}</span>;
}

// ------------------------------------------------------------------ containers & states
export function Card({ title, sub, right, children, note, className }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; note?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || right) && <div className="card-head"><h2>{title}</h2>{sub && <span className="sub">{sub}</span>}<div className="right">{right}</div></div>}
      <div className="card-body">{children}</div>
      {note && <div className="card-note">{note}</div>}
    </section>
  );
}

export function Kpi({ label, value, hint, icon }: { label: ReactNode; value: ReactNode; hint?: ReactNode; icon?: ReactNode }) {
  return <div className="card kpi"><div className="label">{icon}{label}</div><div className="value">{value}</div>{hint && <div className="hint">{hint}</div>}</div>;
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return <div className="stack" aria-busy="true" aria-label="Loading">{Array.from({ length: lines }).map((_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />)}</div>;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="state error" role="alert"><div>{msg}</div>{retry && <button className="btn sm" style={{ marginTop: 8 }} onClick={retry}>Retry</button>}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state">{children}</div>;
}

export function DataState<T>({ q, children, empty, lines }: { q: { data?: T; error: unknown; isLoading: boolean; refetch: () => void }; children: (d: T) => ReactNode; empty?: (d: T) => boolean; lines?: number }) {
  if (q.isLoading) return <Loading lines={lines} />;
  if (q.error && !q.data) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  if (!q.data) return <Empty>No data.</Empty>;
  if (empty && empty(q.data)) return <Empty>Nothing to show.</Empty>;
  return <>{children(q.data)}</>;
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <span>{total === 0 ? "0 results" : `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)} of ${total}`}</span>
      <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>Page {page} / {pages}</span>
      <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="row"><h2 style={{ marginRight: "auto" }}>{title}</h2><button className="btn ghost-btn sm" onClick={onClose} aria-label="Close"><X size={16} /></button></div>
        {children}
      </div>
    </div>
  );
}

/** Confirmation dialog with optional note; used for every state-changing operator action. */
export function ConfirmDialog({ title, body, confirmLabel, danger, withNote, requireText, onConfirm, onClose }: {
  title: string; body: ReactNode; confirmLabel: string; danger?: boolean; withNote?: boolean; requireText?: string;
  onConfirm: (note: string) => void | Promise<void>; onClose: () => void;
}) {
  const [note, setNote] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const blocked = busy || (requireText !== undefined && typed !== requireText);
  return (
    <Modal title={title} onClose={onClose}>
      <div className="secondary">{body}</div>
      {withNote && <label className="field">Note (optional, stored in the incident timeline)<textarea className="input" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} /></label>}
      {requireText && <label className="field">Type <code>{requireText}</code> to confirm<input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} /></label>}
      <div className="row">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className={`btn ${danger ? "danger" : "primary"}`} disabled={blocked} onClick={async () => { setBusy(true); await onConfirm(note); setBusy(false); onClose(); }}>{confirmLabel}</button>
      </div>
    </Modal>
  );
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="overlay" style={{ background: "rgba(0,0,0,.25)" }} onMouseDown={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head"><div style={{ flex: 1, minWidth: 0 }}>{title}</div><button className="btn ghost-btn sm" onClick={onClose} aria-label="Close"><X size={16} /></button></div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode }[] }) {
  return <div className="tabs" role="tablist">{items.map((i) => <button key={i.id} role="tab" aria-selected={value === i.id} className={value === i.id ? "on" : ""} onClick={() => onChange(i.id)}>{i.label}</button>)}</div>;
}

export function Seg<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode }[]; label: string }) {
  return <div className="seg" role="group" aria-label={label}>{items.map((i) => <button key={i.id} aria-pressed={value === i.id} className={value === i.id ? "on" : ""} onClick={() => onChange(i.id)}>{i.label}</button>)}</div>;
}

/** Chart wrapper: legend, and a table view (relief for low-contrast series and screen readers). */
export function ChartCard({ title, sub, legend, chart, table, note }: { title: string; sub?: ReactNode; legend?: { label: string; color: string; square?: boolean }[]; chart: ReactNode; table: { columns: string[]; rows: (string | number | null)[][] }; note?: ReactNode }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card title={title} sub={sub} note={note} right={<Seg label={`${title} view`} value={view} onChange={setView} items={[{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }]} />}>
      {legend && legend.length > 1 && view === "chart" && <div className="legend" style={{ marginBottom: 8 }}>{legend.map((l) => <span key={l.label}><i className={l.square ? "sq" : ""} style={{ background: l.color }} />{l.label}</span>)}</div>}
      {view === "chart" ? chart : (
        <div className="table-wrap" style={{ maxHeight: 260 }}>
          <table className="table"><thead><tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={typeof c === "number" ? "num" : ""}>{c ?? "-"}</td>)}</tr>)}</tbody></table>
        </div>
      )}
    </Card>
  );
}

export function ChartTooltip({ active, payload, label, unit, labelFmt }: { active?: boolean; payload?: { name: string; value: number | number[]; color: string; dataKey: string }[]; label?: string; unit?: string; labelFmt?: (l: string) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="t">{labelFmt ? labelFmt(label ?? "") : label}</div>
      {payload.filter((p) => p.value != null).map((p) => (
        <div key={p.dataKey} className="row" style={{ gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color, display: "inline-block" }} />
          <span className="secondary">{p.name}</span>
          <strong style={{ marginLeft: "auto" }}>{Array.isArray(p.value) ? `${fmtNum(p.value[0])}-${fmtNum(p.value[1])}` : fmtNum(p.value)}{unit ? ` ${unit}` : ""}</strong>
        </div>
      ))}
    </div>
  );
}
