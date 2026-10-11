"""Send the Ledger build (SPEC.md) to the RUNNING app as a normal chat, wait for it to finish, grade with acceptance.py.

  venv\\Scripts\\python.exe evals\\build\\run_build_in_app.py --name ornith
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--name", default="ornith")
ap.add_argument("--minutes", type=int, default=120)
ap.add_argument("--port", type=int, default=int(os.environ.get("ZEN_PORT", "8765")), help="the app's port")
a = ap.parse_args()
WS = os.path.join(os.path.expanduser("~"), "Documents", "ZenBattery", "build", a.name)
os.makedirs(WS, exist_ok=True)
APP = f"http://127.0.0.1:{a.port}"


def api(path, body=None):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


text = open(os.path.join(HERE, "SPEC.md"), encoding="utf-8").read().format(ws=WS)
t0 = time.time()
cid = api("/api/send", {"text": text, "mode": "agent"})["conv_id"]
print(f"sent -> chat {cid}; building in {WS}", flush=True)
time.sleep(5)
while time.time() - t0 < a.minutes * 60:
    c = api(f"/api/conv/{cid}")
    if not c.get("running"):
        break
    time.sleep(10)
c = api(f"/api/conv/{cid}")
calls = [m["tool"]["name"] for m in c.get("messages") or [] if m.get("kind") == "tool"]
score = {"passed": None, "total": None}   # graded by hand against RUBRIC.md
lines = [""]
res = {"name": a.name, "conv": cid, "acceptance": lines[:-1], "passed": score["passed"], "total": score["total"],
       "wall_s": round(time.time() - t0), "calls": calls, "still_running": bool(c.get("running"))}
os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
json.dump(res, open(os.path.join(HERE, "runs", a.name + ".json"), "w", encoding="utf-8"), indent=1)
print(f"{a.name}: build finished in {res['wall_s']}s, {len(calls)} tool calls (grade by hand)", flush=True)
