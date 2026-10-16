"""Score context_pack.focus_pack variants on the 50 gold questions: slice size x link-following strength x budget.

    venv\\Scripts\\python.exe evals\\context_pack\\measure_focus.py
Writes focus_results.json and prints a markdown table. CPU only; embeddings are cached across variants.
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

import context_pack as cp
import run

MODEL = "BAAI/bge-small-en-v1.5"
SLICES = [120, 40]
LINKS = [(0, 0.0), (3, 0.01), (5, 0.005), (5, 0.01), (5, 0.02)]
BUDGETS = [2000, 4000, 8000]


def embed(texts, query=False):
    return run.embed(MODEL, texts)


def covered(chosen, piece):
    lines = set()
    for u in chosen:
        if u["file"] == piece["file"]:
            lines.update(range(u["start"], u["end"] + 1))
    need = set(range(int(piece["start"]), int(piece["end"]) + 1))
    return len(need & lines) / max(1, len(need)) >= run.COVER


def main():
    gold = [json.loads(l) for p in sorted(glob.glob(os.path.join(HERE, "gold_*.jsonl")))
            for l in open(p, encoding="utf-8") if l.strip()]
    rows = []
    for corpus in ("bonsai8", "fps", "zen"):
        files = run.corpus_files(corpus)
        root = run.CORPORA[corpus]["root"]
        qs = [g for g in gold if g["corpus"] == corpus]
        for ml in SLICES:
            t0 = time.time()
            cp._index(root, files, embed, ml)
            print(f"{corpus} slices<={ml}: indexed in {time.time() - t0:.0f}s", flush=True)
            for seeds, boost in LINKS:
                for g in qs:
                    q = g["question"] + ("\n" + g["trace"] if g.get("trace") else "")
                    for B in BUDGETS:
                        _t, chosen = cp.focus_pack(root, q, B, files=files, embed=embed, max_lines=ml,
                                                   link_seeds=seeds, link_boost=boost)
                        hit = [covered(chosen, p) for p in g["gold"]]
                        rows.append({"id": g["id"], "corpus": corpus, "slices": ml, "seeds": seeds, "boost": boost,
                                     "budget": B, "pieces": len(hit), "covered": sum(hit), "all": all(hit)})
    json.dump(rows, open(os.path.join(HERE, "focus_results.json"), "w", encoding="utf-8"), indent=0)
    print("\n| slices | links (seeds, boost) | " + " | ".join(f"{b // 1000}K all / pieces" for b in BUDGETS)
          + " | Zen 8K all |")
    print("|---|---|" + "---|" * len(BUDGETS) + "---|")
    for ml in SLICES:
        for seeds, boost in LINKS:
            cells = []
            for B in BUDGETS:
                rs = [r for r in rows if r["slices"] == ml and r["seeds"] == seeds and r["boost"] == boost and r["budget"] == B]
                cells.append(f"{sum(r['all'] for r in rs)}/{len(rs)} / {sum(r['covered'] for r in rs) / sum(r['pieces'] for r in rs):.0%}")
            zen = [r for r in rows if r["slices"] == ml and r["seeds"] == seeds and r["boost"] == boost
                   and r["budget"] == 8000 and r["corpus"] == "zen"]
            link = "off" if not seeds else f"{seeds}, {boost}"
            print(f"| {ml} | {link} | " + " | ".join(cells) + f" | {sum(r['all'] for r in zen)}/{len(zen)} |")


if __name__ == "__main__":
    main()
