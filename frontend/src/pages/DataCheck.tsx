import { DragEvent, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, ArrowDownUp, CheckCircle2, FileUp, Info, Trash2, XCircle } from "lucide-react";
import { api, Upload, UploadIssue, UploadSeries, uploadFile } from "../api";
import { Card, ChartTooltip, ConfirmDialog, DataState, Kpi, Seg } from "../components";
import { fmtNum, fmtTime, useAction, useApi, useOperator, useToast } from "../lib";

const LEVEL_ICON = {
  error: <XCircle size={14} color="var(--critical)" aria-label="error" />,
  warn: <AlertTriangle size={14} color="var(--warning)" aria-label="warning" />,
  info: <Info size={14} color="var(--info)" aria-label="info" />,
};
const CHECK_LABEL: Record<string, string> = {
  NO_VALID_VALUES: "No valid values", EMPTY_VALUES: "Empty values", NON_NUMERIC: "Non-numeric values", DUPLICATE_ROWS: "Duplicate rows",
  CONFLICTING_DUPLICATES: "Conflicting duplicates", TRANSMISSION_GAPS: "Transmission gaps", LOW_COMPLETENESS: "Low completeness",
  OUT_OF_RANGE: "Out of plausible range", SPIKES: "Sudden spikes", FLATLINE: "Frozen value (flat-line)", COUNTER_DECREASES: "Counter decreases",
  UNKNOWN_ROOM: "Room not in digital twin", DIFFERS_FROM_HISTORIAN: "Differs from historian",
};

function size(b: number) {
  return b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} kB`;
}

export default function DataCheck() {
  const { id } = useParams();
  return id ? <UploadReport id={id} /> : <UploadList />;
}

function UploadList() {
  const q = useApi<{ items: Upload[]; maxUploadMb: number }>(["uploads"], "/api/uploads", {
    refetchInterval: (query) => (query.state.data?.items.some((u) => u.status === "PROCESSING") ? 2000 : 20000),
  });
  const { token } = useOperator();
  const toast = useToast();
  const qc = useQueryClient();
  const nav = useNavigate();
  const act = useAction();
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);
  const [del, setDel] = useState<Upload | null>(null);

  const send = async (file: File | undefined) => {
    if (!file) return;
    if (!token) { toast("error", "Operator sign-in required to upload."); return; }
    if (q.data && file.size > q.data.maxUploadMb * 1024 * 1024) { toast("error", `File larger than ${q.data.maxUploadMb} MB.`); return; }
    setProgress(0);
    try {
      const u = await uploadFile(file, setProgress);
      toast("success", `${u.filename} uploaded - analysing…`);
      qc.invalidateQueries({ queryKey: ["uploads"] });
      nav(`/data/${u.id}`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files?.[0]); };

  return (
    <>
      <div className="page-head"><div><h1>Data check</h1><p>Upload a sensor CSV (temperature, humidity, CO₂, energy, water, lighting, occupancy…) to check its quality before using it. Files are analysed, never imported into the live platform.</p></div></div>
      <div className={`dropzone ${drag ? "on" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        aria-label="Upload a CSV file">
        <FileUp size={28} />
        <strong>{progress !== null ? `Uploading… ${progress} %` : "Drop a CSV here or click to choose"}</strong>
        <span className="muted" style={{ fontSize: 12.5 }}>
          Accepted: the BMS export format (<code>timestamp;display name;sensor id;value;room;…</code>), a long table (timestamp, sensor, value) or a wide table
          (timestamp + one column per sensor). Separator <code>;</code> <code>,</code> or tab. Up to {q.data?.maxUploadMb ?? 300} MB.
        </span>
        {progress !== null && <div className="bar-track" style={{ width: "60%" }}><div style={{ width: `${progress}%`, background: "var(--accent)" }} /></div>}
        {!token && <span className="badge">Operator sign-in required</span>}
        <input ref={input} type="file" accept=".csv,.tsv,.txt,text/csv" hidden onChange={(e) => send(e.target.files?.[0])} />
      </div>
      <Card title="Uploaded files">
        <DataState q={q} empty={(d) => d.items.length === 0}>
          {(d) => (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>File</th><th>Status</th><th className="num">Rows</th><th className="num">Series</th><th>Types</th><th>Errors / warnings</th><th>Period</th><th>Uploaded</th><th /></tr></thead>
              <tbody>{d.items.map((u) => (
                <tr key={u.id} className="clickable" onClick={() => nav(`/data/${u.id}`)}>
                  <td><Link to={`/data/${u.id}`} onClick={(e) => e.stopPropagation()}>{u.filename}</Link><div className="muted" style={{ fontSize: 11.5 }}>{size(u.sizeBytes)} · <span className="mono">{u.id}</span></div></td>
                  <td>{u.status === "DONE" ? <span className="badge"><CheckCircle2 size={13} color="var(--good)" /> Done</span> : u.status === "FAILED" ? <span className="badge" title={u.error ?? ""}><XCircle size={13} color="var(--critical)" /> Failed</span> : <span className="badge">Analysing…</span>}</td>
                  <td className="num">{u.summary?.rows.toLocaleString() ?? "-"}</td>
                  <td className="num">{u.summary?.series ?? "-"}</td>
                  <td>{u.summary ? Object.entries(u.summary.types).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ") : u.error ?? "-"}</td>
                  <td>{u.summary ? `${u.summary.seriesWithErrors} / ${u.summary.seriesWithWarnings}` : "-"}</td>
                  <td>{u.summary ? `${fmtTime(u.summary.first)} → ${fmtTime(u.summary.last)}` : "-"}</td>
                  <td>{fmtTime(u.createdAt)}<div className="muted" style={{ fontSize: 11.5 }}>{u.uploadedBy}</div></td>
                  <td><button className="btn sm ghost-btn" disabled={!token} aria-label={`Delete ${u.filename}`} onClick={(e) => { e.stopPropagation(); setDel(u); }}><Trash2 size={14} /></button></td>
                </tr>))}</tbody>
            </table></div>
          )}
        </DataState>
      </Card>
      {del && <ConfirmDialog title={`Delete ${del.filename}?`} confirmLabel="Delete" danger onClose={() => setDel(null)}
        body="The uploaded file and its analysis are removed. Platform data is not affected."
        onConfirm={() => act(() => api.del(`/api/uploads/${del.id}`), "Upload deleted").then(() => undefined)} />}
    </>
  );
}

type SortKey = "issues" | "series" | "completeness" | "gaps" | "outOfRange";

function UploadReport({ id }: { id: string }) {
  const q = useApi<Upload>(["upload", id], `/api/uploads/${id}`, { refetchInterval: (query) => (query.state.data?.status === "PROCESSING" ? 1500 : false) });
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "problems">("all");
  const [text, setText] = useState("");
  const [sort, setSort] = useState<SortKey>("issues");

  return (
    <DataState q={q} lines={8}>
      {(u) => {
        const s = u.summary;
        return (
          <>
            <div className="page-head">
              <div><div className="row" style={{ gap: 6 }}><Link to="/data">Data check</Link><span className="muted">/</span><span className="mono">{u.id}</span></div>
                <h1>{u.filename}</h1><p>{size(u.sizeBytes)} uploaded {fmtTime(u.createdAt)} by {u.uploadedBy}</p></div>
            </div>
            {u.status === "PROCESSING" && <Card><div className="state">Analysing the file… large exports take up to a minute.</div></Card>}
            {u.status === "FAILED" && <div className="banner err"><XCircle size={16} /> The file could not be analysed: {u.error}</div>}
            {s && (
              <>
                <div className="kpis">
                  <Kpi label="Format" value={<span style={{ fontSize: 17 }}>{s.layout}</span>} hint={`${s.delimiter} separated · ${s.columns.length} columns`} />
                  <Kpi label="Rows" value={s.rows.toLocaleString()} hint={`${s.badLinesSkipped} malformed lines skipped · ${s.unparseableTimestamps} bad timestamps`} />
                  <Kpi label="Series" value={s.series} hint={Object.entries(s.types).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ")} />
                  <Kpi label="Period" value={<span style={{ fontSize: 15 }}>{fmtTime(s.first)}</span>} hint={`to ${fmtTime(s.last)}`} />
                  <Kpi label="Series with errors" value={s.seriesWithErrors} hint={`${s.seriesWithWarnings} with warnings`} />
                  <Kpi label="Matched to platform" value={s.seriesMatchedToPlatform} hint="compared with the historian" />
                </div>
                <Card title="Checks" sub={s.timezone}>
                  {s.checks.length === 0 ? <div className="row"><CheckCircle2 size={16} color="var(--good)" /> No problem found in any series.</div> : (
                    <div className="row" style={{ gap: 8 }}>
                      {s.checks.map((c) => <span key={c.code} className="badge">{LEVEL_ICON[c.level]} {CHECK_LABEL[c.code] ?? c.code} · {c.series} series</span>)}
                      {s.exactDuplicates > 0 && <span className="muted" style={{ fontSize: 12 }}>{s.exactDuplicates.toLocaleString()} duplicate rows in total</span>}
                    </div>
                  )}
                </Card>
                <SeriesTable series={u.series ?? []} selected={selected} onSelect={setSelected} filter={filter} setFilter={setFilter} text={text} setText={setText} sort={sort} setSort={setSort} />
                {(selected ?? u.series?.[0]?.series) && <SeriesDetail uploadId={u.id} name={(selected ?? u.series![0].series)!} />}
              </>
            )}
          </>
        );
      }}
    </DataState>
  );
}

function SeriesTable({ series, selected, onSelect, filter, setFilter, text, setText, sort, setSort }: {
  series: UploadSeries[]; selected: string | null; onSelect: (s: string) => void; filter: "all" | "problems"; setFilter: (f: "all" | "problems") => void;
  text: string; setText: (t: string) => void; sort: SortKey; setSort: (s: SortKey) => void;
}) {
  const rows = useMemo(() => {
    let r = series.filter((s) => (filter === "all" || s.errors + s.warnings > 0) && (!text || `${s.series} ${s.displayName ?? ""} ${s.room ?? ""}`.toLowerCase().includes(text.toLowerCase())));
    const key: Record<SortKey, (s: UploadSeries) => number | string> = {
      issues: (s) => -(s.errors * 100 + s.warnings), series: (s) => s.displayName ?? s.series, completeness: (s) => s.completenessPct ?? 0,
      gaps: (s) => -s.gapHours, outOfRange: (s) => -s.outOfRange,
    };
    r = [...r].sort((a, b) => (key[sort](a) < key[sort](b) ? -1 : key[sort](a) > key[sort](b) ? 1 : 0));
    return r;
  }, [series, filter, text, sort]);
  const th = (k: SortKey, label: string) => <th><button onClick={() => setSort(k)} aria-label={`Sort by ${label}`}>{label}{sort === k && <ArrowDownUp size={12} />}</button></th>;
  return (
    <Card title={`Series (${rows.length}/${series.length})`} right={<div className="row">
      <input className="input" placeholder="Filter series / room" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter series" />
      <Seg label="Series filter" value={filter} onChange={setFilter} items={[{ id: "all", label: "All" }, { id: "problems", label: "With problems" }]} />
    </div>}>
      <div className="table-wrap" style={{ maxHeight: 420 }}><table className="table">
        <thead><tr>{th("series", "Series")}<th>Type</th><th>Room</th>{th("issues", "Issues")}{th("completeness", "Completeness")}{th("gaps", "Gaps")}{th("outOfRange", "Out of range")}<th className="num">Spikes</th><th>Min / mean / max</th><th>Platform</th></tr></thead>
        <tbody>{rows.slice(0, 500).map((s) => (
          <tr key={s.series} className="clickable" onClick={() => onSelect(s.series)} style={selected === s.series ? { background: "var(--surface-2)" } : undefined}>
            <td style={{ maxWidth: 280 }}><div style={{ overflowWrap: "anywhere" }}>{s.displayName ?? s.series}</div>{s.displayName && <div className="muted mono" style={{ fontSize: 11 }}>{s.series.slice(0, 60)}</div>}</td>
            <td>{s.type}</td><td>{s.room ?? "-"}</td>
            <td><span className="row" style={{ gap: 4 }}>{s.errors > 0 && <>{LEVEL_ICON.error}{s.errors}</>}{s.warnings > 0 && <>{LEVEL_ICON.warn}{s.warnings}</>}{s.errors + s.warnings === 0 && <CheckCircle2 size={14} color="var(--good)" aria-label="ok" />}</span></td>
            <td className="num">{s.completenessPct != null ? `${s.completenessPct} %` : "-"}</td>
            <td className="num">{s.gaps} ({fmtNum(s.gapHours)} h)</td>
            <td className="num">{s.outOfRange}</td>
            <td className="num">{s.spikes}</td>
            <td className="num">{s.stats ? `${fmtNum(s.stats.min)} / ${fmtNum(s.stats.mean)} / ${fmtNum(s.stats.max)} ${s.unit}` : "-"}</td>
            <td>{s.platformSensor ? <Link to={`/sensors?q=${encodeURIComponent(s.platformSensor)}`} onClick={(e) => e.stopPropagation()} className="mono">{s.platformSensor}</Link> : <span className="muted">-</span>}</td>
          </tr>))}</tbody>
      </table></div>
    </Card>
  );
}

function SeriesDetail({ uploadId, name }: { uploadId: string; name: string }) {
  const q = useApi<{ metrics: any; resolutionS: number; points: { t: string; mean: number | null; min: number | null; max: number | null; n: number }[] }>(
    ["upload-series", uploadId, name], `/api/uploads/${uploadId}/series?name=${encodeURIComponent(name)}`, { refetchInterval: false });
  return (
    <Card title={name.length > 70 ? `${name.slice(0, 70)}…` : name} sub={q.data ? `resampled every ${Math.round(q.data.resolutionS / 60)} min` : undefined}>
      <DataState q={q} lines={6}>
        {(d) => {
          const m = d.metrics;
          const rows = d.points.map((p) => ({ ...p, band: p.min != null && p.max != null ? [p.min, p.max] : null }));
          return (
            <div className="grid cols-2">
              <div>
                <div className="legend" style={{ marginBottom: 6 }}><span><i style={{ background: "var(--series-1)" }} />Mean</span><span><i className="sq" style={{ background: "var(--seq-2)" }} />Min-max per interval</span><span className="muted">gaps appear as breaks</span></div>
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={rows} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="t" tickFormatter={(v) => fmtTime(v).slice(5, 16)} tick={{ fontSize: 11, fill: "var(--text-muted)" }} minTickGap={50} stroke="var(--border)" />
                    <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--border)" domain={["auto", "auto"]} />
                    <Tooltip content={<ChartTooltip unit={m.unit} labelFmt={fmtTime} />} />
                    <Area dataKey="band" name="Min-max" stroke="none" fill="var(--seq-2)" fillOpacity={0.35} isAnimationActive={false} connectNulls={false} />
                    <Line dataKey="mean" name="Mean" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="stack">
                <h3>Findings</h3>
                {m.issues.length === 0 ? <div className="row"><CheckCircle2 size={16} color="var(--good)" /> No problem detected.</div> :
                  (m.issues as UploadIssue[]).map((i, k) => <div key={k} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>{LEVEL_ICON[i.level]}<span>{i.text}</span></div>)}
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>Type</dt><dd>{m.type} {m.unit && `(${m.unit})`}</dd>
                  <dt>Rows</dt><dd>{m.rows.toLocaleString()} ({m.validValues.toLocaleString()} valid, {m.emptyValues} empty, {m.exactDuplicates} duplicates)</dd>
                  <dt>Sampling</dt><dd>median every {fmtNum(m.medianIntervalS / 60, 1)} min · completeness {m.completenessPct ?? "-"} %</dd>
                  <dt>Longest gap</dt><dd>{m.gaps.longest ? `${m.gaps.longest.hours} h, ending ${fmtTime(m.gaps.longest.to)}` : "none"}</dd>
                  {m.stats && <><dt>Distribution</dt><dd>p05 {fmtNum(m.stats.p05)} · mean {fmtNum(m.stats.mean)} · p95 {fmtNum(m.stats.p95)} · std {fmtNum(m.stats.std, 2)} · {m.stats.zeros} zeros (valid)</dd></>}
                  <dt>Plausible range</dt><dd>[{m.outOfRange.range[0] ?? "-"}, {m.outOfRange.range[1] ?? "-"}] · {m.outOfRange.count} outside</dd>
                  {m.longestFlatHours != null && <><dt>Longest flat</dt><dd>{m.longestFlatHours} h</dd></>}
                  {m.room && <><dt>Room</dt><dd>{m.roomInDigitalTwin ? <Link to={`/twin?room=${m.room}`}>{m.room}</Link> : `${m.room} (not in digital twin)`}</dd></>}
                  {m.historianComparison && <><dt>vs historian</dt><dd>{m.historianComparison.mae != null ? `MAE ${m.historianComparison.mae}, max diff ${m.historianComparison.maxAbsDiff}, correlation ${m.historianComparison.correlation ?? "-"} over ${m.historianComparison.overlapBuckets} buckets` : m.historianComparison.note ?? "no overlapping period"}</dd></>}
                </dl>
              </div>
            </div>
          );
        }}
      </DataState>
    </Card>
  );
}
