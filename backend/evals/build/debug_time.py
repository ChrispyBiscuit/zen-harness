"""Where a build run's time went: docs, writing code, DEBUGGING, and after the suite went green — with every debug
episode broken out (what failed, how long, how many steps, what it touched). Debug time, not dev time, is what makes
small-model builds slow; this measures it from the app's own log.

  python evals/build/debug_time.py <chat id> [--until HH:MM] [<chat id> ...]

A debug episode starts at the first failing check (a test run with failures, or a project command that exited
non-zero) and ends at the next check that fully passes. Time is wall-clock between tool calls (thinking included).
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "agent_log.jsonl")
CODE_RX = re.compile(r"\.(py|js|ts|tsx|jsx|mjs|html?|css|go|rs|java|cs|cpp|c|h)$", re.I)
TEST_CMD_RX = re.compile(r"\b(pytest|unittest|test_\w*\.py|\w*_test\.py|smoke|e2e|npm test|node --test)\b", re.I)


def rows(conv):
    out = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if conv not in ln:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("conv") == conv and d.get("tool") and d.get("t"):
                d["_t"] = dt.datetime.strptime(d["t"], "%Y-%m-%d %H:%M:%S")
                out.append(d)
    return out


def verdict(d):
    """'pass' / 'fail' for a check (tests, or running the project's code), else None."""
    name, a, res = d["tool"], d.get("args") or {}, str(d.get("result") or "")
    if name == "run_tests":
        if res.startswith("tests: PASSED"):
            return "pass"
        return "fail" if res.startswith(("tests: FAILED", "tests: exit code", "tests (only")) else None
    if name in ("powershell", "python_host", "debug_run"):
        cmd = str(a.get("command") or a.get("code") or a.get("script") or a.get("path") or "")
        m = re.match(r"exit code (-?\d+)", res)
        if not m or not re.search(r"\bpython\b|\.py\b|node\b|npm\b", cmd + " " + name):
            return None
        if TEST_CMD_RX.search(cmd) or name == "debug_run" or int(m.group(1)) != 0:
            return "pass" if int(m.group(1)) == 0 else "fail"
    if name == "http_request" and res.startswith("Error"):
        return "fail"
    return None


def analyse(conv, until=None):
    rs = rows(conv)
    if until and rs:                                   # the first HH:MM after the start (a run can cross midnight)
        h, m = map(int, until.split(":"))
        cut = rs[0]["_t"].replace(hour=h, minute=m, second=59)
        if cut < rs[0]["_t"]:
            cut += dt.timedelta(days=1)
        rs = [r for r in rs if r["_t"] <= cut]
    if not rs:
        return None
    t0, tend = rs[0]["_t"], rs[-1]["_t"]
    first_code = next((r["_t"] for r in rs if r["tool"] in ("write_file", "edit_file")
                       and CODE_RX.search(str((r.get("args") or {}).get("path") or ""))), None)
    episodes, cur, last_green = [], None, None
    prev_t = t0
    for r in rs:
        v = verdict(r)
        gap = (r["_t"] - prev_t).total_seconds()
        prev_t = r["_t"]
        if cur is not None:
            cur["calls"] += 1
            cur["tools"][r["tool"]] = cur["tools"].get(r["tool"], 0) + 1
            p = str((r.get("args") or {}).get("path") or "")
            if r["tool"] in ("write_file", "edit_file") and p:
                cur["edited"].add(os.path.basename(p))
        if v == "fail" and cur is None:
            first = str(r.get("result") or "").splitlines()
            cur = {"start": r["_t"], "calls": 1, "tools": {r["tool"]: 1}, "edited": set(),
                   "what": (r["tool"] + ": " + " ".join(first[:2]))[:150]}
        elif v == "pass":
            last_green = r["_t"]
            if cur is not None:
                cur["end"] = r["_t"]
                episodes.append(cur)
                cur = None
    if cur is not None:
        cur["end"], cur["open"] = tend, True
        episodes.append(cur)
    mins = lambda a, b: round((b - a).total_seconds() / 60, 1)
    debug = sum(mins(e["start"], e["end"]) for e in episodes)
    working = None
    return {"conv": conv, "start": t0, "end": tend, "total": mins(t0, tend),
            "docs": mins(t0, first_code) if first_code else None,
            "debug": round(debug, 1), "episodes": episodes, "last_green": last_green,
            "after_green": mins(last_green, tend) if last_green else None, "calls": len(rs), "_working": working}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("convs", nargs="+")
    ap.add_argument("--until", help="ignore calls after this time of day (HH:MM, same day as the start)")
    a = ap.parse_args()
    for conv in a.convs:
        s = analyse(conv, a.until)
        if not s:
            print(f"{conv}: no tool calls in {LOG}")
            continue
        dev = round(s["total"] - (s["docs"] or 0) - s["debug"] - (s["after_green"] or 0), 1)
        print(f"\n{conv}  {s['start']:%H:%M} -> {s['end']:%H:%M}  ({s['total']} min, {s['calls']} tool calls)")
        print(f"  docs/plan before code : {s['docs']} min")
        print(f"  writing code          : {dev} min")
        print(f"  DEBUGGING             : {s['debug']} min in {len(s['episodes'])} episode(s)  "
              f"({round(100 * s['debug'] / max(s['total'], 0.1))}% of the run)")
        print(f"  after the last green  : {s['after_green']} min")
        for i, e in enumerate(s["episodes"], 1):
            top = ", ".join(f"{k} {v}" for k, v in sorted(e["tools"].items(), key=lambda x: -x[1])[:5])
            print(f"  [{i}] {e['start']:%H:%M}-{e['end']:%H:%M} {round((e['end'] - e['start']).total_seconds() / 60, 1):>5} min "
                  f"{e['calls']:>3} calls{' (still open)' if e.get('open') else ''} | {e['what']}")
            print(f"      tools: {top} | edited: {', '.join(sorted(e['edited'])) or '-'}")


if __name__ == "__main__":
    main()
