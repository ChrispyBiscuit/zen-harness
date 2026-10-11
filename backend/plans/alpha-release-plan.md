# Zen Harness — alpha release plan

Status: plan + execution log (2026-10-11). Goal from the user: *"single exe installer gets everything running"* on a
fresh Windows machine, with documentation, an in-app Help section, easy API/MCP hookup in the menu, an easy way to
drop in and run new models, and the code in its own private GitHub repo (`zen-harness`) that the user works from.

Decisions already made by the user (2026-10-11): repo **private**; build tools **PyInstaller + Inno Setup**
(installed: Inno Setup 6.7.3 at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`); models **downloaded on first run**
(the installer ships none).

## 1. What "alpha" means here

- One file, `ZenHarness-Setup-<version>.exe`. Double-click → per-user install (no admin) → Start-menu + desktop
  shortcut → app opens → first-run screen detects the GPU/RAM, recommends a model, downloads it with progress,
  test-loads it, and the user is chatting. No Python, Node, git, Ollama or llama.cpp needed beforehand.
- Works on: Windows 10 22H2+/11 x64. NVIDIA GPU (CUDA 12) gets GPU inference; anything else runs on CPU (slow, but
  works) — the first-run screen says which.
- Not in alpha: auto-update (manual: download the new setup; settings/data survive), code signing (SmartScreen will
  warn — documented), macOS/Linux, multi-user installs.

## 2. Architecture decisions

| Topic | Decision | Why |
|---|---|---|
| Repo | **Monorepo** `zen-harness/` with `backend/`, `ui/`, `docs/`, `packaging/`, outside OneDrive (`%USERPROFILE%\dev\zen-harness`) | one version for UI+backend; the UI build output feeds the backend; OneDrive sync and `.git`/`node_modules`/venvs don't mix |
| Freeze | PyInstaller **onedir** (not onefile) | Qt WebEngine + CUDA DLLs are huge; onefile would unpack ~2 GB to %TEMP% on every start |
| Installer | Inno Setup, per-user (`PrivilegesRequired=lowest`), installs to `%LOCALAPPDATA%\Programs\Zen Harness` | no admin prompt; clean uninstall |
| Install vs data | install folder is **read-only at runtime**; all user data in `%LOCALAPPDATA%\ZenHarness\` (`settings.json`, chats, memory, logs, attachments, trackers, browser profiles, `models\`) — overridable by `LWS_DATA_DIR` | required for an install; also what refactor Stage 1 does |
| llama.cpp | bundle the Windows **CUDA 12** build (llama-server + ggml-cuda + cudart/cublas DLLs, ~1.4 GB uncompressed) + its CPU backends; llama.cpp loads the CPU backend when there is no NVIDIA driver | GPU users work out of the box, others fall back to CPU |
| Models | none bundled; first-run catalog (curated list with size/VRAM needs + direct Hugging Face GGUF URLs) + "add your own" | keeps setup.exe to ~1–1.5 GB |
| Web search | keyless `ddgs` engine race by default (already the fallback); local SearXNG becomes an optional add-on | SearXNG needs its own Python venv + 130 MB |
| Offline Wikipedia | optional download (Settings → Knowledge), off by default | 14 GB |
| Speech (TTS/STT) | optional; models (~1 GB) downloaded when first enabled | most users won't use it |
| Ollama | optional backend only; not required for any default feature | today Chat mode and personas silently need it |
| Formula OCR (pix2tex + torch) | excluded from alpha | torch alone is 2+ GB |
| Version | `VERSION` file (start `0.1.0-alpha.1`), shown in About and `/v1/health` | |

### Footprint estimate (to confirm at build)
Python runtime + live deps ~0.6–0.9 GB, PySide6 + WebEngine ~0.4 GB, llama.cpp CUDA ~1.4 GB, UI ~5 MB →
~2.5–2.8 GB installed, **~1.0–1.4 GB setup.exe** (LZMA2). Model download on first run: 4.9 GB for the
recommended 9B, more for larger ones.

## 3. Work breakdown (execution order)

### Phase A — Repository (refactor Stage 0)
1. Create `%USERPROFILE%\dev\zen-harness`; copy backend **code only** into `backend/` (no data, no `*.bak-*`, no
   venv, no datasets/RAG indexes, no logs, no `agent_*` data folders, no `openai_key.txt`/`telegram_config.json`);
   copy `dev/lws-web` source into `ui/` (no `node_modules`, no `dist`).
2. `.gitignore` (models, venvs, data, logs, build outputs, secrets — see §5), `settings.example.json` (secrets and
   personal paths stripped), `README.md` with setup/dev/sync instructions.
3. Secret scan before the first commit (token patterns: `sk-`, `csk-`, `ghp_`, `xox`, bot tokens, 32+ char base64,
   `token`/`key` JSON fields; personal paths). Fix, then `git init`, commit.
4. Bring the offline suite into `backend/tests/` (refactor Stage 0) and make `pytest -q` green.
5. Create the **private** GitHub repo `zen-harness` and push — only after the user confirms the first push.

**Cutover**: after Phase B, the user's launcher runs the repo checkout with `LWS_DATA_DIR` pointing at the old
`web-agent` folder, so every chat/setting/memory stays where it is. From then on all work happens in the repo
(parallel sessions included); `web-agent/` becomes a data folder.

### Phase B — Paths and settings (refactor Stage 1, minimal cut needed for packaging)
- `paths.py`: app dir (frozen-aware: `sys._MEIPASS`/exe dir), data dir (`LWS_DATA_DIR` → dev checkout → frozen:
  `%LOCALAPPDATA%\ZenHarness`), models dir (data dir `models\`, plus any extra folders listed in settings), llama dir
  (bundled `llama\` in the install, overridable).
- Route every writer through it (attachments, logs, trackers, trash, bg, pyhook, browser profiles, thinking library,
  conversations, memory DB, playbook state, schedules…).
- Settings: one loader with defaults + migration; secrets redacted from `/api/state` and websocket broadcasts.
- Model paths in profiles stored relative to the models dir.
- Vite `outDir` → `../backend/web_dist` (relative).

### Phase C — Product features
1. **Help & docs**: `docs/` (Getting started, Models, Agent & tools, Settings reference, Connect apps (API & MCP),
   Data & privacy, Troubleshooting, FAQ). Backend serves them (`GET /api/docs`, `/api/docs/{page}`); the UI gets a
   **Help** entry (sidebar/menu + `?` button) that renders them with search, plus an **About** box (version, data
   folder, logs folder, open-folder buttons, licences).
2. **Connect apps (API & MCP)**: the headless panel exists (`ServicePanel.tsx`); make it a first-class Settings tab
   "Connect apps" with: one switch each for REST API / MCP, port, LAN toggle, token (show/copy/regenerate), a **Test
   connection** button, and copy-ready snippets: curl, Python, Claude Desktop `claude_desktop_config.json` (stdio and
   HTTP), Claude Code `claude mcp add`, Cursor/VS Code `mcp.json`. Snippets use the frozen exe path for stdio.
3. **Models**: a Settings → Models tab:
   - list: profiles + GGUF files in the models dir(s) with size, architecture, params, MoE/dense, quant, "fits on your
     GPU" verdict;
   - **Add model**: pick a file / drop it into the folder ("Open models folder") / paste a Hugging Face URL
     (downloads with progress, resumable);
   - on add: read GGUF metadata, propose a profile via `fit_context` (context, KV type, MoE offload, drafter/mmproj
     auto-match), editable;
   - **Test load**: start the model on a spare port, send a 1-token prompt, report load time, VRAM, tokens/s, then
     stop; on success save the profile and offer "use as default";
   - backend endpoints: `GET /api/models`, `POST /api/models/inspect`, `POST /api/models/profile`,
     `POST /api/models/test`, `POST /api/models/download` (+ progress events), `DELETE /api/models/profile/{name}`.
4. **First-run screen** (no model configured): hardware check (GPU name/VRAM via nvidia-smi, RAM, disk) →
   recommended models from the catalog with sizes → download → auto-profile → test load → done. Skip = "I'll add a
   model myself" (opens the Models tab) or "use a cloud API key" (OpenAI/Anthropic key entry).
5. **Diagnostics**: Help → "Copy diagnostics" (versions, GPU, paths, last 200 log lines, redacted) for bug reports.

### Phase D — Packaging
1. `backend/requirements.txt` / `pyproject.toml` pinned to what the live code imports (exclude torch, transformers
   etc.). Build venv: `packaging/build-venv` created fresh from it.
2. `packaging/zen-harness.spec` (PyInstaller onedir): entry `app_web.py` (window) with `--server` / `--headless` /
   `--mcp-stdio` modes dispatched from the same exe (one exe, several roles — `app_web.py --server` already re-execs
   itself; frozen it re-execs `ZenHarness.exe --server`); data files: `web_dist/`, `guides/`, `playbooks/`, `docs/`,
   `assets/`, `thinking_library.json`, `VERSION`; hidden imports for lazily imported modules; PySide6 WebEngine
   resources; exclude Qt modules not used.
3. `packaging/llama/` fetch script: copies (dev) or downloads (CI) the pinned llama.cpp CUDA build into the bundle.
4. `packaging/zen-harness.iss` (Inno Setup): per-user, app files + llama, shortcuts, optional "start the headless
   service at sign-in" task, uninstall keeps user data unless the user ticks "remove my data".
5. `packaging/build.ps1`: build UI (`npm ci && npm run build`) → freeze → assemble → ISCC → `dist/ZenHarness-Setup-
   <version>.exe`, plus SHA-256.
6. **Test on a fresh machine**: preferred Windows Sandbox (needs the Windows feature enabled — the user's call) or a
   second Windows user account; minimum: run the installed app with a brand-new empty data dir and a PATH without
   Python/Node/llama.cpp, and walk the first-run flow end to end (download a small model, chat, agent task, API call,
   MCP call, uninstall).

### Phase E — GitHub workflow
- Private repo `zen-harness`; `main` is releasable; feature branches → PRs (sessions open PRs; the user merges).
- README "Working on Zen Harness": clone, `py -3.13 -m venv .venv`, `pip install -e backend[dev]`, `cd ui && npm ci`,
  `npm run dev` (UI on Vite, proxied to the backend), `python backend/app_web.py`, tests, building the installer.
- Sync: `git pull` to update a dev checkout; data never lives in the repo (it's in the data dir), so pulls never
  touch chats/settings. Releases: tag `v0.1.0-alpha.N`, attach the setup.exe to a GitHub Release.
- Optional later: a GitHub Actions workflow that runs the offline tests on push (Windows runner, no GPU).

## 4. Risks

| Risk | Mitigation |
|---|---|
| PyInstaller misses lazily imported modules | explicit hidden-imports list generated from the import inventory + a frozen smoke test that imports every module |
| Qt WebEngine packaging (QtWebEngineProcess.exe, resources, locales) | use PySide6's PyInstaller hooks; smoke test the window and the agent browser in the frozen build |
| CUDA DLL/driver mismatch on other machines | ship the CUDA 12 runtime DLLs with llama.cpp; first-run reports driver version; CPU fallback |
| Setup size | onedir + LZMA2; exclude unused Qt modules and ML packages |
| Repo/live divergence before cutover | cut over right after Phase B; announce to parallel sessions that edits move to the repo |
| Secrets in history | scan before the first commit; settings.json, key files, chats never tracked; `git log -p` review before the first push |

## 5. Never in the repo
settings.json (+ `.bak-*`), `openai_key.txt`, `telegram_config.json`, `telegram_history.json`, `conversations.json`,
`agent_log.jsonl`, `agent_memory.db*`, `agent_memory.md`, `playbooks_state.json`, `schedules.json`, `projects.json`,
all `*.log`, `swarm_stats.jsonl`, `agent_attachments/`, `agent_artifacts/`, `agent_sandbox/`, `agent_trash/`,
`agent_trackers/`, `agent_bg/`, `agent_browser_*/`, `research_notes/`, `reports/`, `repomap/` cache, `sandbox/`,
RAG indexes and datasets (`*_rag_index.npy`, `*_rag_meta.jsonl`, `*_dataset.jsonl`, `marx_*`, `kirk_*`), `venv/`,
`node_modules/`, `web_dist/`, `dist/`, `build/`, `*.gguf`, `evals/**/runs/`, `evals/**/results/`, `evals/build/*.log`.

## 6. Execution log
- 2026-10-11: plans written; Inno Setup installed; user chose private repo / PyInstaller + Inno / first-run model
  download.
- 2026-10-11: Phase A done — monorepo at `%USERPROFILE%\dev\zen-harness` (code only; secrets/data excluded and
  scanned), offline suite in backend/tests (pytest: 140 pass, 2 xfail probes), pinned requirements, private repo
  https://github.com/ChrispyBiscuit/zen-harness.
- 2026-10-11: Phase B done — `paths.py` (data/resources/models/llama dirs; installed builds use
  `%LOCALAPPDATA%\ZenHarness`); llama.cpp logs/pids moved to the data dir. Security fix: the UI routes and `/ws`
  refuse cross-origin requests (a browser page could read chats/token over the websocket before).
- 2026-10-11: Phase C done — docs/ (7 pages) + title-bar Help menu (docs, About, Licences, diagnostics); Settings →
  Models (catalog, inspect/propose, test load, download, file picker); Connect apps (Claude Desktop / Claude Code /
  Cursor snippets, test connection); first-run Welcome (hardware check, recommended model, auto setup, cloud key).
  Licences: `packaging/gen_licenses.py` (python, npm, llama.cpp, CUDA runtime, Qt, Python, models); espeak-ng
  (GPL-3.0) kept out of the installer.
- 2026-10-11: Phase D — PyInstaller onedir (`ZenHarness.exe` + `zenharness-cli.exe`), roles via flags, bundled Python
  for agent scripts / tests / the Python tool's kernel, Inno Setup per-user installer, `packaging/build.ps1`.
