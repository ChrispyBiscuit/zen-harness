"""Code graph P2/P4 A/B (plans/code-graph-prd.md): the 20 multi-file "where in the code is X" questions of the
context-pack benchmark (gold_zen.jsonl, the backend pinned at 98af6e9), answered by the local agent twice:
  off  no code_graph tool, no broken-reference check (the harness before the code graph)
  on   code_graph offered on code work, the check on (as shipped)
Same model, settings and prompt; order alternates per question. Records tool calls (all, and search/read ones),
time, tokens, and accuracy: the share of each question's gold pieces (function, or file when the gold names no
function) that the answer names.

  venv\\Scripts\\python.exe evals\\code_graph\\run_ab.py --run ab1 [--limit 2] [--cap 600]
  venv\\Scripts\\python.exe evals\\code_graph\\run_ab.py --run ab1 --report
Resumes: (question, variant) pairs already in results.jsonl are skipped.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--run", default="ab1")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--cap", type=int, default=600, help="seconds per answer")
ap.add_argument("--settings", default=os.path.expanduser(r"~\programs(x86)\Zazen\settings.json"))
ap.add_argument("--report", action="store_true")
args = ap.parse_args()
RUN = os.path.join(HERE, "runs", args.run)
os.makedirs(RUN, exist_ok=True)
OUT = os.path.join(RUN, "results.jsonl")
SEARCHY = {"search_text", "read_file", "list_dir", "find_files", "trace_symbol", "focus_pack", "code_graph",
           "search_documents", "powershell", "repo_map"}


def gold():
    with open(os.path.join(BACKEND, "evals", "context_pack", "gold_zen.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def score(answer, g):
    """Share of gold pieces the answer names: a function by its last name part, or the file when no function."""
    a = answer.lower()
    hits, files = 0, set()
    for piece in g["gold"]:
        sym = (piece.get("symbol") or "").split(".")[-1].lower()
        name = sym if sym and sym != os.path.splitext(piece["file"])[0].lower() else ""
        hit = (re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", a) is not None) if name else \
            (piece["file"].lower() in a)
        hits += hit
        files.add(piece["file"])
    f_hits = sum(1 for f in files if f.lower() in a or os.path.splitext(f)[0].lower() in a)
    return round(hits / len(g["gold"]), 3), round(f_hits / len(files), 3)


def report():
    rows = [json.loads(l) for l in open(OUT, encoding="utf-8")] if os.path.exists(OUT) else []
    by = {v: [r for r in rows if r["variant"] == v] for v in ("off", "on")}
    both = {r["id"] for r in by["off"]} & {r["id"] for r in by["on"]}
    print(f"{len(both)} questions answered in both variants\n")
    print(f"{'':10}{'pieces':>8}{'files':>8}{'calls':>8}{'search/read':>13}{'code_graph':>12}{'minutes':>9}{'timeouts':>10}")
    for v in ("off", "on"):
        rs = [r for r in by[v] if r["id"] in both]
        if not rs:
            continue
        n = len(rs)
        print(f"{v:10}{sum(r['acc'] for r in rs) / n:8.0%}{sum(r['files_acc'] for r in rs) / n:8.0%}"
              f"{sum(r['calls'] for r in rs) / n:8.1f}{sum(r['search_calls'] for r in rs) / n:13.1f}"
              f"{sum(r['code_graph_calls'] for r in rs) / n:12.1f}{sum(r['secs'] for r in rs) / n / 60:9.1f}"
              f"{sum(r['timed_out'] for r in rs):10d}")
    print("\nper question (off -> on): pieces, calls, minutes")
    for q in sorted(both):
        o = next(r for r in by["off"] if r["id"] == q)
        n = next(r for r in by["on"] if r["id"] == q)
        print(f"  {q}: {o['acc']:.0%} -> {n['acc']:.0%}   {o['calls']} -> {n['calls']}   "
              f"{o['secs'] / 60:.1f} -> {n['secs'] / 60:.1f}" + (f"   (code_graph x{n['code_graph_calls']})"
                                                                 if n["code_graph_calls"] else ""))


if args.report:
    report()
    sys.exit(0)

os.environ["LWS_DATA_DIR"] = os.path.join(RUN, "data")
os.makedirs(os.environ["LWS_DATA_DIR"], exist_ok=True)
for k in ("LWS_NO_TELEGRAM", "LWS_NO_SCHEDULER", "LWS_NO_INDEXER"):
    os.environ[k] = "1"
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "evals", "context_pack"))
import agent_tools as at  # noqa: E402
import agentic  # noqa: E402
import run as cp  # noqa: E402

FULL_BUILTIN = list(at.BUILTIN)
FULL_GROUPS = [(g, b, list(ns)) for g, b, ns in at.TOOL_GROUPS]


def set_variant(v):
    """off: the code_graph tool removed from the tool table and its groups; the write check off (cfg)."""
    if v == "off":
        at.BUILTIN[:] = [t for t in FULL_BUILTIN if t[0] != "code_graph"]
        at.TOOL_GROUPS[:] = [(g, b, [n for n in ns if n != "code_graph"]) for g, b, ns in FULL_GROUPS]
    else:
        at.BUILTIN[:] = FULL_BUILTIN
        at.TOOL_GROUPS[:] = [(g, b, list(ns)) for g, b, ns in FULL_GROUPS]


def base_cfg(ws):
    from agent_config import DEFAULT_AGENT
    with open(args.settings, encoding="utf-8") as f:
        s = json.load(f)
    c = dict(DEFAULT_AGENT, **(s.get("agent") or {}))
    c.update(roots=[ws], fanout=dict(c.get("fanout") or {}, enabled=False), hybrid={"enabled": False},
             advisor={"enabled": False}, mcp_servers=[], screen_control=False, delegation=False, plan_mode=False,
             file_index=False, allow_rules=[], memory_recall=False, playbooks=False, lean_reference=False)
    return c


def ask(g, root):
    return (f"{g['question']}\n\nAnswer from the code in {root} (read it; don't change anything). Name the files and "
            f"the functions or classes involved.")


def run_one(g, variant):
    set_variant(variant)
    cp.materialize("zen")
    ws = os.path.join(RUN, "ws", variant, g["id"])
    shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(cp.CORPORA["zen"]["root"], os.path.join(ws, "backend"))
    root = os.path.join(ws, "backend")
    cfg = base_cfg(ws)
    cfg["code_graph_check"] = variant == "on"
    calls, by_id = [], {}
    t0 = time.time()
    deadline = t0 + args.cap

    def emit(kind, /, **d):
        if kind == "tool_call":
            c = {"name": d.get("name"), "ok": None, "t": round(time.time() - t0, 1)}
            by_id[d.get("id")] = c
            calls.append(c)
        elif kind == "tool_result" and d.get("id") in by_id:
            by_id[d["id"]]["ok"] = bool(d.get("ok"))

    conv = f"ab-{variant}-{g['id']}"
    e = agentic.AgentEngine(cfg, emit, ask=lambda req: "deny", should_stop=lambda: time.time() > deadline,
                            conv_id=conv, title=conv)
    answer, err = "", ""
    try:
        e.prepare()
        answer = e.run(ask(g, root)) or ""
    except Exception:
        err = traceback.format_exc()[-800:]
    secs = time.time() - t0
    acc, facc = score(answer, g)
    names = [c["name"] for c in calls]
    tu = e.turn or {}
    return {"id": g["id"], "variant": variant, "acc": acc, "files_acc": facc, "calls": len(calls),
            "search_calls": sum(1 for n in names if n in SEARCHY), "code_graph_calls": names.count("code_graph"),
            "failed_calls": sum(1 for c in calls if c["ok"] is False), "secs": round(secs, 1),
            "timed_out": time.time() > deadline, "tools": names, "prompt_tokens": tu.get("prompt_tokens", 0),
            "completion_tokens": tu.get("completion_tokens", 0), "answer": answer[:3000], "error": err}


def main():
    done = set()
    if os.path.exists(OUT):
        done = {(json.loads(l)["id"], json.loads(l)["variant"]) for l in open(OUT, encoding="utf-8") if l.strip()}
    qs = gold()[:args.limit] if args.limit else gold()
    for i, g in enumerate(qs):
        order = ("off", "on") if i % 2 == 0 else ("on", "off")
        for v in order:
            if (g["id"], v) in done:
                continue
            r = run_one(g, v)
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"[{g['id']} {v:3}] pieces {r['acc']:.0%} files {r['files_acc']:.0%} calls {r['calls']} "
                  f"(search/read {r['search_calls']}, code_graph {r['code_graph_calls']}) {r['secs'] / 60:.1f} min"
                  + (" TIMEOUT" if r["timed_out"] else "") + (" ERROR" if r["error"] else ""), flush=True)


if __name__ == "__main__":
    main()
