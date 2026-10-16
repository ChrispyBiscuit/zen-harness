"""Round 4: the second round without its flaws. The first pack uses the whole budget; the model may say nothing is
missing (the pack stays); otherwise what it names replaces the LOWEST-ranked slices instead of a fixed 60/40 split;
it gets up to `--rounds` asks; the prompt lists the project's files and names that match nothing are dropped.

    venv\\Scripts\\python.exe evals\\context_pack\\adaptive_rounds.py --url http://127.0.0.1:8140 --rounds 2 3
Reuses the embeddings saved by second_round.py (no re-indexing).
"""
import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import context_pack as cp
import run
from second_round import covered, embed

PROMPT = ("You are helping find the code needed to answer a question about a codebase. Below are the question, the "
          "project's files, and the code found so far.\n\nQUESTION:\n{q}\n\nPROJECT FILES: {files}\n\n"
          "CODE FOUND SO FAR:\n{pack}\n\n"
          "Is code that is needed to answer the question still missing? Only code in the project files counts. "
          "Name up to 6 missing items: exact function, method (Class.method) or class names, or short search phrases "
          "for code you expect but cannot name. Do not list code already shown above. If nothing important is "
          'missing, reply {{"missing": []}}. Reply with JSON only: {{"missing": ["name or phrase", ...]}}')


def ask(url, q, files, pack):
    body = {"messages": [{"role": "user", "content": PROMPT.format(q=q, files=", ".join(files), pack=pack)}],
            "max_tokens": 300, "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    text = re.sub(r"<think>.*?</think>", "", r["choices"][0]["message"].get("content") or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return [str(x) for x in json.loads(m.group(0)).get("missing", [])][:6] if m else []
    except (ValueError, AttributeError):
        return []


def requested_units(ix, items, files):
    """Units for what the model named: exact definitions first (head window first), then a search on the phrases.
    Names and phrases that match nothing in the project are dropped (no search on pure noise)."""
    units, defs = ix["units"], ix["defs"]
    named, phrases = [], []
    for it in items:
        hit = False
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", it):
            short = name.split(".")[-1]
            ids = defs.get(short) or []
            if "." in name:
                ids = [i for i in ids if (units[i]["symbol"] or "").split("#")[0].endswith(name)] or ids
            if ids and len(short) >= 3 and len({units[i]["file"] for i in ids}) <= 3:
                named += sorted(ids, key=lambda i: (not units[i].get("head"), units[i]["start"]))
                hit = True
        mentions_file = any(os.path.basename(f).lower() in it.lower() for f in files)
        if not hit and (mentions_file or ix["bm25"].scores(cp.terms(it)) and max(ix["bm25"].scores(cp.terms(it))) > 0):
            phrases.append(it)
    found = list(dict.fromkeys(named))
    if phrases:
        found += [i for i in cp.rank(ix, " ".join(phrases), embed, 0, 0)[:40] if i not in found]
    return found


def refine(ix, pack, want, budget, share=0.4):
    """Add the requested units (up to `share` of the budget), then drop the pack's lowest-ranked slices to fit."""
    units = ix["units"]
    have = {id(u) for u in pack}
    add, add_tok = [], 0
    for i in want:
        u = units[i]
        if id(u) in have or add_tok + u["tokens"] > budget * share:
            continue
        add.append(u)
        add_tok += u["tokens"]
    keep = list(pack)                                  # in rank order: drop from the end
    while keep and sum(u["tokens"] for u in keep) + add_tok > budget:
        keep.pop()
    return keep + add


def render(pack):
    by = {}
    for u in pack:
        by.setdefault(u["file"], []).append(u)
    return "\n\n".join(f"### {f}:{u['start']}-{u['end']}" + (f" ({u['symbol']})" if u["symbol"] else "") + "\n" +
                       "\n".join(f"{n:>6}\t{ln}" for n, ln in enumerate(u["text"].split("\n"), u["start"]))
                       for f, us in by.items() for u in sorted(us, key=lambda x: x["start"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8140")
    ap.add_argument("--rounds", type=int, nargs="+", default=[2, 3])
    ap.add_argument("--budgets", type=int, nargs="+", default=[4000, 8000])
    a = ap.parse_args()
    gold = [json.loads(l) for p in sorted(glob.glob(os.path.join(HERE, "gold_*.jsonl")))
            for l in open(p, encoding="utf-8") if l.strip()]
    rows = []
    for corpus in ("bonsai8", "fps", "zen"):
        files, root = run.corpus_files(corpus), run.CORPORA[corpus]["root"]
        ix = cp._index(root, files, embed, 40)
        for g in [x for x in gold if x["corpus"] == corpus]:
            q = g["question"] + ("\n" + g["trace"] if g.get("trace") else "")
            for B in a.budgets:
                _t, single = cp.focus_pack(root, q, B, files=files, embed=embed, max_lines=40, link_seeds=0)
                pack, asks, t0 = list(single), 0, time.time()
                result = {"id": g["id"], "corpus": corpus, "budget": B, "pieces": len(g["gold"]),
                          "single": sum(covered(single, p) for p in g["gold"])}
                for R in range(1, max(a.rounds)):
                    items = ask(a.url, q, files, render(pack))
                    asks += 1
                    if items:
                        want = requested_units(ix, items, files)
                        if want:
                            pack = refine(ix, pack, want, B)
                    if R + 1 in a.rounds:
                        result[f"r{R + 1}"] = sum(covered(pack, p) for p in g["gold"])
                    if not items:                       # nothing missing: later rounds keep this pack
                        cur = sum(covered(pack, p) for p in g["gold"])
                        for RR in a.rounds:
                            result.setdefault(f"r{RR}", cur)
                        break
                result["asks"], result["model_s"] = asks, round(time.time() - t0, 1)
                rows.append(result)
                print(json.dumps(result), flush=True)
    json.dump(rows, open(os.path.join(HERE, "adaptive_rounds.json"), "w", encoding="utf-8"), indent=1)
    print("\n| Budget | Single | " + " | ".join(f"Up to {R} rounds" for R in a.rounds) + " |")
    print("|---|---|" + "---|" * len(a.rounds))
    for B in a.budgets:
        rs = [r for r in rows if r["budget"] == B]
        tot = sum(r["pieces"] for r in rs)
        cell = lambda k: f"{sum(r[k] == r['pieces'] for r in rs)}/{len(rs)} / {sum(r[k] for r in rs) / tot:.0%}"
        print(f"| {B // 1000}K | {cell('single')} | " + " | ".join(cell(f"r{R}") for R in a.rounds) + " |")
    for c in ("bonsai8", "fps", "zen"):
        for B in a.budgets:
            rs = [r for r in rows if r["corpus"] == c and r["budget"] == B]
            tot = sum(r["pieces"] for r in rs)
            print(f"{c} {B // 1000}K pieces: single {sum(r['single'] for r in rs) / tot:.0%} " + " ".join(
                f"r{R} {sum(r[f'r{R}'] for r in rs) / tot:.0%} (worse {sum(r[f'r{R}'] < r['single'] for r in rs)})"
                for R in a.rounds))


if __name__ == "__main__":
    main()
