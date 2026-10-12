"""Replay a real failure point through the CURRENT harness: does the model take the right next steps?

Takes a chat at a chosen moment (its history up to a message, and the harness state rebuilt from its tool log up to
that time), copies the project to a sandbox (all paths in the history rewritten to the copy), and lets the model
continue for a few steps with today's harness. Then it scores what generically goes wrong when a small model loses
its place:

  redone      calls identical to a call that already succeeded before the cut (re-proving, re-adding data)
  reopened    edits to a test that had been resolved (failed, then passed) before the cut
  reruns      test runs with no app change since the last passing run
  progressed  whether the current checklist item got marked done

  python evals/context/replay_point.py --conv <id> --after-text "11 passed" --log-until 03:38:41 --dry-run
  python evals/context/replay_point.py --conv <id> --after-text "11 passed" --log-until 03:38:41 \\
         --base-url http://127.0.0.1:8099 --steps 12

--dry-run prints the rebuilt brief and work state (no model needed). A real run needs a model server that is free
(the app idle): --base-url attaches to it instead of starting another one.
"""
import argparse
import copy
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DATA = None                       # where the chat, its log and the settings are read from (--data); never written


def load_conv(conv_id):
    store = os.path.join(DATA, "conversations.json")
    data = json.load(open(store, encoding="utf-8"))
    convs = data.get("convs") if isinstance(data, dict) and "convs" in data else data
    if isinstance(convs, dict):
        return convs.get(conv_id)
    return next((c for c in convs if c.get("id") == conv_id), None)


def log_rows(conv_id, until=None):
    log = os.path.join(DATA, "agent_log.jsonl")
    rows, first = [], None
    for ln in open(log, encoding="utf-8", errors="replace"):
        if conv_id not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("conv") != conv_id or not d.get("t"):
            continue
        t = dt.datetime.strptime(d["t"], "%Y-%m-%d %H:%M:%S")
        first = first or t
        if until:
            h, m, s = (list(map(int, until.split(":"))) + [0, 0])[:3]
            cut = first.replace(hour=h, minute=m, second=s)
            if cut < first:
                cut += dt.timedelta(days=1)
            if t > cut:
                break
        rows.append(d)
    return rows


def project_folder(rows):
    for d in rows:
        p = str((d.get("args") or {}).get("path") or "")
        if "ZenBattery" in p or os.path.isabs(p):
            parts = p.replace("/", "\\").split("\\")
            for i in range(len(parts), 0, -1):
                cand = "\\".join(parts[:i])
                if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, "docs")):
                    return cand
    return None


def rewrite(obj, src, dst):
    """Every occurrence of the project path (plain and JSON-escaped) -> the sandbox copy."""
    s = json.dumps(obj, ensure_ascii=False)
    for a, b in ((src, dst), (src.replace("\\", "\\\\"), dst.replace("\\", "\\\\")),
                 (src.replace("\\", "\\\\\\\\"), dst.replace("\\", "\\\\\\\\")), (src.replace("\\", "/"), dst.replace("\\", "/"))):
        s = s.replace(a, b)
    return json.loads(s)


def rebuild_state(ctx, rows, conv_id=None):
    """Harness state (checklist, test history, obligations) as it was at the cut, from the tool log. The checklist
    comes from the chat's checklist history when it has one (progress= on other calls isn't in the tool log)."""
    import agent_tools as at
    import code_insight as ci
    import obligations
    saved = []
    if conv_id:
        try:
            p = os.path.join(DATA, "agent_attachments", conv_id, "archive", "trackers.jsonl")
            saved = [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
        except (OSError, ValueError):
            saved = []
    cut_at = rows[-1]["t"] if rows else ""
    for d in rows:
        name, args, res = d.get("tool"), d.get("args") or {}, str(d.get("result") or "")
        if not name:
            continue
        if saved:                                     # the checklist as it was just before this call
            before = [s for s in saved if s["at"] <= d["t"]]
            if before:
                ctx.tracker = copy.deepcopy(before[-1]["tracker"])
        if name == "update_tracker" and saved:
            continue
        if name == "update_tracker":
            try:
                at.t_update_tracker(copy.deepcopy(args), ctx)
            except Exception:
                pass
            continue
        obligations.record(ctx, name, args, res, ok_call=str(d.get("ok")) != "False", at=d["t"][11:])
        if name == "run_tests" or (name in ("powershell", "python_host") and "test" in json.dumps(args).lower()):
            p_, f_ = at.test_counts(res)
            if p_ or f_:
                h = at._hist(ctx)
                prev = h.get("last_tests")
                cur = {"passed": p_, "failed": f_, "t": d["t"][11:], "cmd": str(args.get("path") or name)[:80],
                       "failing": ci.failing_names(res)}
                h["last_tests"] = cur
                obligations.on_tests(ctx, prev, cur)
                if not f_:
                    at.mark_green(ctx)
    if saved:
        before = [s for s in saved if s["at"] <= cut_at]
        if before:
            ctx.tracker = copy.deepcopy(before[-1]["tracker"])


def score(before_rows, new_calls, resolved):
    ok_before = {(d.get("tool"), json.dumps(d.get("args") or {}, sort_keys=True)) for d in before_rows
                 if d.get("tool") and str(d.get("ok")) != "False" and not str(d.get("result") or "").startswith("Error")}
    redone = [c for c in new_calls if (c["name"], json.dumps(c["args"], sort_keys=True)) in ok_before
              and c["name"] not in ("read_file", "update_tracker", "list_dir")]
    reopened = [c for c in new_calls if c["name"] in ("edit_file", "write_file")
                and any(r.split("::")[-1] in json.dumps(c["args"]) for r in resolved)]
    reruns, app_changed = 0, True
    for c in new_calls:
        if c["name"] in ("edit_file", "write_file"):
            app_changed = True
        elif c["name"] == "run_tests":
            reruns += 0 if app_changed else 1
            app_changed = False
    return {"redone": len(redone), "reopened": len(reopened), "reruns_without_change": reruns,
            "redone_calls": [f"{c['name']} {json.dumps(c['args'])[:100]}" for c in redone][:6]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conv", required=True)
    ap.add_argument("--after-text", required=True, help="cut the history right after the first message containing it")
    ap.add_argument("--log-until", required=True, help="harness state from tool calls up to this time (HH:MM:SS)")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--base-url", help="an idle model server to attach to (else: dry run only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", help="the app's data folder holding conversations.json, agent_log.jsonl, settings.json "
                                   "(default: this install's)")
    a = ap.parse_args()
    global DATA
    try:
        import paths
        DATA = a.data or paths.data_dir()
    except ImportError:                               # an older layout without paths.py: data next to the code
        DATA = a.data or BACKEND
    # everything the replay writes (checklist, file history, logs) goes to a scratch data folder, never the app's
    os.environ["LWS_DATA_DIR"] = tempfile.mkdtemp(prefix="replay_data_")

    import agent_tools as at
    import agentic
    import obligations
    from agent_config import DEFAULT_AGENT

    conv = load_conv(a.conv)
    if not conv or not conv.get("agent_state"):
        raise SystemExit("conversation or its agent_state not found")
    hist = conv["agent_state"].get("history") or []
    cut = next((i for i, m in enumerate(hist) if a.after_text in json.dumps(m, ensure_ascii=False)), None)
    if cut is None:
        raise SystemExit(f"no message contains {a.after_text!r}")
    rows = log_rows(a.conv, a.log_until)
    src = project_folder(rows)
    if not src:
        raise SystemExit("couldn't find the project folder in the tool log")
    sandbox = os.path.join(tempfile.mkdtemp(prefix="replay_"), os.path.basename(src))
    shutil.copytree(src, sandbox, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    hist = rewrite(hist[:cut + 1], src, sandbox)
    rows = rewrite(rows, src, sandbox)

    cfg = dict(DEFAULT_AGENT)
    try:
        import paths
        st = json.load(open(os.path.join(DATA, "settings.json"), encoding="utf-8"))
        cfg.update(st.get("agent") or {})
    except Exception:
        pass
    cfg.update(roots=[os.path.dirname(sandbox)], max_steps=a.steps)
    if a.base_url:
        import llama_local
        llama_local.ensure = lambda *x, **k: a.base_url.rstrip("/")
        llama_local.running = lambda *x, **k: True
    calls = []

    def emit(kind, **kw):
        if kind == "tool_call":
            calls.append({"name": kw.get("name"), "args": kw.get("args") or {}})

    eng = agentic.AgentEngine(cfg, emit, ask=lambda r: "once", conv_id=f"replay-{a.conv[:8]}")
    eng.import_state(dict(conv["agent_state"], history=hist))
    eng.ctx.cwd = sandbox
    eng.ctx.tracker = None                            # rebuilt from the log below, saved in the sandbox only
    eng.ctx.tracker_path = os.path.join(os.path.dirname(sandbox), "CHECKLIST.md")
    rebuild_state(eng.ctx, rows, a.conv)
    eng.turn = {"steps": 0}
    print(f"sandbox: {sandbox}\ncut after message {cut} of {len(conv['agent_state']['history'])}\n")
    print(obligations.brief(eng.ctx) or "(no checklist)")
    if a.dry_run or not a.base_url:
        print("\n(dry run: no model call)")
        return
    resolved = list(obligations._state(eng.ctx)["resolved"])
    eng.run("Continue the task.")
    s = score(rows, calls, resolved)
    cur = at.tracker_current(eng.ctx.tracker) if eng.ctx.tracker else None
    s["current_item_after"] = cur[0] if cur else "all done"
    s["calls"] = [f"{c['name']} {json.dumps(c['args'])[:90]}" for c in calls]
    print(json.dumps(s, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
