# Ghost Sensor — Topological Digital Twin, Virtual Sensors & Multi-Agent Incident Management

Ghost Sensor monitors the **Campus Métropolitain Dijon (ESEO / ESTP)** building from its real BMS
historian and IFC model. It detects sensor failures and anomalies. When a temperature sensor goes
silent, it infers the missing value from neighbouring rooms with a validated machine-learning model.
Four agents then document, analyse, and notify each incident. Orchestration runs through n8n, with a
built-in fallback.

Everything shown is derived from the data in this folder. No measurement, room, or adjacency is
invented. Inferred values are always labelled, and simulated faults are always labelled.

```
 IFC model ──► topology ETL ──┐                            ┌──► React dashboard (8 pages)
 MSI workbook ────────────────┼──► SQLite ◄──► FastAPI ────┤
 BMS CSV exports ─► historian ┘        ▲      backend      └──► n8n (WSL): workflows A→B→C→D, E
                                       │   (engine + agents)          │
                         RF virtual sensor ◄─┘                         └── authenticated HTTP
```

* **Presentation & how to run it:** [GUIDE.md](GUIDE.md)
* **Architecture & agents:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* **API reference:** [docs/API.md](docs/API.md) (interactive version at `http://localhost:8000/docs`)
* **n8n setup:** [docs/N8N_SETUP.md](docs/N8N_SETUP.md)
* **Model card (generated):** [docs/MODEL_CARD.md](docs/MODEL_CARD.md)
* **Troubleshooting:** [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## What is in the data (and what the platform does with it)

| Source | Content | Use |
|---|---|---|
| `V7-CAMPUS-DIJON-BATIMENT.ifc` | 387 `IfcSpace` (368 unique names), 9 storeys, 498 doors; **no** space boundaries | Room footprints. Adjacency is **derived from geometry**: shared wall ≥ 0.8 m within a 0.6 m gap, door contacts, and vertical overlap. Façade zones, aggregate occupancy zones and voids are excluded (332 real spaces kept). |
| `temperature_csv/` … `water_csv/` | Jan–Jun 2026, 5–10 min points, **UTC**, every row duplicated | De-duplicated and bucketed (15 min, or 1 h for meters). Energy/water cumulative counters become kWh/h, with counter resets detected. |
| `20220825 (On Dijon) Coordination MSI.xlsx` | Device → acquisition station, room equipment, feeders | Gateway evidence for root-cause analysis (e.g. all 31 ground-floor room controllers report via `OnDijon_Hub_03`). |

Facts found in the historian that the platform surfaces:
* A **building-wide outage** on 2026-03-03, ~09:45–16:00 UTC. Every `/OnDijon_Center_01/` point went silent while the energy meters kept reporting. Agent 3 ranks it **DATA_PIPELINE, STRONG**.
* **Recurring simultaneous silences** of the 31 ground-floor (N0) rooms (75 episodes in 60 days).
* Three temperature points (`00-02a-S`, `01-49-N`, `01-51-N`) and the humidity/lighting points **never** deliver a valid value.

The data ends on 2026-06-30, so the platform runs in **REPLAY** mode by default. A building clock
replays the historian at a configurable speed, and readings only become "received" once building
time passes them. The UI always shows `REPLAY`. LIVE mode plus `POST /api/ingest/measurements`
covers real-time ingestion.

## Quick start (Windows + n8n in WSL)

Prerequisites: Python 3.13 and Node 22. The Python packages are listed in `backend/requirements.txt`.

```powershell
# 1. configuration
copy .env.example .env        # then edit: GHOST_DATA_DIR, the two tokens, email/LLM if wanted
pip install -r backend/requirements.txt

# 2. build data (idempotent, ~2-3 min) and train the virtual-sensor model (~1 min)
cd backend
python -m ghost.etl.build_all
python -m ghost.ml.train            # writes data/models + docs/MODEL_CARD.md

# 3. run
python -m ghost.api.main            # API + engine on GHOST_HOST:GHOST_PORT (default 0.0.0.0:8000)
cd ..\frontend; npm install; npm run dev   # dashboard on http://localhost:5173
```

Or run `scripts/start-dev.ps1` after the build. Then:

1. Open `http://localhost:5173`.
2. Click **Operator sign-in** and paste `GHOST_OPERATOR_TOKEN`.
3. On **Workflows & health**, control the replay or inject a simulated outage. Example: `SILENCE` on `T-02-14-N`, then watch the **Ghost sensors** page.

Connect n8n: see [docs/N8N_SETUP.md](docs/N8N_SETUP.md). Until the n8n workflows are active, the
backend processes incidents itself after `GHOST_LOCAL_FALLBACK_AFTER_S` (300 s), so nothing is lost.

## Tests

```powershell
cd backend;  python -m pytest tests -q     # 22 tests on a temporary copy of the real database
cd frontend; npm test                      # 7 UI tests (physical vs inferred, formatting, sensors page)
npm run build                              # typecheck + production build
```

The backend suite covers acceptance scenarios A–J on real data: normal operation, outage, virtual
sensor, agent hand-off, email in test mode, SMTP failure and retry, duplicates, service outages,
recovery, data integrity, and an independent model validation. It also covers auth, lifecycle,
LLM-output validation, and the local fallback.

## Configuration summary

All settings live in environment variables or `.env` (template: `.env.example`). Secrets are never
written to code, configs or workflow JSON. Rule files:

* `backend/config/detection.json`: detection thresholds (range, spike, drift, environmental, CO₂, energy), severity rules, and escalations. Heartbeat timeouts are stored per sensor.
* `backend/config/notification.json`: per-severity email policy, retries with backoff, storm limits.

## Repository layout

```
backend/ghost/etl        IFC + MSI topology, CSV historian (build_all.py)
backend/ghost/ml         features (shared train/serve), training + evaluation
backend/ghost/engine     replay clock, readings + fault overlay, Agent 1 detection, virtual sensors, incidents, pipeline
backend/ghost/agents     Agent 2 documentation, Agent 3 analysis, Agent 4 notification, LLM providers, schemas
backend/ghost/api        FastAPI app, read / operator / automation routes, auth
backend/tests            pytest suite (real data)
frontend/src             React + TypeScript dashboard
n8n/workflows            exported, importable workflows A-E (no credentials inside)
docs/                    architecture, API, n8n setup, model card, troubleshooting
```

Docker is not used. It is not installed on this machine, and n8n already runs natively in WSL. The
backend and frontend run directly; see TROUBLESHOOTING for packaging notes.
