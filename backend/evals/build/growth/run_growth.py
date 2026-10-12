"""Growth benchmark: grow ONE project over many fresh chats in the RUNNING app, one feature request per chat
(SESSIONS.json), and measure after each session what a long-lived codebase needs: time, tool calls, failed tool
calls, lines of code, the project's own tests, and regressions — tests that passed after an earlier session and
fail now. Each session starts with an empty context, so whatever the agent knows about the code it gets from the
files and the harness (work state, code map, contract), not from memory of earlier chats.

  venv\\Scripts\\python.exe evals\\build\\growth\\run_growth.py --name bonsai-g1
  ... --from s05            resume at a session (the project folder keeps its code)
  ... --only s01,s02        a subset
  ... --stop-loc 10000      stop once the project reaches this many lines

Results: runs/<name>.json (one row per session, updated as it goes) and a table printed at the end.
Needs pytest on the --python interpreter (default: python on PATH) to run the project's tests.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--sessions", default=os.path.join(HERE, "SESSIONS.json"))
ap.add_argument("--minutes", type=int, default=120, help="time limit per session")
ap.add_argument("--from", dest="start", help="first session id to run")
ap.add_argument("--only", help="comma-separated session ids")
ap.add_argument("--stop-loc", type=int, default=0)
ap.add_argument("--python", default="python", help="interpreter that runs the project's tests")
ap.add_argument("--port", type=int, default=int(os.environ.get("ZEN_PORT", "8765")), help="the app's port")
a = ap.parse_args()
APP = f"http://127.0.0.1:{a.port}"
spec = json.load(open(a.sessions, encoding="utf-8"))
WS = os.path.join(os.path.expanduser("~"), "Documents", "ZenBattery", "growth", a.name)
os.makedirs(WS, exist_ok=True)
OUT = os.path.join(HERE, "runs", a.name + ".json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
SKIP_DIRS = {"venv", ".venv", "node_modules", "__pycache__", ".git", ".pytest_cache", "docs"}
CODE_EXT = (".py", ".js", ".ts", ".html", ".css")


def api(path, body=None):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def loc():
    """(code lines, test lines, files): non-blank lines, tests counted apart."""
    code = tests = files = 0
    for dp, dns, fns in os.walk(WS):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fns:
            if not f.endswith(CODE_EXT):
                continue
            try:
                n = sum(1 for ln in open(os.path.join(dp, f), encoding="utf-8", errors="replace") if ln.strip())
            except OSError:
                continue
            files += 1
            if f.startswith("test_") or f.endswith("_test.py") or os.sep + "tests" in dp:
                tests += n
            else:
                code += n
    return code, tests, files


def run_tests():
    """-> (passed names, failed names, seconds, note). Runs the project's own tests with per-test results."""
    t0 = time.time()
    try:
        r = subprocess.run([a.python, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "--timeout=60"],
                           cwd=WS, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        out = r.stdout + r.stderr
        if "unrecognized arguments: --timeout" in out:          # pytest-timeout not installed
            r = subprocess.run([a.python, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider"], cwd=WS,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return set(), set(), round(time.time() - t0), "test run timed out (900s)"
    passed = set(re.findall(r"^PASSED (\S+)", out, re.M))
    failed = set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M))
    note = "" if passed or failed else (out.strip().splitlines() or ["no output"])[-1][:200]
    return passed, failed, round(time.time() - t0), note


def run_session(s):
    text = s["request"].format(ws=WS)
    t0 = time.time()
    cid = api("/api/send", {"text": text, "mode": "agent"})["conv_id"]
    print(f"[{s['id']}] sent -> chat {cid}", flush=True)
    time.sleep(5)
    while time.time() - t0 < a.minutes * 60:
        if not api(f"/api/conv/{cid}").get("running"):
            break
        time.sleep(10)
    c = api(f"/api/conv/{cid}")
    if c.get("running"):                                         # out of time: stop it so the next session is clean
        try:
            api("/api/stop", {"conv_id": cid})
            for _ in range(30):
                time.sleep(2)
                if not api(f"/api/conv/{cid}").get("running"):
                    break
        except Exception as e:
            print(f"[{s['id']}] stop failed: {e!r}", flush=True)
    tools = [m for m in c.get("messages") or [] if m.get("kind") == "tool"]
    failed_calls = sum(1 for m in tools if str((m.get("tool") or {}).get("result") or "").startswith("Error"))
    return cid, round(time.time() - t0), len(tools), failed_calls, bool(c.get("running"))


rows = json.load(open(OUT, encoding="utf-8")) if os.path.isfile(OUT) else []
ever_passed = set()
for r in rows:
    ever_passed |= set(r.get("passed_names") or [])
todo = spec["sessions"]
if a.only:
    want = set(a.only.split(","))
    todo = [s for s in todo if s["id"] in want]
if a.start:
    ids = [s["id"] for s in todo]
    todo = todo[ids.index(a.start):]
print(f"growth run '{a.name}': {len(todo)} session(s) on {WS}", flush=True)
for s in todo:
    cid, wall, calls, bad_calls, timed_out = run_session(s)
    code, tlines, nfiles = loc()
    passed, failed, test_s, note = run_tests()
    regressed = sorted(ever_passed & failed)                    # passed after an earlier session, fails now
    ever_passed |= passed
    row = {"id": s["id"], "conv": cid, "wall_s": wall, "tool_calls": calls, "failed_calls": bad_calls,
           "timed_out": timed_out, "loc_code": code, "loc_tests": tlines, "files": nfiles,
           "tests_passed": len(passed), "tests_failed": len(failed), "regressions": regressed,
           "test_note": note, "test_s": test_s, "passed_names": sorted(passed)}
    rows = [r for r in rows if r["id"] != s["id"]] + [row]
    json.dump(rows, open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"[{s['id']}] {wall // 60}m, {calls} calls ({bad_calls} failed){' TIMED OUT' if timed_out else ''} | "
          f"{code} code + {tlines} test lines | tests {len(passed)} pass / {len(failed)} fail | "
          f"{len(regressed)} regression(s) {note}", flush=True)
    if a.stop_loc and code + tlines >= a.stop_loc:
        print(f"reached {code + tlines} lines (>= {a.stop_loc}), stopping", flush=True)
        break

print("\nsession  min  calls  fail  code  tests  pass  fail  regr", flush=True)
for r in rows:
    print(f"{r['id']:<8} {r['wall_s'] // 60:>4} {r['tool_calls']:>6} {r['failed_calls']:>5} {r['loc_code']:>5} "
          f"{r['loc_tests']:>6} {r['tests_passed']:>5} {r['tests_failed']:>5} {len(r['regressions']):>5}"
          f"{'  (timed out)' if r['timed_out'] else ''}", flush=True)
