import { DragEvent, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, ArrowDownUp, CheckCircle2, FileUp, Info, Trash2, XCircle } from "lucide-react";
import { api, Upload, UploadIssue, UploadSeries, uploadFile } from "../api";
import { Card, ChartTooltip, ConfirmDialog, DataState, Kpi, Seg } from "../components";
import { fmtNum, fmtTime, useAction, useApi, useOperator, useToast } from "../lib";
import { t } from "../i18n";

const LEVEL_ICON = {
  error: <XCircle size={14} color="var(--critical)" aria-label={t("error")} />,
  warn: <AlertTriangle size={14} color="var(--warning)" aria-label={t("warning")} />,
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
    if (!token) { toast("error", t("Operator sign-in required to upload.")); return; }
    if (q.data && file.size > q.data.maxUploadMb * 1024 * 1024) { toast("error", t("File larger than {n} MB.", { n: q.data.maxUploadMb })); return; }
    setProgress(0);
    try {
      const u = await uploadFile(file, setProgress);
      toast("success", `${u.filename} ${t("uploaded - analysing…")}`);
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
      <div className="page-head"><div><h1>{t("Data check")}</h1><p>{t("Upload a sensor CSV (temperature, humidity, CO₂, energy, water, lighting, occupancy…) to check its quality before using it. Files are analysed, never imported into the live platform.")}</p></div></div>
      <div className={`dropzone ${drag ? "on" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        aria-label={t("Upload a CSV file")}>
        <FileUp size={28} />
        <strong>{progress !== null ? `${t("Uploading…")} ${progress} %` : t("Drop a CSV here or click to choose")}</strong>
        <span className="muted" style={{ fontSize: 12.5 }}>
          {t("Accepted: the BMS export format")} (<code>timestamp;display name;sensor id;value;room;…</code>), {t("a long table (timestamp, sensor, value) or a wide table (timestamp + one column per sensor).")}{" "}
          {t("Separator")} <code>;</code> <code>,</code> {t("or tab. Up to {n} MB.", { n: q.data?.maxUploadMb ?? 300 })}
        </span>
        {progress !== null && <div className="bar-track" style={{ width: "60%" }}><div style={{ width: `${progress}%`, background: "var(--accent)" }} /></div>}
        {!token && <span className="badge">{t("Operator sign-in required")}</span>}
        <input ref={input} type="file" accept=".csv,.tsv,.txt,text/csv" hidden onChange={(e) => send(e.target.files?.[0])} />
      </div>
      <Card title={t("Uploaded files")}>
        <DataState q={q} empty={(d) => d.items.length === 0}>
          {(d) => (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>{t("File")}</th><th>{t("Status")}</th><th className="num">{t("Rows")}</th><th className="num">{t("Series")}</th><th>{t("Types")}</th><th>{t("Errors / warnings")}</th><th>{t("Period")}</th><th>{t("Uploaded")}</th><th /></tr></thead>
              <tbody>{d.items.map((u) => (
                <tr key={u.id} className="clickable" onClick={() => nav(`/data/${u.id}`)}>
                  <td><Link to={`/data/${u.id}`} onClick={(e) => e.stopPropagation()}>{u.filename}</Link><div className="muted" style={{ fontSize: 11.5 }}>{size(u.sizeBytes)} · <span className="mono">{u.id}</span></div></td>
                  <td>{u.status === "DONE" ? <span className="badge"><CheckCircle2 size={13} color="var(--good)" /> {t("Done")}</span> : u.status === "FAILED" ? <span className="badge" title={u.error ?? ""}><XCircle size={13} color="var(--critical)" /> {t("Failed")}</span> : <span className="badge">{t("Analysing…")}</span>}</td>
                  <td className="num">{u.summary?.rows.toLocaleString() ?? "-"}</td>
                  <td className="num">{u.summary?.series ?? "-"}</td>
                  <td>{u.summary ? Object.entries(u.summary.types).map(([k, v]) => `${v} ${t(k).toLowerCase()}`).join(", ") : u.error ?? "-"}</td>
                  <td>{u.summary ? `${u.summary.seriesWithErrors} / ${u.summary.seriesWithWarnings}` : "-"}</td>
                  <td>{u.summary ? `${fmtTime(u.summary.first)} → ${fmtTime(u.summary.last)}` : "-"}</td>
                  <td>{fmtTime(u.createdAt)}<div className="muted" style={{ fontSize: 11.5 }}>{u.uploadedBy}</div></td>
                  <td><button className="btn sm ghost-btn" disabled={!token} aria-label={`${t("Delete")} ${u.filename}`} onClick={(e) => { e.stopPropagation(); setDel(u); }}><Trash2 size={14} /></button></td>
                </tr>))}</tbody>
            </table></div>
          )}
        </DataState>
      </Card>
      {del && <ConfirmDialog title={`${t("Delete")} ${del.filename}?`} confirmLabel={t("Delete")} danger onClose={() => setDel(null)}
        body={t("The uploaded file and its analysis are removed. Platform data is not affected.")}
        onConfirm={() => act(() => api.del(`/api/uploads/${del.id}`), t("Upload deleted")).then(() => undefined)} />}
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
              <div><div className="row" style={{ gap: 6 }}><Link to="/data">{t("Data check")}</Link><span className="muted">/</span><span className="mono">{u.id}</span></div>
                <h1>{u.filename}</h1><p>{t("{s} uploaded {d} by {u}", { s: size(u.sizeBytes), d: fmtTime(u.createdAt), u: u.uploadedBy })}</p></div>
            </div>
            {u.status === "PROCESSING" && <Card><div className="state">{t("Analysing the file… large exports take up to a minute.")}</div></Card>}
            {u.status === "FAILED" && <div className="banner err"><XCircle size={16} /> {t("The file could not be analysed:")} {u.error}</div>}
            {s && (
              <>
                <div className="kpis">
                  <Kpi label={t("Format")} value={<span style={{ fontSize: 17 }}>{s.layout}</span>} hint={t("{d} separated · {n} columns", { d: s.delimiter, n: s.columns.length })} />
                  <Kpi label={t("Rows")} value={s.rows.toLocaleString()} hint={t("{a} malformed lines skipped · {b} bad timestamps", { a: s.badLinesSkipped, b: s.unparseableTimestamps })} />
                  <Kpi label={t("Series")} value={s.series} hint={Object.entries(s.types).map(([k, v]) => `${v} ${t(k).toLowerCase()}`).join(", ")} />
                  <Kpi label={t("Period")} value={<span style={{ fontSize: 15 }}>{fmtTime(s.first)}</span>} hint={`${t("to")} ${fmtTime(s.last)}`} />
                  <Kpi label={t("Series with errors")} value={s.seriesWithErrors} hint={t("{n} with warnings", { n: s.seriesWithWarnings })} />
                  <Kpi label={t("Matched to platform")} value={s.seriesMatchedToPlatform} hint={t("compared with the historian")} />
                </div>
                <Card title={t("Checks")} sub={s.timezone}>
                  {s.checks.length === 0 ? <div className="row"><CheckCircle2 size={16} color="var(--good)" /> {t("No problem found in any series.")}</div> : (
                    <div className="row" style={{ gap: 8 }}>
                      {s.checks.map((c) => <span key={c.code} className="badge">{LEVEL_ICON[c.level]} {t(CHECK_LABEL[c.code] ?? c.code)} · {t("{n} series", { n: c.series })}</span>)}
                      {s.exactDuplicates > 0 && <span className="muted" style={{ fontSize: 12 }}>{t("{n} duplicate rows in total", { n: s.exactDuplicates.toLocaleString() })}</span>}
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
  const th = (k: SortKey, label: string) => <th><button onClick={() => setSort(k)} aria-label={`${t("Sort by")} ${label}`}>{label}{sort === k && <ArrowDownUp size={12} />}</button></th>;
  return (
    <Card title={`${t("Series")} (${rows.length}/${series.length})`} right={<div className="row">
      <input className="input" placeholder={t("Filter series / room")} value={text} onChange={(e) => setText(e.target.value)} aria-label={t("Filter series")} />
      <Seg label={t("Series filter")} value={filter} onChange={setFilter} items={[{ id: "all", label: t("All") }, { id: "problems", label: t("With problems") }]} />
    </div>}>
      <div className="table-wrap" style={{ maxHeight: 420 }}><table className="table">
        <thead><tr>{th("series", t("Series"))}<th>{t("Type")}</th><th>{t("Room")}</th>{th("issues", t("Issues"))}{th("completeness", t("Completeness"))}{th("gaps", t("Gaps"))}{th("outOfRange", t("Out of range"))}<th className="num">{t("Spikes")}</th><th>{t("Min / mean / max")}</th><th>{t("Platform")}</th></tr></thead>
        <tbody>{rows.slice(0, 500).map((s) => (
          <tr key={s.series} className="clickable" onClick={() => onSelect(s.series)} style={selected === s.series ? { background: "var(--surface-2)" } : undefined}>
            <td style={{ maxWidth: 280 }}><div style={{ overflowWrap: "anywhere" }}>{s.displayName ?? s.series}</div>{s.displayName && <div className="muted mono" style={{ fontSize: 11 }}>{s.series.slice(0, 60)}</div>}</td>
            <td>{t(s.type)}</td><td>{s.room ?? "-"}</td>
            <td><span className="row" style={{ gap: 4 }}>{s.errors > 0 && <>{LEVEL_ICON.error}{s.errors}</>}{s.warnings > 0 && <>{LEVEL_ICON.warn}{s.warnings}</>}{s.errors + s.warnings === 0 && <CheckCircle2 size={14} color="var(--good)" aria-label={t("ok")} />}</span></td>
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
    <Card title={name.length > 70 ? `${name.slice(0, 70)}…` : name} sub={q.data ? t("resampled every {n} min", { n: Math.round(q.data.resolutionS / 60) }) : undefined}>
      <DataState q={q} lines={6}>
        {(d) => {
          const m = d.metrics;
          const rows = d.points.map((p) => ({ ...p, band: p.min != null && p.max != null ? [p.min, p.max] : null }));
          return (
            <div className="grid cols-2">
              <div>
                <div className="legend" style={{ marginBottom: 6 }}><span><i style={{ background: "var(--series-1)" }} />{t("Mean")}</span><span><i className="sq" style={{ background: "var(--seq-2)" }} />{t("Min-max per interval")}</span><span className="muted">{t("gaps appear as breaks")}</span></div>
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={rows} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="t" tickFormatter={(v) => fmtTime(v).slice(5, 16)} tick={{ fontSize: 11, fill: "var(--text-muted)" }} minTickGap={50} stroke="var(--border)" />
                    <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--border)" domain={["auto", "auto"]} />
                    <Tooltip content={<ChartTooltip unit={m.unit} labelFmt={fmtTime} />} />
                    <Area dataKey="band" name={t("Min-max")} stroke="none" fill="var(--seq-2)" fillOpacity={0.35} isAnimationActive={false} connectNulls={false} />
                    <Line dataKey="mean" name={t("Mean")} stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="stack">
                <h3>{t("Findings")}</h3>
                {m.issues.length === 0 ? <div className="row"><CheckCircle2 size={16} color="var(--good)" /> {t("No problem detected.")}</div> :
                  (m.issues as UploadIssue[]).map((i, k) => <div key={k} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>{LEVEL_ICON[i.level]}<span>{i.text}</span></div>)}
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>{t("Type")}</dt><dd>{t(m.type)} {m.unit && `(${m.unit})`}</dd>
                  <dt>{t("Rows")}</dt><dd>{m.rows.toLocaleString()} ({t("{a} valid, {b} empty, {c} duplicates", { a: m.validValues.toLocaleString(), b: m.emptyValues, c: m.exactDuplicates })})</dd>
                  <dt>{t("Sampling")}</dt><dd>{t("median every {a} min · completeness {b} %", { a: fmtNum(m.medianIntervalS / 60, 1), b: m.completenessPct ?? "-" })}</dd>
                  <dt>{t("Longest gap")}</dt><dd>{m.gaps.longest ? `${m.gaps.longest.hours} h, ${t("ending")} ${fmtTime(m.gaps.longest.to)}` : t("none")}</dd>
                  {m.stats && <><dt>{t("Distribution")}</dt><dd>p05 {fmtNum(m.stats.p05)} · {t("mean")} {fmtNum(m.stats.mean)} · p95 {fmtNum(m.stats.p95)} · std {fmtNum(m.stats.std, 2)} · {t("{n} zeros (valid)", { n: m.stats.zeros })}</dd></>}
                  <dt>{t("Plausible range")}</dt><dd>[{m.outOfRange.range[0] ?? "-"}, {m.outOfRange.range[1] ?? "-"}] · {t("{n} outside", { n: m.outOfRange.count })}</dd>
                  {m.longestFlatHours != null && <><dt>{t("Longest flat")}</dt><dd>{m.longestFlatHours} h</dd></>}
                  {m.room && <><dt>{t("Room")}</dt><dd>{m.roomInDigitalTwin ? <Link to={`/twin?room=${m.room}`}>{m.room}</Link> : `${m.room} (${t("not in digital twin")})`}</dd></>}
                  {m.historianComparison && <><dt>{t("vs historian")}</dt><dd>{m.historianComparison.mae != null ? `MAE ${m.historianComparison.mae}, max diff ${m.historianComparison.maxAbsDiff}, correlation ${m.historianComparison.correlation ?? "-"} over ${m.historianComparison.overlapBuckets} buckets` : m.historianComparison.note ?? t("no overlapping period")}</dd></>}
                </dl>
              </div>
            </div>
          );
        }}
      </DataState>
    </Card>
  );
}
