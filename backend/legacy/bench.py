"""Benchmark Ollama models the way the swarm uses them.

    python bench.py                                  # qwen3:4b-instruct, ctx 4096 vs 10000
    python bench.py qwen3:0.6b llama3.2:1b           # several models
    python bench.py --ctx 4096 8192 -- qwen3:30b-a3b-instruct-2507-q4_K_M

For each (model, ctx): cold-load time, how much of the model sits on the GPU, then 3
swarm-style JSON replies timed with Ollama's own counters (prompt tokens/s = prefill,
gen tokens/s = decode). Close other apps first; the big 30b needs ~13 GB of free RAM.
"""
import sys
import time
import ollama

import swarm

PROMPT = (
    "Question: Should cities replace parking minimums with a congestion fee?\n\n"
    "Your current position: Yes, because it prices road space by demand.\n\n"
    "What the others said last round:\n"
    "[Bram] (not yet agreed) position: No, fees hurt low-income commuters.\n"
    "  argument: Parking minimums protect neighborhoods from spillover parking and a fee "
    "falls hardest on people with no transit option.\n"
    "[Cleo] (agrees) position: Yes, if revenue funds transit.\n"
    "[Dax] (not yet agreed) position: Only in the densest downtown cores.\n"
    "  argument: Outside the core there is no alternative to driving, so the fee only taxes.\n\n"
    "Respond to the others. Reply as a JSON object with exactly these keys: \"argument\" "
    "(under 70 words), \"position\" (one sentence), \"agreement\" (\"YES\" or \"NO\")."
)


def run(client, model, ctx, n=3):
    try:
        client.generate(model=model, prompt="", keep_alive=0)      # force a cold load
    except Exception:
        pass
    time.sleep(1)
    t0 = time.time()
    client.generate(model=model, prompt="", options={"num_ctx": ctx}, keep_alive="5m")
    load = time.time() - t0
    split = "?"
    for m in client.ps().models:
        if m.model == model:
            tot = m.size or 1
            gpu = getattr(m, "size_vram", 0) or 0
            split = f"{100 * gpu / tot:.0f}% GPU ({gpu / 1e9:.1f}/{tot / 1e9:.1f} GB)"
    rows = []
    for _ in range(n):
        kw = {"think": False} if model.lower().startswith("qwen3") else {}
        r = client.chat(model=model, messages=[
            {"role": "system", "content": "You are Ava in a group discussion."},
            {"role": "user", "content": PROMPT + (" /no_think" if kw else "")}],
            format=swarm.SCHEMA_R1, options={"num_ctx": ctx, "temperature": 0.7,
                                              "num_predict": 260}, **kw)
        pt, pd = r.prompt_eval_count or 0, (r.prompt_eval_duration or 1) / 1e9
        et, ed = r.eval_count or 0, (r.eval_duration or 1) / 1e9
        total = (r.total_duration or 0) / 1e9
        rows.append((pt / pd, et / ed, et, total))
    avg = lambda i: sum(r[i] for r in rows) / len(rows)
    return load, split, avg(0), avg(1), avg(2), avg(3)


def main():
    args = sys.argv[1:]
    ctxs = [4096, 10000]
    if "--ctx" in args:
        i = args.index("--ctx")
        j = args.index("--", i) if "--" in args else len(args)
        ctxs = [int(x) for x in args[i + 1:j]]
        args = args[:i] + args[j + 1:] if "--" in args else args[:i]
    models = args or [swarm.DEFAULT_MODEL]
    client = ollama.Client()
    print(f"{'model':36} {'ctx':>6} {'load s':>7} {'on GPU':>22} {'prompt t/s':>11} "
          f"{'gen t/s':>8} {'out tok':>8} {'s/reply':>8}")
    for model in models:
        for ctx in ctxs:
            try:
                load, split, ptps, gtps, tok, sec = run(client, model, ctx)
                print(f"{model[:36]:36} {ctx:>6} {load:>7.1f} {split:>22} {ptps:>11.0f} "
                      f"{gtps:>8.1f} {tok:>8.0f} {sec:>8.1f}", flush=True)
            except Exception as e:
                print(f"{model[:36]:36} {ctx:>6}  FAILED: {type(e).__name__}: {str(e)[:80]}", flush=True)
        try:
            client.generate(model=model, prompt="", keep_alive=0)     # leave RAM free
        except Exception:
            pass


if __name__ == "__main__":
    main()
