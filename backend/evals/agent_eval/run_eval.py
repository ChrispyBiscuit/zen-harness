"""Agent harness eval: run every task in tasks.py through the real agent engine and score it.

  venv\\Scripts\\python.exe evals\\agent_eval\\run_eval.py                       all tasks, gpt-4.1-mini
  venv\\Scripts\\python.exe evals\\agent_eval\\run_eval.py --model gpt-5.5 --label after-fix
  venv\\Scripts\\python.exe evals\\agent_eval\\run_eval.py --only web_fact,math_days

Runs fully isolated from the live app: its own data folder, browser profile, sandbox outputs, trackers and log
(all under evals\\agent_eval\\runs\\<run>), the OpenAI API as the model (no GPU), the app's own agent settings
otherwise — and the tool list sent the way the local model gets it (stable_tools), so the same harness paths run.

Each task gets an empty workspace; pass/fail comes from the task's automatic check. A judge model also scores
how good the final answer is (1-5). Results: runs\\<run>\\results.json and report.md (with a diff against the
previous run when there is one).
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
RUNS = os.path.join(HERE, "runs")

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="gpt-4.1-mini")
ap.add_argument("--judge", default="gpt-4.1")
ap.add_argument("--only", default="")
ap.add_argument("--cats", default="", help="comma-separated categories (e.g. tc_files,tc_code)")
ap.add_argument("--label", default="")
ap.add_argument("--repeat", type=int, default=1)
ap.add_argument("--max-steps", type=int, default=30)
ap.add_argument("--task-minutes", type=float, default=8)
ap.add_argument("--backend", default="openai", choices=["openai", "llama"],
                help="llama = the local llama.cpp model named by --model, with its settings profile (model_overrides)")
ap.add_argument("--no-judge", action="store_true", help="automatic checks only (no OpenAI judge call)")
ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                help="override an agent setting for this run (JSON value), e.g. --set plan_tokens=0 --set playbook_notes=false")
args = ap.parse_args()

stamp = time.strftime("%Y%m%d-%H%M%S")
RUN = os.path.join(RUNS, stamp + (f"-{args.label}" if args.label else ""))
DATA = os.path.join(RUN, "data")
os.makedirs(DATA, exist_ok=True)
# isolation: set BEFORE importing the app's modules (they read these at import time)
os.environ["LWS_DATA_DIR"] = DATA
os.environ["LWS_NO_TELEGRAM"] = "1"
os.environ["LWS_NO_SCHEDULER"] = "1"
sys.path.insert(0, APP)
sys.path.insert(0, HERE)
os.chdir(APP)

import agent_tools as at          # noqa: E402
import agentic                    # noqa: E402
import browser_agent              # noqa: E402
import swarm                      # noqa: E402
import tasks as T                 # noqa: E402

browser_agent.PROFILE = os.path.join(RUN, "browser_profile")      # never the live app's profile
browser_agent.SHOTS = os.path.join(RUN, "browser_shots")
agentic.LOG = os.path.join(RUN, "agent_log.jsonl")
at.TRASH = os.path.join(RUN, "trash")
try:
    import bg_tasks
    bg_tasks.LOG_DIR = os.path.join(RUN, "bg")
except Exception:
    pass

PRICE = dict(agentic.PRICE, **{"gpt-4.1": (2.0, 8.0), "gpt-4.1-nano": (0.1, 0.4)})


def base_cfg():
    try:
        with open(os.path.join(APP, "settings.json"), encoding="utf-8") as f:
            cfg = dict(json.load(f).get("agent") or {})
    except Exception:
        cfg = {}
    from agent_config import DEFAULT_AGENT
    c = dict(DEFAULT_AGENT, **cfg)
    c.update(backend="openai", model=args.model, max_steps=args.max_steps, stable_tools=True,
             hybrid={"enabled": False}, advisor={"enabled": False}, mcp_servers=[], screen_control=False,
             delegation=False, plan_mode=False, file_index=False, allow_rules=[])
    if args.backend == "llama":       # the real local setup: its profile (context, cache, drafter), no workers
        c.update(backend="llama", stable_tools=c.get("stable_tools", False),
                 fanout=dict(c.get("fanout") or {}, enabled=False))
    for kv in args.set:               # ablations: --set key=value (value parsed as JSON when it can be)
        k, _, v = kv.partition("=")
        try:
            c[k.strip()] = json.loads(v)
        except ValueError:
            c[k.strip()] = v
    return c


class R:
    pass


def judge(task, prompt, answer, passed, detail):
    q = (f"You grade an AI agent's final answer to a user request.\nREQUEST: {prompt}\n\nFINAL ANSWER:\n{answer[:6000]}\n\n"
         f"An automatic check {'PASSED' if passed else 'FAILED'} ({detail}).\n"
         "Score the answer 1-5 for how well it serves the user: correct, complete, honest about limits, concise, "
         "useful (5 = excellent, 3 = acceptable, 1 = useless or wrong). JSON only: {\"score\": n, \"why\": \"...\"}")
    body = {"model": args.judge, "messages": [{"role": "user", "content": q}],
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {swarm.api_key()}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            d = json.load(resp)
        u = d.get("usage") or {}
        p = PRICE.get(args.judge, (2.0, 8.0))
        cost = (u.get("prompt_tokens", 0) * p[0] + u.get("completion_tokens", 0) * p[1]) / 1e6
        j = json.loads(d["choices"][0]["message"]["content"])
        return int(j.get("score") or 0), str(j.get("why") or "")[:300], cost
    except Exception as e:
        return 0, f"judge error: {e}", 0.0


def run_task(task, rep):
    ws = os.path.join(RUN, "ws", f"{task['id']}" + (f"-{rep}" if args.repeat > 1 else ""))
    os.makedirs(ws, exist_ok=True)
    extra = (task["setup"](ws) or {}) if task.get("setup") else {}    # e.g. {"url": local test site}
    cfg = base_cfg()
    cfg["roots"] = [ws]
    cfg["tracker_path"] = os.path.join(RUN, "trackers", os.path.basename(ws) + ".md")
    os.makedirs(os.path.dirname(cfg["tracker_path"]), exist_ok=True)
    calls, by_id, events = [], {}, {"warn": 0, "not_run": 0, "nudges": 0}
    deadline = time.time() + args.task_minutes * 60

    def emit(kind, /, **d):
        if kind == "tool_call":
            c = {"name": d.get("name"), "args": d.get("args"), "ok": None, "text": ""}
            by_id[d.get("id")] = c
            calls.append(c)
        elif kind == "tool_result":
            c = by_id.get(d.get("id"))
            if c is not None:
                c["ok"], c["text"] = bool(d.get("ok")), str(d.get("text") or "")[:1500]
                if "⚠" in c["text"]:
                    events["warn"] += 1
                if c["text"].startswith(("Not run again", "Not saved", "Checklist unchanged")) or " did not run" in c["text"][:80]:
                    events["not_run"] += 1

    e = agentic.AgentEngine(cfg, emit, ask=lambda req: "once", should_stop=lambda: time.time() > deadline,
                            conv_id=f"eval-{os.path.basename(ws)}", title=task["id"])
    t0 = time.time()
    answer, err = "", ""
    try:
        e.prepare()
        answer = e.run(task["prompt"].format(ws=ws, **extra)) or ""
        for fu in task.get("followups") or []:          # later turns in the same chat
            if time.time() > deadline:
                break
            answer = e.run(fu.format(ws=ws, **extra)) or ""
    except Exception:
        err = traceback.format_exc()[-1500:]
    secs = time.time() - t0
    events["nudges"] = sum(1 for m in e.history if m.get("role") == "user" and isinstance(m.get("content"), str)
                           and m["content"].startswith(agentic.REMINDER_TAG))
    r = R()
    r.ws, r.answer, r.calls, r.data_dir = ws, answer, calls, DATA
    try:
        passed, detail = task["check"](r)
    except Exception as ex:
        passed, detail = False, f"check error: {ex}"
    if err:
        passed, detail = False, "engine error: " + err.strip().splitlines()[-1]
    if time.time() > deadline:
        detail += " (hit time limit)"
    tu = e.turn or {}
    p = PRICE.get(args.model, (0.4, 1.6))
    cost = (tu.get("prompt_tokens", 0) * p[0] + tu.get("completion_tokens", 0) * p[1]) / 1e6
    score, why, jcost = ((0, "", 0.0) if args.no_judge else
                         judge(task, task["prompt"].format(ws=ws), answer, passed, detail))
    if args.backend == "llama":
        cost = 0.0
    return {"id": task["id"], "cat": task["cat"], "rep": rep, "pass": bool(passed), "detail": detail,
            "score": score, "judge": why, "steps": tu.get("steps", 0), "tool_calls": len(calls),
            "failed_calls": sum(1 for c in calls if c["ok"] is False), "warnings": events["warn"],
            "blocked_repeats": events["not_run"], "nudges": events["nudges"], "planned": bool(tu.get("planned")),
            "seconds": round(secs, 1), "prompt_tokens": tu.get("prompt_tokens", 0),
            "completion_tokens": tu.get("completion_tokens", 0), "cost": round(cost + jcost, 4),
            "answer": answer[:3000], "tools": [c["name"] for c in calls], "error": err[-600:]}


def previous_run():
    if not os.path.isdir(RUNS):
        return None
    for d in sorted(os.listdir(RUNS), reverse=True):
        p = os.path.join(RUNS, d, "results.json")
        if d != os.path.basename(RUN) and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "results" in data:      # (other tools' runs share the folder)
                return d, data
    return None


def report(results, meta):
    n = len(results)
    passed = sum(r["pass"] for r in results)
    avg = lambda k: sum(r[k] for r in results) / max(n, 1)
    lines = [f"# Agent eval — {meta['run']}", "",
             f"Model **{meta['model']}** (judge {meta['judge']}) · {n} runs · **{passed}/{n} passed "
             f"({100 * passed / max(n, 1):.0f}%)** · judge score {avg('score'):.2f}/5 · "
             f"{avg('steps'):.1f} steps and {avg('tool_calls'):.1f} tool calls per task · "
             f"{avg('seconds'):.0f}s per task · cost ${sum(r['cost'] for r in results):.2f}", "",
             "## By category", "", "| category | passed | judge | steps | failed calls | warnings | nudges |",
             "|---|---|---|---|---|---|---|"]
    cats = sorted({r["cat"] for r in results})
    for c in cats:
        rs = [r for r in results if r["cat"] == c]
        a = lambda k: sum(r[k] for r in rs) / len(rs)
        lines.append(f"| {c} | {sum(r['pass'] for r in rs)}/{len(rs)} | {a('score'):.1f} | {a('steps'):.1f} | "
                     f"{a('failed_calls'):.1f} | {a('warnings'):.1f} | {a('nudges'):.1f} |")
    lines += ["", "## Tasks", "", "| task | result | judge | steps | calls | failed | blocked | ⚠ | nudges | s | detail |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {'✅' if r['pass'] else '❌'} | {r['score']} | {r['steps']} | {r['tool_calls']} | "
                     f"{r['failed_calls']} | {r['blocked_repeats']} | {r['warnings']} | {r['nudges']} | {r['seconds']:.0f} | "
                     f"{r['detail'].replace('|', '/')[:90]} |")
    prev = previous_run()
    if prev:
        pname, pres = prev
        pm = {x["id"]: x for x in pres.get("results", [])}
        changes = []
        for r in results:
            o = pm.get(r["id"])
            if o and o["pass"] != r["pass"]:
                changes.append(f"- {r['id']}: {'❌→✅' if r['pass'] else '✅→❌'}")
        pp = sum(x["pass"] for x in pres.get("results", []))
        lines += ["", f"## Versus previous run ({pname}: {pp}/{len(pres.get('results', []))} passed)", ""]
        lines += changes or ["- no pass/fail changes"]
    lines += ["", "## Judge notes", ""] + [f"- **{r['id']}** ({r['score']}/5): {r['judge']}" for r in results]
    return "\n".join(lines) + "\n"


def main():
    if (args.backend == "openai" or not args.no_judge) and not swarm.api_key():
        sys.exit("No OpenAI API key (OPENAI_API_KEY or openai_key.txt).")
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cats = {x.strip() for x in args.cats.split(",") if x.strip()}
    todo = [t for t in T.TASKS if (not only or t["id"] in only) and (not cats or t["cat"] in cats)]
    meta = {"run": os.path.basename(RUN), "model": args.model, "judge": args.judge, "started": stamp,
            "max_steps": args.max_steps}
    results = []
    for rep in range(args.repeat):
        for t in todo:
            print(f"[{len(results) + 1}/{len(todo) * args.repeat}] {t['id']} …", flush=True)
            res = run_task(t, rep)
            results.append(res)
            print(f"    {'PASS' if res['pass'] else 'FAIL'} judge={res['score']} steps={res['steps']} "
                  f"calls={res['tool_calls']} {res['seconds']:.0f}s ${res['cost']:.3f} — {res['detail'][:100]}", flush=True)
            with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "results": results}, f, indent=1)
    with open(os.path.join(RUN, "report.md"), "w", encoding="utf-8") as f:
        f.write(report(results, meta))
    print(f"\nreport: {os.path.join(RUN, 'report.md')}")
    for stop in (lambda: browser_agent.MANAGER.close(), lambda: __import__("sandbox").shutdown_all()):
        try:                                          # the eval's own browser and sandbox processes
            stop()
        except Exception:
            pass
    os._exit(0)                                       # background threads (browser, sandbox) must not keep us alive


if __name__ == "__main__":
    main()
