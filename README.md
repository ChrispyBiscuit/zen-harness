# Zazen

> [!WARNING]
> **Experimental software.** Zazen is an early alpha from a small independent project. It runs commands and edits files on your
> PC (it asks before risky steps), its AI makes mistakes, and settings, file formats and features can change without
> notice. Use it on files you have backed up, review what it builds, and don't rely on it for anything important.

**A private AI agent that runs on your own Windows PC.** Zazen is a desktop app that runs a capable open model,
Ternary Bonsai 2 27B, on an ordinary 8 GB graphics card, and gives it tools: files, a terminal, web search, a
browser, offline Wikipedia, documents, and code it can test. Your chats and files stay on your computer.

Status: **experimental alpha**. It works, and it has rough edges. See [What it can and can't do](#what-it-can-and-cant-do).

## Who it's for

- People who want an AI assistant that works **offline and privately**, with no account, no subscription and no
  telemetry.
- Developers and tinkerers who want a **local coding agent** for small apps, scripts, data jobs and fixes in their
  own projects.
- Anyone with a gaming PC or laptop who wants to use its graphics card for more than games.

It is not built for teams, servers, or work that has to be right the first time without review.

## What it was designed for

Small local models get lost on long tasks: they forget the goal, repeat themselves, and say "done" when it isn't.
Zazen is a **harness** built around that problem, tuned on a real 8 GB laptop:

- **A plan and a checklist** for every larger task, kept in view as the work goes on.
- **Proof before "done":** code changes need a passing test run, and a program it built must actually start (a page
  loads without errors, a server answers) before the agent may call it finished.
- **Loop guards:** repeated identical actions are caught and the agent is told how to get unstuck.
- **Context management for slow models:** older steps are compacted into short structured notes, so a 1-bit 27B
  model can work for hours on a 60K-token window.
- **Hardware detection:** it finds your graphics card and sets up the model, runtime and memory settings for it.

## What it runs on

| | Requirement |
|---|---|
| Operating system | Windows 10 (22H2) or 11, 64-bit |
| Graphics card | A dedicated card with **8 GB or more** of video memory, up to 16 GB supported |
| Memory (RAM) | 16 GB |
| Disk | About 10 GB: the app, the GPU runtime (30–700 MB), the model (5.9 or 7.2 GB). Offline Wikipedia adds 14 GB (optional). |

**Graphics cards:**

| Vendor | Status |
|---|---|
| NVIDIA GeForce RTX, 8 GB+ | Supported and tested (RTX 3070 Laptop, 8 GB) |
| AMD Radeon, 8 GB+ | Supported, **not yet tested on real hardware** |
| Intel Arc (8 GB+) | Supported, **not yet tested on real hardware** |
| Under 8 GB, integrated graphics, no GPU | Runs on the CPU, very slowly: not a supported setup |

The app picks the runtime for your card (CUDA, HIP, Vulkan or SYCL builds of
[PrismML's llama.cpp](https://github.com/PrismML-Eng/llama.cpp)) and sizes the model to its memory:

| Video memory | Model file | Context | Image reading |
|---|---|---|---|
| 8–10 GB | PTQ1_0 (5.9 GB) | up to 64K tokens | on the CPU |
| 11–13 GB | PQ2_0 (7.2 GB, same model, faster) | up to 96K tokens | on the GPU |
| 14–16 GB | PQ2_0 | up to 128K tokens | on the GPU |

Every setting can be changed by hand in **Settings → Hardware**.

**Speed:** about 15–30 tokens a second on an RTX 3070 laptop, slower as a long task fills the context. Quick
questions take seconds; building a small app takes from half an hour to a few hours.

**Not supported:** macOS (Apple Silicon is on the roadmap), Linux, and 32-bit Windows.

Cloud models (OpenAI, Anthropic) can be used instead of the local one with your own API key.

## What it can and can't do

**Does well:**
- Builds small software projects end to end: plans them, writes the code and tests, runs them, and documents how to
  use them. Example: a multi-user expense tracker with a command-line client, an HTTP server, 15 passing tests and a
  README.
- Fixes and extends code in your own projects, and runs the project's own tests to show the change works (pytest,
  npm, cargo, go, dotnet and other runners).
- Researches the web and answers with sources, or answers from offline Wikipedia with no internet.
- Works with your files: reads documents and PDFs, writes reports, spreadsheets, Word and PowerPoint files.
- Drives a built-in browser to check pages and web apps it built.
- Serves other apps: a local REST API and an MCP server, so Claude Desktop, Claude Code, Cursor and other tools can
  use it.
- Remembers facts and preferences you give it.

**Weak or not suitable:**
- **3D games and visual polish.** It can write the code, but it judges what's on screen poorly. In testing, a 3D
  game "passed" its own checks as a flat shape, and a second attempt ended on a black screen.
- **Large or long projects.** It gets there on small to medium tasks; on big ones it is slow and may need you to
  steer.
- **First-time-right results.** Like PrismML's own tests of this model, expect to give feedback a round or two:
  plain, specific complaints ("the button does nothing") get fixed well.
- **Document design.** Files are plain-looking today; better styles are being worked on.
- It is a 27B model compressed to under 2 bits per weight. It's strong for its size and far from cloud frontier
  models.

## Privacy

With the local model, everything runs on your PC. Only tools that reach the internet send anything out: web search,
web pages, map tiles, and the one-time downloads (the runtime from GitHub, the model from Hugging Face, Wikipedia
from Kiwix). No telemetry, no account. Details: [Your data and privacy](docs/data-and-privacy.md).

## Getting it

The newest code is on `main`. The last packaged installer,
[0.1.0-alpha.3](https://github.com/ChrispyBiscuit/zen-harness/releases), predates the multi-vendor hardware setup
described here (it supports NVIDIA only and asks for models during install). A new installer is coming; until then,
[build from source](docs/BUILD_FROM_SOURCE.md).

On first run the app checks your hardware and downloads what it needs: the runtime for your graphics card, the
model file that fits, its image reader and, if you want it, offline Wikipedia. Each download is checked against its
published checksum.

Documentation: [Getting started](docs/getting-started.md) · [Using the agent](docs/agent.md) ·
[Models and hardware](docs/models.md) · [Settings](docs/settings.md) · [Connect other apps](docs/connect-apps.md) ·
[Troubleshooting](docs/troubleshooting.md)

## Working on it

```
backend/     Python app: window (app_web.py), server (web_backend.py, :8765), agent engine (agentic.py),
             tools (agent_tools.py), hardware and runtimes (hardware.py, runtimes.py, setup_api.py),
             headless API/MCP (harness_api.py, harness_mcp.py)
  guides/      agent-facing how-to guides      playbooks/  task playbooks
  tests/       pytest suite (no model or GPU needed)
  evals/       model-in-the-loop benchmarks
  plans/       design notes and the roadmap (plans/TODO.md)
ui/          React + Vite front end; `npm run build` writes into backend/web_dist
docs/        user documentation (also shown in the app's Help)
packaging/   installer build (PyInstaller + Inno Setup)
```

Requirements: Windows 10/11 x64, Python 3.13, Node 22, git.

```powershell
git clone https://github.com/ChrispyBiscuit/zen-harness.git
cd zen-harness
py -3.13 -m venv backend\venv
backend\venv\Scripts\pip install -r backend\requirements.txt
cd ui; npm ci; npm run build; cd ..
backend\venv\Scripts\python.exe backend\app_web.py
```

- Tests: `backend\venv\Scripts\python.exe -m pytest backend\tests -q`
- UI with hot reload: `cd ui; npm run dev` (proxies `/api` and `/ws` to the backend on 8765).
- Your data (settings, chats, memory, logs) goes to `LWS_DATA_DIR` if set, never into the repo.
- Installer: `powershell -File packaging\build.ps1` (see `packaging/README.md`).
- `main` is always releasable; changes go through branches and pull requests. Never commit keys, `settings.json`,
  chats, models or build output.

## Built with

Zazen stands on a lot of other people's work.

**The model:** [Ternary Bonsai 2 27B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf) by
[PrismML](https://prismml.com), a ternary (1.58-bit) compression of Qwen3.8 27B, Apache 2.0.

**Running it:** [llama.cpp](https://github.com/ggml-org/llama.cpp) (ggml-org, MIT), through
[PrismML's fork](https://github.com/PrismML-Eng/llama.cpp), which adds the Bonsai formats and builds for CUDA, HIP,
Vulkan and SYCL.

**The app:**
- Backend: Python, [aiohttp](https://github.com/aio-libs/aiohttp), [PySide6 / Qt](https://www.qt.io/qt-for-python)
  for the window and its built-in browser, [Playwright](https://playwright.dev) for browser control, psutil, pywin32.
- Front end: [React](https://react.dev), [Vite](https://vite.dev), [Tailwind CSS](https://tailwindcss.com),
  [shadcn/ui](https://ui.shadcn.com) and [Base UI](https://base-ui.com), Zustand, CodeMirror, Shiki, KaTeX, MathLive,
  [Mermaid](https://mermaid.js.org), [Leaflet](https://leafletjs.com), Dockview, Lucide icons, Geist fonts.
- Documents and data: python-docx, python-pptx, openpyxl, xlrd, pypdf, pandas, DuckDB, NumPy, SciPy, SymPy, Pint,
  Matplotlib, Pillow, NetworkX, Jupyter (the agent's Python sessions).
- Search and reading: [ddgs](https://github.com/deedy5/ddgs) (keyless web search), trafilatura, lxml.
- Memory and speech: [fastembed](https://github.com/qdrant/fastembed) with ONNX Runtime,
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and CTranslate2, [Kokoro](https://github.com/thewh1teagle/kokoro-onnx).
- Offline knowledge: [Wikipedia](https://www.wikipedia.org) (CC BY-SA) packaged by [Kiwix](https://kiwix.org), read
  with libzim. libzim is GPL, so it is downloaded separately on first use and never shipped in the installer.
- Maps: [OpenStreetMap](https://www.openstreetmap.org) tiles and Nominatim place lookup.
- Packaging: [PyInstaller](https://pyinstaller.org) and [Inno Setup](https://jrsoftware.org/isinfo.php).

**Development:** designed and directed by its author, and written largely with
[Claude Code](https://claude.com/claude-code), Anthropic's AI coding agent.

Every third-party licence is listed in the app (Help → About → Licences) and in `backend/licenses/` of a build.

## License

[PolyForm Shield 1.0.0](LICENSE): free for anyone to use, copy and change, for personal or business use, except to
provide a product that competes with Zazen (for example, selling or redistributing a rebranded or modified copy).
It is source-available, not OSI open source. Third-party components keep their own licences (Help → About →
Licences). The model, Ternary Bonsai 2 27B by [PrismML](https://huggingface.co/prism-ml), is Apache 2.0.
