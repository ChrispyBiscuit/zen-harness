# Zen Harness — refactor plan

Status: plan (2026-10-11). Nothing here has been executed yet. Built from a full read-only inventory of the backend
(`web-agent/`) and the UI (`dev/lws-web`). Line numbers are as of this date and will drift.

## Why, and what "done" means

The app grew feature by feature for months without a structural pass. It works, and the behaviour that works is
the asset — so this is a **staged, behaviour-preserving refactor**, never a rewrite. Each stage ships on its own,
keeps the app runnable, and is verified by the same regression net before and after.

Done = the live app is ~40% smaller on its import path, no module above ~1,500 lines, one place for paths and
settings, no import cycles, no private-name reach-ins across modules, and a real test suite that runs in CI-like
fashion (`pytest -q`) in under two minutes without a model or GPU.

## 1. What exists today

### Live path

```
Local Agent.bat → app_web.py (Qt window, native agent browser)
                    └─ spawns app_web.py --server → web_backend.serve_headless()  (aiohttp :8765)
                                                     ├─ harness_api  (/v1 REST, run queue, auth)
                                                     ├─ harness_mcp  (/mcp; also a separate --stdio bridge process)
                                                     └─ AgentEngine (agentic.py) → agent_tools.Registry → ~40 feature modules
headless.py (tray service, autostart) → same web_backend + harness_api
```

### Size and reachability (top modules)

| Module | Lines | Live? | Note |
|---|---|---|---|
| agentic.py | 6,082 | yes | engine: prompts, providers, routing, gates, loop, model calls |
| agent_tools.py | 4,257 | yes | every tool + schemas + Registry + approval policy |
| swarm.py | 3,530 | **partial** | imported by agentic only for `api_key()` and `Swarm._thinks_by_default`; drags in `agent` → `tools` → `chris_rag` → numpy/ollama |
| gui.py, agent_ui.py, swarm_ui.py, swarm_board.py, richtext.py, theme.py, app.qss | ~5,050 | no | legacy Qt app |
| web_backend.py | 1,880 | yes | Store, Bus, ConvWriter, Host, ~49 routes, websocket |
| checkpoints.py, projects.py, git_tools.py | 1,433 | no | built and unit-tested, never wired into the app |
| agent_runs.py, agent_host.HeadlessHost | ~700 | no | dead third/second "host" implementations |
| agent.py | 449 | partial | Chat mode uses `SYSTEM`, `compress_history`, `run_agent_events` |
| chris_rag.py, telegram_chris.py | 608 | partial | personas in the model list; Telegram bot if a token exists |
| top-level test_*.py (7), bench.py, diag_mem.py, smoke_gui.py, build_*_dataset.py, terrain/ | small | no | Ollama-era scripts |
| ~60 `*.bak-*` files | — | no | manual backups (git replaces them) |

### Structural problems (ranked by how much they hurt)

1. **Two god-modules.** `agentic.py` mixes ~12 concerns; the final-answer path alone has **17 send-back gates** in
   one ~230-line block of `run()`, plus 4 lean-pass checks and ~15 pre-tool refusals in `_run_tool`. Each new gate
   was added inline, so ordering and interactions are implicit (tonight's "checklist bounce gate refused a real
   re-plan" and "masking vs repeat gate" bugs were both gate-interaction bugs).
2. **Paths and data dirs.** `os.environ.get("LWS_DATA_DIR") or APP_DIR` is copied ~20 times and *not* honoured by
   about a third of writers (attachments, log, trackers, trash, bg, browser profiles, logs). This is a live bug under
   tests/evals (attachments split across two trees) and a **packaging blocker**: an install under Program Files
   cannot write to its own folder.
3. **Settings read five ways** (web_backend Store, gui.py, harness_api.cfg, harness_mcp raw read, six evals reading
   the file directly); `DEFAULT_AGENT` exists twice. The full settings object, including `headless.token`, is
   broadcast to every UI websocket (harness_api:530).
4. **Import cycles held together by lazy imports and a `sys.modules` alias** (web_backend ↔ harness_api;
   agent_tools ↔ bg_tasks / evidence / artifacts / gopher).
5. **Private reach-ins**: web_backend calls `eng._maybe_compact`, `_history_tokens`, `_call_openai`,
   `_call_ollama`, `speech_engine._stream`, `doc_extract._office_convert`, `at._backup`, and writes engine callback
   attributes directly; agentic uses `at._hist`, `at._first_sentence`, `factcheck._contains`; artifacts/evidence/
   deep_search/doc_extract do the same.
6. **Legacy on the live import path** (swarm/agent/chris_rag/telegram) costs startup time, memory, and makes Ollama
   a hidden hard dependency (`import ollama` at web_backend import time).
7. **Duplicated helpers**: atomic JSON write ×6, safe-name ×5, port helpers ×4, "is this the app's process" ×2,
   think-tag stripping ×4, tool-markup regexes ×3, OpenAI-compatible HTTP calls ×6, two API-key lookups, two memory
   stores (`agent_memory.md` + `agent_memory.db`).
8. **Hardcoded user paths**: `%USERPROFILE%\...` in agent.py, chris_rag.py, evals, tests; ~12 absolute model paths in
   settings.json; the MTP drafter lookup ignores `LWS_MODELS_DIR`; Vite's `outDir` is an absolute path into the
   backend folder.
9. **Tests**: nothing in `tests/` covers agentic or agent_tools. The only coverage is the 72-test offline suite that
   lives outside the project (`%TEMP%\claude\audit\offline_suite.py`).

## 2. Target layout

One repository, two top-level packages (see the release plan for why it is a monorepo):

```
zen-harness/
  backend/
    zen/                      # the Python package (was: flat modules in web-agent/)
      config/                 # paths.py (ONE data/app/models dir resolver), settings.py (load/save/defaults/migrate,
                              #   redaction), ports.py
      server/                 # app.py (make_app, routes split by area: chat, settings, models, files, bg, tts…),
                              #   store.py, bus.py, host.py, headless_api.py, mcp_http.py, mcp_stdio.py, tray.py
      engine/
        engine.py             # AgentEngine: state + the turn loop only
        prompts.py            # SYSTEM, LEAN_*, PLAN_PROMPT, SUMMARY_PROMPT, notes (text only)
        providers/            # base.py (one chat-completions client), llama.py, ollama.py, anthropic.py,
                              #   openai_responses.py, keys.py (ONE key lookup)
        parsing.py            # text tool-call parsers, think stripping, markup regexes
        routing.py            # lean pass, effort triage, continues_work, interpret, basic/extensive
        planning.py           # plan phase, checklist rules, project mode, PRD/build gates
        gates/                # one module per final-answer check, each a small class:
                              #   applies(turn) -> bool, check(final) -> SendBack|None, max_rounds
        detectors.py          # repeat gate, same-attempt, stuck reset, progress watch, read streak
        context.py            # masking, key-output stubs, work state, compaction, pins
        fanout.py, executor.py, advisor.py
      tools/
        registry.py           # Registry, schemas assembled from per-module TOOLS lists, aliases, approval policy
        files.py, shell.py, bgproc.py, http.py, debug.py, browser.py, screen.py, tracker.py, memory.py,
        documents.py, data.py, delegation.py, web.py, symbols.py (trace_symbol/search windows/outline)
      models/                 # llama_server.py (lifecycle), gguf.py (metadata), fit.py (context/KV/offload),
                              #   profiles.py (load/propose/validate/save model profiles), catalog.py (downloads)
      features/               # memory_store, playbooks, scheduler, artifacts, sandbox, py_kernel, factcheck,
                              #   quotecheck, evidence, encyclopedia, search (searx/ddgs), speech (tts/stt)
      window/                 # app_web (Qt window), qt_browser
      util/                   # jsonio (atomic write/read), names (safe_name), procs (app-process check, ports)
    tests/                    # pytest; conftest.py isolates data dirs; offline suite split by cluster
    guides/ playbooks/        # agent-facing content (data, unchanged)
    pyproject.toml            # deps pinned; optional extras: [speech], [docs-ocr], [dev]
  ui/                         # the React app (was dev/lws-web)
  docs/                       # user documentation (also rendered in-app)
  packaging/                  # PyInstaller spec, Inno Setup script, build scripts
  legacy/                     # (not shipped) gui.py & co., swarm, telegram, chris_rag, datasets — kept for history
```

Rules the layout enforces: `engine` never imports `server`; `tools` never imports `engine` (the engine passes a
small interface — advisor/gopher/host callables already exist on `Ctx`); `config` imports nothing from the app;
no module reads `settings.json` except `config.settings`; no module computes a data path except `config.paths`.

## 3. Staged plan

Every stage: (1) run the full test net before, (2) change, (3) run it after, (4) launch the app and do the smoke
script in §4, (5) commit. Stages are ordered so that the riskiest moves happen only after the safety net exists.

### Stage 0 — Safety net and repo (low risk, 1 session)
- Put the code under git (the monorepo; see the release plan). Delete the `*.bak-*` files in the repo copy — git
  history replaces them.
- Move the offline suite into `backend/tests/`: `conftest.py` with a session fixture that sets `LWS_DATA_DIR`,
  `LWS_NO_TELEGRAM`, `LWS_NO_SCHEDULER` to `tmp_path` and monkeypatches `agentic._log/LOG`,
  `agent_tools.TRACKERS/TRASH/BACKUPS`; helpers (`engine()`, `tc()`, `Emits`, `_fake_openai`) into
  `tests/helpers.py`; `@test def x` → `def test_x`; `probe_*` → `pytest.mark.xfail(strict=False)`; split into
  `test_text_heuristics.py`, `test_gates.py`, `test_lean_pass.py`, `test_context.py`, `test_tools_files.py`,
  `test_debug_tools.py`. Add the scratch tests written during the 2026-10-10 session (stall detector, read-before-
  write guard, trace_symbol, deliverables check, app-port guard, tracker re-plan) as proper tests.
- Delete the 7 Ollama-era top-level `test_*.py`.
- Add `pytest.ini` (testpaths, markers: `slow`, `gpu`, `network`). Target: `pytest -q -m "not gpu and not network"`
  < 2 min.
- **Verify**: suite green; app launches from the repo checkout.

### Stage 1 — One place for paths and settings (medium risk, fixes real bugs)
- `zen/config/paths.py`: `app_dir()` (install/read-only), `data_dir()` (user data: `LWS_DATA_DIR` →
  `%LOCALAPPDATA%\ZenHarness` for installs, the checkout folder in dev), `models_dir()`, `llama_dir()`,
  `logs_dir()`, `cache_dir()`. Replace all ~20 copies, including the writers that ignore `LWS_DATA_DIR` today
  (attachments, agent_log, trackers, trash, backups, agent_bg/pids/pyhook, browser profiles/shots, app logs,
  thinking_library writes, telegram files).
- `zen/config/settings.py`: load/merge defaults/migrate/save (atomic); `public_view()` that redacts secrets
  (`headless.token`, MCP server headers/env, provider keys) — used by `/api/state` and every websocket broadcast.
  Replace gui.py/harness_api/harness_mcp/eval readers. One `DEFAULT_AGENT`.
- Model paths in profiles become relative to `models_dir()` when they are inside it (portable settings).
- Fix the MTP drafter lookup to use `models_dir()`; Vite `outDir` relative (`../backend/zen/web_dist` or env).
- **Verify**: tests; a run with `LWS_DATA_DIR` set leaves the app folder untouched (assert no writes by diffing the
  tree); attachments and logs land in one place; token absent from `/api/state` and websocket frames.

### Stage 2 — Cut legacy off the live path (low-medium risk, big startup win)
- Move `api_key()` into `engine/providers/keys.py`; `_thinks_by_default` into providers; agentic stops importing
  swarm. Chat mode: port the three functions it uses from `agent.py` into `engine/chat.py` (or route Chat mode
  through the engine with tools off). `ollama` becomes a lazy, optional import used only when the Ollama backend is
  selected.
- chris_rag personas and the Telegram bot become an optional plugin (`features/personas`, off unless configured);
  their data files and the `digital-self` paths leave the repo (personal data).
- Move gui.py & co., swarm*, agent_runs, HeadlessHost, bench/diag/smoke/build_*_dataset, terrain to `legacy/` (not
  imported, not packaged). Decide checkpoints/projects/git_tools: wire in behind a feature flag or move to `legacy/`
  — they are tested but unused (recommendation: keep `git_tools` for the engineering-workspace PRD, park the other two).
- **Verify**: tests; `python -X importtime` on server start shows swarm/agent/chris_rag/numpy/ollama gone; app smoke.

### Stage 3 — Break the cycles and private reach-ins (medium risk)
- web_backend ↔ harness_api: introduce `server/events.py` (an in-process pub/sub the API subscribes to) so
  web_backend never names harness_api; remove the `sys.modules` alias.
- Give AgentEngine a small public surface the server uses: `compact()`, `history_tokens()`, `complete(messages)`
  (for compress/summarise helpers), `bind(host)` (emit/ask/should_stop wiring) — replace the attribute pokes.
- Promote the helpers others reach for to public names in `util/` (`backup_file`, `file_history`, `first_sentence`,
  `contains_phrase`, `safe_name`, `loose_match`).
- bg_tasks/evidence/artifacts/gopher: move the shared bits they need from agent_tools into `util/` or `models/` so
  the dependency points one way.
- **Verify**: tests; a lint rule (`import-linter` contracts or a 30-line test that parses imports) forbids
  engine→server, tools→engine, and any `._private` access across modules.

### Stage 4 — Split agent_tools.py (medium risk, mechanical)
- One module per cluster (files, shell, bgproc, http, debug, browser, screen, tracker, memory, documents, data,
  delegation, web, symbols). Each exports `TOOLS = [(name, kind, desc, schema, fn), ...]`; `registry.py` assembles
  BUILTIN from them in a fixed order (keeps the schema order — and therefore the prompt prefix — byte-identical, which
  matters for the llama.cpp prompt cache). Approval policy → `registry/approval.py`.
- **Verify**: tests; a snapshot test of the full tool-schema JSON (must be identical before/after); app smoke.

### Stage 5 — Split agentic.py (highest risk, do last, in sub-steps)
Order (each sub-step is its own commit, tests + snapshot between):
1. `prompts.py` (pure text) — snapshot the assembled system prompt for 3 configs before moving; must be identical.
2. `parsing.py` (tool-call parsers, think stripping, markup regexes; dedupe with memory_store/agent.py copies).
3. `providers/` (Anthropic/Responses adapters, retrying urlopen, PROVIDERS table, one chat-completions client used
   by the engine, gopher, speech_engine and the advisor).
4. `context.py` (masking, stubs, key output, work state, compaction, pins).
5. `detectors.py` (repeat gate, same-attempt, stuck reset, progress watch).
6. **Gates**: turn the 17 inline send-backs into `gates/*.py` objects with an explicit ordered pipeline:
   `for gate in FINAL_GATES: if gate.applies(turn) and (sb := gate.check(final)): ...`. Each gate owns its counter
   and max rounds; the pipeline logs which gate fired. This makes ordering explicit and testable per gate (each gate
   gets a unit test with a fake turn). Same for the ~15 pre-tool refusals (`gates/pre_tool.py`) and the 4 lean-pass
   checks.
7. `routing.py`, `planning.py`, `fanout.py`, `executor.py`, `advisor.py`.
8. What remains in `engine.py`: state, `prepare()`, `run()`, `_run_tool()` dispatch — target < 1,500 lines.
- **Verify**: tests; prompt/schema snapshots; replay 10 recorded turns from `agent_log.jsonl` through the engine with
  a fake model that returns the recorded replies — the sequence of tool calls, gates fired and final text must match;
  then one live battery sample (10 tasks) against the 9B before/after (expect equal pass rate within noise).

### Stage 6 — Server split and API hygiene (low-medium risk)
- web_backend's ~49 routes → `server/routes_*.py` by area; Store/Bus/ConvWriter/Host into their own modules.
- Conversation store: `conversations.json` (12.6 MB, rewritten on every save) → SQLite (one row per conversation,
  messages as JSON) with a one-time importer. Tonight's crash showed `save_convs` failing under memory pressure;
  incremental writes remove that spike.
- **Verify**: tests (test_headless_api already boots the server — extend it to cover the moved routes); app smoke.

### Stage 7 — Dependencies (low risk)
- `pyproject.toml` with pinned versions of what the live code imports; extras: `speech` (kokoro-onnx,
  faster-whisper), `ocr` (pix2tex + torch — 2+ GB, optional), `dev` (pytest, import-linter). Drop from the venv:
  transformers, trl, peft, accelerate, bitsandbytes, datasets, opencv, albumentations, x_transformers, pyarrow (unless
  duckdb/pandas need it), torch unless OCR is enabled.
- **Verify**: fresh venv from `pyproject.toml` runs the suite and the app.

## 4. Smoke script (every stage)

1. Launch the window; the UI loads; Settings opens; the model list shows profiles and model files.
2. Chat mode: one message gets an answer (Ollama or llama backend).
3. Agent mode: "what time is it in Tokyo" (lean pass + web), "create hello.py that prints 2+2 and run it"
   (files + shell), "list the processes listening on ports" (shell), start/stop a background server.
4. Headless: `GET /v1/health`; `POST /v1/runs` with the token; MCP `tools/list` over HTTP.
5. Restart the app: conversations and settings persist; no files written outside the data dir.

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Behaviour drift in the engine split (gate order, prompt text) | prompt/schema byte-snapshots; recorded-turn replay; one battery sample per stage 5 sub-step |
| Prompt-cache regressions (prefix changes slow every turn) | snapshot test on the assembled prompt + tool schema JSON |
| Data loss when moving data dirs | Stage 1 never moves user files automatically; a one-time migrator copies old-layout data into the new data dir and leaves the original in place |
| Parallel feature work collides with the refactor | stages are small; feature work continues on the repo; each stage is one PR-sized commit series |
| Hidden users of "dead" modules | Stage 2 moves to `legacy/` (not delete) and runs the import-time check; delete only after a release cycle |

## 6. Order relative to the alpha release

Stages 0, 1 and 7 are **prerequisites for the installer** (git, data dirs outside the install folder, a dependency
list). Stage 2 is strongly recommended before packaging (removes Ollama as a hidden requirement and ~3 GB of unused
packages from the bundle). Stages 3–6 can follow the alpha without blocking it.
