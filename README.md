# Zen Harness

A local AI agent for Windows: a desktop app that runs open-weight models on your own GPU through llama.cpp, with
tools (files, shell, web, browser, documents, code debugging), a planning/checklist harness for long tasks, memory,
and a headless REST API + MCP server so other apps can use it.

Status: **alpha** (`VERSION`). Private repository.

## Layout

```
backend/     Python app: window (app_web.py), server (web_backend.py, :8765), agent engine (agentic.py),
             tools (agent_tools.py), model servers (llama_local.py), headless API/MCP (harness_api.py, harness_mcp.py)
  guides/      agent-facing how-to guides      playbooks/  task playbooks
  tests/       pytest suite (no model or GPU needed)
  evals/       model-in-the-loop benchmarks (need a model; outputs are git-ignored)
  plans/       design docs: refactor-plan.md, alpha-release-plan.md, PRDs
  legacy/      old Qt app and scripts, not used by the app (kept for history)
ui/          React + Vite + shadcn front end; `npm run build` writes into backend/web_dist
docs/        user documentation (also shown in the app's Help)
packaging/   installer build (PyInstaller + Inno Setup)
```

## Working on it

Requirements: Windows 10/11 x64, Python 3.13, Node 22, git. For GPU inference: an NVIDIA GPU and a llama.cpp
CUDA build (set `LLAMA_CPP_DIR`, default `%USERPROFILE%\llama.cpp`). Models: GGUF files in `LWS_MODELS_DIR`
(default `%USERPROFILE%\models`).

```powershell
git clone https://github.com/<you>/zen-harness.git
cd zen-harness
py -3.13 -m venv backend\venv
backend\venv\Scripts\pip install -r backend\requirements.txt
cd ui; npm ci; npm run build; cd ..
backend\venv\Scripts\python.exe backend\app_web.py
```

- UI development with hot reload: `cd ui; npm run dev` (Vite proxies `/api` and `/ws` to the backend on 8765).
- Tests: `backend\venv\Scripts\python.exe -m pytest backend\tests -q`
- Where your data lives: settings, chats, memory, logs and attachments go to `LWS_DATA_DIR` if set (in a dev
  checkout point it at a folder outside the repo, e.g. your existing data folder). Nothing in the data dir is
  tracked by git, so pulling never touches your chats or settings. `settings.example.json` shows the shape of
  `settings.json`; the app creates its own on first run.

## Keeping a local copy in sync

```powershell
git pull            # get the latest
git status          # see what you changed
git checkout -b my-change; git commit -am "..."; git push -u origin my-change   # then open a PR
```

`main` is always releasable. Work happens on branches and is merged by pull request.

## Building the installer

See `packaging/README.md`: `powershell -File packaging\build.ps1` produces `packaging\out\ZenHarness-Setup-<version>.exe`.

## Never commit

API keys, `settings.json`, chats, memory databases, logs, models, venvs, build outputs — `.gitignore` covers them.
Run `git status` before every commit.
