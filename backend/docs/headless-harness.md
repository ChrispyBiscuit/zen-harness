# Zen Headless Harness: tray service, REST API, MCP

The Zen Headless Harness runs Zen Harness in the background, with no window, so other programs can give it tasks over HTTP or MCP.
Design and rationale: `plans/headless-harness-prd.md`.

## Start it

| How | Command / place |
|---|---|
| Now | `venv\Scripts\pythonw.exe headless.py` (in this folder). A tray icon appears; no window. |
| At every sign-in | Settings → Headless → **Start at sign-in** → Save. Adds `Zen Headless Harness.lnk` to your Startup folder (your account only, no admin). Remove it the same way, or in Task Manager → Startup apps. |
| On demand from an MCP client | the stdio bridge (below) starts the service if it is not running. |

The service *is* the app's backend (same port, same chats, same settings). Opening the desktop app attaches to it,
and closing the window leaves it running. If the app's own server is already running when the service starts, the
service waits until it is idle and takes over. Only one service runs at a time.

**Tray menu** (right-click the icon): status line (idle / running / queued / paused, port) · Open Zen Harness
(double-click does the same) · Pause/Resume API runs · Copy API token · Open logs (`headless_service.log`) · Quit Zen Headless Harness.

## Authentication

Every request except `GET /v1/health` needs `Authorization: Bearer <token>` (or `X-API-Key: <token>`). Copy the token
from the tray menu or Settings → Headless. Regenerating it there invalidates the old one immediately.

By default only this PC can connect (127.0.0.1). Settings → Headless → "Allow other devices on the network" binds all
interfaces after a restart; other devices then reach only `/v1` and `/mcp` (with the token), never the app UI or
`/api`. Browser pages from other sites are refused (non-local `Origin`).

## REST API (`http://127.0.0.1:8765/v1`)

```bash
TOKEN=...   # Settings → Headless → API token
# run a task and wait up to 5 minutes for the answer
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"input": "Find the three most-cited papers on speculative decoding and summarise each", "wait": 300}' \
     http://127.0.0.1:8765/v1/runs
```

| Endpoint | What |
|---|---|
| `GET /v1/health` | `{ok, resident, paused, busy, queued, current_run, port}` (no token needed) |
| `POST /v1/runs` | body `{input, conversation_id?, title?, read_only?, wait?}` → `202 {run}`; with `wait` (≤ 600 s) → `200 {run}` once finished |
| `GET /v1/runs?limit=` | recent runs |
| `GET /v1/runs/{id}?wait=` | one run (optionally wait for it to finish) |
| `GET /v1/runs/{id}/events?since=&wait=` | long-poll the engine events: `{events:[{seq,kind,data,t}], next, status}` |
| `GET /v1/runs/{id}/stream` | the same as Server-Sent Events (`event: <kind>`), ends with `event: end` |
| `POST /v1/runs/{id}/cancel` | cancel (queued) or stop (running) |
| `GET /v1/conversations?limit=` | chats, newest first (`source: "api"` or `"app"`) |
| `GET /v1/conversations/{id}?last=` | a chat's text messages |

Run: `{id, status: queued|running|done|failed|stopped|cancelled, conversation_id, input, result, error, created,
started, ended, pending_approvals, queue_position}`. Errors: 400 bad input · 401 token · 403 disabled/other origin ·
404 · 409 queue full · 429 rate limit (`Retry-After`).

Event kinds are the engine's own (`status`, `ans_delta`, `think_delta`, `tool_call`, `tool_result`, `tracker`,
`answer_end`, `failed`, …); clients should ignore kinds they don't know.

Python example:

```python
import json, urllib.request
TOKEN, BASE = "...", "http://127.0.0.1:8765"
req = urllib.request.Request(f"{BASE}/v1/runs", method="POST",
    data=json.dumps({"input": "What changed in Python 3.14?", "wait": 300}).encode(),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
run = json.load(urllib.request.urlopen(req, timeout=330))["run"]
print(run["status"], run["result"])
```

API runs are normal chats: they appear in the app's sidebar, where you can read every step or continue them.

## MCP

Tools: `run_task(task, conversation_id?, read_only?, wait_seconds?)`, `get_run(run_id, wait_seconds?)`,
`cancel_run(run_id)`, `list_runs(limit?)`, `list_conversations(limit?)`, `get_conversation(conversation_id, last?)`,
`get_status()`. `run_task` returns the final answer, or the run id to poll with `get_run` if it takes longer than
`wait_seconds` (default 300).

**Streamable HTTP** (clients that support remote/HTTP servers):

```json
{ "mcpServers": { "zen-headless-harness": { "type": "http", "url": "http://127.0.0.1:8765/mcp",
                                   "headers": { "Authorization": "Bearer <token>" } } } }
```

**stdio** (clients that spawn a command, e.g. Claude Desktop). It reads the port and token from `settings.json`
and starts the tray service if needed:

```json
{ "mcpServers": { "zen-headless-harness": { "command": "C:\\Users\\user\\OneDrive\\Desktop\\Diffusion\\web-agent\\venv\\Scripts\\python.exe",
                                   "args": ["C:\\Users\\user\\OneDrive\\Desktop\\Diffusion\\web-agent\\harness_mcp.py", "--stdio"] } } }
```

Settings → Headless shows both snippets with your token filled in, ready to copy.

## What API runs may do (approvals)

Nobody is there to click approval cards, so:

- Reads, web research and read-only commands follow Settings → Permissions, as in the app.
- Anything that would ask for approval is **denied** (default), or — with "Ask me in the app" — shown as a card in
  the app and denied if nobody answers within the timeout.
- Settings → Headless → "Always allowed for API runs" lists the only rules API runs get. Your Permissions →
  Always allowed list does **not** apply to them.
- `read_only: true` makes a run investigate and propose only.

## Queue

One GPU, one job: API runs wait in a queue (default 20) and start only when nothing else — API or app — is running.
Runs longer than 30 minutes are stopped (Settings → Headless → Advanced). Pause in the tray or Settings holds the
queue. A desktop chat started while an API run is going still runs at once (as before).

## Files

`headless.py` (tray + service) · `harness_api.py` (REST, queue, policy, auth) · `harness_mcp.py` (MCP + stdio bridge)
· `autostart.py` (Startup shortcut) · settings in `settings.json` → `headless` · log `headless_service.log`
(rotated at 5 MB on start) · tests `tests/scripts/headless_api.py`, `tests/scripts/headless_tray.py` (stub engine, spare
port, throwaway data dir — safe while the app runs).
