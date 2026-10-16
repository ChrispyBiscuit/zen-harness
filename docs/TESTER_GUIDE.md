# Zazen — tester guide (give this whole file to your Claude)

> **To the tester:** from <https://github.com/ChrispyBiscuit/zen-harness/releases/tag/v0.1.0-alpha.3> download
> `ZenHarness-Setup-0.1.0-alpha.3.exe` and `zen-harness-tester-kit-0.1.0-alpha.3.zip`. Install Zazen
> (section 1), unzip the kit (e.g. to `%USERPROFILE%\zen-tester-kit`), then open Claude Code in the kit folder and say:
> *"Read TESTER_GUIDE.md and run the test plan in it. Ask me before anything it says to ask about."* Everything
> below is written for Claude.

---

## Your role (Claude)

You are helping test **Zazen**, a Windows desktop app that runs a local AI agent on local GGUF models with
llama.cpp. You will drive the app through its local HTTP API (as its developer does), run two benchmarks on up to
three local models, grade the results, and write one report. Work in the folder this file is in (the **kit
folder**).

### Rules — follow them exactly

1. **Never stop processes by image name** (`taskkill /IM`, `Stop-Process -Name`, `pkill`). Only by PID, and only a
   process you identified by its full command line or path. A name-based kill once stopped another app's model server.
2. **Don't help the model under test.** No hints, corrections or extra messages to a benchmark chat once it has
   started. If you must intervene (it is stuck for over an hour, the app crashed), stop it, write down exactly what
   you did and when, and mark that run "intervened" in the report. A run with a hint is not comparable.
3. **Don't change the app's harness** (its prompts, guides, code) to make a test pass. You only change *settings*
   listed here.
4. **One GPU job at a time.** The app's model and the benchmark runner's model cannot share an 8 GB GPU. Never start
   one while the other is running.
5. **Ask the tester first** before: installing software, downloading more than 1 GB not listed here, using any paid
   API (cloud models cost money), or deleting anything outside the kit folder and `Documents\ZenBattery`.
6. Never put API keys, tokens or personal data in the report.
7. Report honestly: if something failed or you skipped it, say so with the error text.

---

## 0. Check the PC (5 minutes)

Run and record in the report:

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
Get-CimInstance Win32_ComputerSystem | Select-Object Model, @{n='RAM_GB';e={[math]::Round($_.TotalPhysicalMemory/1GB)}}
Get-CimInstance Win32_Processor | Select-Object Name
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB)}}
(Get-CimInstance Win32_Battery) | Select-Object BatteryStatus   # 2 = on mains power; laptops: stay plugged in
py -0p; git --version
```

Needs: an NVIDIA GPU with **8 GB+** (Bonsai and the 35B model were tuned for 8 GB; with 6 GB use only the 9B), 16 GB+
RAM, ~40 GB free disk, Python 3.11–3.13 with the `py` launcher (python.org installer) and git. If Python or git is
missing, ask the tester to install them. Laptops: plugged in and on a performance power plan — on battery the GPU is
about 3.5× slower and the timing results are meaningless. Note the GPU temperature/clock during runs
(`nvidia-smi --query-gpu=temperature.gpu,clocks.sm --format=csv`): thermal throttling is common and should be
reported.

## 1. Install the app (the tester does this)

1. Run `ZenHarness-Setup-0.1.0-alpha.3.exe` (from the GitHub release above). It is not code-signed yet, so Windows
   SmartScreen may say "Windows protected your PC": choose **More info → Run anyway**. Compare its SHA-256 with the
   `.sha256` file on the release page first (`Get-FileHash .\ZenHarness-Setup-0.1.0-alpha.3.exe`).
2. On the **Models** page keep all three ticked (≈22 GB download, resumable only by re-running setup):
   Ornith 1.5 9B, Qwen3.6 35B-A3B REAP-48 (mixture of experts), Ternary Bonsai 2 27B. Setup also fetches the PrismML
   llama.cpp build Bonsai needs. Each file is checked against its SHA-256.
3. Finish, and let it start the app. It should open on the Ornith model with no "download a model" dialog.

Models land in `%LOCALAPPDATA%\ZenHarness\models`; the app in `%LOCALAPPDATA%\Programs\Zen Harness`.

## 2. Connect to the app

The kit's `zenctl.py` (standard library only — any Python 3.11+) wraps the app's local API on port 8765 (if the app
uses another port, it is shown in Settings → Connect apps; set `$env:ZEN_PORT`):

```powershell
py zenctl.py health        # {"ok": true, ...}
py zenctl.py models        # the three profiles should show ✓ (file present)
py zenctl.py settings model effort think memory_recall
```

What the commands do (use them; they are what the developer uses):

| Command | Does |
|---|---|
| `py zenctl.py set model=ternary-bonsai-2-27b effort=high` | change agent settings (values are JSON) |
| `py zenctl.py send "task"` | start a new agent chat → prints `{"conv_id": ...}` |
| `py zenctl.py status <conv_id>` | running or idle, tool-call count, last messages |
| `py zenctl.py watch <conv_id> --every 120` | print new activity until the run ends (run it in the background) |
| `py zenctl.py stop <conv_id>` | stop a run |
| `py zenctl.py note <conv_id> "text"` | message a running agent — **never during a benchmark** (rule 2) |

The same API by hand: `POST /api/send {"text","mode":"agent","conv_id"?}`, `GET /api/conv/{id}`,
`POST /api/stop {"conv_id"}`, `PUT /api/settings {"agent":{...}}`, `GET /api/state`, `GET /v1/health`. There is
also a token-protected public API (`/v1/runs`, and MCP at `/mcp`) described in the app's Help → Connect apps; you
don't need it for this plan.

Smoke test before benchmarking: `py zenctl.py send "What is 17 * 23? Use the calculator."`, then `status` until it
is idle, and check the answer is 391. If the app shows an error, record it (Help → Diagnostics has logs).

## 3. Benchmark A — the build test (in the app, ~2–4 h per model)

The agent gets one real-world request and must build working software on its own. Same request, same folder layout,
same grading for every model — this is the developer's main comparison.

The request (`build/SPEC.md`): *"I want an expense tracker my roommates and I can all use from our own laptops on
the home Wi-Fi. Everyone logs their own expenses, and each month we want to see totals by category. Using it from
the terminal is fine. Make sure it actually works. Put it in {folder}."*

For each model, **one at a time**, in this order: `ternary-bonsai-2-27b`, `qwen3.6-35b-a3b-reap48`, `ornith-1.5-9b`:

```powershell
py zenctl.py set model=ternary-bonsai-2-27b backend=llama effort=high think=true memory_recall=false
py build\run_build_in_app.py --name bonsai-<yourname>-1 --minutes 240
```

`run_build_in_app.py` sends the request as a new chat, waits (up to `--minutes`), and writes
`build\runs\<name>.json` (time, tool calls). The project is built in `Documents\ZenBattery\build\<name>`. Run it in
the background and check with `zenctl status` every 15–30 minutes; don't send the chat anything (rule 2).
`memory_recall=false` stops the agent from seeing earlier attempts' chats — keep it off for every run, and set it
back to `true` when all runs are done.

### Grade each build by hand (`build/RUBRIC.md`)

Grade from a **copy** of the project folder. Follow the build's own README exactly; don't fix its code.

1. Start the server as its README says (if it needs `pip install`, do it in a throwaway venv).
2. Act as **two people** (two terminals/processes, different names): each logs 2–3 expenses, including two in the
   same category.
3. Ask for this month's totals by category; check the numbers by hand.
4. Restart the server; check the data is still there.
5. Bad input: a negative amount, `abc` as the amount, an impossible date (`2026-13-45`) if it takes dates — each must
   be rejected with a clear message, no crash.
6. Run its own tests (`python -m pytest` or what its README says).
7. Check it listens on the network (`0.0.0.0` or the LAN IP, not only 127.0.0.1) and the client can be pointed at
   another PC's address.

Score the 11 rubric items 1 / 0.5 / 0 with one line of evidence each (copy the table format in `build/GRADES.md`,
which has the developer's grades for other models to compare). Also record: wall time, tool calls, whether it planned
first (PRD/design files), errors it hit and fixed, any intervention.

## 4. Benchmark B — standard benchmarks (the runner, ~4–10 h per model)

Well-known benchmarks with published numbers, run on the same fixed questions for every model. **Close Zazen
first** (tray icon → Quit, or the window's close) — the runner starts its own llama-server on port 8111 and refuses
to run while the app's model server (port 8099) is up.

```powershell
cd standard
powershell -ExecutionPolicy Bypass -File setup.ps1        # once: a venv in %USERPROFILE%\venvs\zen-evals
$py = "$env:USERPROFILE\venvs\zen-evals\Scripts\python.exe"
& $py run.py --list                                        # the three models should show ✓
# dry run without the GPU, proves the install:
Start-Process $py "mock_server.py 8150" -WindowStyle Hidden
& $py run.py --model mock --base-url http://127.0.0.1:8150/v1 --suite quick --limit 3 --no-record
```

Then for each model (one at a time; each line can run overnight — start it in the background and check its output):

```powershell
& $py run.py --model ternary-bonsai-2-27b --suite battery
& $py run.py --model qwen3.6-35b-a3b-reap48 --suite battery
& $py run.py --model ornith-1.5-9b --suite battery
```

`battery` = IFEval (541), BFCL function calling (400), MMLU-Pro (1,000), GPQA Diamond with thinking off (198). If
time allows afterwards: `--suite gpqa` (thinking on; hours per model). Optional, **ask first** (paid API):
`--model anthropic/claude-haiku-4-5 --suite battery` as a cloud reference (needs `ANTHROPIC_API_KEY`).

Results accumulate in `standard\results.md` / `results.jsonl`; full transcripts in `standard\logs\` (browse with
`& "$env:USERPROFILE\venvs\zen-evals\Scripts\inspect.exe" view --log-dir logs`). If a model server fails to start,
the log is in `standard\logs\server_<model>.log` — include its last 30 lines in the report.

Kill a stuck runner by its PID only (rule 1): `Get-CimInstance Win32_Process | ? CommandLine -match 'run.py|llama-server'`.

## 5. The report

Write `REPORT.md` in the kit folder:

1. **PC**: GPU (+VRAM, driver), CPU, RAM, laptop/desktop, power state, typical GPU temperature and clock under load.
2. **Install**: did setup and the downloads work; first start (did it open on a model?); smoke test result.
3. **Benchmark A**: per model, the rubric table + score /11, wall time, tool calls, notes, interventions.
4. **Benchmark B**: paste `standard\results.md`.
5. **Problems**: every error, crash, hang or confusing moment, with the exact text and what you did.
6. Then zip `REPORT.md`, `standard\results.*`, `standard\logs\`, `build\runs\` and the build folders
   (`Documents\ZenBattery\build\*`, without any `venv` inside) into `zen-test-results.zip` and tell the tester to
   send it to the developer.

When everything is done: `py zenctl.py set memory_recall=true`.

### Time budget (an 8 GB laptop GPU)

Setup + downloads 1–2 h · smoke test 5 min · Benchmark A ≈ 2–4 h × 3 models · Benchmark B ≈ 4–10 h × 3 models.
Benchmark A on all three models is the priority; then B's `quick` suite (`--suite quick`) if time is short.
