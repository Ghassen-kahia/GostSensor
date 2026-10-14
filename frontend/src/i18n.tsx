/* Dashboard language (English / French). Strings are written in English in the code and wrapped in t();
   FR maps each English string to its French version. A missing entry falls back to English. */
import { createContext, Fragment, ReactNode, useContext, useEffect, useState } from "react";
import { FR } from "./i18n-fr";

export type Lang = "en" | "fr";
const KEY = "ghost.lang";

function initial(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "en" || v === "fr") return v;
  } catch { /* storage blocked */ }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("fr") ? "fr" : "en";
}

let current: Lang = initial();
export const getLang = () => current;
export const locale = () => (current === "fr" ? "fr-FR" : "en-GB");

/** Translate an English UI string; {name} placeholders are filled from vars. */
export function t(en: string, vars?: Record<string, string | number | null | undefined>): string {
  let s = current === "fr" ? FR[en] ?? en : en;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v ?? ""));
  return s;
}

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: current, setLang: () => undefined });
export const useLang = () => useContext(Ctx);

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(current);
  current = lang;
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const setLang = (l: Lang) => {
    current = l;
    try { localStorage.setItem(KEY, l); } catch { /* ignore */ }
    setLangState(l);
  };
  // Remount the tree on change so every t() call re-runs; the query cache lives above and is kept.
  return <Ctx.Provider value={{ lang, setLang }}><Fragment key={lang}>{children}</Fragment></Ctx.Provider>;
}

function FlagFR() {
  return (
    <svg viewBox="0 0 3 2" width="22" height="15" aria-hidden>
      <rect width="1" height="2" x="0" fill="#002654" /><rect width="1" height="2" x="1" fill="#fff" /><rect width="1" height="2" x="2" fill="#ce1126" />
    </svg>
  );
}

function FlagUK() {
  return (
    <svg viewBox="0 0 60 30" width="22" height="15" aria-hidden>
      <clipPath id="uk-s"><path d="M0,0 v30 h60 v-30 z" /></clipPath>
      <clipPath id="uk-t"><path d="M30,15 h30 v15 z v15 h-30 z h-30 v-15 z v-15 h30 z" /></clipPath>
      <g clipPath="url(#uk-s)">
        <path d="M0,0 v30 h60 v-30 z" fill="#012169" />
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" strokeWidth="6" />
        <path d="M0,0 L60,30 M60,0 L0,30" clipPath="url(#uk-t)" stroke="#C8102E" strokeWidth="4" />
        <path d="M30,0 v30 M0,15 h60" stroke="#fff" strokeWidth="10" />
        <path d="M30,0 v30 M0,15 h60" stroke="#C8102E" strokeWidth="6" />
      </g>
    </svg>
  );
}

export function LangSwitch() {
  const { lang, setLang } = useLang();
  return (
    <div className="seg flag-seg" role="group" aria-label={t("Language")}>
      <button aria-pressed={lang === "en"} className={lang === "en" ? "on" : ""} onClick={() => setLang("en")} title="English" aria-label="English"><FlagUK /></button>
      <button aria-pressed={lang === "fr"} className={lang === "fr" ? "on" : ""} onClick={() => setLang("fr")} title="Français" aria-label="Français"><FlagFR /></button>
    </div>
  );
}
