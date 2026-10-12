"""Where a run's time went, from the per-step generation log (context_memory gen.jsonl) — numbers that don't depend
on the GPU's speed that day (tokens) next to the ones that do (seconds).

  python evals/build/step_report.py <chat id> [<chat id> ...]      several ids: one column each, for a delta
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import context_memory as cm  # noqa: E402


def summary(conv):
    rows = [r for r in cm.gen_log(conv) if r.get("role") in (None, "main")]
    if not rows:
        return None
    s = lambda k: sum(r.get(k) or 0 for r in rows)
    by_tool = {}
    for r in rows:
        key = ",".join(sorted(set(r.get("calls") or []))) or "(no tool call)"
        b = by_tool.setdefault(key, [0, 0, 0.0])
        b[0] += 1
        b[1] += (r.get("think_tok") or 0)
        b[2] += r.get("wall_s") or 0
    gen_tps = s("gen_tok") / (s("gen_ms") / 1000) if s("gen_ms") else 0
    read_tps = s("read_tok") / (s("read_ms") / 1000) if s("read_ms") else 0
    return {"steps": len(rows), "minutes": round(s("wall_s") / 60, 1),
            "think_tok": s("think_tok"), "text_tok": s("text_tok"), "call_tok": s("call_tok"),
            "read_tok (not cached)": s("read_tok"), "cached_tok": s("cached_tok"),
            "gen tok/s": round(gen_tps, 1), "read tok/s": round(read_tps, 1),
            "think share of output": f"{100 * s('think_tok') // max(1, s('think_tok') + s('text_tok') + s('call_tok'))}%",
            "cut / truncated steps": sum(1 for r in rows if r.get("cut") or r.get("finish") == "length"),
            "_by_tool": by_tool}


def main():
    ids = sys.argv[1:]
    if not ids:
        raise SystemExit(__doc__)
    sums = [(i, summary(i)) for i in ids]
    keys = [k for k in next((s for _, s in sums if s), {}) if not k.startswith("_")]
    print(f"{'':26}" + "".join(f"{i[:10]:>14}" for i in ids))
    for k in keys:
        print(f"{k:26}" + "".join(f"{(s or {}).get(k, '-')!s:>14}" for _, s in sums))
    for i, s in sums:
        if not s:
            print(f"\n{i}: no gen.jsonl (a run from before step logging)")
            continue
        print(f"\n{i}: steps by what they called (count, thinking tokens, minutes)")
        for k, (n, th, w) in sorted(s["_by_tool"].items(), key=lambda x: -x[1][2])[:15]:
            print(f"  {k[:40]:40} {n:4} {th:8} {w / 60:7.1f}")


if __name__ == "__main__":
    main()
