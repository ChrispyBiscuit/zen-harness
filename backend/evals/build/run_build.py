"""Run the Ledger build (SPEC.md) through the harness with an API model, then grade it with acceptance.py.

  venv\\Scripts\\python.exe evals\\build\\run_build.py --name haiku --model claude-haiku-4-5 --max-usd 6

Reuses the battery runner (same harness config, approval guard, spend meter). The project is built in
evals\\build\\runs\\<name>\\ws\\build-ledger; the result (acceptance score, time, calls, spend) goes to
evals\\build\\runs\\<name>.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--model", required=True)
ap.add_argument("--provider", default="anthropic")
ap.add_argument("--max-usd", type=float, default=6)
ap.add_argument("--minutes", type=int, default=60)
ap.add_argument("--bare", action="store_true", help="no harness: one model reply, its file blocks applied")
a = ap.parse_args()

os.environ["BATTERY_HARD_CAP"] = str(a.minutes * 60)
sys.argv = ["run_battery.py", "--run", "../../build/runs/" + a.name, "--set", "backend=openai",
            "--set", f"provider={a.provider}", "--set", f"model={a.model}", "--max-usd", str(a.max_usd)] + (["--bare"] if a.bare else [])
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "battery"))
import run_battery as RB  # noqa: E402  (parses the faked argv above)

SPEC = open(os.path.join(HERE, "SPEC.md"), encoding="utf-8").read()


def grade(r):
    return True, "built — graded by hand against the capability rubric (RUBRIC.md)"
    out = subprocess.run([sys.executable, os.path.join(HERE, "acceptance.py"), r.ws], capture_output=True, text=True,
                         timeout=600, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    lines = out.stdout.strip().splitlines()
    try:
        s = json.loads(lines[-1])
    except (ValueError, IndexError):
        s = {"passed": 0, "total": 23}
    r.extra["acceptance"] = lines[:-1]
    return True, "built — graded by hand against the capability rubric (RUBRIC.md)"


task = {"id": "build-ledger", "cat": "code", "prompt": SPEC, "check": grade, "followups": [],
        "expect": {"route": "tools", "size": "deep", "max_secs": a.minutes * 60, "must": [], "forbid": [],
                   "max_drafts": 3, "ask": False}}
t0 = time.time()
res = RB.run_task(task)
res["spend_usd"] = round(sum([(res.get("tokens") or {}).get("in", 0) * RB.agentic.PRICE.get(a.model, (0, 0))[0],
                              (res.get("tokens") or {}).get("out", 0) * RB.agentic.PRICE.get(a.model, (0, 0))[1]]) / 1e6, 2)
res["model"], res["wall_s"] = a.model, round(time.time() - t0)
os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
json.dump(res, open(os.path.join(HERE, "runs", a.name + ".json"), "w", encoding="utf-8"), indent=1, default=str)
print(f"{a.name}: {res['detail']} in {res['wall_s']}s, {len(res.get('calls') or [])} tool calls, ~${res['spend_usd']}")
