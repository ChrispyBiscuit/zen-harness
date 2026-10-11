# Zen Headless Harness — PRD

Status: implemented 2026-10-10 (see "Implementation notes" at the end). Owner: Zen Harness.

## 1. Problem

The harness (AgentEngine + tools + checklists + fact checks) is only reachable through the desktop window. Other
programs on this PC (scripts, editors, other agents) cannot use it, and it is not there unless the user opens the app.

## 2. Goals

1. **Runs without the window.** A resident service process hosts the existing backend (`web_backend`: STORE, HOST,
   engine) with no QtWebEngine window. Closing or never opening the window does not stop it.
2. **System tray presence**: icon + menu (Open Zen Harness, status line, Pause/Resume API runs, Copy API token,
   Open logs, Quit).
3. **Auto-start at sign-in** for the current user, toggled in Settings.
4. **Programmatic access**: a versioned REST API (`/v1`) and an MCP server (streamable HTTP at `/mcp`, plus a stdio
   bridge) exposing the harness as tools.
5. **Safe by default**: loopback only, bearer token, no human approval cards — a fixed approval policy.
6. **One engine, one GPU**: the desktop window and API clients share the same backend process; API runs are queued.
7. **Configurable in Settings** (new "Headless" tab).

## 3. Non-goals

- A Windows *service* (SCM) or running before sign-in: the model, browser tool and tray need the user's desktop
  session, and installing a service needs admin rights. "Boot" here means **at sign-in**.
- Multi-user / remote hosting, TLS, OAuth. LAN binding exists but is off by default and token-protected only.
- New engine features. No changes to `agentic.py` / `agent_tools.py`; everything hooks in at the `Host` layer.
- Third-party clients approving their own tool calls (that would defeat the approval policy).

## 4. Architecture

```
 headless.py  (pythonw, one process)                  app_web.py (desktop window, optional)
 ├─ Qt main thread: QSystemTrayIcon + menu  ◄──────── attaches to the same port, never shuts a
 └─ server thread: web_backend.serve(host, port)        resident service down
      ├─ /api/*, /ws      UI routes (unchanged; loopback-only)
      ├─ /v1/*            REST API  (harness_api.py, bearer token)
      ├─ /mcp             MCP JSON-RPC over streamable HTTP (harness_mcp.py, bearer token)
      └─ HOST / STORE / engines / llama-server (on demand)
 harness_mcp.py --stdio   stdio⇄HTTP bridge for MCP clients that spawn servers (starts the service if needed)
```

- **Process model.** Today `app_web.py` spawns `app_web.py --server` (`serve_headless`, idle-exits after 5 min with
  no window). The resident service is the same backend run in-process by `headless.py` with `web_backend.RESIDENT =
  True`: no idle exit, `/api/shutdown` refuses unless it comes from the tray (`{"service": true}`).
- **Coexistence.** `app_web.start_backend()` already attaches to a live server on the port; with the service
  running, the window simply attaches. If a non-resident (window-started) server holds the port when the service
  starts, the service asks it to exit when idle and takes over (retrying every 10 s while it is busy).
- **Port.** One port for UI + API: `LWS_PORT` env → `settings.headless.port` → 8765. Changing it needs a restart.

## 5. Tray (PySide6 `QSystemTrayIcon`, already a dependency — no pystray)

| Item | Action |
|---|---|
| status line (disabled) | `Idle` / `Running: <title>` · `N queued` · `Paused` · `:8765` — refreshed every 2 s, also the tooltip |
| Open Zen Harness | launches `pythonw app_web.py` (attaches to this server); double-click on the icon does the same |
| Pause API runs / Resume | stops / restarts dequeuing; new requests still queue (202) |
| Copy API token | to clipboard |
| Open logs | opens `headless_service.log` |
| Quit | stops runs, saves conversations, stops model servers this process started, exits |

Finished API runs show a tray balloon only when `ui.notifications` is on (existing notifier hook).

## 6. Auto-start (per-user)

**Choice: a shortcut in the user's Startup folder** (`shell:startup\Zen Headless Harness.lnk` → `venv\Scripts\pythonw.exe
headless.py`). Per-user, no admin, no registry or system policy changes, visible and disableable in Task Manager →
Startup apps, removed by deleting one file. A per-user Task Scheduler task was the alternative (supports delays and
restart-on-failure) but is less visible to the user and harder to inspect; not needed.

- Toggled by Settings → Headless → "Start at sign-in"; the backend creates/deletes the shortcut (`autostart.py`,
  via the `WScript.Shell` COM object). The UI shows the real state (shortcut exists), not just the saved flag.
- Nothing is registered by tests or by default; the user turns it on.

## 7. REST API (`/v1`, JSON)

Auth: `Authorization: Bearer <token>` on everything except `GET /v1/health`. Requests carrying a non-local
`Origin` header are rejected (DNS-rebinding guard).

| Method & path | Body / query | Returns |
|---|---|---|
| `GET /v1/health` | — | `{ok, version, resident, paused, busy, queued, port}` |
| `POST /v1/runs` | `{input, conversation_id?, title?, read_only?, wait?}` | `202 {run}`; with `wait` (s, ≤ 600) blocks until finished or timeout and returns `200 {run}` |
| `GET /v1/runs` | `?limit=` | `{runs:[…]}` newest first |
| `GET /v1/runs/{id}` | — | `{run}` |
| `GET /v1/runs/{id}/events` | `?since=<seq>&wait=<s>` | long-poll: `{events:[{seq,kind,data,t}], next, status}` |
| `GET /v1/runs/{id}/stream` | — | Server-Sent Events, one `event: <kind>` per engine event, ends with `event: end` |
| `POST /v1/runs/{id}/cancel` | — | `{run}` (dequeues, or stops the running turn) |
| `GET /v1/conversations` | `?limit=` | `{conversations:[{id,title,updated,running,source}]}` |
| `GET /v1/conversations/{id}` | — | `{id,title,messages:[{role,content,kind?}]}` (text only) |

Run object: `{id, status: queued|running|done|failed|stopped|cancelled, conversation_id, input, created,
started, ended, result, error, pending_approvals}`. A conversation started by the API is a normal chat (shown in the
desktop sidebar, tagged `source: "api"`), so the user can audit or continue it.

Errors: `401` bad token, `403` non-local origin / disabled, `404`, `409` (queue full), `429` rate limit
(`Retry-After`), `400` bad input.

## 8. MCP server

- **Streamable HTTP** at `POST /mcp` (JSON-RPC 2.0; responses as `application/json`, no SSE needed since tools are
  request/response). `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`. Session id
  issued in `Mcp-Session-Id`. Same bearer token. `GET /mcp` → 405.
- **stdio**: `venv\Scripts\python.exe harness_mcp.py --stdio` reads the port/token from `settings.json`, starts the
  service if it is not running, and forwards each line to `/mcp`. For clients like Claude Desktop/Code.
- Hand-written (≈150 lines) instead of the `mcp` SDK: no new package, and the needed surface is small.

Tools:

| Tool | Args | Behaviour |
|---|---|---|
| `run_task` | `task`, `conversation_id?`, `read_only?`, `wait_seconds?` (default 300, max 600) | queues a run; returns the final answer, or the run id + status if still running |
| `get_run` | `run_id`, `wait_seconds?` | status/result, optionally waiting for completion |
| `cancel_run` | `run_id` | cancel |
| `list_runs` | `limit?` | recent runs |
| `list_conversations` | `limit?` | recent chats |
| `get_conversation` | `conversation_id`, `last?` | the last N text messages |
| `get_status` | — | health/queue |

## 9. Approvals for headless runs

Headless runs have no one to click approval cards. Everything the engine auto-allows today (reads, web, read-only
commands, per the existing Permissions toggles) still runs. For anything that would *ask*:

- `headless.approval = "deny"` (default): denied immediately; the agent is told and works around it.
- `headless.approval = "ask"`: the card appears in the desktop UI as usual; denied after `approval_timeout`
  seconds (default 120) if nobody answers.
- `headless.allow_rules` (same `{tool, pattern}` format as Permissions → Always allowed) are auto-approved for API
  runs. The desktop "Always allowed" list does **not** apply to API runs: third-party callers get only what is listed
  for them.
- `read_only: true` on a request forces read-only (plan) mode for that conversation.
- Delegated sub-tasks of an API run inherit the same policy.

Implemented in `Host.cfg_for` (rules, read-only) and `Host._ask` (decision) — no engine change.

## 10. Concurrency

- One dispatcher thread; API runs start one at a time and only when **no** run (API or desktop) is active, so the
  GPU is never asked to hold two jobs because of the API. Desktop runs are not queued (unchanged behaviour).
- Queue bound `headless.max_queue` (default 20) → 409 when full. Runs longer than `headless.max_run_minutes`
  (default 30) are stopped.
- A run on an existing conversation that is busy waits in the queue (never interjects into a live turn).

## 11. Security

- Bind `127.0.0.1` by default. `headless.lan = true` binds `0.0.0.0` (restart needed; Windows Firewall will ask).
  With LAN on, only `/v1` and `/mcp` answer non-loopback clients; the UI and `/api/*` stay loopback-only.
- Token: 32-byte urlsafe, generated on first start, stored in `settings.json` (`headless.token`), regenerable in
  Settings. Compared in constant time. `api_enabled=false` turns off `/v1` and `/mcp` (403).
- Rate limit: `headless.rate_limit` requests/minute per client (default 120), sliding window → 429.
- API routes are off-limits to the UI's unauthenticated path and vice versa (`/api/*` never accepts the token).

## 12. Settings (`settings.json` → `headless`; UI: Settings → Headless)

| Key | Default | UI |
|---|---|---|
| `autostart` | false | "Start at sign-in" (creates/removes the Startup shortcut) |
| `api_enabled` | true | "REST API" |
| `mcp_enabled` | true | "MCP server" |
| `port` | 8765 | number (restart) |
| `lan` | false | "Allow other devices on the network" (restart) |
| `token` | generated | shown masked, Copy / Regenerate |
| `approval` | `"deny"` | select: deny / ask in the app |
| `approval_timeout` | 120 | number |
| `allow_rules` | `[]` | rule list (same editor as Permissions) |
| `max_queue`, `rate_limit`, `max_run_minutes` | 20, 120, 30 | Advanced |

Also shown: whether the resident service is running, the base URL, and copyable MCP client config snippets.

## 13. Logging

- `headless_service.log` (stdout/stderr + faulthandler), one line per API request (`method path status ms client`)
  and per run state change. Rotated at 5 MB when the service starts (one backup).
- Runs and their events are kept in memory (last 200 runs); conversations persist as usual.

## 14. Acceptance criteria

1. `pythonw headless.py` shows a tray icon; no window. The UI works at `http://127.0.0.1:<port>` and the desktop
   window attaches to it; closing the window leaves the service running.
2. `POST /v1/runs` without a token → 401; with it → a run that queues, runs, and returns a result; events can be
   polled and streamed; cancel works for queued and running runs.
3. An MCP client (stdio bridge and HTTP) can `initialize`, list the tools and `run_task` end to end.
4. A tool call that needs approval is denied under `deny`, allowed when a `headless.allow_rules` entry matches.
5. Two submitted runs execute one after the other, never concurrently with another active run.
6. Tray: status updates, Pause stops dequeuing, Resume continues, Copy token, Open logs, Quit exits cleanly.
7. Settings → Headless round-trips every field; "Start at sign-in" creates/removes the shortcut.
8. With `lan=false` nothing listens on non-loopback addresses.

## 15. Test plan

- `tests/test_headless_api.py`: boots the backend in-process on a spare port with a copied data dir and a stub
  engine (emits status/answer events, asks for one approval) — covers auth, origin guard, rate limit, run lifecycle,
  queue serialisation, cancel, events/SSE, approval policy (deny / rules / read-only), MCP HTTP (initialize,
  tools/list, run_task, errors), stdio bridge, settings round-trip, autostart against a temp Startup folder.
- Manual/live: start `headless.py` on a test port with a copied data dir; drive the tray menu items; one real
  model run once the GPU is free.

---

## Implementation notes (2026-10-10)

Files: `harness_api.py` (REST, queue, policy, auth middleware), `harness_mcp.py` (MCP HTTP + stdio bridge),
`headless.py` (tray + resident server), `autostart.py` (Startup shortcut), small hooks in `web_backend.py`
(settings defaults, host/port, middleware install, `_ask`/`cfg_for` policy, resident shutdown guard),
`lws-web/src/components/ServicePanel.tsx` + a "Headless" tab in `SettingsDialog.tsx`. User docs: `docs/headless-harness.md`.

Tested 2026-10-10 (stub engine, spare ports, throwaway data dirs; the running app on 8765 was not touched):
`tests/test_headless_api.py` 56/56 (auth, origin, LAN guard, rate limit, lifecycle, queue order + no overlap, waits for
a desktop run, cancel, failure, deny / desktop-rules-ignored / allow_rules / read_only / ask+timeout / ask+human,
SSE, MCP HTTP + batch + errors, stdio bridge, settings + shortcut round-trip, token regenerate, pause, 409, 403s,
resident shutdown guard); `tests/test_headless_tray.py` 12/12 (every menu action). Live: `headless.py` took the port
over from a window-started `app_web.py --server`, refused window shutdowns, the stdio bridge auto-started it, and
Settings → Headless saved and created the Startup shortcut (temp folder via LWS_STARTUP_DIR).

Open: a real-model run through the API (GPU was busy with an eval); `npm run build` into web_dist (the live app
does not show the Service tab until then); the user turning on "Start at sign-in" on the real Startup folder.
