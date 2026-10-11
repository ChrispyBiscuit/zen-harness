"""Send battery tasks through the RUNNING app (http://127.0.0.1:8765) one at a time, so each appears as a normal chat
the user can watch; then grade the result with the task's check.

  venv\\Scripts\\python.exe evals\\battery\\run_in_app.py --only files-021,compare-009 [--name ui10]

Task folders go under Documents\\ZenBattery\\<name>\\<task> (inside the app's allowed folders). Tool approvals appear
in the app as usual — the person watching decides them.
"""
import argparse
import json
import os
import sys
import time
import types
import urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import battery as B  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--only", required=True)
ap.add_argument("--name", default="ui10")
ap.add_argument("--app", default="http://127.0.0.1:8765")
args = ap.parse_args()
ROOT = os.path.join(os.path.expanduser("~"), "Documents", "ZenBattery", args.name)
OUT = os.path.join(HERE, "runs", "app-" + args.name + ".jsonl")


def api(path, body=None):
    req = urllib.request.Request(args.app + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def final_answer(conv):
    for m in reversed(conv.get("messages") or []):
        if m.get("role") == "assistant" and m.get("kind") != "tool" and not m.get("error") and (m.get("content") or "").strip():
            return m["content"]
    return ""


def calls_of(conv):
    return [{"name": (m.get("tool") or {}).get("name"), "args": (m.get("tool") or {}).get("desc"),
             "ok": (m.get("tool") or {}).get("ok")} for m in conv.get("messages") or [] if m.get("kind") == "tool"]


tasks = {t["id"]: t for t in B.TASKS}
for tid in [x for x in args.only.split(",") if x]:
    t = tasks[tid]
    ws = os.path.join(ROOT, tid)
    os.makedirs(ws, exist_ok=True)
    extra = (t["setup"](ws) or {}) if t.get("setup") else {}
    fmt = {"ws": ws, **{k: v for k, v in extra.items() if isinstance(v, (str, int, float))}}
    msgs = [t["prompt"].format(**fmt)] + [f.format(**fmt) for f in t.get("followups") or []]
    t0 = time.time()
    cid = None
    for i, text in enumerate(msgs):
        r = api("/api/send", {"text": text, "mode": "agent", **({"conv_id": cid} if cid else {})})
        cid = r["conv_id"]
        print(f"[{tid}] sent{' follow-up' if i else ''} -> chat {cid}: {text[:90]}", flush=True)
        time.sleep(3)
        limit = time.time() + max(600, t["expect"]["max_secs"] * 4)
        while time.time() < limit:
            c = api(f"/api/conv/{cid}")
            if not c.get("running"):
                break
            time.sleep(4)
    c = api(f"/api/conv/{cid}")
    res = types.SimpleNamespace(answer=final_answer(c), calls=calls_of(c), ws=ws, extra=extra)
    try:
        ok, detail = t["check"](res)
    except Exception as e:
        ok, detail = False, f"check error: {e}"
    secs = round(time.time() - t0)
    print(f"[{tid}] {'PASS' if ok else 'FAIL'} in {secs}s, {len(res.calls)} tool calls — {detail}", flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": tid, "cat": t["cat"], "conv": cid, "pass": bool(ok), "detail": detail, "secs": secs,
                            "calls": [x["name"] for x in res.calls], "answer": res.answer[:2000]}) + "\n")
print("done", flush=True)
