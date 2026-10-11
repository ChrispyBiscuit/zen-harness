"""Control arms for the thinking-library test: where does any gain come from?
Reuses results/judgment.json (plain + library-framed answers, same scenarios) and adds:
  self    : the model writes its OWN 3-6 step frame first (no library index)      -> "just planning" effect
  random  : a frame built from 2-4 RANDOM library entries                          -> does selection matter?
  nothink : plain answer with the model's built-in thinking OFF                    -> size of trained reasoning
Judged blind, both orders, by the same judge:  each arm vs plain,  and library-framed vs self / vs random.
python controls.py
"""
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import judgment as J  # noqa: E402

SELF_PROMPT = """THINKING FRAME. Before working on the request above, decide how to think about it. Write a \
reasoning frame for THIS request: 3-6 numbered steps that say what to establish first, where the approach branches \
(if … then …), the key judgment calls, and how to check the result. Concrete, under 120 words, no preamble. Do not \
answer the request itself."""

RANDOM_PROMPT = """THINKING FRAME. Before working on the request above, adapt these thinking tools to it:
{tools}
Write a reasoning frame for THIS request using them: 3-6 numbered steps (what to establish first, where the approach \
branches, the key judgment calls, how to check the result). Under 120 words, no preamble. Do not answer the request."""


def framed_answer(q, frame_text):
    block = "THINKING FRAME for this request:\n" + frame_text.strip() + "\n" + J.thinking.GUIDE
    return J.chat([{"role": "system", "content": J.SYSTEM},
                   {"role": "user", "content": q + f"\n\n<system-reminder>\n{block}\n</system-reminder>"}])


def pairwise(q, a_new, a_ref):
    """+1 new wins in both orders, -1 ref wins in both, 0 otherwise"""
    v1, w1, _ = J.judge(q, a_ref, a_new)       # new = "2"
    v2, w2, _ = J.judge(q, a_new, a_ref)       # new = "1"
    if v1 == "2" and v2 == "1":
        return 1, [w1, w2]
    if v1 == "1" and v2 == "2":
        return -1, [w1, w2]
    return 0, [w1, w2]


def main():
    t0 = time.time()
    base = json.load(open(os.path.join(J.OUT, "judgment.json"), encoding="utf-8"))
    rows = base["rows"]
    lib = J.thinking.entries()
    rng = random.Random(5)
    srv = J.start_server()
    try:
        for i, r in enumerate(rows):
            q = r["q"]
            sf, _ = J.chat([{"role": "system", "content": J.SYSTEM}, {"role": "user", "content": q},
                            {"role": "user", "content": SELF_PROMPT}], max_tokens=400, think=False)
            r["self_frame"] = sf
            r["self"], _ = framed_answer(q, sf)
            pick = rng.sample(lib, rng.randint(2, 4))
            tools = "\n".join(f"- {e['name']}: {e['form']}" for e in pick)
            rf, _ = J.chat([{"role": "system", "content": J.SYSTEM}, {"role": "user", "content": q},
                            {"role": "user", "content": RANDOM_PROMPT.format(tools=tools)}], max_tokens=400, think=False)
            r["random_use"], r["random_frame"] = [e["id"] for e in pick], rf
            r["random"], _ = framed_answer(q, rf + "\nDrawn from:\n" + tools)
            r["nothink"], _ = J.chat([{"role": "system", "content": J.SYSTEM}, {"role": "user", "content": q}],
                                     think=False)
            print(f"[{i + 1}/{len(rows)}] done ({time.time() - t0:.0f}s)", flush=True)
            json.dump({"partial": True, "rows": rows[:i + 1]}, open(os.path.join(J.OUT, "controls_partial.json"), "w",
                      encoding="utf-8"), indent=1, ensure_ascii=False)
    finally:
        srv.terminate()
    comps = {"self_vs_plain": ("self", "plain"), "random_vs_plain": ("random", "plain"),
             "nothink_vs_plain": ("nothink", "plain"), "library_vs_self": ("framed", "self"),
             "library_vs_random": ("framed", "random")}
    summary = {}
    for name, (a, b) in comps.items():
        tally = {1: 0, -1: 0, 0: 0}
        for r in rows:
            s, why = pairwise(r["q"], r[a], r[b])
            tally[s] += 1
            r.setdefault("judge", {})[name] = {"score": s, "why": why}
        summary[name] = {f"{a}_wins": tally[1], f"{b}_wins": tally[-1], "tie_or_split": tally[0]}
        print(name, summary[name], flush=True)
    json.dump({"summary": summary, "library_vs_plain": base["summary"], "rows": rows,
               "seconds": round(time.time() - t0)},
              open(os.path.join(J.OUT, "controls.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("SUMMARY", summary, flush=True)


if __name__ == "__main__":
    main()
