# API reference

Base URL: `http://<host>:8000`. The interactive OpenAPI docs are at `/docs` (schema: `/openapi.json`).
Times are ISO-8601 UTC strings. "Building time" is the replay clock in REPLAY mode.

| Auth | Header | Used by |
|---|---|---|
| read | none when `GHOST_ALLOW_ANONYMOUS_READ=true`, otherwise `Authorization: Bearer <GHOST_OPERATOR_TOKEN>` | dashboard |
| operator | `Authorization: Bearer <GHOST_OPERATOR_TOKEN>`, optional `X-Operator-Name` (audit trail) | dashboard actions |
| automation | `X-Ghost-Token: <GHOST_AUTOMATION_TOKEN>` | n8n |

Errors use `{"detail": "..."}` with status 400 (bad request), 401, 404, 409 (invalid state
transition), 413 (body too large), 422 (validation; unknown fields are rejected), 429 (rate limit),
500, or 503 (feature disabled because a token is not configured).

## Reading object (physical vs inferred)

Returned by `/api/sensors/{id}/current` and embedded as `reading` in sensor lists:

```json
{
  "sensorId": "T-02-14-N", "type": "TEMPERATURE", "unit": "degC", "roomId": "02-14-N",
  "sensorStatus": "STALE",               // ONLINE | STALE | OFFLINE | INVALID | NO_DATA
  "source": "INFERRED",                  // PHYSICAL | INFERRED | NONE
  "value": 19.54,
  "observedAt": null,                    // set only for PHYSICAL
  "inferredAt": "2026-03-04T03:30:00Z",  // set only for INFERRED
  "modelVersion": "temp-rf-20261012-9d32496b8e",
  "uncertainty": {"lower": 19.17, "upper": 19.92, "level": 0.9, "method": "split-conformal prediction interval from held-out validation"},
  "inferenceQuality": "GOOD", "qualityReasons": [],
  "incidentId": "INC-20260304-E33D63",
  "lastPhysical": {"value": 19.68, "observedAt": "2026-03-04T02:30:00Z"},
  "lastTransmissionAt": "2026-03-04T02:30:00Z", "simulatedFault": true
}
```

The `source` field is decided on the server:
* `PHYSICAL` only when the sensor is ONLINE with a fresh valid reading.
* `INFERRED` only when the sensor is STALE, OFFLINE or INVALID and a prediction newer than its last valid reading exists.
* `NONE` otherwise. In that case `value` is null and the last physical value appears only under `lastPhysical`.

## Read endpoints

| Method & path | Description |
|---|---|
| `GET /api/health` | Overall and per-component status (database, engine, ML model, n8n, LLM, email), clock, last detection, last sensor update |
| `GET /api/clock` | Building clock (`mode`, `now`, `speed`, `paused`, data range) |
| `GET /api/dashboard/summary` | KPI counts, open incidents by severity, active virtual sensors, recent runs and notifications |
| `GET /api/dashboard/timeseries?hours=48` | Temperature mean/min/max with physical vs inferred counts, availability %, electricity kWh/h, anomaly frequency, severity distribution |
| `GET /api/topology?storey=N1` | Storeys, room footprints (local metres) with status overlay, adjacency edges |
| `GET /api/rooms?storey=&q=&instrumented=&allSpaces=` | Room list (non-room IFC spaces only with `allSpaces=true`) |
| `GET /api/rooms/{roomId}` | Room, sensors with readings, neighbours with readings, MSI equipment, incidents |
| `GET /api/rooms/{roomId}/neighbors` | Adjacent rooms with edge kind and justification |
| `GET /api/sensors?type=&status=&source=&storey=&q=&room=&anomaly=&sort=&order=&page=&pageSize=` | Paginated sensors with readings |
| `GET /api/sensors/{sensorId}` | Sensor metadata, reading, room, simulated faults |
| `GET /api/sensors/{sensorId}/current` | Reading object |
| `GET /api/sensors/{sensorId}/history?hours=48` | Physical buckets + inferred values with intervals (and the true value for simulated outages) |
| `GET /api/virtual-sensors` | Active and recently recovered virtual sensors with inputs, baseline and history |
| `GET /api/virtual-sensors/validation` | Offline evaluation (model card), conformal table, live validation on simulated outages, usage |
| `GET /api/incidents?status=&severity=&category=&scope=&q=&simulated=&sort=&page=` | Paginated incidents (comma-separated filters allowed) |
| `GET /api/incidents/{id}` | Incident, sensors, events, timeline, report summary, analysis, notifications, agent runs |
| `GET /api/incidents/{id}/report?format=json\|md\|html&version=` | Technical report (Markdown download; printable HTML for PDF) |
| `GET /api/incidents/{id}/recommendations` | Hypotheses and recommendations of the latest analysis |
| `GET /api/incidents/{id}/agents` | Per-agent outputs and runs (AI investigation centre) |
| `GET /api/reports?q=&severity=&category=&page=` | Latest report per incident |
| `GET /api/reports/export.csv?severity=&status=` | CSV export of incidents |
| `GET /api/analytics` | MTTD, MTTR, failures by type, most affected sensors, trend, hypothesis outcomes, virtual-sensor metrics |
| `GET /api/system/workflows` | n8n reachability and last call, pending work, per-agent statistics, runs, workflow/error events, n8n executions (if `N8N_API_KEY`) |
| `GET /api/notifications?status=&limit=` | Delivery log and email configuration status |
| `GET /api/simulation/faults?active=` | Simulated faults |
| `GET /api/stream` | Server-Sent Events: `tick`, `incident.updated` (the dashboard falls back to polling) |

## Operator endpoints

| Method & path | Body |
|---|---|
| `POST /api/incidents/{id}/status` | `{"status": "INVESTIGATING\|ACKNOWLEDGED\|RESOLVED\|CLOSED\|OPEN", "note": "..."}` — allowed transitions OPEN→{INVESTIGATING, ACKNOWLEDGED, RESOLVED}, INVESTIGATING↔ACKNOWLEDGED, →RESOLVED, RESOLVED→{CLOSED, OPEN}; 409 otherwise |
| `POST /api/hypotheses/{id}/outcome` | `{"outcome": "CONFIRMED\|REJECTED\|null", "note": "..."}` |
| `POST /api/recommendations/{id}/status` | `{"status": "PROPOSED\|ACCEPTED\|DONE\|DISMISSED", "note": "..."}` |
| `POST /api/incidents/{id}/reprocess` | — runs Agents 2-4 now |
| `POST /api/clock` | `{"action": "pause\|resume\|speed\|jump", "speed": 60, "toTs": 1772517600}` (jumps move forward only) |
| `POST /api/simulation/faults` | `{"sensorId": "T-02-14-N", "kind": "SILENCE\|NULL_VALUES\|OFFSET\|SPIKE", "durationMinutes": 120, "delta": 5, "ratePerDay": 0.5, "note": "..."}` |
| `DELETE /api/simulation/faults/{id}` | ends the fault (the sensor recovers) |
| `POST /api/notifications/test` | `{"confirm": true}` — test mode writes an .eml file; SMTP mode really sends |
| `POST /api/admin/reset` | `{"confirm": "RESET", "startAt": "2026-03-02T06:00:00Z"}` — **destructive**: wipes runtime tables and restarts the replay (paused) |
| `POST /api/detection/run` | forces Agent 1 now |

## Automation endpoints (n8n)

| Method & path | Body → response |
|---|---|
| `POST /api/automation/detect` | `{"executionId", "workflow", "force": false}` → `{"evaluated", "summary", "pending", "clock"}`. A no-op if the current bucket was already evaluated. |
| `POST /api/automation/claim` | `{"executionId", "limit": 5}` → `{"items": [{"incidentId", "version", "severity", "status"}]}`. Atomic; stale claims are taken over. |
| `GET /api/automation/incidents/{id}/context` | Full evidence bundle used by Agents 2/3 |
| `POST /api/agents/documentation` | `{"incidentId", "version", "executionId", "workflow"}` → `{"valid", "version", "created", "llm"}` |
| `POST /api/agents/analysis` | same → `{"valid", "version", "leadingHypothesis", "llm"}` |
| `POST /api/agents/notification` | same → `{"valid", "actions": [{"kind", "status", "reason"}], "policy"}`; marks the version processed |
| `POST /api/automation/events` | `{"level", "kind", "message", "workflow", "executionId", "node", "details"}` (keys resembling secrets are redacted) |
| `POST /api/ingest/measurements` | `{"measurements": [{"sensorId", "ts", "value"}]}` (LIVE mode; bucketed like the historian) |

Passing a `version` older than the processed one makes the agent endpoints return
`{"skipped": true}`. This makes retries safe.
