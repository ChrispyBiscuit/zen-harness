"""Public-harness survey: the same battery tasks through other agent harnesses, all on the SAME local model
(the app's llama-server, Ornith 9B, OpenAI-compatible at 127.0.0.1:8099), graded with the battery's checks.

  %USERPROFILE%\\survey-env\\Scripts\\python.exe evals\\survey\\run_survey.py --harness oi|smol|goose [--only ids]

Zen Harness itself runs the same tasks with evals\\battery\\run_battery.py --only <ids> (see SURVEY.md).
Do NOT run while something else uses the local model (the user's rule: one LLM user at a time).
"""
import argparse
import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import types
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "battery"))
import battery as B  # noqa: E402

TASKS = ("fact-037,fact-036,math-043,math-044,math-100,data-007,data-013,files-021,files-036,files-020,"
         "codefix-026,codefix-029,code-045,code-006,webpage-016,webpage-022,current-010,current-017,"
         "explain-059,format-016")
ap = argparse.ArgumentParser()
ap.add_argument("--harness", required=True, choices=["oi", "smol", "goose"])
ap.add_argument("--only", default=TASKS)
ap.add_argument("--base", default="http://127.0.0.1:8099/v1")
ap.add_argument("--minutes", type=float, default=10)
a = ap.parse_args()
RUN = os.path.join(os.path.expanduser("~"), "survey-runs", a.harness)       # task folders (outside OneDrive)
OUT = os.path.join(HERE, "runs", a.harness + ".jsonl")
GOOSE = os.path.expanduser(r"~\survey-tools\goose\goose-package\goose.exe")
PY = sys.executable


def model_up():
    try:
        return json.load(urllib.request.urlopen(a.base.rsplit("/v1", 1)[0] + "/health", timeout=5)).get("status") == "ok"
    except Exception:
        return False


def serve(folder):
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x):
            pass
    h = functools.partial(Q, directory=folder)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


# ---- one runner per harness: (prompt, folder) -> final answer text. Each runs in a child process with a timeout.
RUNNER = {
    "oi": r'''
import sys, json
from interpreter import interpreter
interpreter.llm.model = "openai/ornith"; interpreter.llm.api_base = sys.argv[2]; interpreter.llm.api_key = "x"
interpreter.llm.context_window = 32000; interpreter.llm.max_tokens = 4096; interpreter.llm.supports_functions = False
interpreter.auto_run = True; interpreter.offline = True; interpreter.safe_mode = "off"; interpreter.verbose = False
import os; os.chdir(sys.argv[3])
msgs = interpreter.chat(sys.argv[1], display=False, stream=False)
ans = [m.get("content") for m in msgs if m.get("role") == "assistant" and m.get("type") == "message"]
print("\n<<<ANSWER>>>\n" + (ans[-1] if ans else ""))
''',
    "smol": r'''
import sys, os
from smolagents import CodeAgent, OpenAIServerModel, DuckDuckGoSearchTool, VisitWebpageTool
os.chdir(sys.argv[3])
model = OpenAIServerModel(model_id="ornith", api_base=sys.argv[2], api_key="x", max_tokens=4096)
agent = CodeAgent(tools=[DuckDuckGoSearchTool(), VisitWebpageTool()], model=model, max_steps=15,
                  additional_authorized_imports=["os", "shutil", "csv", "json", "math", "glob", "pathlib", "re",
                                                 "subprocess", "datetime", "statistics"])
out = agent.run(sys.argv[1])
print("\n<<<ANSWER>>>\n" + str(out))
''',
}


def run_one(prompt, ws):
    t0 = time.time()
    limit = a.minutes * 60
    if a.harness == "goose":
        env = dict(os.environ, GOOSE_PROVIDER="openai", GOOSE_MODEL="ornith", OPENAI_API_KEY="x",
                   OPENAI_HOST=a.base.rsplit("/v1", 1)[0], OPENAI_BASE_PATH="v1/chat/completions",
                   GOOSE_MODE="auto", GOOSE_DISABLE_KEYRING="1", GOOSE_CONTEXT_LIMIT="32000")
        cmd = [GOOSE, "run", "--no-session", "--quiet", "-t", prompt]
    else:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        cmd = [PY, "-c", RUNNER[a.harness], prompt, a.base, ws]
    try:
        p = subprocess.run(cmd, cwd=ws, env=env, capture_output=True, text=True, timeout=limit, encoding="utf-8",
                           errors="replace", creationflags=0x08000000)
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        out, err = (e.stdout or "") if isinstance(e.stdout, str) else "", "TIMEOUT"
    ans = out.split("<<<ANSWER>>>", 1)[1].strip() if "<<<ANSWER>>>" in out else out.strip()
    return ans, err, round(time.time() - t0)


tasks = {t["id"]: t for t in B.TASKS}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
done = set()
if os.path.exists(OUT):
    done = {json.loads(l)["id"] for l in open(OUT, encoding="utf-8") if l.strip()}
for tid in [x for x in a.only.split(",") if x and x not in done]:
    if not model_up():
        print("model server not answering — stopping (the survey never starts or loads the model itself)")
        break
    t = tasks[tid]
    ws = os.path.join(RUN, tid)
    os.makedirs(ws, exist_ok=True)
    extra = (t["setup"](ws) or {}) if t.get("setup") else {}
    srv = None
    if extra.get("serve"):
        srv, extra["url"] = serve(os.path.join(ws, extra["serve"]))
    fmt = {"ws": ws, **{k: v for k, v in extra.items() if isinstance(v, (str, int, float))}}
    prompt = t["prompt"].format(**fmt)          # follow-ups are left out: one-shot CLIs (none in this slice)
    ans, err, secs = run_one(prompt, ws)
    if srv:
        srv.shutdown()
    r = types.SimpleNamespace(answer=ans, calls=[], ws=ws, extra=extra)
    try:
        ok, detail = t["check"](r)
    except Exception as e:
        ok, detail = False, f"check error: {e}"
    print(f"[{a.harness}] {tid:13} {'PASS' if ok else 'FAIL'} {secs:4}s  {detail[:70]}", flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": tid, "cat": t["cat"], "pass": bool(ok), "detail": detail, "secs": secs,
                            "answer": ans[:2000], "stderr": (err or "")[-600:]}) + "\n")
