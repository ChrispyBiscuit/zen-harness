"""Analyse a battery run: how the harness handled each kind of request vs how it should have.

  venv\\Scripts\\python.exe evals\\battery\\analyze.py base1            -> runs/base1/analysis.md (+ printed summary)
  venv\\Scripts\\python.exe evals\\battery\\analyze.py base1 --vs base0 -> also a before/after table
"""
import argparse
import collections
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--vs", default="")
args = ap.parse_args()


def load(run):
    p = os.path.join(HERE, "runs", run, "results.jsonl")
    rows = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                rows[d["id"]] = d
    return list(rows.values())


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


rows = load(args.run)
n = len(rows)

# fairer expectations than the runner recorded (decided after reading answers, applied to every run alike):
#  - a clarifying question can be phrased without "?" ("Tell me: where, when, how many")
#  - "quick" for a task expected to be "standard" is fine (a tiny script or fix IS quick); only deeper-than-needed or
#    much shallower ("quick" for "deep") counts as a sizing miss
import re as _re
_ASK = _re.compile(r"\?|\b(tell me|let me know|which (one|file|folder)|what (would you|do you) (like|mean|want)|could you "
                   r"(clarify|share|tell)|please (share|provide|specify|tell)|need (a few|more) details)\b", _re.I)
for r in rows:
    e, ex = r.get("exp") or {}, r.get("expect") or {}
    if ex.get("ask"):
        e["ask_ok"] = "ask_user" in (r.get("tools") or []) or bool(_ASK.search((r.get("answer") or "")[-600:]))
        if r["cat"] == "ambig":          # its pass check used the narrow "?" test: same rule, wider question test
            changed = any(t in ("trash_path", "move_path", "write_file", "edit_file") for t in (r.get("tools") or []))
            r["pass"] = e["ask_ok"] and not changed
    if r["cat"] in ("current", "local"):
        # the background reference carries live web results: a cited answer from it is a looked-up answer
        cited = bool(_re.search(r"https?://|\]\(http|\[\d+\]", r.get("answer") or ""))
        web = any(t in ("web_search", "web_fetch", "browser_open", "browser_read", "gather") for t in (r.get("tools") or []))
        e["must_ok"] = web or cited
        if r["cat"] == "current" and not r["pass"] and cited and len(r.get("answer") or "") > 60:
            r["pass"] = True
    le = ((r.get("lean") or {}).get("lean") or {}).get("effort") or {}
    got, want = le.get("size"), ex.get("size")
    if got and want:
        order = {"quick": 0, "standard": 1, "deep": 2}
        e["size_ok"] = got not in order or want not in order or (order[got] <= order[want] and order[want] - order[got] < 2)
    r["exp"] = e
cats = sorted({r["cat"] for r in rows})
L = [f"# Battery analysis — {args.run}", "", f"{n} tasks recorded."]

# ---------------------------------------------------------------- headline
passed = sum(r["pass"] for r in rows)
EXP = ["route_ok", "time_ok", "must_ok", "forbid_ok", "drafts_ok", "calls_ok", "ask_ok", "size_ok"]
ideal = sum(1 for r in rows if r["pass"] and all((r.get("exp") or {}).get(k, True) for k in EXP))
secs = [r.get("secs", 0) for r in rows]
L += ["", "## Headline", "",
      f"- **Correct (automatic check): {passed}/{n} ({pct(passed, n)})**",
      f"- **Correct AND handled as it should be (route, time, tools, drafts, questions): {ideal}/{n} ({pct(ideal, n)})**",
      f"- time per task: median {st.median(secs):.0f}s, p90 {q(secs, .9):.0f}s, total {sum(secs) / 3600:.1f} h",
      f"- answers that mention the background search: {sum(r.get('mentions_search', False) for r in rows)}",
      f"- narration-only answers: {sum(r.get('narration_only', False) for r in rows)} · empty answers: "
      f"{sum(r.get('empty', False) for r in rows)} · engine errors: {sum(bool(r.get('error')) for r in rows)} · "
      f"hit the hard time limit: {sum(r.get('timed_out', False) for r in rows)}",
      f"- redrafts: {sum(len(r.get('retracts') or []) for r in rows)} in {sum(1 for r in rows if r.get('retracts'))} tasks"]

# ---------------------------------------------------------------- by category
L += ["", "## By category", "",
      "| category | n | correct | ideal | median s | p90 s | budget s | over budget | first answer s (median) | "
      "drafts/task | calls/task | route miss | must miss | size miss |", "|" + "---|" * 14]
for c in cats:
    rs = [r for r in rows if r["cat"] == c]
    m = len(rs)
    e = lambda k: sum(1 for r in rs if not (r.get("exp") or {}).get(k, True))
    fa = [r["first_answer_s"] for r in rs if r.get("first_answer_s")]
    bud = rs[0].get("expect", {}).get("max_secs", 0)
    L.append(f"| {c} | {m} | {pct(sum(r['pass'] for r in rs), m)} | "
             f"{pct(sum(1 for r in rs if r['pass'] and all((r.get('exp') or {}).get(k, True) for k in EXP)), m)} | "
             f"{st.median([r.get('secs', 0) for r in rs]):.0f} | {q([r.get('secs', 0) for r in rs], .9):.0f} | {bud} | "
             f"{e('time_ok')} | {st.median(fa) if fa else '-'} | "
             f"{sum(len(r.get('retracts') or []) for r in rs) / m:.2f} | {sum(r.get('tool_calls', 0) for r in rs) / m:.1f} | "
             f"{e('route_ok')} | {e('must_ok')} | {e('size_ok')} |")

# ---------------------------------------------------------------- expectation misses
L += ["", "## Expectation misses (all categories)", ""]
for k in EXP:
    miss = [r for r in rows if not (r.get("exp") or {}).get(k, True)]
    by = collections.Counter(r["cat"] for r in miss)
    L.append(f"- **{k}**: {len(miss)} — " + ", ".join(f"{c} {v}" for c, v in by.most_common()))

# ---------------------------------------------------------------- redrafts
rc = collections.Counter(x for r in rows for x in (r.get("retracts") or []))
L += ["", "## Why answers were redrafted", ""] + [f"- {v} × {k}" for k, v in rc.most_common()]
rcc = collections.defaultdict(collections.Counter)
for r in rows:
    for x in r.get("retracts") or []:
        rcc[x][r["cat"]] += 1
L += [""] + [f"  - {k}: " + ", ".join(f"{c} {v}" for c, v in cc.most_common(6)) for k, cc in rcc.items()]

# ---------------------------------------------------------------- lean decisions
sizes = collections.Counter()
for r in rows:
    le = (r.get("lean") or {}).get("lean") or {}
    eff = le.get("effort") or {}
    want = (r.get("expect") or {}).get("size")
    got = eff.get("size") if le else ("answered-directly" if (r.get("lean") or {}).get("answered_lean") else "none")
    sizes[(want, got)] += 1
L += ["", "## Router sizing (expected → chosen)", "", "| expected | chosen | n |", "|---|---|---|"]
L += [f"| {a} | {b} | {v} |" for (a, b), v in sorted(sizes.items(), key=lambda x: (str(x[0][0]), -x[1]))]
fcl = collections.Counter(((r.get("lean") or {}).get("lean") or {}).get("fact_check") for r in rows)
L += ["", "Router fact_check choices: " + ", ".join(f"{k}: {v}" for k, v in fcl.most_common())]
direct = collections.Counter((r["cat"], (r.get("lean") or {}).get("answered_lean", False)) for r in rows)
L += ["", "Answered by the router directly, by category: " +
      ", ".join(f"{c} {direct[(c, True)]}/{direct[(c, True)] + direct[(c, False)]}" for c in cats)]

# ---------------------------------------------------------------- tools
tc = collections.Counter(t for r in rows for t in (r.get("tools") or []))
L += ["", "## Tool use", "", ", ".join(f"{k} {v}" for k, v in tc.most_common(30)),
      f"", f"Failed tool calls: {sum(r.get('failed_calls', 0) for r in rows)} · blocked repeats: "
      f"{sum(r.get('blocked', 0) for r in rows)} · loop warnings: {sum(r.get('warnings', 0) for r in rows)}"]
heavy = sorted(rows, key=lambda r: -r.get("tool_calls", 0))[:15]
L += ["", "Most tool calls:"] + [f"- {r['id']} ({r.get('tool_calls')} calls, {r.get('secs')}s): {r.get('prompt', '')[:90]}"
                                 for r in heavy]

# ---------------------------------------------------------------- slowest vs budget
over = sorted(rows, key=lambda r: -(r.get("secs", 0) / max(1, (r.get("expect") or {}).get("max_secs", 1))))[:20]
L += ["", "## Furthest over their time budget", ""] + [
    f"- {r['id']}: {r.get('secs')}s (budget {(r.get('expect') or {}).get('max_secs')}s), {r.get('tool_calls')} calls, "
    f"drafts {len(r.get('retracts') or [])} — {r.get('prompt', '')[:80]}" for r in over]

# ---------------------------------------------------------------- failures (sampled per category)
L += ["", "## Failures by category (up to 6 each)", ""]
for c in cats:
    fails = [r for r in rows if r["cat"] == c and not r["pass"]]
    if not fails:
        continue
    L.append(f"### {c} — {len(fails)} failed")
    for r in fails[:6]:
        L.append(f"- **{r['id']}** {r.get('detail', '')[:120]} · tools {r.get('tools', [])[:8]} · "
                 f"{r.get('secs')}s\n  - Q: {r.get('prompt', '')[:140]}\n  - A: {(r.get('answer') or '')[:300].replace(chr(10), ' ')}")
    L.append("")

# ---------------------------------------------------------------- search mentions, errors
sm = [r for r in rows if r.get("mentions_search")]
if sm:
    L += ["## Answers mentioning the background search", ""] + [f"- {r['id']}: {(r.get('answer') or '')[:200]}" for r in sm[:15]]
er = [r for r in rows if r.get("error")]
if er:
    errs = collections.Counter((r.get("error") or "").strip().splitlines()[-1][:120] for r in er)
    L += ["", "## Engine errors", ""] + [f"- {v} × {k}" for k, v in errs.most_common(10)]

# ---------------------------------------------------------------- versus another run
if args.vs:
    old = {r["id"]: r for r in load(args.vs)}
    both = [r for r in rows if r["id"] in old]
    L += ["", f"## Versus {args.vs} ({len(both)} tasks in both)", "",
          "| category | correct before | after | median s before | after | drafts/task before | after |", "|---|---|---|---|---|---|---|"]
    for c in cats:
        a = [old[r["id"]] for r in both if r["cat"] == c]
        b = [r for r in both if r["cat"] == c]
        if not a:
            continue
        L.append(f"| {c} | {pct(sum(x['pass'] for x in a), len(a))} | {pct(sum(x['pass'] for x in b), len(b))} | "
                 f"{st.median([x.get('secs', 0) for x in a]):.0f} | {st.median([x.get('secs', 0) for x in b]):.0f} | "
                 f"{sum(len(x.get('retracts') or []) for x in a) / len(a):.2f} | "
                 f"{sum(len(x.get('retracts') or []) for x in b) / len(b):.2f} |")
    flips = [(r["id"], old[r["id"]]["pass"], r["pass"]) for r in both if old[r["id"]]["pass"] != r["pass"]]
    L += ["", f"Pass/fail flips: {sum(1 for f in flips if f[2])} fixed, {sum(1 for f in flips if not f[2])} broke"]
    L += [f"- {i}: {'❌→✅' if b else '✅→❌'}" for i, a, b in flips[:60]]

out = os.path.join(HERE, "runs", args.run, "analysis.md")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L[:40]))
print(f"\n→ {out}")
