/* Plain-language summaries for people who are not familiar with data or IT.
   Everything here is derived from fields the page already has; nothing is invented.
   Sentences are written per language (not word-by-word translated) so they read naturally. */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { MessageCircleQuestion } from "lucide-react";
import type { IncidentDetail, Severity, Summary } from "./api";
import { getLang, t } from "./i18n";

export function PlainSummary({ headline, children }: { headline: ReactNode; children: ReactNode }) {
  return (
    <section className="plain" aria-label={t("Summary in plain words")}>
      <div className="plain-head"><MessageCircleQuestion size={16} /> {t("In plain words")}</div>
      <p className="plain-headline">{headline}</p>
      {children}
    </section>
  );
}

const WHAT: Record<string, { en: string; fr: string }> = {
  TEMPERATURE: { en: "temperature", fr: "température" },
  CO2: { en: "air quality (CO₂)", fr: "qualité de l'air (CO₂)" },
  HUMIDITY: { en: "humidity", fr: "humidité" },
  LIGHTING: { en: "lighting", fr: "éclairage" },
  OCCUPANCY: { en: "room occupancy", fr: "occupation de la salle" },
  ENERGY: { en: "electricity use", fr: "consommation électrique" },
  WATER: { en: "water use", fr: "consommation d'eau" },
};

const SEVERITY_PLAIN: Record<Severity, { en: string; fr: string }> = {
  CRITICAL: { en: "Urgent: needs action right now", fr: "Urgent : il faut agir tout de suite" },
  HIGH: { en: "Important: should be handled today", fr: "Important : à traiter aujourd'hui" },
  MEDIUM: { en: "Worth checking soon", fr: "À vérifier bientôt" },
  LOW: { en: "Minor: no rush", fr: "Mineur : rien d'urgent" },
  INFO: { en: "For information only", fr: "Pour information seulement" },
};

const STATUS_PLAIN: Record<string, { en: string; fr: string }> = {
  OPEN: { en: "Nobody has taken charge of it yet.", fr: "Personne ne s'en occupe encore." },
  INVESTIGATING: { en: "The system is still analysing it. Nobody has taken charge of it yet.", fr: "Le système est encore en train de l'analyser. Personne ne s'en occupe encore." },
  ACKNOWLEDGED: { en: "Someone has taken charge of it.", fr: "Quelqu'un s'en occupe." },
  RESOLVED: { en: "It has been marked as fixed.", fr: "Il a été marqué comme réglé." },
  CLOSED: { en: "It is fixed and the case is closed.", fr: "Il est réglé et le dossier est clos." },
};

const fr = () => getLang() === "fr";
const L = (x: { en: string; fr: string }) => (fr() ? x.fr : x.en);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const n_ = (n: number, en1: string, enN: string, fr1: string, frN: string) => `${n} ${fr() ? (n > 1 ? frN : fr1) : n === 1 ? en1 : enN}`;
const OPEN_LINK = "/incidents?status=OPEN,INVESTIGATING,ACKNOWLEDGED";

export function OverviewPlain({ s }: { s: Summary }) {
  const bs = s.sensors.byStatus;
  const total = s.sensors.total;
  const ok = bs.ONLINE ?? 0;
  const silent = (bs.OFFLINE ?? 0) + (bs.STALE ?? 0);
  const invalid = bs.INVALID ?? 0;
  const sev = s.incidents.openBySeverity;
  const crit = sev.CRITICAL ?? 0, high = sev.HIGH ?? 0, medium = sev.MEDIUM ?? 0;
  const minor = (sev.LOW ?? 0) + (sev.INFO ?? 0);
  const open = s.incidents.open;
  const F = fr();

  const headline = crit > 0
    ? (F ? `Il faut agir maintenant : ${n_(crit, "", "", "problème urgent", "problèmes urgents")} dans le bâtiment.` : `Action needed now: ${n_(crit, "urgent problem", "urgent problems", "", "")} in the building.`)
    : high > 0
      ? (F ? `Le bâtiment fonctionne, mais ${n_(high, "", "", "problème important", "problèmes importants")} ${high > 1 ? "sont" : "est"} à traiter aujourd'hui.` : `The building is running, but ${n_(high, "important problem", "important problems", "", "")} should be handled today.`)
      : open > 0
        ? (F ? "Rien d'important à signaler. Il reste seulement de petits problèmes à vérifier quand vous aurez le temps." : "Everything important is fine. There are only small problems to check when you have time.")
        : (F ? "Tout fonctionne normalement. Il n'y a rien à faire." : "Everything is running normally. There is nothing to do.");

  const parts: string[] = [];
  if (crit) parts.push(F ? `${crit} urgent${crit > 1 ? "s" : ""}` : `${crit} urgent`);
  if (high) parts.push(F ? `${high} important${high > 1 ? "s" : ""}` : `${high} important`);
  if (medium) parts.push(F ? `${medium} à vérifier bientôt` : `${medium} to check soon`);
  if (minor) parts.push(F ? `${minor} mineur${minor > 1 ? "s" : ""}` : `${minor} minor`);

  return (
    <PlainSummary headline={headline}>
      <ul className="plain-list">
        {F ? <li><strong>{ok} sur {total}</strong> capteurs (les petits appareils qui mesurent la température, la qualité de l'air, l'électricité…) fonctionnent normalement.</li>
          : <li><strong>{ok} of {total}</strong> sensors (the small devices that measure temperature, air quality, electricity, etc.) are working normally.</li>}
        {silent > 0 && (F
          ? <li><strong>{n_(silent, "", "", "capteur", "capteurs")}</strong> {silent > 1 ? "n'envoient" : "n'envoie"} plus de mesures. C'est en général un problème de pile, de câble ou de réseau sur l'appareil lui-même, pas un problème dans la salle.</li>
          : <li><strong>{n_(silent, "sensor", "sensors", "", "")}</strong> stopped sending measurements. This is usually a battery, cable or network problem with the device itself, not a problem with the room.</li>)}
        {invalid > 0 && (F
          ? <li><strong>{n_(invalid, "", "", "capteur", "capteurs")}</strong> {invalid > 1 ? "envoient" : "envoie"} des valeurs impossibles, elles sont donc ignorées.</li>
          : <li><strong>{n_(invalid, "sensor", "sensors", "", "")}</strong> {invalid === 1 ? "sends" : "send"} numbers that cannot be right, so they are ignored.</li>)}
        {s.virtualSensorsActive > 0 && (F
          ? <li>Pour <strong>{n_(s.virtualSensorsActive, "", "", "capteur manquant", "capteurs manquants")}</strong>, le système <em>estime</em> la valeur à partir des salles voisines pour que vous voyiez quand même un chiffre. On les appelle capteurs « fantômes » ou « virtuels », et ils sont marqués comme estimés.</li>
          : <li>For <strong>{n_(s.virtualSensorsActive, "missing sensor", "missing sensors", "", "")}</strong>, the system <em>estimates</em> the value from nearby rooms so you still see a number. These are called "ghost" or "virtual" sensors and are marked as estimated.</li>)}
        {open > 0 && (F
          ? <li><strong>{n_(open, "", "", "problème", "problèmes")}</strong> {open > 1 ? "sont" : "est"} encore {open > 1 ? "ouverts" : "ouvert"} : {parts.join(", ")}.</li>
          : <li><strong>{n_(open, "problem", "problems", "", "")}</strong> {open === 1 ? "is" : "are"} still open: {parts.join(", ")}.</li>)}
        {s.health !== "OK" && (F
          ? <li>Le système de surveillance lui-même fonctionne en partie seulement ({t(s.health).toLowerCase()}). Certaines informations peuvent arriver en retard.</li>
          : <li>The monitoring system itself is partly not working ({s.health.toLowerCase()}). Some information may be late.</li>)}
      </ul>
      <p className="plain-todo"><strong>{F ? "Que faire :" : "What to do:"}</strong>{" "}
        {crit + high > 0
          ? (F ? <>ouvrez les <Link to={OPEN_LINK}>Incidents</Link> et commencez par ceux marqués <em>Critique</em> ou <em>Élevé</em>. Un e-mail est déjà envoyé automatiquement pour ceux-là.</>
            : <>open <Link to={OPEN_LINK}>Incidents</Link> and start with the problems marked <em>Critical</em> or <em>High</em>. An email is already sent automatically for these.</>)
          : open > 0
            ? (F ? <>rien d'urgent. Quand vous aurez le temps, parcourez les <Link to={OPEN_LINK}>Incidents</Link>.</> : <>nothing urgent. When you have time, look through <Link to={OPEN_LINK}>Incidents</Link>.</>)
            : (F ? "rien. Vous recevrez un e-mail si quelque chose d'important se produit." : "nothing. You will get an email if something important happens.")}
      </p>
    </PlainSummary>
  );
}

function whatHappened(d: IncidentDetail): string {
  const i = d.incident;
  const type = d.sensors[0]?.type;
  const F = fr();
  const what = type && WHAT[type] ? L(WHAT[type]) : F ? "mesure" : "measurement";
  const g = i.scope === "GROUP";
  const room = i.primaryRoomId ?? (F ? "(salle inconnue)" : "(unknown room)");
  if (F) {
    const who = g ? `${d.sensorCount} capteurs à la fois` : `le capteur de ${what} de la salle ${room}`;
    switch (i.category) {
      case "SENSOR_OFFLINE": return `${cap(who)} ${g ? "ont" : "a"} arrêté d'envoyer des mesures.`;
      case "SENSOR_STALE": return `${cap(who)} n'${g ? "ont" : "a"} pas envoyé de nouvelle mesure depuis plus longtemps que d'habitude.`;
      case "SENSOR_INVALID": return `${cap(who)} ${g ? "envoient" : "envoie"} des valeurs impossibles (vides ou absurdes).`;
      case "SENSOR_SPIKE": return `${cap(who)} ${g ? "ont" : "a"} brusquement sauté à une valeur très probablement fausse.`;
      case "SENSOR_DRIFT": return `${cap(who)} s'éloigne peu à peu de ce qu'indiquent les capteurs voisins. Il faudra peut-être le réétalonner.`;
      case "SENSOR_OUT_OF_RANGE": return `${cap(who)} ${g ? "indiquent" : "indique"} une valeur hors de la plage normale pour la ${what}.`;
      case "ENVIRONMENTAL_ANOMALY": return `La ${what} ${g ? "de plusieurs salles" : `de la salle ${room}`} semble anormale : par exemple trop chaud, trop froid ou air confiné.`;
      case "ENERGY_ANOMALY": return `${g ? "La consommation électrique" : `Le compteur ${i.primarySensorId}`} montre une consommation inhabituelle par rapport à la normale à cette heure-ci.`;
      case "TOPOLOGY_INCONSISTENCY": return "Les données ne correspondent pas au plan du bâtiment. Par exemple, un capteur semble rattaché à la mauvaise salle.";
      default: return i.title;
    }
  }
  const who = g ? `${d.sensorCount} sensors at the same time` : `the ${what} sensor in room ${room}`;
  switch (i.category) {
    case "SENSOR_OFFLINE": return `${cap(who)} stopped sending measurements.`;
    case "SENSOR_STALE": return `${cap(who)} ${g ? "have" : "has"} not sent a new measurement for longer than normal.`;
    case "SENSOR_INVALID": return `${cap(who)} ${g ? "are" : "is"} sending numbers that cannot be right (empty or impossible values).`;
    case "SENSOR_SPIKE": return `${cap(who)} suddenly jumped to a value that is very unlikely to be real.`;
    case "SENSOR_DRIFT": return `${cap(who)} slowly moved away from what similar sensors nearby show. It may need to be re-adjusted.`;
    case "SENSOR_OUT_OF_RANGE": return `${cap(who)} shows a value outside the normal range for ${what}.`;
    case "ENVIRONMENTAL_ANOMALY": return `The ${what} in ${g ? "several rooms" : `room ${room}`} looks unusual, for example too hot, too cold or stuffy air.`;
    case "ENERGY_ANOMALY": return `${g ? "Electricity use" : `The meter ${i.primarySensorId}`} shows unusual consumption compared with what is normal at this time.`;
    case "TOPOLOGY_INCONSISTENCY": return "The data does not match the building plan. For example, a sensor seems to be linked to the wrong room.";
    default: return i.title;
  }
}

function whatItMeans(d: IncidentDetail): string {
  const i = d.incident;
  const F = fr();
  if (i.scope === "GROUP") return F
    ? "Quand beaucoup de capteurs tombent en panne au même moment, la cause est généralement commune (coupure de réseau, d'électricité ou d'un automate), plutôt qu'une panne de chaque appareil."
    : "When many sensors fail at the same moment, the cause is usually shared, such as a network, power or controller outage, rather than each device breaking separately.";
  if (i.category.startsWith("SENSOR_")) return F
    ? "Le problème vient très probablement de l'appareil de mesure ou de sa connexion, pas de la salle elle-même. Tant qu'il n'est pas réparé, on ne peut pas faire entièrement confiance à ce qu'il affiche."
    : "The problem is most likely the measuring device or its connection, not the room itself. Until it is fixed, you cannot fully trust what this sensor shows.";
  if (i.category === "ENERGY_ANOMALY") return F
    ? "Un équipement est peut-être resté allumé, ou quelque chose consomme plus que prévu. Cela peut coûter de l'argent."
    : "Equipment may be left on, or something may be using more power than it should. This can cost money.";
  if (i.category === "ENVIRONMENTAL_ANOMALY") return F
    ? "Les personnes dans cette salle peuvent être mal à l'aise, ou le chauffage, la ventilation ou la climatisation doivent peut-être être réglés."
    : "People in this room may be uncomfortable, or the heating, ventilation or air-conditioning may need adjusting.";
  return F
    ? "Les informations affichées pour cette partie du bâtiment peuvent être fausses tant que les données ne sont pas corrigées."
    : "The information shown for this part of the building may be wrong until the data is corrected.";
}

export function IncidentPlain({ d }: { d: IncidentDetail }) {
  const i = d.incident;
  const F = fr();
  const firstStep = d.analysis?.recommendations.find((r) => r.status !== "DISMISSED" && r.status !== "DONE")?.action;
  const done = i.status === "RESOLVED" || i.status === "CLOSED";
  return (
    <PlainSummary headline={whatHappened(d)}>
      <ul className="plain-list">
        <li><strong>{F ? "Gravité :" : "How serious:"}</strong> {L(SEVERITY_PLAIN[i.severity])}.</li>
        <li><strong>{F ? "Ce que cela veut dire :" : "What it means:"}</strong> {whatItMeans(d)}</li>
        {i.virtualSensorActive && (F
          ? <li><strong>En attendant :</strong> le système estime les valeurs manquantes à partir des capteurs voisins, pour que vous voyiez quand même un chiffre. Il est marqué « estimé » ou « virtuel ».</li>
          : <li><strong>Meanwhile:</strong> the system estimates the missing values from nearby sensors, so you still see a number. It is marked as "estimated" or "virtual".</li>)}
        {i.conditionClearedAt && !done && (F
          ? <li><strong>Bonne nouvelle :</strong> le problème a cessé de lui-même, mais personne n'a encore confirmé qu'il est réglé.</li>
          : <li><strong>Good news:</strong> the problem has stopped by itself, but nobody has confirmed it is fixed yet.</li>)}
        <li><strong>{F ? "Où en est-on :" : "Where it stands:"}</strong> {STATUS_PLAIN[i.status] ? L(STATUS_PLAIN[i.status]) : i.status}</li>
        {i.simulated && (F
          ? <li><strong>Remarque :</strong> c'est un problème de <em>test</em> créé volontairement. Il n'est pas réel.</li>
          : <li><strong>Note:</strong> this is a <em>test</em> problem created on purpose. It is not real.</li>)}
      </ul>
      {!done && (
        <p className="plain-todo"><strong>{F ? "Que faire :" : "What to do:"}</strong>{" "}
          {firstStep ? <>{firstStep.replace(/\.\s*$/, "")}{F ? " (consigne technique, en anglais)" : ""}. </> : null}
          {F ? "Si vous n'êtes pas technicien, transmettez ceci à l'équipe de maintenance ou informatique en indiquant la référence " : "If you are not technical, forward this to the maintenance or IT team and give them the reference "}
          <strong className="mono">{i.id}</strong>.
        </p>
      )}
    </PlainSummary>
  );
}
