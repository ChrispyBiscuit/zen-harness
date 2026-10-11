"""Build the dev-only replay files under lws-web/replays/ from the backend's saved chats (read-only).

    python scripts/make_replays.py

Writes
  replays/fixture.json    one chat holding every card type (static: no events) + a few real chats for the sidebar
  replays/build-run.json  the 22-step / 28-call build, reconstructed as a live event stream (?replay=build-run)
  replays/files/          images the fixtures point at (served at /api/file?raw=1 by the replay dev server)

A replay file is {version, title, state: {settings, models, ...}, convs: [Conv], select, events: [{at, ev}]} where
`ev` is a WebSocket message the backend would send (t = msgs | threads | status | tps | ctx | run_end), or a
compact `_delta` ({conv, i, field, add}: append text to message i) that the player turns into a `msgs` event, and
`at` is milliseconds from the start at 1x speed. Nothing here talks to the backend or the model server.
"""
import copy
import json
import os
import random
import re
import shutil
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = Path(os.environ.get("LWS_DATA_DIR") or ROOT.parent / "backend")    # where conversations.json lives
OUT = ROOT / "replays"
FILES = OUT / "files"
BUILD_CONV = "3be98ece"           # "Please make me a mario style video game": 22 steps, 28 tool calls, 741 s
NOW = 1791600000.0                # fixed clock, so the files are stable

rng = random.Random(42)


def load():
    convs = json.loads((AGENT / "conversations.json").read_text(encoding="utf-8"))
    settings = json.loads((AGENT / "settings.json").read_text(encoding="utf-8"))
    for s in (settings.get("agent", {}).get("mcp_servers") or []):     # never copy secrets into dev files
        if isinstance(s, dict):
            for k in ("env", "headers"):
                s.pop(k, None)
    return convs, settings


def find(convs, prefix):
    return next(c for c in convs if c["id"].startswith(prefix))


def tiny_png(path, w, h, seed):
    """A small gradient PNG standing in for a screenshot (no PIL needed)."""
    r = random.Random(seed)
    c1, c2 = [r.randint(30, 90) for _ in range(3)], [r.randint(120, 220) for _ in range(3)]
    raw = b""
    for y in range(h):
        raw += b"\0" + bytes(int(c1[i] + (c2[i] - c1[i]) * (x + y) / (w + h)) for x in range(w) for i in range(3))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def stage_file(src, name, w=320, h=180):
    """Copy a real image into replays/files (or draw a placeholder) -> the path the fixture uses."""
    dst = FILES / name
    try:
        if src and Path(src).is_file() and Path(src).stat().st_size < 3_000_000:
            shutil.copyfile(src, dst)
        else:
            raise FileNotFoundError
    except OSError:
        tiny_png(dst, w, h, name)
    return f"replay://{name}"


def first(convs, pred):
    for c in convs:
        for m in c["messages"]:
            try:
                if pred(m):
                    return copy.deepcopy(m)
            except (KeyError, TypeError, AttributeError):
                pass
    return None


def tool_msg(name, kind, desc, result, display, ok=True, **extra):
    t = {"name": name, "kind": kind, "desc": desc, "result": result, "display": display, "by": "", "ok": ok,
         "awaiting": False}
    t.update(extra)
    return {"role": "assistant", "kind": "tool", "content": name, "id": f"fx{rng.randrange(10**9)}", "tool": t}


# ------------------------------------------------------------------------------------------------ fixture
def fixture_conv(convs):
    dtype = lambda ty: (lambda m: m["kind"] == "tool" and (m["tool"].get("display") or {}).get("type") == ty)
    real = {
        "playbook": first(convs, lambda m: m["kind"] == "playbook"),
        "plan": first(convs, lambda m: m["kind"] == "plan" and len(m["content"]) > 400),
        "thinking": first(convs, lambda m: m["role"] == "assistant" and not m.get("kind") and m.get("thinking")
                          and not m.get("content") and len(m["thinking"]) > 300),
        "web_hits": first(convs, lambda m: dtype("web_hits")(m) and m["tool"]["display"].get("pages")),
        "tracker": first(convs, lambda m: dtype("tracker")(m) and len(m["tool"]["display"].get("milestones") or
                                                                      m["tool"]["display"].get("steps") or []) >= 3),
        "term_ok": first(convs, lambda m: dtype("terminal")(m) and str(m["tool"]["display"].get("exit")) == "0"
                         and 3 < len(m["tool"]["display"].get("output") or "") < 1500),
        "term_bad": first(convs, lambda m: dtype("terminal")(m) and str(m["tool"]["display"].get("exit")) not in
                          ("0", "None") and "Error" in (m["tool"]["display"].get("output") or "")),
        "diff": first(convs, lambda m: dtype("diff")(m) and m["tool"]["name"] == "edit_file" and
                      10 < (m["tool"]["display"].get("added") or 0) < 40),
        "file": first(convs, dtype("file")),
        "python": first(convs, lambda m: dtype("python")(m) and not m["tool"]["display"].get("error")),
        "math": first(convs, dtype("math")),
        "browser": first(convs, lambda m: dtype("browser")(m) and m["tool"]["display"].get("shot")),
        "image": first(convs, dtype("image")),
        "gather_run": first(convs, lambda m: dtype("gather")(m) and m["tool"]["display"].get("running")),
        "gather_done": first(convs, lambda m: dtype("gather")(m) and not m["tool"]["display"].get("running")),
        "doc_created": first(convs, dtype("document_created")),
        "document": first(convs, dtype("document")),
        "draft_tool": first(convs, dtype("draft")),
        "compacted": first(convs, lambda m: m["kind"] == "compacted"),
        "factcheck": first(convs, lambda m: m["kind"] == "factcheck" and any(not i["ok"] for i in m["items"])),
        "draft": first(convs, lambda m: m["kind"] == "draft"),
        "changes": first(convs, lambda m: m["kind"] == "changes" and len(m["files"]) >= 3),
        "bg_search": first(convs, lambda m: m["kind"] == "tool" and "background search" in (m["tool"].get("desc") or "")),
    }
    missing = [k for k, v in real.items() if v is None]
    if missing:
        print("no real sample for:", ", ".join(missing), "(synthesised)")

    # normalise the odd stringly-typed terminal fields the store sometimes holds
    for k in ("term_ok", "term_bad"):
        d = real[k]["tool"]["display"]
        d["exit"] = int(d["exit"]) if str(d.get("exit")).lstrip("-").isdigit() else d.get("exit")
        d["seconds"] = float(d["seconds"]) if d.get("seconds") not in (None, "None") else None
        d["stopped"] = None if d.get("stopped") in (None, "None", "") else d["stopped"]
    if real["browser"]:
        real["browser"]["tool"]["display"]["shot"] = stage_file(real["browser"]["tool"]["display"]["shot"],
                                                                "browser-shot.png")
    if real["image"]:
        real["image"]["tool"]["display"]["path"] = stage_file(real["image"]["tool"]["display"]["path"], "coin.png")
    if real["changes"]:
        real["changes"]["files"] = dict(list(real["changes"]["files"].items())[:5])
    if real["bg_search"] is None:
        real["bg_search"] = tool_msg(
            "web_search", "net", "background search for this message (automatic)",
            "Offline Wikipedia: CSV (Comma-separated values) — a delimited text file that uses a comma to separate "
            "values…\nWeb: 'Working with CSV files in Python' (realpython.com)", None,
            desc_args=None)
        real["bg_search"]["tool"]["desc"] = "background search for this message (automatic)"

    term_ask = copy.deepcopy(real["term_ok"])
    d = term_ask["tool"]["display"]
    d.update(command="Remove-Item -Recurse .\\build", output="", exit=None, seconds=None)
    term_ask["tool"].update(result=None, ok=None, awaiting=True,
                            approval={"call_id": "fx-approve", "rule_label": "powershell: Remove-Item *",
                                      "warnings": ["Deletes files: .\\build (recursive)"]})
    term_run = copy.deepcopy(real["term_ok"])
    term_run["tool"]["display"].update(command="npm run build", output="> vite build\n\nvite v8.3.0 building for "
                                       "production...\ntransforming (812) src/components/Transcript.tsx", exit=None,
                                       seconds=None)
    term_run["tool"].update(result=None, ok=None)

    syn = {
        "interpretation": {"role": "assistant", "kind": "interpretation",
                           "content": "Build a small CLI tool that summarises a folder of CSV exports into one report",
                           "deliverable": "a Python script plus a sample report", "constraints": ["runs on Windows",
                           "no paid services"], "conditions": ["handles files with different column orders"],
                           "ambiguity": "‘report’ could mean Markdown or Excel", "reading": "Markdown, with an "
                           "optional --xlsx flag", "implicit": ["a README with usage"], "ask": True,
                           "question": "Markdown or Excel output?", "seconds": 3.1},
        "table": tool_msg("query_data", "read", 'query_data: {"sql": "select region, sum(total) ..."}', "4 rows",
                          {"type": "table", "sql": "select region, count(*) as orders, sum(total) as revenue from "
                           "sales group by region order by revenue desc", "columns": ["region", "orders", "revenue"],
                           "rows": [["West", "1,204", "$412,880"], ["North", "998", "$301,442"],
                                    ["South", "870", "$255,010"], ["East", "611", "$190,775"]], "total": 4}),
        "artifact": tool_msg("create_artifact", "write", "create_artifact", "created artifact report-preview v2",
                             {"type": "artifact", "id": "report-preview", "title": "Sales report preview",
                              "atype": "html", "version": 2, "action": "revised"}),
        "note": tool_msg("remember", "note", "remember", "saved",
                         {"type": "note", "action": "saved", "text": "Prefers Markdown reports with a summary table "
                          "first"}),
        "doc_hits": tool_msg("search_files", "read", 'search_files: {"query": "quarterly totals"}', "3 files",
                             {"type": "doc_hits", "query": "quarterly totals", "hits": [
                                 {"path": "C:\\Users\\demo\\Documents\\reports\\q3-summary.md",
                                  "snippet": "Quarterly totals by region: West leads with $412k…"},
                                 {"path": "C:\\Users\\demo\\Documents\\exports\\sales-2026-09.csv",
                                  "snippet": "region,orders,total,date…"}]}),
        "bg": tool_msg("bg_start", "exec", "bg_start", "started bg3",
                       {"type": "bg", "name": "dev server", "id": "bg3", "command": "python -m http.server 8000"}),
        "delegate": tool_msg("delegate_task", "note", "delegate_task", "delegated",
                             {"type": "delegate", "title": "Write unit tests for the CSV merger",
                              "task": "Write pytest tests covering mismatched column orders, empty files and "
                              "non-UTF-8 input.", "conv_id": "fixture-sub"}),
        "advisor": tool_msg("consult_advisor", "net", "consult_advisor", "advice",
                            {"type": "advisor", "model": "gpt-6-luna", "cost": 0.012,
                             "question": "Is pandas overkill for merging 30 small CSVs?",
                             "advice": "For 30 small files the **csv** module is enough; use pandas only if you "
                             "need group-bys. Keep the dependency optional."}),
        "generic_fail": tool_msg("read_file", "read", 'read_file: {"path": "C:/Users/demo/Documents/missing.csv"}',
                                 "Error: file not found: C:/Users/demo/Documents/missing.csv", None, ok=False),
        "generic_ok": tool_msg("list_dir", "read", 'list_dir: {"path": "C:/Users/demo/Documents/exports"}',
                               "sales-2026-07.csv\nsales-2026-08.csv\nsales-2026-09.csv", None),
        "task_done": {"role": "assistant", "kind": "task_done", "content": "", "title": "Write unit tests for the "
                      "CSV merger", "status": "done", "result": "12 tests written, all passing (pytest 0.8 s).",
                      "conv_id": "fixture-sub"},
    }
    final_answer = {
        "role": "assistant", "streaming": False,
        "thinking": "The report script works; summarise what was built and how to run it.",
        "content": "Done. **csv_report.py** merges every CSV in a folder and writes a Markdown report.\n\n"
                   "## How to run it\n\n```powershell\npython csv_report.py C:\\Users\\demo\\Documents\\exports "
                   "--out report.md\n```\n\n| Region | Orders | Revenue |\n|---|---:|---:|\n| West | 1,204 | "
                   "$412,880 |\n| North | 998 | $301,442 |\n\nThe average order value is "
                   "$\\frac{1{,}160{,}107}{3{,}683} \\approx \\$315$ [1].\n\n- Handles any column order\n- Skips "
                   "empty files and reports them\n- `--xlsx` also writes an Excel copy",
        "stats": {"steps": 14, "tool_calls": 21, "prompt_tokens": 210400, "completion_tokens": 6120,
                  "seconds": 188.4, "context_used": 23800, "context_max": 65536, "compactions": 1,
                  "failed_calls": 2},
    }
    ask_user = {"role": "assistant", "streaming": False, "content":
                "Before I start, a few questions so I head in the right direction:\n\n1. Which folder holds the "
                "exports? *(default: Documents\\exports)*\n2. Markdown or Excel output? *(default: Markdown)*\n"
                "3. Should totals include refunds? *(default: no)*\n\nAnswer any or all, or say **go with the "
                "defaults**."}
    error_msg = {"role": "assistant", "error": True, "streaming": False,
                 "content": "Model server error: llama-server exited (code 3221225477) while loading the model."}
    stopped = {"role": "assistant", "streaming": False, "content": "(stopped)"}

    T = NOW - 3600
    U = lambda text, **kw: {"role": "user", "content": text, "t": T, **kw}
    seq = [
        U("Build me a small tool that turns a folder of CSV exports into one summary report."),
        real["bg_search"], syn["interpretation"], real["playbook"], real["plan"], real["thinking"],
        real["web_hits"], syn["doc_hits"], real["tracker"], syn["generic_ok"], real["file"], real["term_ok"],
        real["term_bad"], real["diff"], real["draft_tool"], real["python"], syn["table"], real["math"],
        real["browser"], real["image"], real["document"], real["doc_created"], syn["artifact"], syn["note"],
        syn["bg"], syn["delegate"], real["gather_run"], real["gather_done"], syn["advisor"], syn["generic_fail"],
        real["compacted"], syn["task_done"], real["draft"], real["factcheck"], real["changes"], final_answer,
        U("Can you also handle a folder of Excel files?"), ask_user,
        U("Go with the defaults.", interjection=False), term_run, term_ask,
        U("Try the bigger model for this one."), error_msg,
        U("Stop for now."), stopped,
    ]
    msgs = [m for m in seq if m]
    for m in msgs:
        m.pop("_v", None)
    return {"id": "fixture", "title": "Fixture · every card type", "created": T, "updated": NOW, "mode": "agent",
            "messages": msgs, "ctx": {"used": 23800, "max": 65536, "compact": 49152}}


# ------------------------------------------------------------------------------------------------ stream
def build_stream(conv, total_seconds):
    """Reconstruct the last turn of `conv` as backend events. Returns (initial conv, events)."""
    msgs = conv["messages"]
    start = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    before = copy.deepcopy(msgs[:start + 1])
    turn = copy.deepcopy(msgs[start + 1:])
    for m in turn:
        m.pop("_v", None)
    cid = conv["id"]
    CPS = 280                                       # ~70 tok/s at ~4 chars per token

    def tool_secs(m):
        d = m["tool"].get("display") or {}
        try:
            return max(0.4, float(d.get("seconds")))
        except (TypeError, ValueError):
            return {"web_search": 4.0, "web_fetch": 3.0, "browser_open": 2.5, "python_sandbox": 2.0}.get(
                m["tool"]["name"], 0.8)

    stream_secs = sum((len(m.get("thinking") or "") + len(m.get("content") or "")) / CPS
                      for m in turn if m.get("kind") in (None, "", "plan"))
    tool_total = sum(tool_secs(m) for m in turn if m.get("kind") == "tool")
    gaps = sum(1 for m in turn if m.get("kind") == "tool")
    gap = max(0.6, (total_seconds - stream_secs - tool_total) / max(1, gaps))   # model time before each call

    events, t = [], 0.0
    n = len(before)
    live = list(before)
    ms = lambda: int(t * 1000)

    def emit(ev):
        events.append({"at": ms(), "ev": ev})

    def put(i, m):
        if i == len(live):
            live.append(m)
        else:
            live[i] = m
        emit({"t": "msgs", "conv": cid, "len": len(live), "upd": {str(i): m}})

    emit({"t": "threads", "threads": "__running__"})
    emit({"t": "status", "conv": cid, "text": "Searching offline Wikipedia and the web…"})
    t += 1.5
    used = (conv.get("ctx") or {}).get("used") or 20000
    ctx_max = (conv.get("ctx") or {}).get("max") or 65536
    for j, m in enumerate(turn):
        i = n + j
        k = m.get("kind")
        if k == "tool":
            t += gap
            emit({"t": "status", "conv": cid, "text": f"Running {m['tool']['name'].replace('_', ' ')}…"})
            pend = copy.deepcopy(m)
            pend["tool"].update(result=None, ok=None)
            d = pend["tool"].get("display")
            if d and d.get("type") == "terminal":
                d.update(output="", exit=None, seconds=None)
            put(i, pend)
            dur = tool_secs(m)
            if d and d.get("type") == "terminal" and (m["tool"]["display"].get("output") or "") and dur > 2:
                out = m["tool"]["display"]["output"].split("\n")
                for q in range(1, 4):           # output arrives in pieces
                    t += dur / 4
                    part = copy.deepcopy(pend)
                    part["tool"]["display"]["output"] = "\n".join(out[: len(out) * q // 4])
                    put(i, part)
                t += dur / 4
            else:
                t += dur
            put(i, m)
            used = min(int(ctx_max * 0.7), used + rng.randint(300, 1400))
            emit({"t": "ctx", "conv": cid, "used": used, "max": ctx_max, "compact": int(ctx_max * 0.75)})
        elif k in (None, "", "plan"):
            final = m
            for field in ("thinking", "content"):
                text = final.get(field) or ""
                if not text:
                    continue
                emit({"t": "status", "conv": cid, "text": "Thinking…" if field == "thinking" else "Writing…"})
                pos = 0
                while pos < len(text):
                    step = rng.randint(10, 18)
                    pos = min(len(text), pos + step)
                    if pos == step or (field == "content" and pos - step <= 0):   # first chunk: the message
                        part = copy.deepcopy(final)
                        part["streaming"] = True
                        if field == "thinking":
                            part["content"] = ""
                        part[field] = text[:pos]
                        part.pop("stats", None)
                        put(i, part)
                    else:                       # later chunks: compact text deltas, expanded by the player
                        emit({"t": "_delta", "conv": cid, "i": i, "field": field, "add": text[pos - step:pos]})
                    if pos % 5 == 0:
                        emit({"t": "tps", "conv": cid, "tps": round(rng.uniform(62, 76), 1)})
                    t += step / CPS
            done = copy.deepcopy(final)
            done["streaming"] = False
            put(i, done)
            t += 0.2
        else:
            t += 0.4
            put(i, m)
    emit({"t": "threads", "threads": "__done__"})
    emit({"t": "run_end", "conv": cid})
    return dict(conv, messages=before), events


# ------------------------------------------------------------------------------------------------ main
def thread_row(c, running=False):
    last = next((m for m in reversed(c["messages"]) if m.get("content") and not m.get("kind")), None)
    return {"id": c["id"], "title": c.get("title") or "New chat", "updated": c.get("updated") or c.get("created"),
            "running": running, "waiting": False, "run_started": None, "run_ended": None, "parent": None,
            "task_id": None, "mode": c.get("mode"),
            "snippet": (last or {}).get("content", "")[:90].replace("\n", " ")}


def main():
    convs, settings = load()
    OUT.mkdir(exist_ok=True)
    FILES.mkdir(exist_ok=True)
    model = settings.get("agent", {}).get("model", "model.gguf")
    state = {"settings": settings, "models": [model], "user": "demo", "openai": False, "openai_models": []}

    fx = fixture_conv(convs)
    build = find(convs, BUILD_CONV)
    side = [c for c in convs if c["messages"] and c["id"] != build["id"]][-6:]
    fixture = {"version": 1, "title": "Fixture: every card type", "state": state, "select": "fixture",
               "convs": [fx, build] + side, "events": []}
    fixture["state"]["threads"] = [thread_row(c) for c in fixture["convs"]]
    (OUT / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    stats = next(m["stats"] for m in reversed(build["messages"]) if m.get("stats"))
    initial, events = build_stream(build, stats["seconds"])
    run = {"version": 1, "title": f"Live build: {stats['steps']} steps, {stats['tool_calls']} tool calls",
           "state": dict(state, threads=[thread_row(build)]), "select": build["id"], "convs": [initial],
           "events": events}
    (OUT / "build-run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    for f in ("fixture.json", "build-run.json"):
        print(f, f"{(OUT / f).stat().st_size / 1024:.0f} KB")
    print("fixture messages:", len(fx["messages"]), "| build-run events:", len(events),
          f"| 1x duration {events[-1]['at'] / 1000:.0f} s")


def history():
    """replays/history.json: every saved chat, as stored (static; for reviewing how real conversations look)."""
    convs, settings = load()
    OUT.mkdir(exist_ok=True)
    model = settings.get("agent", {}).get("model", "model.gguf")
    keep = sorted((c for c in convs if c.get("messages")), key=lambda c: -(c.get("updated") or c.get("created") or 0))
    state = {"settings": settings, "models": [model], "user": "demo", "openai": False, "openai_models": [],
             "threads": [thread_row(c) for c in keep]}
    out = {"version": 1, "title": f"History: {len(keep)} saved chats", "state": state,
           "select": keep[0]["id"] if keep else None, "convs": keep, "events": []}
    (OUT / "history.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("history.json", f"{(OUT / 'history.json').stat().st_size / 1024:.0f} KB", len(keep), "chats")


if __name__ == "__main__":
    import sys
    history() if sys.argv[1:] == ["history"] else main()
