"""Local A/B: the same query through the real engine with the local llama.cpp model, under different settings.

  venv\\Scripts\\python.exe evals\\agent_eval\\ab_local.py

Isolated like run_eval.py (own data folder, log, browser profile, trackers). Records wall time, live generation speed
(the engine's own stats events), every tool call, the routing decisions from the log, GPU memory and the GPU 'shared'
spill of the main server, and the final answer. Results: runs\\<stamp>-ab\\results.json + answers.
"""
import json
import os
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
QUERY = "What is the average pay for a senior program manager?"
RUN = os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S") + "-ab")
DATA = os.path.join(RUN, "data")
os.makedirs(DATA, exist_ok=True)
os.environ["LWS_DATA_DIR"] = DATA
os.environ["LWS_NO_TELEGRAM"] = "1"
os.environ["LWS_NO_SCHEDULER"] = "1"
sys.path.insert(0, APP)
os.chdir(APP)

import agent_tools as at          # noqa: E402
import agentic                    # noqa: E402
import browser_agent              # noqa: E402
import llama_local                # noqa: E402

browser_agent.PROFILE = os.path.join(RUN, "browser_profile")
browser_agent.SHOTS = os.path.join(RUN, "browser_shots")
agentic.LOG = os.path.join(RUN, "agent_log.jsonl")
at.TRASH = os.path.join(RUN, "trash")

MODEL = "qwen3.5:9b"
VARIANTS = {
    "A_64k_workers_gpu": {"ctx": 65536, "fanout": True, "gpu_assist": True},
    "B_128k_no_workers": {"ctx": 131072, "fanout": False, "gpu_assist": False},
}


def base_cfg():
    with open(os.path.join(APP, "settings.json"), encoding="utf-8") as f:
        cfg = dict(json.load(f).get("agent") or {})
    from agent_config import DEFAULT_AGENT
    return dict(DEFAULT_AGENT, **cfg)


def gpu():
    o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout.strip()
    return int(o or 0)


def shared_mib(pid):
    ps = (f"(Get-Counter '\\GPU Process Memory(pid_{pid}*)\\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples"
          " | Measure-Object CookedValue -Maximum | % {{ [int]($_.Maximum/1MB) }}")
    o = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    try:
        return int(o.splitlines()[-1])
    except Exception:
        return -1


def run(name, v):
    for slot in ("main", "gopher"):
        llama_local.stop(slot)
    time.sleep(3)
    cfg = base_cfg()
    ov = dict((cfg.get("model_overrides") or {}).get(MODEL) or {})
    ov.update(num_ctx=v["ctx"], spec_draft_ctx=v["ctx"])
    cfg["model_overrides"] = dict(cfg.get("model_overrides") or {}, **{MODEL: ov})
    fan = dict(cfg.get("fanout") or {})
    fan.update(enabled=v["fanout"], gpu_assist=v["gpu_assist"])
    cfg.update(model=MODEL, backend="llama", fanout=fan, max_steps=40, hybrid={"enabled": False},
               advisor={"enabled": False}, mcp_servers=[], screen_control=False, delegation=False, file_index=False)
    ws = os.path.join(RUN, "ws", name)
    os.makedirs(ws, exist_ok=True)
    cfg["roots"] = [ws]
    cfg["tracker_path"] = os.path.join(RUN, "trackers", name + ".md")
    os.makedirs(os.path.dirname(cfg["tracker_path"]), exist_ok=True)
    calls, by_id, tps, statuses = [], {}, [], []
    deadline = time.time() + 15 * 60

    def emit(kind, /, **d):
        if kind == "tool_call":
            c = {"name": d.get("name"), "args": str(d.get("args"))[:300], "ok": None, "text": "", "t": time.time()}
            by_id[d.get("id")] = c
            calls.append(c)
        elif kind == "tool_result":
            c = by_id.get(d.get("id"))
            if c is not None:
                c["ok"], c["text"] = bool(d.get("ok")), str(d.get("text") or "")[:400]
        elif kind == "stats" and d.get("tps"):
            tps.append(float(d["tps"]))
        elif kind == "status":
            statuses.append(f"{time.strftime('%H:%M:%S')} {d.get('text', '')}"[:200])

    e = agentic.AgentEngine(cfg, emit, ask=lambda req: "once", should_stop=lambda: time.time() > deadline,
                            conv_id=f"ab-{name}", title=name)
    mon = {"stop": False, "gpu_max": 0, "shared_max": 0}

    def monitor():
        while not mon["stop"]:
            mon["gpu_max"] = max(mon["gpu_max"], gpu())
            p = (llama_local._slots.get("main") or {}).get("proc")
            if p is not None and p.poll() is None:
                mon["shared_max"] = max(mon["shared_max"], shared_mib(p.pid))
            time.sleep(2)
    threading.Thread(target=monitor, daemon=True).start()
    t_prep = time.time()
    answer, err = "", ""
    try:
        e.prepare()
        t0 = time.time()
        answer = e.run(QUERY) or ""
    except Exception:
        t0 = t0 if "t0" in dir() else time.time()
        err = traceback.format_exc()[-1500:]
    secs = time.time() - t0
    mon["stop"] = True
    tu = e.turn or {}
    res = {"variant": name, "settings": v, "load_seconds": round(t0 - t_prep, 1), "seconds": round(secs, 1),
           "hit_limit": time.time() > deadline, "steps": tu.get("steps", 0), "tool_calls": len(calls),
           "failed_calls": [(c["name"], c["text"][:200]) for c in calls if c["ok"] is False],
           "tools": [c["name"] for c in calls], "gather_calls": tu.get("gather_calls", 0),
           "takeovers": tu.get("takeovers", 0), "plan_decision": tu.get("plan_decision"), "lean": tu.get("lean"),
           "fact_check": tu.get("fact_check"), "future_dates": tu.get("future_dates"),
           "tps_live_median": sorted(tps)[len(tps) // 2] if tps else None, "tps_live_min": min(tps) if tps else None,
           "tps_samples": len(tps), "gpu_max_mib": mon["gpu_max"], "main_shared_max_mib": mon["shared_max"],
           "prompt_tokens": tu.get("prompt_tokens", 0), "completion_tokens": tu.get("completion_tokens", 0),
           "answer": answer, "error": err, "statuses": statuses[-60:],
           "calls": [{k: c[k] for k in ("name", "args", "ok", "text")} for c in calls]}
    with open(os.path.join(RUN, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"{name}: {res['seconds']}s, {res['steps']} steps, {res['tool_calls']} calls "
          f"({len(res['failed_calls'])} failed), gather {res['gather_calls']}, live tok/s median "
          f"{res['tps_live_median']} min {res['tps_live_min']}, GPU max {res['gpu_max_mib']} MiB, main shared max "
          f"{res['main_shared_max_mib']} MiB{' (HIT LIMIT)' if res['hit_limit'] else ''}{' ERROR' if err else ''}",
          flush=True)
    return res


if __name__ == "__main__":
    out = [run(n, v) for n, v in VARIANTS.items()]
    for slot in ("main", "gopher"):
        llama_local.stop(slot)
    with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("results:", RUN)
