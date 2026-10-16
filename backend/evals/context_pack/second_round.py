"""Model-driven second round: a small local model reads the first focus pack, names what is still missing, and a
second pack fetches it — within the same total token budget as a single pack.

    venv\\Scripts\\python.exe evals\\context_pack\\second_round.py --url http://127.0.0.1:8140

Needs a llama-server (OpenAI-compatible) at --url. Writes second_round.json and prints a markdown table.
Embeddings are cached on disk (scratch) so reruns skip the slow CPU indexing.
"""
import argparse
import glob
import hashlib
import json
import os
import pickle
import re
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

import context_pack as cp
import run

MODEL = "BAAI/bge-small-en-v1.5"
CACHE = os.path.join(tempfile.gettempdir(), "context_pack_embed_cache.pkl")
FIRST_SHARE = 0.6
PROMPT = ("You are helping find the code needed to answer a question about a codebase. Below are the question and the "
          "code found so far.\n\nQUESTION:\n{q}\n\nCODE FOUND SO FAR:\n{pack}\n\n"
          "What code is still missing to answer the question fully? Name up to 6 items: exact function, method "
          "(Class.method) or class names you need to see, file names, or short search phrases for code you expect "
          "but cannot name. Do not list code that is already shown above. Reply with JSON only: "
          '{{"missing": ["name or phrase", ...]}}')


def load_cache():
    try:
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    except (OSError, EOFError, pickle.UnpicklingError):
        return {}


DISK = load_cache()


def embed(texts, query=False):
    import numpy as np
    keys = [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest() for t in texts]
    todo = [i for i, k in enumerate(keys) if k not in DISK]
    if todo:
        vecs = run.embed(MODEL, [texts[i] for i in todo])
        for i, v in zip(todo, vecs):
            DISK[keys[i]] = v
    return np.stack([DISK[k] for k in keys])


def ask(url, q, pack):
    body = {"messages": [{"role": "user", "content": PROMPT.format(q=q, pack=pack)}], "max_tokens": 300,
            "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    text = re.sub(r"<think>.*?</think>", "", r["choices"][0]["message"].get("content") or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    items = []
    if m:
        try:
            items = [str(x) for x in json.loads(m.group(0)).get("missing", [])][:6]
        except (ValueError, AttributeError):
            items = []
    if not items:                                   # not JSON: take quoted or code-like names from the reply
        items = re.findall(r"`([^`]{2,80})`", text)[:6] or [text[:200]]
    return items, time.time() - t0, r.get("usage", {})


def second_pack(ix, chosen, items, budget):
    """Fill the rest of the budget: definitions the model named exactly (head window first), then a search on what it
    asked for. Units already in the first pack are skipped."""
    units, defs = ix["units"], ix["defs"]
    have = {id(u) for u in chosen}
    used = sum(u["tokens"] for u in chosen)
    want = []
    for it in items:
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", it):
            short = name.split(".")[-1]
            ids = defs.get(short) or []
            if "." in name:                              # Class.method: prefer the exact owner
                exact = [i for i in ids if (units[i]["symbol"] or "").split("#")[0].endswith(name)]
                ids = exact or ids
            if len({units[i]["file"] for i in ids}) > 3 or len(short) < 3:
                continue
            want += sorted(ids, key=lambda i: (not units[i].get("head"), units[i]["start"]))
    want += cp.rank(ix, " ".join(items), embed, 0, 0)[:200]
    out = list(chosen)
    for i in dict.fromkeys(want):
        u = units[i]
        if id(u) in have:
            continue
        if used + u["tokens"] <= budget:
            out.append(u)
            have.add(id(u))
            used += u["tokens"]
        if used >= budget - 40:
            break
    return out


def covered(chosen, piece):
    lines = set()
    for u in chosen:
        if u["file"] == piece["file"]:
            lines.update(range(u["start"], u["end"] + 1))
    need = set(range(int(piece["start"]), int(piece["end"]) + 1))
    return len(need & lines) / max(1, len(need)) >= run.COVER


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8140")
    ap.add_argument("--budgets", type=int, nargs="+", default=[4000, 8000])
    a = ap.parse_args()
    gold = [json.loads(l) for p in sorted(glob.glob(os.path.join(HERE, "gold_*.jsonl")))
            for l in open(p, encoding="utf-8") if l.strip()]
    rows = []
    for corpus in ("bonsai8", "fps", "zen"):
        files, root = run.corpus_files(corpus), run.CORPORA[corpus]["root"]
        t0 = time.time()
        ix = cp._index(root, files, embed, 40)
        with open(CACHE, "wb") as f:
            pickle.dump(DISK, f)
        print(f"{corpus}: {len(ix['units'])} units indexed in {time.time() - t0:.0f}s", flush=True)
        for g in [x for x in gold if x["corpus"] == corpus]:
            q = g["question"] + ("\n" + g["trace"] if g.get("trace") else "")
            for B in a.budgets:
                _t, single = cp.focus_pack(root, q, B, files=files, embed=embed, max_lines=40, link_seeds=0)
                pack1, first = cp.focus_pack(root, q, int(B * FIRST_SHARE), files=files, embed=embed, max_lines=40,
                                             link_seeds=0)
                items, secs, usage = ask(a.url, q, pack1)
                both = second_pack(ix, first, items, B)
                rows.append({"id": g["id"], "corpus": corpus, "budget": B, "pieces": len(g["gold"]),
                             "single": sum(covered(single, p) for p in g["gold"]),
                             "first": sum(covered(first, p) for p in g["gold"]),
                             "two_round": sum(covered(both, p) for p in g["gold"]),
                             "asked": items, "model_s": round(secs, 1), "prompt_tokens": usage.get("prompt_tokens")})
                r = rows[-1]
                print(f"{g['id']} {B // 1000}K single {r['single']}/{r['pieces']} two-round {r['two_round']}/{r['pieces']} "
                      f"({secs:.1f}s) asked {items}", flush=True)
    json.dump(rows, open(os.path.join(HERE, "second_round.json"), "w", encoding="utf-8"), indent=1)
    print("\n| Budget | Single pack: all / pieces | First pack alone (60%) | Two rounds: all / pieces | Zen two rounds |")
    print("|---|---|---|---|---|")
    for B in a.budgets:
        rs = [r for r in rows if r["budget"] == B]
        tot = sum(r["pieces"] for r in rs)
        f = lambda k: f"{sum(r[k] == r['pieces'] for r in rs)}/{len(rs)} / {sum(r[k] for r in rs) / tot:.0%}"
        zen = [r for r in rs if r["corpus"] == "zen"]
        print(f"| {B // 1000}K | {f('single')} | {f('first')} | {f('two_round')} | "
              f"{sum(r['two_round'] == r['pieces'] for r in zen)}/{len(zen)} |")


if __name__ == "__main__":
    main()
