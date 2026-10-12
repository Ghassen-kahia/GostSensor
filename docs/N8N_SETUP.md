# Connecting n8n (Ubuntu WSL)

## Current state

The five workflows were **created directly in your n8n instance** (v2.35.7, personal project) through
the n8n MCP server. They are tagged `ghost-sensor` and are **not published or active**:

| Workflow | ID |
|---|---|
| Ghost Sensor A - Sensor Monitoring | `ZVReyltjkeEm1cib` |
| Ghost Sensor B - Incident Documentation | `qDM2g5nh3R90KraK` |
| Ghost Sensor C - Root-Cause Analysis | `06v0WsE2IUbblMMT` |
| Ghost Sensor D - Email Alerting | `P3KmGYg9y29OVKP0` |
| Ghost Sensor E - Error Handling | `fb0rKkBZE2wFciBB` |

Importable copies are in `n8n/workflows/*.json`. They contain no credentials or secrets, and they
keep their IDs, so the Execute-Workflow links between A→B→C→D survive an import.

Already configured:
* execution timeouts (A/B/C 300 s, D 120 s);
* failed-execution retention;
* sub-workflows only callable by their parent (B←A, C←B, D←C);
* HTTP timeouts with bounded retries (2–3 tries, 5 s apart).

Still to do (needs your credentials):
1. Create two credentials.
2. Publish the workflows.
3. Set E as the error workflow.

## 1. Network: how n8n reaches the backend

WSL2 runs in NAT mode here, so `localhost` inside WSL is **not** Windows. This was verified: from
WSL, `http://172.23.32.1:8000` works and `http://localhost:8000` does not. The backend must
therefore listen on `0.0.0.0` (`GHOST_HOST=0.0.0.0` in `.env`).

Find the current Windows host IP from WSL. It can change after a WSL restart:

```bash
ip route | awk '/default/ {print $3}'      # e.g. 172.23.32.1
curl http://$(ip route | awk '/default/ {print $3}'):8000/api/clock
```

If it changes, update `apiBaseUrl` in two places: the **Config** node of Workflow A, and the
**Sanitize Error** node of Workflow E. B, C and D receive it from A.

In the other direction, the backend reaches n8n at `http://localhost:5678`, which WSL forwards to
Windows. That is `N8N_BASE_URL`.

## 2. Credentials (in n8n, never in workflow JSON)

| Credential name | Used by | Value |
|---|---|---|
| **Ghost Sensor API token** | all HTTP Request nodes (A–E) | n8n *Custom Auth (templated)*, template `{"headers":{"X-Ghost-Token":"{{api_key}}"}}`, api_key = `GHOST_AUTOMATION_TOKEN` from `.env` |
| **Ghost Sensor webhook token** | Webhook trigger of A (optional push) | *Header Auth*, name `X-Ghost-Token`, value = `GHOST_AUTOMATION_TOKEN` |

To create them: open a node such as *Run Agent 1 Detection*, then **Credential → Create new**. n8n
offers the credential type the node expects. Select the same credential in every HTTP node of
A–E: there are 9 HTTP nodes (2 in A, 2 in each of B, C and D, 1 in E). The n8n UI lists nodes with
missing credentials.

## 3. Test, publish, link the error workflow

1. Open **A** and click **Execute workflow** (manual run). Expect:
   * *Run Agent 1 Detection* returns `evaluated` and `pending`.
   * *Claim Pending Incidents* returns up to 5 items.
   * B → C → D run for each claimed incident.
   * The dashboard's **Workflows & health** page shows `Last call from n8n` and agent runs with trigger `n8n` and the n8n execution ID.
2. Publish in this order: **E**, then **D**, **C**, **B**, and finally **A**. Publishing A starts the every-minute schedule.
3. In A, B, C and D, open **Workflow settings → Error workflow → "Ghost Sensor E - Error Handling"**. n8n only allows this once E is published. I can also do this step for you through the n8n MCP connection once E is published.
4. Optional, for instant processing instead of the 1-minute poll: set `N8N_TRIGGER_WEBHOOK_URL=http://localhost:5678/webhook/ghost-sensor/incidents` in `.env` and restart the backend.

To check the backend side without n8n, replay the exact calls from WSL:

```bash
API=http://$(ip route | awk '/default/ {print $3}'):8000
curl -s -X POST $API/api/automation/detect -H "X-Ghost-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"executionId":"manual"}'
curl -s -X POST $API/api/automation/claim  -H "X-Ghost-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"executionId":"manual","limit":1}'
```

## Importing into another n8n instance

```bash
n8n import:workflow --separate --input=/mnt/c/Users/<you>/OneDrive/Bureau/BOS/n8n/workflows
```

Then follow steps 1–3.

## Safety properties

* **Loop-free:** agent endpoints never call n8n; Workflow A's detect is a no-op for an already-evaluated bucket.
* **No double processing:** the backend's atomic claim, `pipeline_version` / `processed_version`, and content-hashed report/analysis versions.
* **n8n down or inactive:** the backend's local fallback runs Agents 2–4 after `GHOST_LOCAL_FALLBACK_AFTER_S` (300 s) and logs a `LOCAL_FALLBACK` event. Set `GHOST_ORCHESTRATION_MODE=local` to bypass n8n entirely.
* **Error workflow E** forwards only the error message (≤ 500 chars), node, workflow and execution ID. No payloads, no credentials.
