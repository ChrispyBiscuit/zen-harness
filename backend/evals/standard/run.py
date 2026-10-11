"""Run standard benchmarks (Inspect Evals) on a local model profile or a cloud model, and record the scores.

  python run.py --model ternary-bonsai-2-27b --suite ifeval
  python run.py --model ternary-bonsai-2-27b --suite gpqa --think off
  python run.py --model ornith-1.5:9b --suite ifeval,bfcl,gpqa
  python run.py --model anthropic/claude-haiku-4-5 --suite bfcl          (cloud reference; key from ~/.anthropic_key)
  python run.py --model mock --base-url http://127.0.0.1:8150/v1 --suite ifeval --limit 3   (dry run, no GPU)

A local model gets its own llama-server (serve.py, port 8111), started per suite at the suite's context size and
stopped afterwards. Results: logs/ (full Inspect logs, viewable with `inspect view --log-dir logs`), results.jsonl
(one row per run) and results.md (the table, rebuilt from results.jsonl).
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from suites import GROUPS, SEED, SUITES  # noqa: E402
import serve  # noqa: E402

LOGS = os.path.join(HERE, "logs")
RESULTS = os.path.join(HERE, "results.jsonl")
TABLE = os.path.join(HERE, "results.md")
REFS = os.path.join(HERE, "refs.json")       # published scores to compare against: {"suite|model": {score, source}}
THINK_BUDGET = 8192                          # tokens of thinking per answer when thinking is on

# Dataset downloads clone git repos into a deep cache folder: BFCL's file names pass Windows' 260-character limit
# ("Filename too long"). Turn on long paths for the git processes this run starts only (not the user's git config).
os.environ.setdefault("GIT_CONFIG_COUNT", "1")
os.environ.setdefault("GIT_CONFIG_KEY_0", "core.longpaths")
os.environ.setdefault("GIT_CONFIG_VALUE_0", "true")


def _cloud(name):
    return "/" in name and not name.startswith("openai-api/")


def _load_key(env, path):
    if not os.environ.get(env) and os.path.isfile(os.path.expanduser(path)):
        os.environ[env] = open(os.path.expanduser(path), encoding="utf-8").read().strip()


def _config(suite, think, prof):
    """Sampling and thinking settings for one suite. Local models use their profile's sampling (the vendor's
    recommended values); thinking is switched through the chat template, like the app does."""
    from inspect_ai.model import GenerateConfig
    extra = {}
    if prof is not None:
        extra["chat_template_kwargs"] = {"enable_thinking": bool(think)}
        if not think and prof.get("reasoning_off_field"):
            extra["reasoning_effort"] = "none"      # templates that ignore enable_thinking (Bonsai 2)
        if prof.get("min_p") is not None:
            extra["min_p"] = float(prof["min_p"])
    kw = dict(max_tokens=int(suite.get("max_tokens") or (THINK_BUDGET + 4096 if think else 4096)),
              attempt_timeout=3600, timeout=3600, max_connections=1, seed=SEED)
    if prof is not None:
        kw.update(temperature=float(prof.get("temperature", 0.6)), top_p=float(prof.get("top_p", 0.95)))
        if prof.get("top_k") is not None:
            kw["top_k"] = int(prof["top_k"])
    return GenerateConfig(extra_body=extra or None, **kw)


def _docker_ok():
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def _summary(log, metric=None):
    """(primary score, all metrics, samples, tokens) from an Inspect EvalLog. The primary score is the suite's
    named metric, else the first accuracy-like one (never a stderr)."""
    metrics = {}
    for sc in (log.results.scores if log.results else []) or []:
        for k, m in (sc.metrics or {}).items():
            metrics[f"{sc.name}/{k}" if len(log.results.scores) > 1 else k] = round(float(m.value), 4)
    names = [k for k in metrics if "stderr" not in k and "std" not in k.split("/")[-1]]
    pick = next((k for k in names if metric and k.split("/")[-1] == metric), None) or (names[0] if names else None)
    primary = metrics.get(pick) if pick else None
    if primary is None or primary != primary:          # missing or NaN (niah's grouped metric): average the samples
        from inspect_ai.scorer import value_to_float
        to_f = value_to_float()
        vals = [to_f(sc.value) for s in (log.samples or []) for sc in list((s.scores or {}).values())[:1]
                if not isinstance(sc.value, dict)]
        if vals:
            primary = round(sum(vals) / len(vals), 4)
            metrics["sample_mean"] = primary
    tokens = sum((u.total_tokens or 0) for u in (log.stats.model_usage or {}).values()) if log.stats else 0
    n = log.results.completed_samples if log.results else 0
    return primary, metrics, n, tokens


def rebuild_table():
    rows = [json.loads(x) for x in open(RESULTS, encoding="utf-8")] if os.path.isfile(RESULTS) else []
    refs = json.load(open(REFS, encoding="utf-8")) if os.path.isfile(REFS) else {}
    out = ["# Standard benchmark results", "",
           "Fixed seeded subsets (suites.py). Published = the vendor/leaderboard score for the full-precision model "
           "(refs.json, with its source) — not always the same subset or settings, so treat it as a ceiling.", "",
           "| Date | Model | Suite | Think | Samples | Score | Published | Wall time | Tokens | Status |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ref = refs.get(f"{r['suite']}|{r['model']}") or {}
        pub = f"{ref['score']} ([src]({ref['source']}))" if ref.get("score") is not None else "—"
        score = "—" if r.get("score") is None else f"{r['score']:.3f}"
        out.append(f"| {r['date']} | {r['model']} | {r['suite']} | {'on' if r['think'] else 'off'} | {r['samples']} "
                   f"| {score} | {pub} | {r['wall_s'] / 60:.0f} min | {r['tokens']:,} | {r['status']} |")
    open(TABLE, "w", encoding="utf-8").write("\n".join(out) + "\n")


def hardware():
    """GPU name and memory (nvidia-smi) — results from different PCs are not comparable without it."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        return out[0].strip() if out else "no NVIDIA GPU"
    except Exception:
        return "no NVIDIA GPU"


def list_models():
    print(f"GPU: {hardware()}\nModel profiles (✓ = model file found on this PC):")
    for name, prof in sorted(serve.profiles().items()):
        path = serve._model_path(prof.get("model_file"))
        ok = bool(path) and os.path.isfile(path)
        print(f"  {'✓' if ok else ' '} {name:28} {path or '(no model_file)'}")
    print("Folders searched for model files: " + ", ".join(serve.models_dirs()))


def run_suite(model, key, a):
    from inspect_ai import eval as inspect_eval
    suite = SUITES[key]
    think = suite["think"] if a.think == "auto" else (a.think == "on")
    if suite.get("docker") and not _docker_ok():
        raise SystemExit(f"{key} needs Docker: start Docker Desktop (WSL2 backend) and try again")
    prof = None if (_cloud(model) or a.base_url) else serve.profiles().get(model)
    if not (_cloud(model) or a.base_url) and prof is None:
        raise SystemExit(f"no profile named {model!r}; known: {', '.join(sorted(serve.profiles()))}")
    roles = {}
    if suite.get("user_model"):
        roles["user"] = a.user_model
        if a.user_model.startswith("anthropic/"):
            _load_key("ANTHROPIC_API_KEY", "~/.anthropic_key")
    if model.startswith("anthropic/"):
        _load_key("ANTHROPIC_API_KEY", "~/.anthropic_key")
    limit = a.limit if a.limit is not None else suite.get("limit")
    server = None
    t0 = time.time()
    try:
        if _cloud(model):
            target = model
        else:
            if a.base_url:
                base = a.base_url
            else:
                server = serve.Server(model, suite["ctx"], THINK_BUDGET if think else 0)
                print(f"[{key}] starting llama-server for {model} at ctx {server.ctx}…", flush=True)
                base = server.__enter__()
            os.environ["LLAMACPP_BASE_URL"], os.environ["LLAMACPP_API_KEY"] = base, "none"
            target = "openai-api/llamacpp/" + model.replace(":", "-")
        print(f"[{key}] {suite['what']} — {model}, think {'on' if think else 'off'}, "
              f"{limit or 'all'} samples", flush=True)
        logs = inspect_eval(suite["task"], model=target, task_args=suite.get("args") or {}, limit=limit,
                            sample_shuffle=SEED, log_dir=LOGS, max_samples=1, fail_on_error=False,
                            epochs=int(suite.get("epochs") or 1),     # GPQA defaults to 4 passes: too slow here
                            model_roles=roles or None, display=a.display,
                            **_config(suite, think, prof).model_dump(exclude_none=True))
    finally:
        if server is not None:
            server.__exit__()
    log = logs[0]
    primary, metrics, n, tokens = _summary(log, suite.get("metric"))
    row = {"date": time.strftime("%Y-%m-%d %H:%M"), "model": model, "suite": key, "think": think, "samples": n,
           "score": primary, "metrics": metrics, "wall_s": round(time.time() - t0), "tokens": tokens,
           "status": log.status, "log": os.path.basename(str(log.location or "")), "seed": SEED, "limit": limit,
           "hardware": hardware() if not _cloud(model) else "cloud"}
    if not a.no_record:
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        rebuild_table()
    print(f"[{key}] {log.status}: {metrics} on {n} samples, {row['wall_s'] / 60:.1f} min, {tokens:,} tokens", flush=True)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="a profile name (see --list) or provider/model (e.g. anthropic/claude-haiku-4-5)")
    ap.add_argument("--gguf", help="benchmark this .gguf file directly (no profile needed; q8_0 cache, 32K context)")
    ap.add_argument("--list", action="store_true", help="show the model profiles and whether their files are here")
    ap.add_argument("--suite", help="comma list of: " + ", ".join(SUITES) + ", or a group: "
                    + "; ".join(f"{k} = {','.join(v)}" for k, v in GROUPS.items()))
    ap.add_argument("--think", choices=("auto", "on", "off"), default="auto", help="auto = the suite's default")
    ap.add_argument("--limit", type=int, help="override the suite's subset size (same seed)")
    ap.add_argument("--base-url", help="use an already-running OpenAI-compatible server (no llama-server start)")
    ap.add_argument("--user-model", default="anthropic/claude-haiku-4-5", help="user simulator for tau2 suites")
    ap.add_argument("--display", default="plain", help="Inspect display: plain, rich, none")
    ap.add_argument("--no-record", action="store_true", help="don't add the run to results.jsonl/results.md")
    a = ap.parse_args()
    if a.list:
        return list_models()
    if a.gguf:
        if not os.path.isfile(a.gguf):
            raise SystemExit(f"no such file: {a.gguf}")
        a.model = serve.gguf_profile(a.gguf)
    if not (a.model and a.suite):
        ap.error("--model (or --gguf) and --suite are required (or use --list)")
    keys = [k for part in a.suite.split(",") for k in GROUPS.get(part.strip(), [part.strip()])]
    bad = [k for k in keys if k not in SUITES]
    if bad:
        raise SystemExit(f"unknown suite(s): {', '.join(bad)}; known: {', '.join(SUITES)}")
    for k in keys:
        run_suite(a.model, k, a)


if __name__ == "__main__":
    main()
