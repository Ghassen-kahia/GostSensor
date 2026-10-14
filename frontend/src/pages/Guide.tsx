import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Bot, Building2, FileSpreadsheet, Gauge, Mail, Network, PlayCircle, Radio, ShieldAlert } from "lucide-react";
import { Card, GhostIcon, Seg } from "../components";
import { getLang, Lang } from "../i18n";

/* The guide text is written per language rather than translated string by string. */
type Txt = { en: string; fr: string };
const L = (x: Txt) => (getLang() === "fr" ? x.fr : x.en);

const VIDEOS: Record<Lang, { src: string; vtt: string; title: string }> = {
  en: { src: "/api/media/guide-en.mp4", vtt: "/api/media/guide-en.vtt", title: "Ghost Sensor - walkthrough (English)" },
  fr: { src: "/api/media/guide-fr.mp4", vtt: "/api/media/guide-fr.vtt", title: "Ghost Sensor - présentation (français)" },
};

const PAGES: { to: string; Icon: typeof Gauge | null; name: Txt; what: Txt }[] = [
  { to: "/", Icon: Gauge, name: { en: "Executive overview", fr: "Vue d'ensemble" },
    what: { en: "Start here. The green box sums up the building in plain words: how many sensors work, how many problems are open and what to do.",
      fr: "Commencez ici. L'encadré vert résume le bâtiment en langage simple : combien de capteurs fonctionnent, combien de problèmes sont ouverts et que faire." } },
  { to: "/twin", Icon: Building2, name: { en: "Digital twin", fr: "Jumeau numérique" },
    what: { en: "The building's floor plans, storey by storey. Click a room to see its sensors and neighbouring rooms. Violet rooms use a ghost sensor, red-edged rooms have a broken sensor.",
      fr: "Les plans du bâtiment, étage par étage. Cliquez sur une salle pour voir ses capteurs et ses voisines. Les salles violettes utilisent un capteur fantôme, celles bordées de rouge ont un capteur en panne." } },
  { to: "/sensors", Icon: Radio, name: { en: "Live sensors", fr: "Capteurs en direct" },
    what: { en: "Every sensor with its latest value. Use the filters to find offline sensors or one room. Click a row to see its history.",
      fr: "Tous les capteurs avec leur dernière valeur. Utilisez les filtres pour trouver les capteurs hors ligne ou une salle. Cliquez sur une ligne pour voir son historique." } },
  { to: "/ghost", Icon: null, name: { en: "Ghost sensors", fr: "Capteurs fantômes" },
    what: { en: "When a temperature sensor goes silent, the system estimates its value from nearby rooms. This page shows those estimates and how reliable they are.",
      fr: "Quand un capteur de température se tait, le système estime sa valeur à partir des salles voisines. Cette page montre ces estimations et leur fiabilité." } },
  { to: "/incidents", Icon: ShieldAlert, name: { en: "Incidents", fr: "Incidents" },
    what: { en: "The list of problems found. Open one to read what happened, its likely cause and the recommended next steps, then acknowledge or resolve it.",
      fr: "La liste des problèmes détectés. Ouvrez-en un pour lire ce qui s'est passé, sa cause probable et les prochaines étapes, puis prenez-le en charge ou résolvez-le." } },
  { to: "/investigation", Icon: Bot, name: { en: "AI investigation", fr: "Enquête IA" },
    what: { en: "What each of the four automatic agents did for an incident: detect, document, explain and alert.",
      fr: "Ce que chacun des quatre agents automatiques a fait pour un incident : détecter, documenter, expliquer et alerter." } },
  { to: "/reports", Icon: BarChart3, name: { en: "Reports & analytics", fr: "Rapports et analyses" },
    what: { en: "Downloadable incident reports (PDF / Markdown) and statistics: how fast problems are detected and fixed, which sensors fail most.",
      fr: "Rapports d'incident téléchargeables (PDF / Markdown) et statistiques : rapidité de détection et de résolution, capteurs qui tombent le plus souvent en panne." } },
  { to: "/data", Icon: FileSpreadsheet, name: { en: "Data check (CSV)", fr: "Contrôle de données (CSV)" },
    what: { en: "Upload a sensor export to check its quality (gaps, impossible values, duplicates) before using it.",
      fr: "Envoyez un export de capteurs pour vérifier sa qualité (trous, valeurs impossibles, doublons) avant de l'utiliser." } },
  { to: "/system", Icon: Network, name: { en: "Workflows & health", fr: "Workflows et santé" },
    what: { en: "Checks that the platform itself works (database, n8n, AI, email). Operators can also control the replay and inject test faults here.",
      fr: "Vérifie que la plateforme elle-même fonctionne (base de données, n8n, IA, e-mail). Les opérateurs peuvent aussi piloter le rejeu et injecter des pannes de test." } },
];

const COLOURS: { swatch: string; name: Txt; meaning: Txt }[] = [
  { swatch: "var(--critical)", name: { en: "Critical", fr: "Critique" }, meaning: { en: "act now; an email is sent and repeated after 30 min if nobody reacts", fr: "agir tout de suite ; un e-mail est envoyé, puis relancé après 30 min sans réaction" } },
  { swatch: "var(--serious)", name: { en: "High", fr: "Élevé" }, meaning: { en: "handle today; an email is sent", fr: "à traiter aujourd'hui ; un e-mail est envoyé" } },
  { swatch: "var(--warning)", name: { en: "Medium", fr: "Moyen" }, meaning: { en: "check soon; shown on the dashboard only", fr: "à vérifier bientôt ; affiché sur le tableau de bord seulement" } },
  { swatch: "var(--series-1)", name: { en: "Low / Info", fr: "Faible / Info" }, meaning: { en: "minor, no rush", fr: "mineur, rien d'urgent" } },
  { swatch: "var(--ghost)", name: { en: "Violet ghost", fr: "Fantôme violet" }, meaning: { en: "an estimated value, not a real measurement", fr: "une valeur estimée, pas une vraie mesure" } },
];

const STEPS: Txt[] = [
  { en: "You receive an email, or see a red number next to Incidents in the menu.", fr: "Vous recevez un e-mail, ou voyez un nombre rouge à côté d'Incidents dans le menu." },
  { en: "Open the incident and read the green \"In plain words\" box at the top.", fr: "Ouvrez l'incident et lisez l'encadré vert « En clair » en haut." },
  { en: "Sign in as operator (top-right) and click Acknowledge so others know you are on it.", fr: "Connectez-vous en tant qu'opérateur (en haut à droite) et cliquez sur Prendre en charge pour que les autres sachent que vous vous en occupez." },
  { en: "Follow the recommended next step, or forward the incident reference to the maintenance / IT team.", fr: "Suivez l'étape recommandée, ou transmettez la référence de l'incident à l'équipe de maintenance / informatique." },
  { en: "When it is fixed, click Resolve and add a short note. A confirmation email is sent.", fr: "Une fois réglé, cliquez sur Résoudre et ajoutez une courte note. Un e-mail de confirmation est envoyé." },
];

const GLOSSARY: { term: Txt; def: Txt }[] = [
  { term: { en: "Sensor", fr: "Capteur" }, def: { en: "a small device in a room that measures temperature, air quality (CO₂), electricity, etc.", fr: "un petit appareil dans une salle qui mesure la température, la qualité de l'air (CO₂), l'électricité, etc." } },
  { term: { en: "Offline", fr: "Hors ligne" }, def: { en: "the sensor stopped sending measurements.", fr: "le capteur n'envoie plus de mesures." } },
  { term: { en: "Ghost / virtual sensor", fr: "Capteur fantôme / virtuel" }, def: { en: "a value estimated by the computer when the real sensor is silent.", fr: "une valeur estimée par l'ordinateur quand le vrai capteur se tait." } },
  { term: { en: "Incident", fr: "Incident" }, def: { en: "a problem the system found and is tracking until someone resolves it.", fr: "un problème trouvé par le système et suivi jusqu'à ce que quelqu'un le résolve." } },
  { term: { en: "Replay", fr: "Rejeu" }, def: { en: "the dashboard is replaying real data recorded in 2026, sped up, instead of live data.", fr: "le tableau de bord rejoue des données réelles enregistrées en 2026, en accéléré, au lieu de données en direct." } },
  { term: { en: "Agent", fr: "Agent" }, def: { en: "an automatic program that does one job: detect, document, explain or alert.", fr: "un programme automatique qui fait une seule tâche : détecter, documenter, expliquer ou alerter." } },
];

export default function Guide() {
  const [lang, setLang] = useState<Lang>(getLang());
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const v = VIDEOS[lang];
  const fr = getLang() === "fr";
  return (
    <>
      <div className="page-head">
        <div><h1>{fr ? "Guide du tableau de bord" : "Dashboard guide"}</h1>
          <p>{fr ? "Une visite vidéo, puis l'essentiel par écrit : à quoi sert chaque page, comment lire les couleurs et que faire quand un problème apparaît."
            : "A video tour, then the essentials in writing: what each page is for, how to read the colours and what to do when a problem appears."}</p></div>
      </div>

      <Card title={<span className="row"><PlayCircle size={16} /> {fr ? "Visite vidéo" : "Video tour"}</span>}
        right={<Seg label={fr ? "Langue de la vidéo" : "Video language"} value={lang} onChange={setLang} items={[{ id: "fr", label: "Français" }, { id: "en", label: "English" }]} />}
        note={fr ? "Vidéo accélérée à 1,5×. Sous-titres disponibles via le bouton CC. Vous pouvez encore changer la vitesse dans le menu ⋮ du lecteur."
          : "Video sped up to 1.5×. Subtitles are available with the CC button. You can still change the speed from the player's ⋮ menu."}>
        {failed[lang] ? (
          <div className="state">{fr ? "La vidéo n'est pas disponible sur ce serveur (fichier absent du dossier media)." : "The video is not available on this server (file missing from the media folder)."}</div>
        ) : (
          <video key={lang} className="guide-video" controls preload="metadata" title={v.title}>
            <source src={v.src} type="video/mp4" onError={() => setFailed((f) => ({ ...f, [lang]: true }))} />
            <track kind="subtitles" src={v.vtt} srcLang={lang} label={lang === "fr" ? "Français" : "English"} />
          </video>
        )}
      </Card>

      <Card title={fr ? "Les pages du menu" : "The pages in the menu"}>
        <div className="guide-pages">
          {PAGES.map((p) => (
            <Link key={p.to} to={p.to} className="guide-page">
              <strong className="row" style={{ gap: 6 }}>{p.Icon ? <p.Icon size={16} /> : <GhostIcon size={16} />}{L(p.name)}</strong>
              <span className="secondary">{L(p.what)}</span>
            </Link>
          ))}
        </div>
      </Card>

      <div className="grid cols-2">
        <Card title={fr ? "Quand un problème apparaît" : "When a problem appears"}>
          <ol className="guide-steps">{STEPS.map((s, i) => <li key={i}>{L(s)}</li>)}</ol>
          <p className="muted" style={{ fontSize: 12.5, margin: "8px 0 0" }}><Mail size={12} /> {fr ? "Les e-mails ne sont envoyés que pour les problèmes Critiques et Élevés, au plus 12 par heure." : "Emails are only sent for Critical and High problems, at most 12 per hour."}</p>
        </Card>
        <Card title={fr ? "Lire les couleurs" : "Reading the colours"}>
          <ul className="guide-legend">
            {COLOURS.map((c) => <li key={c.name.en}><i style={{ background: c.swatch }} /><strong>{L(c.name)}</strong> - {L(c.meaning)}</li>)}
          </ul>
        </Card>
      </div>

      <Card title={fr ? "Petit lexique" : "Glossary"}>
        <dl className="kv">{GLOSSARY.map((g) => <Fragment key={g.term.en}><dt>{L(g.term)}</dt><dd>{L(g.def)}</dd></Fragment>)}</dl>
      </Card>
    </>
  );
}
