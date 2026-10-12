# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard says **Backend unreachable** | Start the API (`cd backend; python -m ghost.api.main`). The Vite dev server proxies `/api` to `GHOST_API_URL` (default `http://127.0.0.1:8000`). |
| API logs `database has no sensors yet` | Run `python -m ghost.etl.build_all` from `backend/` (it reads the CSV/IFC/MSI files from `GHOST_SOURCE_DIR`, default the repo root). |
| Health **mlModel DEGRADED** / virtual sensors `INSUFFICIENT: model not trained` | Run `python -m ghost.ml.train`. `data/models/CURRENT` points to the active model. |
| `database is locked`, or the DB keeps re-syncing | `GHOST_DATA_DIR` is inside OneDrive. Move it to a local folder, e.g. `%LOCALAPPDATA%\GhostSensor\data`. |
| n8n: `ECONNREFUSED 172.x.x.x:8000` | The backend is bound to 127.0.0.1 → set `GHOST_HOST=0.0.0.0`. Or the WSL host IP changed → update `apiBaseUrl` (see N8N_SETUP). Or the Windows firewall is blocking python.exe on port 8000 → allow it for private networks. |
| n8n: `401 invalid automation token` | The n8n credential value differs from `GHOST_AUTOMATION_TOKEN`. |
| Health **n8n UNREACHABLE** | n8n is not running in WSL (`n8n start`), or `N8N_BASE_URL` is wrong. Incidents are still processed by the local fallback. |
| Incidents stay `pending` | Normal while n8n is inactive: the fallback runs after `GHOST_LOCAL_FALLBACK_AFTER_S` (300 s wall-clock). Check *Workflows & health* for `LOCAL_FALLBACK` events or failed runs. |
| Emails show `SUPPRESSED: no recipients configured` | Set `ALERT_RECIPIENTS`. In `EMAIL_MODE=test`, messages go to `<data>/outbox/*.eml` with status `TEST_MODE`. |
| Emails `PENDING` / `FAILED` | Check `last_error` on the incident's Notifications tab. Transient errors retry 4× with backoff; authentication or recipient errors fail immediately. |
| Operator buttons are disabled | Sign in (top-right) with `GHOST_OPERATOR_TOKEN`. A 503 means the token is not configured in `.env`. |
| Replay reached the end (`atEnd`) | The historian stops on 2026-06-30. Use *Reset replay* (operator) to restart from `GHOST_REPLAY_START`. |
| Clock jump rejected | Jumps move forward only. To go back, use reset (destructive: clears runtime incidents). |
| LLM narrative missing | `LLM_PROVIDER=none` (default), or the output was rejected by validation. The agent run summary says which. Rejection is intentional when the model cites unknown evidence or invents numbers. |
| Rebuilding data | `build_all` is idempotent (upserts). `--skip-history --reuse-topology` reloads only the topology from the cached `topology.json`. |

## Logs

* The backend prints JSON lines on stdout: one per request (method, path, status, ms; never bodies), plus engine and agent errors with tracebacks.
* Agent executions: table `agent_runs` and the dashboard *Workflows & health* page.
* Workflow and error events from n8n Workflow E and the backend: table `workflow_events`.

## Packaging

Docker is not used: it is not installed on this machine, and n8n runs natively in WSL. To containerise
later, the backend needs Python 3.13 with `backend/requirements.txt` (ifcopenshell is only needed for
the ETL). It needs a volume for `GHOST_DATA_DIR`, and `GHOST_HOST=0.0.0.0`. The frontend is a
static `npm run build` (`frontend/dist`) behind any web server that proxies `/api`.
