"""Structured contracts between the four agents (validated before persistence)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "SENSOR_OFFLINE", "SENSOR_STALE", "SENSOR_INVALID", "SENSOR_SPIKE", "SENSOR_DRIFT", "SENSOR_OUT_OF_RANGE",
    "ENVIRONMENTAL_ANOMALY", "ENERGY_ANOMALY", "TOPOLOGY_INCONSISTENCY", "UNKNOWN",
]
Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
Status = Literal["OPEN", "INVESTIGATING", "ACKNOWLEDGED", "RESOLVED", "CLOSED"]
SEVERITIES: list[str] = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NeighbourContext(Strict):
    room: str
    sensorId: str | None = None
    value: float | None = None
    status: str | None = None
    kind: str | None = None


class Connectivity(Strict):
    status: str
    lastTxAt: int | None = None
    lastValidAt: int | None = None
    silentForS: int | None = None


class AnomalyEvent(Strict):
    """Agent 1 output."""
    schemaVersion: Literal[1] = 1
    eventId: str
    dedupKey: str
    category: Category
    severity: Severity
    ruleId: str
    scope: Literal["SENSOR", "GROUP"]
    detectedAt: int
    onsetAt: int | None = None
    sensorId: str | None = None
    sensorType: str | None = None
    roomId: str | None = None
    sensorIds: list[str] = Field(default_factory=list)
    roomIds: list[str] = Field(default_factory=list)
    stations: list[str] = Field(default_factory=list)
    observedValue: float | None = None
    unit: str | None = None
    baseline: dict = Field(default_factory=dict)
    thresholds: dict = Field(default_factory=dict)
    connectivity: Connectivity | None = None
    neighbours: list[NeighbourContext] = Field(default_factory=list)
    evidence: list[str]
    severityReasons: list[str] = Field(default_factory=list)
    simulated: bool = False


class ReportSection(Strict):
    heading: str
    body: str


class IncidentReport(Strict):
    """Agent 2 structured output (the Markdown is rendered from it)."""
    incidentId: str
    title: str
    summary: str
    category: Category
    severity: Severity
    status: Status
    building: str
    rooms: list[str]
    sensors: list[str]
    detectedAt: int
    onsetAt: int | None
    transmission: str
    virtualSensor: dict
    observations: list[dict]
    baseline: dict
    neighbours: list[dict]
    evidence: list[str]
    thresholds: dict
    investigationNotes: list[str]
    relatedIncidents: list[dict]
    workflowRuns: list[dict]
    timeline: list[dict]
    dataProvenance: str


class Hypothesis(Strict):
    code: str
    title: str
    support: Literal["STRONG", "MODERATE", "WEAK"]
    score: float = Field(ge=0, le=1)
    explanation: str
    evidenceFor: list[str]
    evidenceAgainst: list[str]
    origin: Literal["RULE_ENGINE", "LLM"] = "RULE_ENGINE"


class Recommendation(Strict):
    action: str
    hypothesisCode: str | None
    urgency: Literal["IMMEDIATE", "WITHIN_24H", "PLANNED", "MONITOR"]
    expectedImpact: str
    validationMethod: str
    rationale: str
    origin: Literal["RULE_ENGINE", "LLM"] = "RULE_ENGINE"


class Analysis(Strict):
    """Agent 3 output."""
    incidentId: str
    facts: list[str]
    statisticalFindings: list[str]
    hypotheses: list[Hypothesis]
    recommendations: list[Recommendation]
    risk: dict
    sharedCause: dict | None
    modelVersions: dict
    confidenceNote: str


# ---- LLM narrative contracts (validated; anything else is rejected) ----

LLM_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narrative", "citedEvidence"],
    "properties": {
        "narrative": {"type": "string", "description": "3-6 sentence plain-language summary grounded only in the evidence."},
        "citedEvidence": {"type": "array", "items": {"type": "string"}, "description": "Evidence IDs (E1, E2, ...) used."},
    },
}

LLM_ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narrative", "citedEvidence", "additionalHypotheses"],
    "properties": {
        "narrative": {"type": "string"},
        "citedEvidence": {"type": "array", "items": {"type": "string"}},
        "additionalHypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "explanation", "citedEvidence"],
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "citedEvidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}
