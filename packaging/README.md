# Building the Zen Harness installer

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Produces `packaging\out\ZenHarness-Setup-<VERSION>.exe` and its `.sha256`.

## Prerequisites (once)

| What | How |
|---|---|
| Python 3.13 venv with the app's packages + PyInstaller | `py -3.13 -m venv backend\venv`; `backend\venv\Scripts\pip install -r backend\requirements-dev.txt` |
| Node 22 | for the UI build (`npm ci` runs automatically) |
| Inno Setup 6 | `winget install JRSoftware.InnoSetup` (per-user is fine) |
| llama.cpp Windows **CUDA 12** build | unzip a `llama-*-bin-win-cuda-12*-x64.zip` release plus its `cudart-*.zip` into one folder; pass `-LlamaDir` or set `LLAMA_CPP_DIR` (default `%USERPROFILE%\llama.cpp`) |

## What the build does

1. `npm run build` in `ui/` → `backend/web_dist/`
2. `gen_licenses.py` → `backend/licenses/` (every bundled component's licence; shown in Help → Licences)
3. a Windows version resource from `VERSION`
4. PyInstaller (`zen-harness.spec`, onedir) → `out/dist/ZenHarness/` with `ZenHarness.exe` (window, server, tray
   service) and `zenharness-cli.exe` (MCP stdio bridge, `--version`)
5. copies `llama-server.exe`, its DLLs, the CUDA runtime DLLs and the CPU backends into `out/dist/ZenHarness/llama/`
6. smoke test: `zenharness-cli.exe --version`
7. Inno Setup (`zen-harness.iss`) → the setup exe

Flags: `-SkipUi` (reuse `backend/web_dist`), `-SkipFreeze` (reuse `out/dist`, e.g. after changing only the installer
script).

## What is deliberately not in the installer
- **Models** — downloaded on first run (Welcome screen) or added in Settings → Models.
- **Voice output (Kokoro TTS)** — its phonemizer bundles espeak-ng (GPL-3.0); to be offered as an optional download.
- **Formula OCR** (pix2tex + PyTorch, 4+ GB).
- **Offline Wikipedia** (14 GB, optional download), **local SearXNG** (web search uses the keyless DuckDuckGo-style
  engine race instead).

## Releasing
1. Bump `VERSION` (e.g. `0.1.0-alpha.2`), commit.
2. Build; install the result on a clean Windows user/VM and run the checklist in `plans/alpha-release-plan.md` §3 D6.
3. `git tag v<version>`; `git push --tags`; create a GitHub Release and attach the setup exe and `.sha256`.
