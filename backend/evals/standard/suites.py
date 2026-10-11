"""The standard benchmark suites: which Inspect Evals task, which fixed subset, what each needs.

Subsets are fixed: samples are shuffled with SEED and the first `limit` taken, so every model sees the same
questions. `ctx` is the context the model server is started with for the suite (smaller = faster load, more
headroom); it is capped by the model profile's own num_ctx.
"""

SEED = 20261011

# suite groups for --suite: "battery" is the standard overnight set for a local model (no Docker, no API keys)
GROUPS = {
    "quick": ["ifeval", "bfcl"],
    "battery": ["ifeval", "bfcl", "mmlu_pro", "gpqa_nothink"],
    "model-only": ["ifeval", "bfcl", "gpqa"],
}

SUITES = {
    # ---- model-only track: no tools from our harness, published numbers exist for the full-precision models
    "ifeval": dict(
        task="inspect_evals/ifeval", limit=None, ctx=8192, think=False, metric="final_acc",
        what="Instruction following (541 prompts, verifiable constraints)",
        note="Short answers, so cheap; sensitive to quantization damage."),
    "gpqa": dict(
        task="inspect_evals/gpqa_diamond", limit=None, ctx=24576, think=True, max_tokens=12288,
        what="Graduate-level science multiple choice (198 questions)",
        note="Run with think on and off (--think off) - thinking-on is the expensive one."),
    "gpqa_nothink": dict(
        task="inspect_evals/gpqa_diamond", limit=None, ctx=16384, think=False,
        what="GPQA Diamond, thinking off (198 questions)",
        note="The cheap GPQA run (minutes, not hours); compare with 'gpqa' (thinking on) when there's time."),
    "bfcl": dict(
        task="inspect_evals/bfcl", limit=400, ctx=16384, think=False,
        what="Berkeley Function Calling Leaderboard v1-v3 categories (seeded 400-question subset)",
        note="Tool-call accuracy incl. multi-turn; the closest model-only proxy for agent reliability."),
    "mmlu_pro": dict(
        task="inspect_evals/mmlu_pro", limit=1000, ctx=16384, think=False,
        what="MMLU-Pro (seeded 1,000-question subset of 12k)",
        note="Broad knowledge; a quick check that compression didn't erase facts."),
    "aime2025": dict(
        task="inspect_evals/aime2025", limit=None, ctx=40960, think=True, max_tokens=32768,
        what="AIME 2025 (30 competition maths problems)",
        note="Very long thinking per problem; run overnight only."),
    "niah": dict(
        task="inspect_evals/niah", limit=None, ctx=61440, think=False,
        args=dict(min_context=4096, max_context=56000, n_contexts=6, n_positions=5),
        what="Needle in a haystack, 4K-56K tokens (30 probes)",
        note="Optional. Graded 1-10 by a model (by default the one under test) and sized with an OpenAI tokenizer, so "
             "treat it as a rough long-context smoke test, not a leaderboard number."),
    # ---- agent track: the model drives Inspect's own agent scaffold
    "tau2_retail": dict(
        task="inspect_evals/tau2_retail", limit=40, ctx=32768, think=False, user_model=True,
        what="tau2-bench retail (seeded 40 tasks): multi-turn tool use with a simulated customer",
        note="Needs a user-simulator model (--user-model, default claude-haiku-4-5; costs a little)."),
    "tau2_airline": dict(
        task="inspect_evals/tau2_airline", limit=None, ctx=32768, think=False, user_model=True,
        what="tau2-bench airline (50 tasks)",
        note="Needs a user-simulator model, as above."),
    "swe_mini": dict(
        task="inspect_evals/swe_bench_verified_mini", limit=None, ctx=61440, think=True, docker=True,
        what="SWE-bench Verified Mini (50 real GitHub issues)",
        note="Needs Docker Desktop running (WSL2 backend) and ~50 GB of images."),
    "gaia": dict(
        task="inspect_evals/gaia_level1", limit=None, ctx=61440, think=True, docker=True,
        what="GAIA validation, level 1 (web + files + code)",
        note="Needs Docker Desktop running; gaia_level2 is the next step up."),
}
