// Typed client for the Ghost Sensor API. The operator token is entered by the user and kept in
// sessionStorage only (never bundled into the build).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const TOKEN_KEY = "ghost.operatorToken";
const NAME_KEY = "ghost.operatorName";

export const auth = {
  token(): string | null {
    try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  name(): string {
    try { return sessionStorage.getItem(NAME_KEY) || "operator"; } catch { return "operator"; }
  },
  set(token: string, name: string) {
    try { sessionStorage.setItem(TOKEN_KEY, token); sessionStorage.setItem(NAME_KEY, name); } catch { /* storage unavailable */ }
  },
  clear() {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ }
  },
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = auth.token();
  // Always sent when present: GETs need it too if the backend disables anonymous reads.
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    headers["X-Operator-Name"] = auth.name();
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Backend unreachable - check that the API is running.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

// ------------------------------------------------------------------ types
export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type SensorStatus = "ONLINE" | "STALE" | "OFFLINE" | "INVALID" | "NO_DATA";
export type Source = "PHYSICAL" | "INFERRED" | "NONE";

export interface Clock {
  mode: "REPLAY" | "LIVE"; now: number; speed: number; paused: boolean; dataStart: number; dataEnd: number; atEnd: boolean; label: string;
}

export interface Reading {
  sensorId: string; type: string; unit: string; roomId: string | null; sensorStatus: SensorStatus; anomaly: string | null;
  incidentId: string | null; simulatedFault: boolean; source: Source; value: number | null; observedAt: string | null;
  inferredAt: string | null; modelVersion: string | null;
  uncertainty: { lower: number; upper: number; level: number; method: string } | null;
  inferenceQuality: "GOOD" | "DEGRADED" | "INSUFFICIENT" | null; qualityReasons?: string[];
  lastPhysical: { value: number; observedAt: string } | null; lastTransmissionAt: string | null; evaluatedAt: string | null;
}

export interface Sensor {
  id: string; externalId: string; roomId: string | null; type: string; unit: string; displayName: string | null; floor: string | null;
  zone: string | null; station: string | null; stationSource: string | null; resolutionS: number; staleAfterS: number; offlineAfterS: number;
  meta: Record<string, unknown>; storey?: string; reading: Reading;
}

export interface Incident {
  id: string; title: string; category: string; severity: Severity; status: string; scope: "SENSOR" | "GROUP";
  primarySensorId: string | null; primaryRoomId: string | null; occurrences: number; acknowledgedBy: string | null;
  resolvedBy: string | null; resolutionNote: string | null; pipelineStage: string; pipelineVersion: number; processedVersion: number;
  claimedBy: string | null; summary: string | null; onsetAt: string | null; detectedAt: string; lastSeenAt: string;
  conditionClearedAt: string | null; acknowledgedAt: string | null; resolvedAt: string | null; closedAt: string | null;
  virtualSensorActive: boolean; simulated: boolean; sensorCount?: number; updatedAtReal: string;
}

export interface Hypothesis {
  id: number; rank: number; code: string; title: string; support: "STRONG" | "MODERATE" | "WEAK"; score: number; explanation: string;
  evidenceFor: string[]; evidenceAgainst: string[]; origin: "RULE_ENGINE" | "LLM"; outcome: "CONFIRMED" | "REJECTED" | null;
  outcomeBy: string | null; outcomeAt: string | null;
}

export interface Recommendation {
  id: number; rank: number; hypothesisCode: string | null; action: string; urgency: string; expectedImpact: string;
  validationMethod: string; rationale: string; status: string; statusBy: string | null; statusNote: string | null; origin: string;
}

export interface Analysis {
  version: number; createdAt: string; facts: string[]; statisticalFindings: string[]; risk: Record<string, unknown>;
  sharedCause: { count: number; note: string; concurrentIncidents: string[] } | null; modelVersions: Record<string, string>;
  confidenceNote: string; aiNarrative: string | null; llmModel: string | null; hypotheses: Hypothesis[]; recommendations: Recommendation[];
}

export interface NotificationRec {
  id: number; kind: string; severity: Severity; channel: string; recipients: string[]; subject: string | null; status: string;
  statusReason: string | null; attempts: number; lastError: string | null; providerResponse: string | null; createdAt: string;
  sentAt: string | null; nextAttemptAt: string | null; attemptLog?: { attempt_no: number; at: string; success: number; error: string | null; provider_response: string | null }[];
}

export interface AgentRun {
  id: number; agent: string; incident_id: string | null; status: string; trigger: string; n8n_execution_id: string | null;
  n8n_workflow: string | null; startedAt: string; finishedAt: string | null; duration_ms: number | null; summary: string | null; error: string | null;
  details?: Record<string, unknown> | null;
}

export interface TimelineEntry { at: string | null; realAt: string; kind: string; actor: string; message: string }

export interface IncidentDetail {
  incident: Incident; sensors: Sensor[]; sensorCount: number;
  events: { id: string; category: string; severity: Severity; ruleId: string; detectedAt: string; payload: Record<string, any> }[];
  eventCount: number; timeline: TimelineEntry[]; report: { version: number; summary: string; ai_narrative: string | null; llm_model: string | null; createdAt: string } | null;
  analysis: Analysis | null; notifications: NotificationRec[]; agentRuns: AgentRun[];
}

export interface Page<T> { items: T[]; total: number; page: number; pageSize: number }

export interface Health {
  status: "OK" | "DEGRADED" | "DOWN"; coreMonitoring: string; clock: Clock; lastSensorUpdate: string | null;
  lastDetection: Record<string, any> | null; components: Record<string, Record<string, any> & { status: string }>;
}

export interface Summary {
  clock: Clock; building: { id: string; name: string } | null;
  rooms: { total: number; instrumented: number; otherIfcSpaces: number };
  sensors: { total: number; byStatus: Record<string, number>; byType: Record<string, Record<string, number>> };
  incidents: { open: number; openBySeverity: Partial<Record<Severity, number>>; byStatus: Record<string, number> };
  virtualSensorsActive: number; recentRuns: AgentRun[];
  recentNotifications: { id: number; incident_id: string; kind: string; severity: Severity; channel: string; status: string; status_reason: string | null; subject: string | null; createdAt: string }[];
  health: string;
}
