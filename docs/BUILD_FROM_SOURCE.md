# Build and test Zazen from source

For a tester who would rather build Zazen themselves than download the installer. Windows 10/11 x64 with an
NVIDIA GPU (8 GB or more) is assumed. Two ways:

- **A. Build the installer** (recommended) — you get exactly what testers get: the installer downloads the three models
  and Bonsai's llama.cpp build, and the app starts ready to use. ~20–30 min to build, plus downloads.
- **B. Run from source** — no installer; quicker to start, but you download the models yourself.

Don't hand-tune model settings before benchmarking: results are only comparable when everyone uses the shipped
profiles. On cards bigger than 8 GB the app already sizes context, cache and expert placement automatically (the
status line says what it chose).

---

## 1. Prerequisites (once)

| What | How |
|---|---|
| Python **3.13** | python.org installer, with the `py` launcher ticked |
| Node **22** | nodejs.org (needed to build the UI) |
| git | git-scm.com |
| llama.cpp, Windows **CUDA 12** build | from <https://github.com/ggml-org/llama.cpp/releases> download `llama-<build>-bin-win-cuda-12.4-x64.zip` **and** `cudart-llama-bin-win-cuda-12.4-x64.zip`; unzip both into **one** folder: `%USERPROFILE%\llama.cpp` (so `llama-server.exe` and `cudart64_12.dll` sit side by side) |
| Inno Setup 6 (option A only) | `winget install JRSoftware.InnoSetup` |

Get the code and its Python packages:

```powershell
git clone https://github.com/ChrispyBiscuit/zen-harness.git
cd zen-harness
py -3.13 -m venv backend\venv
backend\venv\Scripts\pip install -r backend\requirements-dev.txt
```

Check it works (optional, ~3 min): `backend\venv\Scripts\python.exe -m pytest backend\tests -q` → all pass.

---

## 2A. Build the installer (recommended)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Result: `packaging\out\ZenHarness-Setup-<version>.exe` (~825 MB) and its `.sha256`. If llama.cpp is somewhere else,
add `-LlamaDir C:\path\to\llama.cpp`.

Run the installer:
- On the **Models** page keep all three ticked (~22 GB, each file SHA-256 checked): Ornith 1.5 9B, Qwen3.6 35B-A3B
  REAP-48, Ternary Bonsai 2 27B. Setup also fetches PrismML's llama.cpp build that Bonsai needs.
- Let it start the app — it should open on Ornith 1.5 9B with no "download a model" dialog.

Then go to **3. Run the tests**.

## 2B. Run from source (no installer)

```powershell
cd ui; npm ci; npm run build; cd ..
backend\venv\Scripts\python.exe backend\app_web.py
```

Download the models into `%USERPROFILE%\models` (file names must stay exactly as below — the built-in profiles find
them by name):

| Model | File | Size | Link |
|---|---|---|---|
| Ornith 1.5 9B | `Ornith-1.5-9B-Q4_K_M.gguf` | 5.8 GB | <https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/Ornith-1.5-9B-Q4_K_M.gguf> |
| Ornith vision (optional) | `mmproj-Ornith-1.5-9B-BF16.gguf` | 0.9 GB | <https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/mmproj-Ornith-1.5-9B-BF16.gguf> |
| Qwen3.6 35B-A3B REAP-48 | `Qwen3.6-35B-A3B-REAP-48-v2.gguf` | 9.4 GB | <https://huggingface.co/crucible-labs/Qwen3.6-35B-A3B-REAP-48-v2-GGUF/resolve/main/Qwen3.6-35B-A3B-REAP-48-v2.gguf> |
| Ternary Bonsai 2 27B | `Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5.9 GB | <https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/Ternary-Bonsai-2-27B-PTQ1_0.gguf> |

**Bonsai needs PrismML's llama.cpp fork** (its 1-bit format isn't in standard llama.cpp):
1. Download `llama-prism-b10772-e8fd209-bin-win-cuda-12.4-x64.zip` from
   <https://github.com/PrismML-Eng/llama.cpp/releases/tag/prism-b10772-e8fd209>.
2. Unzip it into `%USERPROFILE%\llama-prism`.
3. Copy `cudart64_12.dll`, `cublas64_12.dll` and `cublasLt64_12.dll` from `%USERPROFILE%\llama.cpp` into
   `%USERPROFILE%\llama-prism`.

Restart the app; Settings → Models should list `ornith-1.5-9b`, `qwen3.6-35b-a3b-reap48` and
`ternary-bonsai-2-27b` as present. Pick one as the model.

---

## 3. Run the tests

The test plan is `docs/TESTER_GUIDE.md`. It is written for **Claude Code**: open Claude Code in the folder that holds
the test tools and say:

> Read TESTER_GUIDE.md and run the test plan in it. Ask me before anything it says to ask about.

The test tools live in the repo under `backend\evals\`:

| In the guide | In your clone |
|---|---|
| `zenctl.py` | `backend\evals\zenctl.py` |
| `build\` (build benchmark) | `backend\evals\build\` |
| `standard\` (benchmark runner) | `backend\evals\standard\` |
| `TESTER_GUIDE.md` | `docs\TESTER_GUIDE.md` |

Easiest: copy `docs\TESTER_GUIDE.md` into `backend\evals\` and open Claude Code there — then every path in the guide
works as written. Or build the same kit zip the release has:
`powershell -ExecutionPolicy Bypass -File packaging\make_tester_kit.ps1` → `packaging\out\zen-harness-tester-kit-<version>.zip`.

What the plan does, in short:
1. Checks the PC (GPU, RAM, disk, power) and records it.
2. **Build benchmark** — the app's agent builds a roommate expense tracker from one plain request, on each model in
   turn; you grade it by hand against `build\RUBRIC.md` (compare with `build\GRADES.md`).
3. **Standard benchmarks** — IFEval, BFCL (function calling), MMLU-Pro, GPQA on each model with the runner
   (`standard\setup.ps1` once, then `run.py --model <name> --suite battery`). Close the app first: one GPU, one model.
4. Writes `REPORT.md` and zips the results to send back.

Timing on a desktop RTX 4070 (estimates): build benchmark ~40–60 min per model; standard battery a few hours per
model — run it overnight.

---

## Keeping up to date

```powershell
git pull
backend\venv\Scripts\pip install -r backend\requirements-dev.txt
```

Then rebuild the installer (2A) or restart the app (2B). Your settings, chats and models are kept (installed app:
`%LOCALAPPDATA%\ZenHarness`; from source: the `backend` folder and `%USERPROFILE%\models`).

## If something goes wrong

- **"llama-server.exe not found"** — llama.cpp isn't in `%USERPROFILE%\llama.cpp` (or pass `-LlamaDir`).
- **Bonsai answers with "////" or won't load** — the PrismML build is missing or lacks the three CUDA DLLs (2B, step 3).
  The installer handles this automatically.
- **SmartScreen warns about the installer** — it isn't code-signed yet: More info → Run anyway.
- **Something else** — Help → Diagnostics in the app has the logs; include them in your report.

Zazen is source-available under the [PolyForm Shield 1.0.0](../LICENSE) license.
