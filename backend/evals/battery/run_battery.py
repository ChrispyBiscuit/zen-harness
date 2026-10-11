"""Run the 1000-query battery through the real agent engine and record how each request was handled.

  venv\\Scripts\\python.exe evals\\battery\\run_battery.py --run base1            (resumes if the run exists)
  venv\\Scripts\\python.exe evals\\battery\\run_battery.py --run base1 --limit 50 --cats fact,math

Isolated from the live app (own data folder, browser profile, workspaces, trackers, log under
evals/battery/runs/<run>) but it REUSES the model server the app already runs (llama-server on 127.0.0.1:8099): it
never starts, stops or kills a model server, so it does not fight the app for the GPU.

Per task it records the automatic check, the expected-vs-actual behaviour (route, size, time budget, tools that must /
must not be used, redrafts, clarifying questions), every tool call, every answer retraction and why, the lean pass's
decision (size, depth, plan, think, fact_check), time to the first answer text, and the final answer.
Results append to runs/<run>/results.jsonl one line per task (safe to stop and resume).
"""
import argparse
import functools
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--run", default=time.strftime("%Y%m%d-%H%M"))
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--shard", default="", help="i/n: run every n-th remaining task starting at i, into results.s<i>.jsonl")
ap.add_argument("--upto", type=int, default=0, help="only the first N tasks of the run order (200 = the run set)")
ap.add_argument("--cool", action="store_true", help="pause before a task while the GPU is >=84 C (off by default: the user watches temps)")
ap.add_argument("--bare", action="store_true",
                help="NO harness: send each message straight to the model (no system prompt, tools, background "
                     "search or checks) and grade the reply with the same checks")
ap.add_argument("--max-usd", type=float, default=3.0, help="API backends: stop once the estimated spend reaches this")
ap.add_argument("--cats", default="")
ap.add_argument("--only", default="")
ap.add_argument("--server", default="http://127.0.0.1:8099")
ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
ap.add_argument("--code", default="", help="a folder whose modules load before the app's (e.g. a baseline agentic.py)")
args = ap.parse_args()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RUN = os.path.join(HERE, "runs", args.run)
DATA = os.path.join(RUN, "data")
os.makedirs(DATA, exist_ok=True)
os.environ["LWS_DATA_DIR"] = DATA
os.environ["LWS_NO_TELEGRAM"] = "1"
os.environ["LWS_NO_SCHEDULER"] = "1"
sys.path.insert(0, APP)
sys.path.insert(0, HERE)
if args.code:                       # e.g. the engine as it was before the fixes (the baseline)
    sys.path.insert(0, os.path.abspath(args.code))
os.chdir(APP)

import agent_tools as at          # noqa: E402
import agentic                    # noqa: E402
import browser_agent              # noqa: E402
import llama_local                # noqa: E402
import battery as B               # noqa: E402

browser_agent.PROFILE = os.path.join(RUN, "browser_profile")
browser_agent.SHOTS = os.path.join(RUN, "browser_shots")
agentic.LOG = os.path.join(RUN, "agent_log.jsonl")
at.TRASH = os.path.join(RUN, "trash")
try:
    import bg_tasks
    bg_tasks.LOG_DIR = os.path.join(RUN, "bg")
except Exception:
    pass

# ---- share the app's model server: never stop or kill one. If none is answering (the app only starts it on demand),
#      start it exactly as the app would (see start_server) — the user handed the app over for the run.
SERVER = args.server.rstrip("/")
_ORIG = {"ensure": llama_local.ensure, "reap_orphans": llama_local.reap_orphans, "_kill_stale": llama_local._kill_stale}
llama_local.ensure = lambda *a, **k: SERVER
llama_local.stop = lambda *a, **k: None
llama_local.reap_orphans = lambda *a, **k: None
llama_local._kill_stale = lambda *a, **k: None


def _props():
    try:
        return json.load(urllib.request.urlopen(SERVER + "/props", timeout=10))
    except Exception:
        return None


def _fit(model, num_ctx, kv="auto", *a, **k):
    p = _props() or {}
    n = int((p.get("default_generation_settings") or {}).get("n_ctx") or num_ctx)
    return {"ctx": min(int(num_ctx), n), "kv": kv if kv != "auto" else "q8_0", "note": ""}


llama_local.fit_context = _fit

SEARCH_MENTION = re.compile(r"\b(the|my|these|those|provided|given|attached|background|automatic)\s+(search results?|"
                            r"references?|reference material|results provided|sources provided|snippets?)\b|"
                            r"\bbased on the (search )?results\b|\baccording to the (search )?results\b", re.I)
NARRATION = re.compile(r"^(let me|i'll|i will|first,? i|now i|i need to|i'm going to)\b", re.I)


# ---- API spend: count the usage OpenAI reports on EVERY request (the engine's own per-turn counters miss the lean
#      pass, background checks and summaries), so the spend cap is real.
import urllib.request as _ur
API_USAGE = {"in": 0, "out": 0}
_urlopen = _ur.urlopen


def _take_usage(chunk):
    if b'"usage"' not in chunk:
        return
    txt = chunk.decode("utf-8", "replace").strip()
    if txt.startswith("data:"):
        txt = txt[5:].strip()
    try:
        d = json.loads(txt)
    except ValueError:
        return
    u = d.get("usage") or (d.get("response") or {}).get("usage") or {}
    if isinstance(u, dict):
        API_USAGE["in"] += int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        API_USAGE["out"] += int(u.get("completion_tokens") or u.get("output_tokens") or 0)


class _Counted:
    def __init__(self, resp):
        self._r = resp

    def __getattr__(self, k):
        return getattr(self._r, k)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._r.__exit__(*a)

    def __iter__(self):
        for line in self._r:
            _take_usage(line)
            yield line

    def readline(self, *a):
        line = self._r.readline(*a)
        _take_usage(line)
        return line

    def read(self, *a):
        data = self._r.read(*a)
        if not a:
            _take_usage(data)
        return data


def _counting_urlopen(req, *a, **k):
    resp = _urlopen(req, *a, **k)
    url = req.full_url if hasattr(req, "full_url") else str(req)
    return _Counted(resp) if ("api.openai.com" in url or "api.groq.com" in url or "api.cerebras.ai" in url) else resp


_ur.urlopen = _counting_urlopen


def _sdk_usage(model, inp, out, cache_read, cache_write):
    """Anthropic SDK calls (not urllib): cache reads bill at 0.1x input, cache writes at 1.25x."""
    API_USAGE["in"] += int(inp + 1.25 * cache_write + 0.1 * cache_read)
    API_USAGE["out"] += int(out)


agentic.USAGE_HOOK = _sdk_usage


def base_cfg():
    with open(os.path.join(APP, "settings.json"), encoding="utf-8") as f:
        cfg = dict(json.load(f).get("agent") or {})
    from agent_config import DEFAULT_AGENT
    c = dict(DEFAULT_AGENT, **cfg)
    # the app's own settings, except what makes no sense in an isolated test (fan-out/hybrid workers are off in the
    # app too). stable_tools is left as the app has it (unset = the model always sees every tool).
    c.update(backend="llama", effort="auto", verify=True,
             fanout=dict(c.get("fanout") or {}, enabled=False), hybrid={"enabled": False}, advisor={"enabled": False},
             mcp_servers=[], screen_control=False, delegation=False, plan_mode=False, file_index=False, allow_rules=[])
    for kv in args.set:
        k, _, v = kv.partition("=")
        try:
            c[k.strip()] = json.loads(v)
        except ValueError:
            c[k.strip()] = v
    return c


def serve(folder):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass
    h = functools.partial(Quiet, directory=folder)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/index.html"


def lean_info(conv):
    """The engine log lines for this conversation: the lean pass's decision and the checks that fired."""
    out = {"lean": None, "answered_lean": False, "playbook": None, "math_checks": 0, "lean_checks_failed": 0,
           "factcheck_rounds": 0, "interpret_ms": None}
    try:
        with open(agentic.LOG, encoding="utf-8") as f:
            for line in f:
                if conv not in line:
                    continue
                d = json.loads(line)
                if d.get("conv") != conv:
                    continue
                if d.get("lean") == "tools":
                    out["lean"] = {"depth": d.get("depth"), "effort": d.get("effort"), "fact_check": d.get("fact_check")}
                elif d.get("lean") == "answered":
                    out["answered_lean"] = True
                if d.get("playbook"):
                    out["playbook"] = d.get("playbook")
                if "math_check" in d:
                    out["math_checks"] += 1
                if "lean_check" in d and (d.get("lean_check") or {}).get("unsupported"):
                    out["lean_checks_failed"] += 1
                if "factcheck" in d:
                    out["factcheck_rounds"] += 1
                if "interpret" in d:
                    out["interpret_ms"] = d.get("ms")
    except OSError:
        pass
    return out


class Res:
    pass


# Commands that act on the PC itself rather than the task's workspace: always denied (see approve() in run_task).
HOST_GUARD = re.compile(
    r"recycle|clear-recyclebin|shell_runrecyclebinempty|shutdown|restart-computer|stop-computer|logoff|"
    r"\breg(\.exe)?\s+(add|delete|import)|set-itemproperty\s+.*hk(lm|cu)|new-itemproperty|remove-itemproperty|"
    r"set-service|stop-service|\bsc(\.exe)?\s+(config|stop|delete)|stop-process|taskkill|format-volume|"
    r"\bformat\s+[a-z]:|diskpart|bcdedit|vssadmin|cipher\s+/w|wevtutil|netsh|set-mppreference|"
    r"set-executionpolicy|schtasks|\$env:|\$home|%userprofile%|%appdata%|~[\\/]|appdata|program ?files|"
    r"\\windows|system32|invoke-webrequest|\biwr\b|\bcurl\b|\bwget\b|start-bitstransfer|\brm\s+-rf\s+/")


def bare_call(cfg, msgs):
    """One plain chat completion for the --bare baseline -> reply text."""
    model, prov = cfg.get("model"), agentic._provider(cfg)
    if prov and prov.get("native"):                       # Anthropic: official SDK (no system prompt, no tools)
        msg = agentic._anthropic_call(cfg, {"model": model, "messages": msgs, "max_completion_tokens": 8192}, None)
        return "".join(b.text for b in msg.content if b.type == "text")
    base = (prov or {}).get("base") or "https://api.openai.com"
    body = {"model": model, "messages": msgs, "max_completion_tokens": 8000}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "User-Agent": "ZenHarness/1.0",
        "Authorization": "Bearer " + (agentic._provider_key(cfg) or "")})
    try:
        r = json.load(_ur.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", "replace")
        raise RuntimeError(("API quota exhausted: " if "quota" in t.lower() else f"model server error {e.code}: ")
                           + t[:200])
    return r["choices"][0]["message"].get("content") or ""


# ---- the no-harness baseline (--bare): each task made solvable without tools
BARE_NA = {"current", "local", "system", "safety"}    # need live web / this PC, or (safety) pass trivially with no tools
BARE_ACT = {"code", "codefix", "files", "docs", "research"}
BARE_PROTOCOL = (
    "\n\n(You have no tools: nothing can be opened or run for you. Answer in this message. To create or overwrite a "
    "file, give its complete content in a fenced code block whose first line is a comment `# file: <name>` "
    "(the name relative to the folder). To move or rename files, write one line per move: `MOVE <old> -> <new>`. "
    "To produce a .docx, .xlsx or .pptx, give a Python script in a ```python block whose first line is `# run`; it runs "
    "in the folder, and python-docx, openpyxl and python-pptx are installed.)")
_TEXT_EXT = (".txt", ".csv", ".py", ".md", ".json", ".log", ".html", ".tsv", ".ini", ".cfg", ".xml", ".yaml", ".yml")


def bare_context(ws, budget=40000):
    """The task folder as text: every file with its size, and the contents of small text files."""
    rows, texts = [], []
    for root, _, files in os.walk(ws):
        for f in sorted(files):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ws)
            rows.append(f"{rel}  ({os.path.getsize(p):,} bytes)")
            if f.lower().endswith(_TEXT_EXT) and budget > 0:
                t = open(p, encoding="utf-8", errors="replace").read()[:budget]
                budget -= len(t)
                texts.append(f"--- {rel} ---\n{t}")
    if not rows:
        return ""
    return ("\n\nThe folder " + ws + " contains:\n" + "\n".join(rows)
            + ("\n\nFile contents:\n" + "\n\n".join(texts) if texts else ""))


def bare_prompt(t, ws, fmt, q, first):
    """The task message for the bare model: inputs inlined, plus the answer format when it has to act."""
    q = q.format(**fmt)
    if t["cat"] == "webpage":
        page = open(os.path.join(ws, "site", "index.html"), encoding="utf-8").read()
        q = q.replace("Open " + fmt.get("url", ""), "Here is the page at " + fmt.get("url", "")) + "\n\n" + page
    elif first:
        q += bare_context(ws)
    if t["cat"] in BARE_ACT or (t["cat"] == "followup" and "{ws}" in t["prompt"]):
        q += BARE_PROTOCOL
    return q


_RISKY_PY = re.compile(r"subprocess|os\.system|rmtree|os\.remove|unlink|requests|urllib|socket|shutil\.move|__import__")


def bare_apply(t, ws, answer):
    """Apply the bare model's answer in the task folder: labelled file blocks, MOVE lines, `# run` scripts."""
    wsa = os.path.abspath(ws)

    def inside(rel):
        p = os.path.abspath(os.path.join(ws, rel.strip().strip("`'\"")))
        return p if p.startswith(wsa + os.sep) else None

    blocks = re.findall(r"```[\w.+-]*[ \t]*\n(.*?)```", answer, re.S)
    wrote = False
    for b in blocks:
        first, _, rest = b.partition("\n")
        m = re.match(r"\s*(?:#|//|<!--|--)\s*file:\s*(.+?)\s*(?:-->)?\s*$", first, re.I)
        if m and inside(m.group(1)):
            p = inside(m.group(1))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(rest)
            wrote = True
        elif re.match(r"\s*#\s*run\b", first, re.I) and not _RISKY_PY.search(b):
            p = os.path.join(ws, "_bare_run.py")
            open(p, "w", encoding="utf-8").write(b)
            try:
                import subprocess
                subprocess.run([sys.executable, p], cwd=ws, timeout=90, capture_output=True, creationflags=0x08000000)
            except Exception:
                pass
            wrote = True
    if not wrote:              # a "# file: name" header without a code fence: the text after it is the content
        unfenced = re.sub(r"```.*?```", "", answer, flags=re.S)
        for name, body in re.findall(r"^[ \t]*#\s*file:\s*(\S+)[ \t]*\n(.*?)(?=^[ \t]*#\s*file:|\Z)", unfenced, re.M | re.S):
            p = inside(name)
            if p:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w", encoding="utf-8").write(body.strip() + "\n")
                wrote = True
    for a, b in re.findall(r"^\s*MOVE\s+(.+?)\s*->\s*(.+?)\s*$", answer, re.M):
        pa, pb = inside(a), inside(b)
        if pa and pb and os.path.exists(pa):
            os.makedirs(os.path.dirname(pb), exist_ok=True)
            os.replace(pa, pb)
    m = re.search(r"Save it as (\S+?) in", t["prompt"])
    if t["cat"] == "code" and not wrote and blocks and m:           # an unlabelled script: save it under the asked name
        open(os.path.join(ws, m.group(1)), "w", encoding="utf-8").write(blocks[0])
    if t["cat"] == "codefix" and not wrote and blocks:
        open(os.path.join(ws, "lib.py"), "w", encoding="utf-8").write(blocks[0])
    if t["cat"] == "research" and not find_report(ws):               # the report written in the reply itself
        open(os.path.join(ws, "report.md"), "w", encoding="utf-8").write(answer)


def find_report(ws):
    return any(f.endswith(".md") for f in os.listdir(ws))


def run_task(t):
    ws = os.path.join(RUN, "ws", t["id"])
    os.makedirs(ws, exist_ok=True)
    extra = (t["setup"](ws) or {}) if t.get("setup") else {}
    srv = None
    if extra.get("serve"):
        srv, url = serve(os.path.join(ws, extra["serve"]))
        extra["url"] = url
    fmt = {"ws": ws, **{k: v for k, v in extra.items() if isinstance(v, (str, int, float))}}
    cfg = base_cfg()
    cfg["roots"] = [ws]
    cfg["tracker_path"] = os.path.join(RUN, "trackers", t["id"] + ".md")
    os.makedirs(os.path.dirname(cfg["tracker_path"]), exist_ok=True)
    ex = t["expect"]
    hard = min(int(os.environ.get("BATTERY_HARD_CAP") or 1500), max(120, ex["max_secs"] * 2.5))
    calls, by_id = [], {}
    ev = {"retracts": [], "first_answer": None, "images": 0, "factcheck": 0, "interpretation": False,
          "compacted": 0, "warn": 0, "blocked": 0}
    t0 = time.time()
    u0 = dict(API_USAGE)
    deadline = t0 + hard

    def emit(kind, /, **d):
        if kind == "tool_call":
            c = {"name": d.get("name"), "args": d.get("args"), "ok": None, "text": "", "t": round(time.time() - t0, 1)}
            by_id[d.get("id")] = c
            calls.append(c)
        elif kind == "tool_result":
            c = by_id.get(d.get("id"))
            if c is not None:
                c["ok"], c["text"] = bool(d.get("ok")), str(d.get("text") or "")[:600]
                if "⚠" in c["text"]:
                    ev["warn"] += 1
                if c["text"].startswith(("Not run again",)) or " did not run" in c["text"][:80]:
                    ev["blocked"] += 1
        elif kind == "answer_retract":
            ev["retracts"].append(str(d.get("reason") or ""))
        elif kind == "ans_delta" and ev["first_answer"] is None and str(d.get("text") or "").strip():
            ev["first_answer"] = round(time.time() - t0, 1)
        elif kind == "images":
            ev["images"] = len(d.get("images") or [])
        elif kind == "factcheck":
            ev["factcheck"] += 1
        elif kind == "interpretation":
            ev["interpretation"] = True
        elif kind == "compacted":
            ev["compacted"] += 1

    approvals = []

    def approve(req):
        """Stand-in for the user's approval card. The battery runs on the real PC, so anything that could reach
        outside this task's workspace is DENIED (as a careful user would), and safety/ambiguous tasks — whose
        point is whether the agent does the risky thing — get every approval denied. Recorded per task."""
        c = by_id.get(req.get("id")) or {}
        a = c.get("args") or {}
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except ValueError:
                a = {"command": a}
        a = a if isinstance(a, dict) else {}
        # only what ACTS on the PC is screened: commands/code and target paths — never the text being written
        # (a report mentioning "recycling" or "curl" was being refused, which failed research/doc tasks)
        acts = " ".join(str(a.get(k) or "") for k in ("command", "code", "script"))
        paths = " ".join(str(a.get(k) or "") for k in ("path", "src", "dst", "source", "destination", "file",
                                                        "filename", "folder", "dir", "target", "output", "out_path"))
        full = (acts + " " + json.dumps(paths)).lower()
        wsl = os.path.normcase(os.path.abspath(ws)).lower()
        outside = [p for p in re.findall(r"[a-z]:\\\\?[^\s\"'|;,)]*", full)
                   if not os.path.normcase(p.replace("\\\\", "\\")).startswith(wsl) and not p.endswith(".exe")]
        if t["cat"] in ("safety", "ambig"):
            dec = "deny"
        elif HOST_GUARD.search(full) or outside:
            dec = "deny"
        else:
            dec = "once"
        approvals.append({"name": req.get("name"), "kind": req.get("kind"), "decision": dec,
                          "desc": str(req.get("desc") or "")[:200]})
        return dec

    conv = f"bat-{t['id']}"
    answer, err, answers = "", "", []
    if args.bare:                                         # the no-harness baseline
        import types
        e = types.SimpleNamespace(turn={})
        msgs = []
        if t["cat"] in BARE_NA:
            return {"id": t["id"], "cat": t["cat"], "prompt": t["prompt"][:300], "pass": None, "na": True,
                    "detail": "not solvable without a harness (live web / this PC / no-tools safety)", "secs": 0,
                    "exp": {}, "tools": [], "retracts": [], "tokens": {"in": 0, "out": 0}}
        try:
            for i, q in enumerate([t["prompt"]] + list(t.get("followups") or [])):
                if time.time() > deadline:
                    break
                msgs.append({"role": "user", "content": bare_prompt(t, ws, fmt, q, i == 0)})
                answer = bare_call(cfg, msgs)
                msgs.append({"role": "assistant", "content": answer or "(empty)"})
                answers.append(answer)
            bare_apply(t, ws, answer)
        except Exception:
            err = traceback.format_exc()[-1500:]
    e = e if args.bare else agentic.AgentEngine(cfg, emit, ask=approve, should_stop=lambda: time.time() > deadline,
                                                conv_id=conv, title=t["id"])
    try:
        if args.bare:
            raise StopIteration
        e.prepare()
        answer = e.run(t["prompt"].format(**fmt)) or ""
        answers.append(answer)
        for fu in t.get("followups") or []:
            if time.time() > deadline:
                break
            answer = e.run(fu.format(**fmt)) or ""
            answers.append(answer)
    except StopIteration:
        pass
    except Exception:
        err = traceback.format_exc()[-1500:]
    try:                                  # the turn is over: stop what the agent started, as the app does
        import bg_tasks
        bg_tasks.MANAGER.stop_owned(conv)
    except Exception:
        pass
    secs = time.time() - t0
    if srv:
        srv.shutdown()
    r = Res()
    r.ws, r.answer, r.calls, r.extra = ws, answer, calls, extra
    try:
        passed, detail = t["check"](r)
    except Exception as x:
        passed, detail = False, f"check error: {x}"
    if err:
        passed, detail = False, "engine error: " + err.strip().splitlines()[-1][:200]
    timed_out = time.time() > deadline
    tu = e.turn or {}
    names = [c["name"] for c in calls]
    li = lean_info(conv)
    route = "tools" if any(n not in ("update_tracker",) for n in names) else "direct"
    asked = "ask_user" in names or "?" in answer[-400:]
    must_ok = (not ex["must"]) or any(n in ex["must"] for n in names)
    forbid_ok = not any(n in ex["forbid"] for n in names)
    exp = {
        "route_ok": ex["route"] == "any" or ex["route"] == route,
        "time_ok": secs <= ex["max_secs"],
        "must_ok": must_ok, "forbid_ok": forbid_ok,
        "drafts_ok": len(ev["retracts"]) <= ex["max_drafts"],
        "calls_ok": len(calls) <= ex.get("max_calls", 999),
        "ask_ok": (asked if ex.get("ask") else True),
        "size_ok": (ex["size"] is None or li["lean"] is None
                    or ((li["lean"].get("effort") or {}).get("size") in (ex["size"], "?", None))),
    }
    return {"id": t["id"], "cat": t["cat"], "prompt": t["prompt"][:300], "pass": bool(passed), "detail": detail[:300],
            "secs": round(secs, 1), "timed_out": timed_out, "first_answer_s": ev["first_answer"], "route": route,
            "expect": {k: (list(v) if isinstance(v, tuple) else v) for k, v in ex.items()}, "exp": exp,
            "steps": tu.get("steps", 0), "tool_calls": len(calls), "tools": names,
            "failed_calls": sum(1 for c in calls if c["ok"] is False), "blocked": ev["blocked"], "warnings": ev["warn"],
            "retracts": ev["retracts"], "images": ev["images"], "factcheck_events": ev["factcheck"],
            "approvals": approvals,
            "interpretation": ev["interpretation"], "compacted": ev["compacted"], "lean": li,
            "prompt_tokens": tu.get("prompt_tokens", 0), "completion_tokens": tu.get("completion_tokens", 0),
            "planned": bool(tu.get("planned")), "effort": tu.get("effort"),
            "answer": answer[:2500], "answer_len": len(answer), "turn_answers": len(answers),
            "tokens": {"in": API_USAGE["in"] - u0["in"], "out": API_USAGE["out"] - u0["out"]},
            "mentions_search": bool(SEARCH_MENTION.search(answer)),
            "narration_only": bool(NARRATION.match(answer.strip())) and len(answer) < 300,
            "empty": not answer.strip(), "error": err[-500:],
            "calls": [{k: c[k] for k in ("name", "ok", "t")} | {"args": json.dumps(c.get("args"))[:300],
                                                                 "text": c["text"][:240]} for c in calls[:40]]}


def gpu_state():
    """-> {temp, clock, max_clock, throttle} from nvidia-smi (None values if unavailable)."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,clocks.sm,clocks.max.sm,"
                              "clocks_throttle_reasons.active", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
        t, c, m, r = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"temp": int(t), "clock": int(c), "max_clock": int(m), "throttle": r}
    except Exception:
        return {"temp": None, "clock": None, "max_clock": None, "throttle": None}


def cool_down(limit=600, hot=84, cool=76):   # gentler since 2026-10-10: hot laptop, swollen battery
    """The laptop GPU throttles hard after hours of load (93 °C, 210 of 2100 MHz; it idles near 81 °C). Before a task,
    if it is `hot`, wait until it is `cool` (at most `limit` s) — comparable timings, and a break for the hardware.
    -> seconds waited."""
    g = gpu_state()
    if g["temp"] is None or g["temp"] < hot:
        return 0
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(15)
        g = gpu_state()
        if g["temp"] is None or g["temp"] <= cool:
            break
    return round(time.time() - t0)


def start_server():
    """Start the model server the way the app does: an engine with the app's settings prepares itself (profile, context,
    drafter, cache types, vision) through the real llama_local.ensure. -> True when it answers."""
    global SERVER
    print("starting the model server like the app does…", flush=True)
    llama_local.ensure = _ORIG["ensure"]
    llama_local.reap_orphans, llama_local._kill_stale = _ORIG["reap_orphans"], _ORIG["_kill_stale"]
    try:
        cfg = base_cfg()
        cfg["roots"] = [os.path.join(RUN, "ws")]
        e = agentic.AgentEngine(cfg, lambda *a, **k: None, ask=lambda req: "once", conv_id="bat-server-start")
        e.prepare()
        if getattr(e, "base", None):
            SERVER = str(e.base).rstrip("/")
    except Exception:
        print("could not start the model server:", traceback.format_exc()[-400:], flush=True)
    finally:
        llama_local.ensure = lambda *a, **k: SERVER
        llama_local.reap_orphans = lambda *a, **k: None
        llama_local._kill_stale = lambda *a, **k: None
    ok = bool(_props())
    print(f"model server {'up at ' + SERVER if ok else 'still not answering'}", flush=True)
    return ok


def wait_server():
    for i in range(120):
        if _props():
            return True
        if i == 0:
            print("model server not answering — waiting…", flush=True)
        if i in (3, 12, 40):                        # 30 s, 2 min, ~7 min without a server: start it ourselves
            if start_server():
                return True
        time.sleep(10)
    return False


def main():
    import glob
    si, sn = (int(x) for x in args.shard.split("/")) if args.shard else (0, 1)
    out_path = os.path.join(RUN, f"results.s{si}.jsonl" if args.shard else "results.jsonl")
    done = set()                                          # every results file of this run (all shards)
    for rp in glob.glob(os.path.join(RUN, "results*.jsonl")):
        if "superseded" not in rp:
            with open(rp, encoding="utf-8") as f:
                done |= {json.loads(l)["id"] for l in f if l.strip()}
    cats = {c for c in args.cats.split(",") if c}
    only = {c for c in args.only.split(",") if c}
    pool = B.interleaved()[:args.upto] if args.upto else B.interleaved()
    todo = [t for t in pool if t["id"] not in done and (not cats or t["cat"] in cats)
            and (not only or t["id"] in only)]
    if args.limit:
        todo = todo[:args.limit]
    todo = todo[si::sn]
    bc = base_cfg()
    api = bc.get("backend", "llama") != "llama"            # an API model: no local server, no GPU cool-down
    model = bc.get("model") if api else ((_props() or {}).get("model_path") or "?")
    price = agentic.PRICE.get(str(model), (0.0, 0.0))
    spent = 0.0
    if api and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for l in f:
                tk = json.loads(l).get("tokens") or {}
                spent += (tk.get("in", 0) * price[0] + tk.get("out", 0) * price[1]) / 1e6
    print(f"run {args.run}: {len(done)} done, {len(todo)} to go; model {os.path.basename(str(model))}; engine "
          f"{agentic.__file__}" + (f"; est. spent so far ${spent:.3f} of ${args.max_usd}" if api else ""), flush=True)
    for i, t in enumerate(todo, 1):
        if api and spent >= args.max_usd:
            print(f"spend cap reached (est. ${spent:.2f}) — stopping", flush=True)
            break
        if not api and not wait_server():
            print("model server gone for 20 minutes — stopping", flush=True)
            break
        waited = 0 if (api or not args.cool) else cool_down()
        g0 = gpu_state()
        try:
            res = run_task(t)
        except Exception:
            res = {"id": t["id"], "cat": t["cat"], "pass": False, "detail": "runner error",
                   "error": traceback.format_exc()[-800:], "secs": 0, "exp": {}, "tools": [], "retracts": []}
        res["model"] = str(model) if api else os.path.basename(((_props() or {}).get("model_path") or "?"))
        _err = (str(res.get("error") or "") + " " + str(res.get("detail") or "")).lower()
        if "quota exhausted" in _err or re.search(r"model server error (402|403)|insufficient|credit|payment", _err):
            print("API quota exhausted — stopping (this task is not recorded)", flush=True)
            break
        if api:
            tk = res.get("tokens") or {}
            spent += (tk.get("in", 0) * price[0] + tk.get("out", 0) * price[1]) / 1e6
            res["est_usd_total"] = round(spent, 4)
        g1 = gpu_state()
        res["gpu"] = {"start": g0, "end": g1, "cooled_s": waited}
        res["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
        bad = [k for k, v in (res.get("exp") or {}).items() if not v]
        print(f"[{len(done) + i}/1000] {t['id']:<14} {'PASS' if res['pass'] else 'FAIL'} {res.get('secs', 0):6.1f}s "
              f"calls={res.get('tool_calls', 0):<2} drafts={len(res.get('retracts') or [])} "
              f"{'exp-miss:' + ','.join(bad) if bad else ''} {res.get('detail', '')[:70]}", flush=True)
    for stop in (lambda: browser_agent.MANAGER.close(), lambda: __import__("sandbox").shutdown_all()):
        try:
            stop()
        except Exception:
            pass
    os._exit(0)


if __name__ == "__main__":
    main()
