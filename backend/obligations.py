"""Obligations: progress as harness-owned state, not something the model reconstructs from its conversation.

A small model loses track of WHERE it is: after a compaction (or just a long debug episode) it re-proves what it
already proved, re-fixes what it already fixed, and retries a whole sequence when one call failed. bonsai9 fixed a
test after 84 minutes, then spent its next steps re-writing a "real run" script, re-adding expenses and re-running
checks. The checklist said where it was; nothing said what was ESTABLISHED, what was RESOLVED, and what exactly had
just failed. This module keeps that from what actually happened, and turns it into a brief rebuilt every step:

  NOW          the current checklist item and its done-when
  ESTABLISHED  items marked done, each with the evidence that closed it (a test run, a command's output) — don't redo
  RESOLVED     tests that failed and pass now (when, after which change) — don't re-fix
  STALE        a done item whose own files changed since its evidence — re-check that one, once
  LAST CHECK   the latest observation on the current item; a failure is ONE narrow retry, not a restart
  UNCHANGED    observations without new evidence; the engine's recovery policy decides what to do

Evidence goes on the checklist item itself (saved with the checklist). An item's files are the app files edited while
it was the current item; a later edit to one of them makes its evidence stale. Scratch scripts never count.
"""
import hashlib
import os
import re
import time

OBS_KEEP = 16
CHECK_TOOLS = ("run_tests", "powershell", "python_host", "debug_run", "http_request", "bg_start", "bg_output")
EDIT_TOOLS = ("write_file", "edit_file")
_FAIL_LINE = re.compile(r"(Error|Exception|error:|FAILED|refused|timed out|not found|denied|invalid|exit code [1-9])",
                        re.I)


def _state(c):
    import agent_tools as at
    return at._hist(c).setdefault("obl", {"obs": [], "item_files": {}, "resolved": {}, "stall": 0, "seen": {}})


def _save(c):
    try:
        import agent_tools as at
        at._save_filehist(c)                          # with the file history: survives an app restart
    except Exception:
        pass


def _md5(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""


def _current(c):
    import agent_tools as at
    t = getattr(c, "tracker", None)
    return at.tracker_current(t) if t else None


def _item(t, iid):
    """The checklist dict for '2' or '2.3' (None if it doesn't exist)."""
    try:
        parts = str(iid).split(".")
        m = t["milestones"][int(parts[0]) - 1]
        return m if len(parts) == 1 else m["subtasks"][int(parts[1]) - 1]
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def verdict(tool, args, text):
    """'pass' / 'fail' for an observation that checks something, else None."""
    t = str(text or "")
    a = args if isinstance(args, dict) else {}
    if tool == "run_tests":
        return "pass" if t.startswith("tests: PASSED") else "fail" if t.startswith(("tests: FAILED", "tests (only",
                                                                                     "tests: exit code")) else None
    if tool in ("powershell", "python_host", "debug_run"):
        m = re.match(r"(?:exit code|still running)\s*(-?\d+)?", t)
        cmd = str(a.get("command") or a.get("code") or a.get("script") or a.get("path") or "")
        if not m or not re.search(r"\bpython\b|\.py\b|\bnode\b|\bnpm\b|\bcurl", cmd + " " + tool):
            return None
        return "pass" if (m.group(1) or "") == "0" else "fail"
    if tool == "http_request":
        if t.startswith("Error"):
            return "fail"
        m = re.search(r"HTTP (\d{3})", t[:200])
        return ("pass" if m.group(1)[0] in "23" else "fail") if m else None
    if tool == "bg_start":
        return "fail" if t.startswith("Error") or "not listening" in t[:300] else "pass"
    return None


_NOISE = re.compile(r"\s*\((?:cwd: [^)]*|[\d.]+s, -m [^)]*|[\d.]+s, [^)]*)\)")


def _line(text, ok):
    lines = [_NOISE.sub("", ln).strip() for ln in str(text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln]
    if not ok:
        hit = next((ln for ln in lines[1:] if _FAIL_LINE.search(ln) and not ln.startswith("(cwd")), None)
        if hit:
            return (lines[0][:80] + " — " + hit)[:220] if lines else hit[:220]
    return (lines[0] if lines else "")[:220]


def is_app_file(path):
    try:
        import agent_tools as at
        return at._code_path(path) and at._part_of_app(path)
    except Exception:
        return False


def record(c, tool, args, text, ok_call=True, at=None):
    """After every tool call: checks become observations of the current item, app edits become its files (and make
    other done items' evidence stale), and the stall counter moves."""
    if not getattr(c, "tracker", None):
        return
    st = _state(c)
    cur = _current(c)
    cid = cur[0] if cur else ""
    v = verdict(tool, args, text) if ok_call else "fail"
    if v and tool in CHECK_TOOLS:
        ln = _line(text, v == "pass")
        st["obs"].append({"t": at or time.strftime("%H:%M:%S"), "tool": tool, "ok": v == "pass", "line": ln,
                          "item": cid})
        del st["obs"][:-OBS_KEEP]
        key = f"{cid}|{v}|{re.sub(r'[0-9.:]+', '#', ln)[:120]}"
        seen = st["seen"].setdefault(cid, [])
        if key in seen:
            st["stall"] += 1                          # the same outcome again: no new evidence
        else:
            seen.append(key)
            del seen[:-40]
            st["stall"] = 0
        return
    if tool in EDIT_TOOLS and isinstance(args, dict) and args.get("path"):
        p = os.path.abspath(str(args["path"]))
        if is_app_file(p):
            files = st["item_files"].setdefault(cid, [])
            if p not in files:
                files.append(p)
            _mark_stale(c, p, except_item=cid)
            st["stall"] = 0  # the evidence store records an edit; recovery is decided by RunPolicy
            return
    st["stall"] += 1


def _mark_stale(c, path, except_item=""):
    import agent_tools as at
    t = c.tracker
    for iid, _text, status, _ms in at.tracker_items(t):
        it = _item(t, iid)
        ev = (it or {}).get("evidence")
        if iid == except_item or status != "done" or not ev or path not in (ev.get("files") or {}):
            continue
        if ev["files"][path] != _md5(path) and not ev.get("stale"):
            ev["stale"] = f"{os.path.basename(path)} changed at {time.strftime('%H:%M')} after this was verified"


def on_done(c, t, iid, given=""):
    """An item was just marked done: attach its evidence (what was given, else the latest passing check on it,
    else the latest passing check) and the files its own work changed."""
    it = _item(t, iid)
    if it is None or it.get("evidence"):
        return
    st = _state(c)
    obs = st["obs"]
    best = next((o for o in reversed(obs) if o["ok"] and o.get("item") == iid), None) or \
        next((o for o in reversed(obs) if o["ok"]), None)
    what = str(given).strip()[:220] or (f"{best['line']} ({best['t']})" if best else "marked done (no check recorded)")
    files = {p: _md5(p) for p in st["item_files"].get(iid, []) + st["item_files"].get(iid.split(".")[0], [])}
    it["evidence"] = {"what": what, "t": time.strftime("%H:%M"), "files": files}
    st["stall"] = 0
    _save(c)


def on_tests(c, prev, cur):
    """A full test run: tests that failed before and pass now are RESOLVED (kept, pinned to the timeline)."""
    if not prev or not cur:
        return
    st = _state(c)
    now_failing = set(cur.get("failing") or [])
    for name in set(prev.get("failing") or []) - now_failing:
        if cur.get("passed") and name not in st["resolved"]:
            first = next((o["t"] for o in st["obs"] if not o["ok"] and name.split("::")[-1] in o["line"]), "")
            st["resolved"][name] = {"t": time.strftime("%H:%M"), "first_fail": first or prev.get("t", "")[:5]}
            try:
                import context_memory as cm
                cm.add_event(getattr(c, "conv_id", None), "resolved",
                             f"{name.split('::')[-1]} passes now (failing since {st['resolved'][name]['first_fail']})")
            except Exception:
                pass
    for name in now_failing:
        st["resolved"].pop(name, None)                # it broke again: not resolved any more
    _save(c)


def reopen_note(c, path, new_text):
    """An edit inside a resolved test (or the function it checks) while it passes: needs a current failing
    observation, else it is reopening settled work."""
    st = _state(c)
    if not st["resolved"] or not str(path).endswith(".py"):
        return ""
    import agent_tools as at
    lt = at._hist(c).get("last_tests") or {}
    try:
        content = open(path, encoding="utf-8", errors="replace").read()
        idx = content.find(str(new_text or "")[:200]) if new_text else -1
        if idx < 0:
            return ""
        line = content[:idx].count("\n") + 1
        import repomap
        fn = None
        for d in repomap.outline(path):
            if d.get("kind") in ("function", "method") and d["line"] <= line:
                fn = d["name"]
    except Exception:
        return ""
    hit = next((n for n in st["resolved"] if fn and n.split("::")[-1] == fn), None)
    if not hit or hit in (lt.get("failing") or []):
        return ""
    r = st["resolved"][hit]
    return (f"\nNote: {fn} was RESOLVED at {r['t']} (it passes). Changing it again needs a current failing "
            f"observation; if nothing fails now, this reopens settled work.")


def brief(c):
    """The execution brief, rebuilt from state every step ('' with no checklist)."""
    import agent_tools as at
    t = getattr(c, "tracker", None)
    if not t:
        return ""
    st = _state(c)
    cur = _current(c)
    rows = []
    if cur:
        ms = _item(t, cur[0].split(".")[0]) or {}
        dw = ms.get("done_when") or ""
        rows.append(f"NOW: {cur[0]} \"{cur[1][:140]}\"" + (f" — done when: {dw[:200]}" if dw else ""))
    else:
        rows.append("NOW: every checklist item is done — write the final answer (or add the items still missing).")
    est, stale = [], []
    for iid, text, status, ms in at.tracker_items(t):
        it = _item(t, iid) or {}
        ev = it.get("evidence")
        if status != "done" or not ev:
            continue
        (stale if ev.get("stale") else est).append(
            f"{iid} {text[:70]} — {ev['stale'] if ev.get('stale') else ev['what'][:150]}")
    if est:
        rows.append("ESTABLISHED (done, with the evidence — don't redo): " + "; ".join(est[-6:]))
    if st["resolved"]:
        rows.append("RESOLVED (passing now — don't re-fix without a new failure): " + "; ".join(
            f"{n.split('::')[-1]} (failing since {r['first_fail']}, passes since {r['t']})"
            for n, r in list(st["resolved"].items())[-4:]))
    if stale:
        rows.append("STALE (its own files changed after it was verified — re-check once): " + "; ".join(stale[-4:]))
    if cur:                                           # a pending item whose deliverable already exists
        import re as _re
        m = _re.search(r"\b([\w-]+\.(?:md|txt|py|json|html|csv))\b", cur[1])
        hit = next((p for p in (getattr(c, "changed", None) or {}) if m
                    and os.path.basename(p).lower() == m.group(1).lower() and os.path.isfile(p)), None)
        if hit:
            rows.append(f"{os.path.basename(hit)} already exists ({sum(1 for _ in open(hit, encoding='utf-8', errors='replace'))} "
                        f"lines): if it is complete, mark {cur[0]} done now instead of redoing it.")
    mine = [o for o in st["obs"] if cur and o.get("item") == cur[0]]
    if mine:
        o = mine[-1]
        rows.append(f"LAST CHECK on {cur[0]}: {o['t']} {'passed' if o['ok'] else 'FAILED'} — {o['line']}"
                    + ("" if o["ok"] else " → fix that one thing and retry that one step; the steps before it "
                                          "already worked, don't restart the sequence."))
    if cur and st['stall']:
        rows.append(f"NO NEW EVIDENCE: {st['stall']} unchanged observations on {cur[0]}.")
    return "BRIEF (kept by the app from what actually happened; rebuilt every step):\n" + "\n".join(rows)
