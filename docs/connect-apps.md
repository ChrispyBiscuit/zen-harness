# Connect other apps (API & MCP)

Zen can take tasks from other programs: scripts (REST API), and AI apps that speak the
**Model Context Protocol** (MCP) — Claude Desktop, Claude Code, Cursor, VS Code and others. A task sent this way runs
like a normal chat: it appears in the sidebar, where you can watch every step or continue it.

Everything is set up in **Settings → Connect apps**. That page shows your token and ready-to-copy configuration with
your real port, token and program path filled in, plus a **Test connection** button.

## 1. Turn it on

Settings → Connect apps:
- **REST API** and **MCP server** switches (both on by default; they only answer on this PC).
- **Run in the background at sign-in** — keeps Zen available to other apps even when its window is closed
  (a tray icon appears). Without it, other apps can use Zen only while the app is open — except MCP over
  stdio, which starts it on demand.
- **Allow other devices on my network** — off by default. When on, other devices can reach the API and MCP (with the
  token) but never the app itself.

## 2. Your token

Every request needs the token (`Authorization: Bearer <token>`). Copy it from Settings → Connect apps or the tray
menu. **Regenerate** invalidates the old one immediately. Treat it like a password: anyone with it can run tasks on
your PC.

## 3. Connect an MCP app

### Claude Desktop
Settings → Connect apps → **Claude Desktop** → Copy, then in Claude Desktop: Settings → Developer → Edit Config, and
paste into `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zen-harness": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Zen Harness\\zenharness-cli.exe",
      "args": ["--mcp-stdio"]
    }
  }
}
```
Restart Claude Desktop. The stdio bridge reads the port and token itself and starts Zen if it isn't running.

### Claude Code
```bash
claude mcp add zen-harness --transport http http://127.0.0.1:8765/mcp --header "Authorization: Bearer <token>"
```

### Cursor / VS Code (`mcp.json`)
```json
{ "servers": { "zen-harness": { "type": "http", "url": "http://127.0.0.1:8765/mcp",
                                "headers": { "Authorization": "Bearer <token>" } } } }
```

### MCP tools Zen provides
| Tool | What it does |
|---|---|
| `run_task(task, conversation_id?, read_only?, wait_seconds?)` | runs a task; returns the answer, or a run id if it takes longer than `wait_seconds` (default 300) |
| `get_run(run_id, wait_seconds?)` | status/result of a run |
| `cancel_run(run_id)` | stop a run |
| `list_runs`, `list_conversations`, `get_conversation`, `get_status` | browse runs and chats |

## 4. Use the REST API

Base URL `http://127.0.0.1:8765/v1` (the port is shown in Settings → Connect apps).

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"input": "Summarise the latest release notes of llama.cpp", "wait": 300}' \
     http://127.0.0.1:8765/v1/runs
```

```python
import json, urllib.request
TOKEN, BASE = "<token>", "http://127.0.0.1:8765"
req = urllib.request.Request(f"{BASE}/v1/runs", method="POST",
    data=json.dumps({"input": "What changed in Python 3.14?", "wait": 300}).encode(),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
run = json.load(urllib.request.urlopen(req, timeout=330))["run"]
print(run["status"], run["result"])
```

| Endpoint | What |
|---|---|
| `GET /v1/health` | is it up, busy, paused (no token needed) |
| `POST /v1/runs` | `{input, conversation_id?, title?, read_only?, wait?}` → the run (finished if `wait` ≤ 600 s) |
| `GET /v1/runs`, `GET /v1/runs/{id}?wait=` | list / get runs |
| `GET /v1/runs/{id}/events?since=&wait=` | long-poll the run's step-by-step events |
| `GET /v1/runs/{id}/stream` | the same as Server-Sent Events |
| `POST /v1/runs/{id}/cancel` | cancel or stop |
| `GET /v1/conversations`, `GET /v1/conversations/{id}` | chats and their messages |

Errors: 400 bad input · 401 token · 403 disabled or another website · 404 · 409 queue full · 429 rate limit.

## What tasks from other apps may do

Nobody is there to click "allow", so:
- Reading, web research and read-only commands follow Settings → Permissions, as in the app.
- Anything that would normally ask you is **denied** — or, with "Ask me in the app", shown as an approval card and
  denied if nobody answers in time.
- Settings → Connect apps → **Always allowed for apps** lists extra rules for these tasks only.
- `read_only: true` makes a task investigate and propose changes without making them.

One GPU, one job: tasks from other apps wait in a queue and start when nothing else is running.
