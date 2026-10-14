import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Activity, BarChart3, BookOpen, Bot, Building2, FileSpreadsheet, FileText, Gauge, LogIn, LogOut, Menu, Moon, Network, Pause, Play, Radio, ShieldAlert, Sun } from "lucide-react";
import { api, Health, Summary } from "./api";
import ChatWidget from "./Chat";
import eseoLogo from "./assets/eseo-logo.png";
import { GhostIcon, Modal } from "./components";
import { fmtEpoch, useAction, useApi, useClock, useLive, useOperator } from "./lib";
import { LangSwitch, t } from "./i18n";
import Overview from "./pages/Overview";
import DigitalTwin from "./pages/DigitalTwin";
import Sensors from "./pages/Sensors";
import GhostMonitor from "./pages/GhostMonitor";
import Incidents from "./pages/Incidents";
import IncidentDetail from "./pages/IncidentDetail";
import Investigation from "./pages/Investigation";
import Reports from "./pages/Reports";
import SystemHealth from "./pages/SystemHealth";
import DataCheck from "./pages/DataCheck";
import Guide from "./pages/Guide";

const NAV = [
  { to: "/", label: "Executive overview", Icon: Gauge, end: true },
  { to: "/twin", label: "Digital twin", Icon: Building2 },
  { to: "/sensors", label: "Live sensors", Icon: Radio },
  { to: "/ghost", label: "Ghost sensors", Icon: null },
  { to: "/incidents", label: "Incidents", Icon: ShieldAlert },
  { to: "/investigation", label: "AI investigation", Icon: Bot },
  { to: "/reports", label: "Reports & analytics", Icon: BarChart3 },
  { to: "/data", label: "Data check (CSV)", Icon: FileSpreadsheet },
  { to: "/system", label: "Workflows & health", Icon: Network },
  { to: "/guide", label: "Guide", Icon: BookOpen },
];

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(() => { try { return localStorage.getItem("ghost.theme"); } catch { return null; } });
  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    try { if (theme) localStorage.setItem("ghost.theme", theme); else localStorage.removeItem("ghost.theme"); } catch { /* ignore */ }
  }, [theme]);
  const dark = theme ? theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return (
    <div className="seg theme-seg" role="group" aria-label={t("Colour theme")}>
      <button aria-pressed={!dark} className={!dark ? "on" : ""} onClick={() => setTheme("light")} title={t("Light mode")}><Sun size={14} /><span>{t("Light")}</span></button>
      <button aria-pressed={!!dark} className={dark ? "on" : ""} onClick={() => setTheme("dark")} title={t("Night mode")}><Moon size={14} /><span>{t("Night")}</span></button>
    </div>
  );
}

function OperatorLogin() {
  const { token, name, login, logout } = useOperator();
  const [open, setOpen] = useState(false);
  const [tok, setTok] = useState("");
  const [n, setN] = useState(name);
  if (token) return <button className="btn sm" onClick={logout} title={t("Sign out operator")}><LogOut size={14} /> {name}</button>;
  return (
    <>
      <button className="btn sm" onClick={() => setOpen(true)}><LogIn size={14} /> {t("Operator sign-in")}</button>
      {open && (
        <Modal title={t("Operator sign-in")} onClose={() => setOpen(false)}>
          <p className="secondary" style={{ margin: 0 }}>{t("Operator actions (acknowledge, resolve, replay controls, simulated faults) require the operator token configured in the backend .env file. It is kept in this browser tab only.")}</p>
          <label className="field">{t("Your name (recorded in audit trail)")}<input className="input" value={n} onChange={(e) => setN(e.target.value)} maxLength={60} /></label>
          <label className="field">{t("Operator token")}<input className="input" type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" /></label>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn primary" disabled={!tok} onClick={() => { login(tok.trim(), n.trim() || "operator"); setOpen(false); }}>{t("Sign in")}</button>
          </div>
        </Modal>
      )}
    </>
  );
}

function ClockBar() {
  const clock = useClock();
  const act = useAction();
  const { token } = useOperator();
  const c = clock.data;
  if (!c) return <span className="muted">{clock.error ? t("Backend unreachable") : t("Loading clock...")}</span>;
  return (
    <div className="row" style={{ gap: 8 }}>
      <span className="badge" title={c.label} style={{ borderColor: c.mode === "REPLAY" ? "var(--warning)" : "var(--good)" }}>{c.mode === "REPLAY" ? t("REPLAY") : t("LIVE")}</span>
      <span className="mono" title={t("Building time")}>{fmtEpoch(c.now)}</span>
      {c.mode === "REPLAY" && (
        <>
          <span className="muted" style={{ fontSize: 12 }}>{c.paused ? t("paused") : `x${c.speed}`}{c.atEnd ? ` · ${t("end of historian")}` : ""}</span>
          {token && (
            <button className="btn sm" aria-label={c.paused ? t("Resume replay") : t("Pause replay")} onClick={() => act(() => api.post("/api/clock", { action: c.paused ? "resume" : "pause" }), c.paused ? t("Replay resumed") : t("Replay paused"))}>
              {c.paused ? <Play size={13} /> : <Pause size={13} />}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function HealthDot() {
  const h = useApi<Health>(["health"], "/api/health", { refetchInterval: 15000 });
  const live = useLive();
  const color = !h.data ? "var(--info)" : h.data.status === "OK" ? "var(--good)" : h.data.status === "DEGRADED" ? "var(--warning)" : "var(--critical)";
  return (
    <NavLink to="/system" className="badge" title={t("System health")} style={{ textDecoration: "none" }}>
      <span className="dot" style={{ background: color }} /> {h.data?.status ? t(h.data.status) : h.error ? t("DOWN") : "..."}
      <span className="muted" style={{ fontWeight: 500 }}>· {t(live)}</span>
    </NavLink>
  );
}

export default function App() {
  const [menu, setMenu] = useState(false);
  const loc = useLocation();
  useEffect(() => setMenu(false), [loc.pathname]);
  const summary = useApi<Summary>(["summary"], "/api/dashboard/summary");
  const open = summary.data?.incidents.open ?? 0;
  return (
    <div className="app">
      <aside className={`sidebar ${menu ? "open" : ""}`} aria-label={t("Main navigation")}>
        <div className="brand"><GhostIcon size={26} title="Ghost Sensor" /><div>Ghost Sensor<small>{summary.data?.building?.name ?? t("Digital twin")}</small></div></div>
        <div className="partner-logo"><img src={eseoLogo} alt="ESEO - Grande école d'ingénieurs" /></div>
        <nav className="nav">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              {Icon ? <Icon size={17} /> : <GhostIcon size={17} />}{t(label)}
              {to === "/incidents" && open > 0 && <span className="count">{open}</span>}
            </NavLink>
          ))}
        </nav>
        <p className="muted" style={{ fontSize: 11.5, padding: "16px 10px 0" }}>
          <Activity size={12} /> {t("Data: OnDijon BMS historian (Jan-Jun 2026) + IFC model. Values marked")} <GhostIcon size={11} /> {t("are model estimates.")}
        </p>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="btn sm menu-btn" aria-label={t("Open navigation")} onClick={() => setMenu(!menu)}><Menu size={16} /></button>
          <ClockBar />
          <div className="spacer" />
          <HealthDot />
          <LangSwitch />
          <ThemeToggle />
          <OperatorLogin />
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/twin" element={<DigitalTwin />} />
            <Route path="/sensors" element={<Sensors />} />
            <Route path="/ghost" element={<GhostMonitor />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/incidents/:id" element={<IncidentDetail />} />
            <Route path="/investigation" element={<Investigation />} />
            <Route path="/investigation/:id" element={<Investigation />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/system" element={<SystemHealth />} />
            <Route path="/data" element={<DataCheck />} />
            <Route path="/data/:id" element={<DataCheck />} />
            <Route path="/guide" element={<Guide />} />
            <Route path="*" element={<div className="state"><FileText size={20} /><p>{t("Page not found.")} <NavLink to="/">{t("Back to overview")}</NavLink></p></div>} />
          </Routes>
        </main>
      </div>
      <ChatWidget />
    </div>
  );
}
