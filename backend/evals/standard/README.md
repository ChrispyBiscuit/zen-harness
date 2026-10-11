# Standard benchmark battery

Well-known benchmarks (via [Inspect Evals](https://github.com/UKGovernmentBEIS/inspect_evals)) run on local GGUF
models with llama.cpp, so models — and PCs — can be compared on the same fixed questions.

## Setup (once)

Needs Windows, an NVIDIA GPU, Python 3.11–3.13 (python.org, with the `py` launcher) and git.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
$py = "$env:USERPROFILE\venvs\zen-evals\Scripts\python.exe"
& $py run.py --list          # model profiles, and whether each model file is on this PC
```

`--list` finds models from the Zen Harness profiles (built-in presets + your Settings → Models profiles) in
`%LOCALAPPDATA%\ZenHarness\models` (the installed app's folder), `LWS_MODELS_DIR`, or `~\models`. llama-server
comes from the profile's own `llama_dir`, `LLAMA_CPP_DIR`, the installed app (`%LOCALAPPDATA%\Programs\Zen
Harness\llama`), or `~\llama.cpp`.

**Close Zen Harness (or wait for its run to finish) before benchmarking**: the runner starts its own llama-server
on port 8111 and refuses to start while the app's model server (port 8099) is up — one GPU holds one model.

## Run

```powershell
& $py run.py --model ornith-1.5:9b --suite quick              # IFEval + BFCL, a few hours
& $py run.py --model ornith-1.5:9b --suite battery            # + MMLU-Pro 1,000 + GPQA (thinking off): overnight
& $py run.py --gguf D:\models\Some-Model-Q4_K_M.gguf --suite battery    # any model file, no profile needed
& $py run.py --model ternary-bonsai-2-27b --suite gpqa         # GPQA with thinking on (long)
& $py run.py --model anthropic/claude-haiku-4-5 --suite quick  # cloud reference (ANTHROPIC_API_KEY or ~/.anthropic_key)
```

Results: `results.md` (table), `results.jsonl` (one row per run, with all metrics and the GPU), `logs/` (full
transcripts; browse with `& "$env:USERPROFILE\venvs\zen-evals\Scripts\inspect.exe" view --log-dir logs`).
A run can be stopped with Ctrl+C; re-running a suite starts it over.

Dry run with no model or GPU (checks the install):

```powershell
Start-Process $py "mock_server.py 8150" -WindowStyle Hidden
& $py run.py --model mock --base-url http://127.0.0.1:8150/v1 --suite quick --limit 3 --no-record
```

## Suites

| Suite | What | Size | Notes |
|---|---|---|---|
| `ifeval` | Instruction following | 541 | score = mean of strict/loose prompt and instruction accuracy |
| `bfcl` | Berkeley Function Calling (v1–v3 categories) | 400 seeded | tool-call accuracy incl. multi-turn |
| `mmlu_pro` | MMLU-Pro knowledge | 1,000 seeded | |
| `gpqa_nothink` / `gpqa` | GPQA Diamond science | 198 | thinking off / on (on: 8K-token budget, hours) |
| `aime2025` | AIME 2025 maths | 30 | thinking on, very long |
| `niah` | Needle in a haystack 4K–56K | 30 | optional; model-graded, approximate token counts |
| `tau2_retail`, `tau2_airline` | τ²-bench agent tasks | 40 / 50 | needs a user-simulator model (`--user-model`, cloud) |
| `swe_mini`, `gaia` | SWE-bench Verified Mini, GAIA L1 | 50 / L1 | need Docker Desktop running |

Groups: `quick` = ifeval, bfcl · `battery` = ifeval, bfcl, mmlu_pro, gpqa_nothink · `model-only` = ifeval, bfcl, gpqa.
Every model gets the same questions (seed 20261011), one pass, temperature/top-p/top-k from its profile. Thinking
follows the suite (`--think on|off` overrides).

Published scores to compare against go in `refs.json` (`{"suite|model": {"score": 0.71, "source": "<url>"}}`) and
appear in the table's Published column — fill them only from a cited source.
