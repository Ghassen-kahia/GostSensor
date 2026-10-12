# Ghost Sensor — Project Presentation & Run Guide

> **Ghost Sensor** is a digital twin of the Campus Métropolitain Dijon building (ESEO / ESTP).
> It watches every sensor, notices when one breaks, **replaces the missing value with a
> machine-learning "ghost" sensor**, and lets four AI agents document, explain and report each
> incident. n8n orchestrates the agents.

---

## Part 1 — Presentation

### The problem

A smart building has hundreds of sensors (temperature, CO₂, energy, occupancy…). Sensors fail all
the time: a battery dies, a gateway disconnects, a data pipeline stops. When that happens:

* the building management system loses visibility of the affected rooms;
* nobody knows immediately **why** it failed: one broken sensor, a network gateway, or the whole data pipeline;
* the technical team gets either no alert or a flood of alerts.

The real data of this building shows exactly this:
* **2026-03-03:** a building-wide outage. Every temperature and CO₂ sensor went silent for about 6 h.
* **January:** the 31 ground-floor rooms repeatedly went silent together.
* Three temperature sensors **never** delivered a valid value.

### The solution

| Capability | What it does |
|---|---|
| 🏢 **Digital twin** | Rebuilds the building from its IFC (BIM) model: 332 rooms with their real floor plans, and which room touches which (shared walls, doors, floors above and below). |
| 📡 **Live monitoring** | Follows all 618 sensors and knows, for each one, whether it is online, stale, offline, or sending invalid values. |
| 👻 **Ghost sensors** | When a temperature sensor goes silent, a Random Forest model estimates the missing value from neighbouring rooms and the room's history. The estimate comes with a 90% uncertainty range and is **always labelled "Capteur Virtuel Actif"**: it is never shown as a real measurement. |
| 🤖 **4 AI agents** | **1 Detection**: finds and classifies the problem. **2 Documentation**: writes the incident report. **3 Root cause**: proposes ranked causes with evidence for and against, plus recommended actions. **4 Alerting**: emails the right people, without duplicates. |
| 🔁 **n8n automation** | Five workflows chain the agents (A → B → C → D, plus E for errors). If n8n is down, the backend runs the agents itself. |
| 📊 **Dashboard** | 9 pages: overview, digital twin, live sensors, ghost sensors, incidents, AI investigation, reports & analytics, data check, system health. |
| 💬 **Assistant (chatbot)** | A chat button on every page. Ask anything about the building ("which sensors are offline?", "explain INC-…", "how accurate is the model?", "what's wrong with my uploaded CSV?"). It answers from the platform's real data through read-only lookups, and shows which lookups it made. With an AI key it handles any free-form question; without one it runs in a built-in mode for the common questions. |
| 📁 **Data check (CSV)** | Upload any sensor CSV (temperature, humidity, CO₂, energy, water, lighting, occupancy) and get a quality report: format detection, duplicates, empty values, transmission gaps, out-of-range values, spikes, frozen values, unknown rooms, and comparison with the platform historian, with a chart per series. Files are analysed, never imported. |

### Results on real data

| | Result |
|---|---|
| Virtual-sensor accuracy (held-out May–June data) | **MAE 0.36 °C**, RMSE 0.60 °C, against 0.68 °C for "repeat the last value" and 0.385 °C for the best simple method |
| On rooms the model never saw | MAE 0.37 °C |
| Uncertainty range | covers the true value **89%** of the time (target 90%) |
| March 3 outage | Detected as **one** CRITICAL incident (not 186 separate alerts). Agent 3 identified the cause as an **upstream data-pipeline failure** (STRONG), because the energy meters kept reporting while every temperature/CO₂ point stopped. |
| Ground-floor outages | Linked to their shared gateway `OnDijon_Hub_03` using the MSI device documentation |

Honest limits: the model was trained on winter/spring data only, and for outages shorter than 2 h,
simply repeating the last value is slightly more accurate. Both are stated in
[docs/MODEL_CARD.md](docs/MODEL_CARD.md).

### How it works

```
 IFC model ─┐
 MSI sheet ─┼─► data build ─► database ◄─► Backend (FastAPI) ◄──── n8n workflows (WSL)
 BMS CSVs ──┘                                │   ▲                  A→B→C→D, E
                                             │   │
                         ML ghost sensor ◄───┘   └──── Dashboard (React)
```

* **Backend (Python / FastAPI):** the "brain". It stores everything, runs detection every 15 minutes of building time, runs the ML model and the agents.
* **Dashboard (React / TypeScript):** what people look at.
* **n8n:** the conductor that calls the agents in order.
* **Replay clock:** the data stops in June 2026, so the platform *replays* it (60× faster by default) as if it were live. The screen always shows **REPLAY**.

Technical detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · API: [docs/API.md](docs/API.md)

### Tech stack

Python 3.13 · FastAPI · SQLite · pandas · scikit-learn · ifcopenshell · shapely · React 18 ·
TypeScript · Vite · Recharts · React Query · n8n 2.x · optional LLM (Claude via the Anthropic SDK,
or any OpenAI-compatible server)

---

## Part 2 — How to run it

### 1. What you need

* **Python 3.13** and **Node.js 22**
* The **data files**. They are **not in git** (about 4 GB). Put them in the project root folder:

```
GostSensor/
├── temperature_csv/   energy_csv/   co2_csv/   humidity_csv/
├── lighting_csv/      occupancy_csv/   water_csv/
├── V7-CAMPUS-DIJON-BATIMENT.ifc
└── 20220825 (On Dijon) Coordination MSI.xlsx
```

* Optional: **n8n** (in WSL or anywhere) for the automated workflows.

### 2. First-time setup (once)

```powershell
git clone https://github.com/Ghassen-kahia/GostSensor.git
cd GostSensor            # then copy the data files listed above into this folder

# configuration
copy .env.example .env
```

Edit `.env`:
* `GHOST_DATA_DIR`: a folder **outside OneDrive**, e.g. `C:/Users/<you>/AppData/Local/GhostSensor/data`.
* `GHOST_OPERATOR_TOKEN` and `GHOST_AUTOMATION_TOKEN`: generate each with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

```powershell
# install
pip install -r backend/requirements.txt
cd frontend; npm install; cd ..

# build the database from the raw data (~2-3 min) and train the ghost-sensor model (~1 min)
cd backend
python -m ghost.etl.build_all
python -m ghost.ml.train
cd ..
```

### 3. Start it (every time)

**One command:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
```

**Or by hand, in two terminals:**

```powershell
# terminal 1 — backend (API + detection engine)
cd backend
python -m ghost.api.main
```

```powershell
# terminal 2 — dashboard
cd frontend
npm run dev
```

Then open:
* **Dashboard:** http://localhost:5173
* **API documentation:** http://localhost:8000/docs

Stop with `Ctrl+C` in each terminal.

### 4. Demo scenario (5 minutes)

1. Click **Operator sign-in** (top right) and paste your `GHOST_OPERATOR_TOKEN`.
2. **Executive overview**: KPIs and trends from the replayed data.
3. **Digital twin**: pick a storey, click a room, and see its sensors and real neighbours.
4. **Workflows & health → Simulated faults**: sensor `T-02-14-N`, fault **Silence**, then **Inject fault**.
5. Wait about 1 minute, then go to **Ghost sensors**. `T-02-14-N` shows **Capteur Virtuel Actif** with its estimated temperature, uncertainty range and neighbour inputs. Because the outage is simulated, the chart also shows the hidden true value for comparison.
6. **Incidents**: open the new incident. You'll find the evidence, the technical report (download as Markdown or print to PDF), and the root-cause hypotheses (confirm or reject them).
7. **AI investigation**: what each of the 4 agents did.
8. Back in **Workflows & health**, click **End (recover)**. The sensor returns to *Physical* and the incident resolves itself.
9. **Data check (CSV)**: drop one of the export files (e.g. `co2_csv/co2_2026-01-01_to_2026-01-31.csv`). After a few seconds you get its quality report; for this file it shows that 11 of 44 CO₂ sensors never sent a valid value in January.
10. Click the **chat button** (bottom right) and ask: *"Which sensors are offline?"* or *"Summarise the last uploaded CSV"*.

To replay the real March 3 outage: **Reset replay** (type `RESET`), then **Jump forward** to `2026-03-03 09:00`.

### 5. Connect n8n (optional)

The workflow files are in `n8n/workflows/`. Import them:

```bash
n8n import:workflow --separate --input=<path>/n8n/workflows
```

Then:
1. Create the credential **Ghost Sensor API token** (header `X-Ghost-Token` = your `GHOST_AUTOMATION_TOKEN`).
2. Set `apiBaseUrl` to an address where n8n can reach the backend. From WSL, use the Windows IP given by `ip route | awk '/default/ {print $3}'`, and set `GHOST_HOST=0.0.0.0` in `.env`.
3. Publish E, D, C, B, then A.

Full guide: [docs/N8N_SETUP.md](docs/N8N_SETUP.md). Without n8n everything still works: the backend
processes incidents itself after 5 minutes.

### 6. Optional: AI (assistant + narratives) and real emails

In `.env`:

```ini
# AI: free-form assistant answers + AI-written report summaries (validated against the evidence).
# Without it, the assistant runs in built-in mode and everything else works.
LLM_PROVIDER=anthropic
LLM_API_KEY=<your key>

# Real emails (default is test mode: emails are saved as .eml files in <data>/outbox)
EMAIL_MODE=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
EMAIL_FROM=ghost-sensor@example.com
ALERT_RECIPIENTS=you@example.com
```

Restart the backend after editing `.env`.

### 7. Run the tests

```powershell
cd backend;  python -m pytest tests -q    # 37 tests on a copy of the real database
cd frontend; npm test                     # 8 UI tests
```

### 8. Common problems

| Problem | Fix |
|---|---|
| "Backend unreachable" in the dashboard | Start the backend (terminal 1). |
| "database has no sensors yet" | Run `python -m ghost.etl.build_all` (the data files must be in the project root). |
| Ghost sensors say "model not trained" | Run `python -m ghost.ml.train`. |
| Port 8000 or 5173 already in use | An old instance is still running. Close it or use it. |
| Operator buttons greyed out | Sign in with `GHOST_OPERATOR_TOKEN`. |
| Replay reached the end (June 30) | **Reset replay** on *Workflows & health*. |
| Assistant says "built-in mode" | No AI configured: set `LLM_PROVIDER=anthropic` and `LLM_API_KEY` in `.env`, then restart the backend. |
| CSV upload refused | Sign in as operator; the file must be `.csv/.tsv/.txt` text, under `GHOST_MAX_UPLOAD_MB` (300 MB), with a timestamp column. |

More in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Project structure

```
backend/      Python API, data build, ML model, detection engine, the 4 agents, tests
frontend/     React dashboard (8 pages)
n8n/          Importable n8n workflows A–E (no secrets inside)
docs/         Architecture, API, n8n setup, model card, troubleshooting
scripts/      start-dev.ps1
.env.example  Configuration template (copy to .env, never commit .env)
```
