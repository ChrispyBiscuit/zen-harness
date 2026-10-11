"""Vanilla baseline: the same local Qwen (same profile, same server flags, same sampling) answering the SimpleQA sample
directly — no harness, no tools, no Wikipedia, no verification. Writes runs/<stamp>-simpleqa-vanilla/results.json in
the run_eval format so grade_simpleqa.py grades it the same way.

  set SIMPLEQA_N=100 && venv\\Scripts\\python.exe evals\\agent_eval\\vanilla_simpleqa.py [model]
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
os.environ.setdefault("SIMPLEQA_N", "100")
RUN = os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S") + "-simpleqa-vanilla")
os.makedirs(RUN, exist_ok=True)
os.environ["LWS_DATA_DIR"] = os.path.join(RUN, "data")
sys.path.insert(0, APP)
sys.path.insert(0, HERE)
os.chdir(APP)
import agentic  # noqa: E402
import tasks as T  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:9b"


def main():
    with open(os.path.join(APP, "settings.json"), encoding="utf-8") as f:
        cfg = dict(json.load(f).get("agent") or {})
    from agent_config import DEFAULT_AGENT
    cfg = dict(DEFAULT_AGENT, **cfg)
    cfg.update(model=MODEL, backend="llama", roots=[RUN], fanout=dict(cfg.get("fanout") or {}, enabled=False),
               hybrid={"enabled": False}, advisor={"enabled": False}, mcp_servers=[])
    e = agentic.AgentEngine(cfg, lambda k, /, **d: None)
    e.prepare()                                        # the same server the harness uses (profile, flags, drafter)
    c = e.cfg
    out = []
    for i, (tid, row) in enumerate(T.SIMPLEQA.items()):
        body = {"messages": [{"role": "user", "content": row["problem"]}], "max_tokens": 2048,
                "temperature": float(c.get("temperature", 0.6)), **agentic._sampling(c)}
        t0 = time.time()
        try:
            req = urllib.request.Request(e.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=300))
            ans = (d["choices"][0]["message"].get("content") or "").strip()
        except Exception as ex:
            ans = f"(error: {ex})"
        out.append({"id": tid, "answer": ans, "seconds": round(time.time() - t0, 1), "steps": 0, "pass": True})
        print(f"[{i + 1}/{len(T.SIMPLEQA)}] {out[-1]['seconds']}s  {row['problem'][:60]}  ->  {ans[:70]!r}", flush=True)
        with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": {"model": MODEL, "mode": "vanilla"}, "results": out}, f, indent=1, ensure_ascii=False)
    print("results:", RUN)


if __name__ == "__main__":
    main()
