import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient, UseQueryOptions } from "@tanstack/react-query";
import { api, auth, Clock } from "./api";
import { locale, t } from "./i18n";

// ------------------------------------------------------------------ formatting
export function fmtTime(iso: string | null | undefined, withDate = true): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const p = (n: number) => String(n).padStart(2, "0");
  const time = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  return withDate ? `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${time} UTC` : `${time}`;
}

export function fmtEpoch(ts: number | null | undefined, withDate = true): string {
  return ts == null ? "-" : fmtTime(new Date(ts * 1000).toISOString(), withDate);
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "-";
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} ${t("d")}`;
}

/** Age of a building-time ISO timestamp relative to the building clock (not the browser clock). */
export function ageAt(iso: string | null | undefined, clockNow: number | undefined): string {
  if (!iso || clockNow == null) return "-";
  return fmtDuration(clockNow - new Date(iso).getTime() / 1000);
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  return v == null || Number.isNaN(v) ? "-" : v.toLocaleString(locale(), { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function unitLabel(u: string | null | undefined): string {
  return ({ degC: "°C", ppm: "ppm", "kWh/h": "kWh/h", "m3/h": "m³/h", ratio: "", "%RH": "%RH", lux: "lux" } as Record<string, string>)[u ?? ""] ?? (u ?? "");
}

// ------------------------------------------------------------------ data hooks
export function useApi<T>(key: unknown[], path: string | null, opts: Partial<UseQueryOptions<T>> = {}) {
  return useQuery<T>({
    queryKey: key,
    queryFn: () => api.get<T>(path as string),
    enabled: path !== null,
    refetchInterval: 20000,
    ...opts,
  });
}

type Live = "live" | "polling" | "offline";
const LiveCtx = createContext<Live>("polling");
export const useLive = () => useContext(LiveCtx);

/** Server-Sent Events invalidate cached queries; if the stream drops, React Query polling continues. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<Live>("polling");
  const last = useRef(0);
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let es: EventSource | null = new EventSource("/api/stream");
    const invalidate = () => {
      const now = Date.now();
      if (now - last.current < 1500) return; // coalesce bursts
      last.current = now;
      qc.invalidateQueries();
    };
    es.onopen = () => setState("live");
    es.onerror = () => setState(navigator.onLine ? "polling" : "offline");
    es.addEventListener("tick", invalidate);
    es.addEventListener("incident.updated", invalidate);
    return () => { es?.close(); es = null; };
  }, [qc]);
  return <LiveCtx.Provider value={state}>{children}</LiveCtx.Provider>;
}

export function useClock() {
  return useApi<Clock>(["clock"], "/api/clock", { refetchInterval: 5000 });
}

// ------------------------------------------------------------------ toasts
type Toast = { id: number; kind: "info" | "error" | "success"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x.slice(-3), { id, kind, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 6000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

// ------------------------------------------------------------------ operator session
type Operator = { token: string | null; name: string; login: (t: string, n: string) => void; logout: () => void };
const OpCtx = createContext<Operator>({ token: null, name: "operator", login: () => undefined, logout: () => undefined });
export const useOperator = () => useContext(OpCtx);

export function OperatorProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(auth.token());
  const [name, setName] = useState(auth.name());
  const value = useMemo<Operator>(() => ({
    token, name,
    login: (t, n) => { auth.set(t, n); setToken(t); setName(n); },
    logout: () => { auth.clear(); setToken(null); },
  }), [token, name]);
  return <OpCtx.Provider value={value}>{children}</OpCtx.Provider>;
}

/** Wrap a mutating call: shows toasts, refreshes data, explains missing operator rights. */
export function useAction() {
  const toast = useToast();
  const qc = useQueryClient();
  const { token } = useOperator();
  return useCallback(async <T,>(fn: () => Promise<T>, success: string): Promise<T | undefined> => {
    if (!token) {
      toast("error", t("Operator sign-in required for this action (top-right)."));
      return undefined;
    }
    try {
      const r = await fn();
      toast("success", success);
      qc.invalidateQueries();
      return r;
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      return undefined;
    }
  }, [toast, qc, token]);
}
