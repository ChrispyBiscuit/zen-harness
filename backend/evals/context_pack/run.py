"""Context packing benchmark: can a packer put ALL the code pieces a question needs into one small window?

For each gold question (gold_*.jsonl) and token budget, every packer builds a context from function-level units of
the corpus; a question counts as solved only if every gold piece is >= 80% covered by the packed lines (set recall).

    venv\\Scripts\\python.exe evals\\context_pack\\run.py [--budgets 2000 4000 8000] [--packers ...] [--code-model]

CPU only (no llama-server). Embeddings: fastembed (bge-small, the app's model; optionally a code model).
"""
import argparse
import ast
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BACKEND)

CORPORA = {
    # pinned: the gold line numbers follow this commit, not the working tree
    "zen": {"root": BACKEND, "globs": ["*.py"], "exclude": [], "git": "98af6e9"},
    "bonsai8": {"root": os.path.expanduser(r"~\Documents\ZenBattery\build\bonsai8"), "globs": ["**/*.py", "**/*.md"],
                "exclude": ["__pycache__", "venv", ".venv", ".pytest_cache"]},
    "fps": {"root": os.path.expanduser(r"~\Documents\Zen Harness\FPS Arena"), "globs": ["**/*.js", "**/*.mjs", "**/*.html"],
            "exclude": ["node_modules", "three", ".min.js", "vendor", "dist"]},
}
CHARS_PER_TOKEN = 3.5
MAX_UNIT_LINES = 120
COVER = 0.8


def tok(text):
    return int(len(text) / CHARS_PER_TOKEN) + 4


# ------------------------------------------------------------------------------------------------- corpus -> units
def materialize(name):
    """A corpus pinned to a git commit is exported once (backend/*.py at that commit) to a cache folder."""
    c = CORPORA[name]
    if not c.get("git") or c.get("_ready"):
        return
    import io
    import subprocess
    import tarfile
    import tempfile
    dest = os.path.join(tempfile.gettempdir(), f"context_pack_{name}_{c['git']}")
    if not os.path.isdir(os.path.join(dest, "backend")):
        tar = subprocess.run(["git", "archive", "--format=tar", c["git"], "backend"], cwd=os.path.dirname(BACKEND),
                             capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            tf.extractall(dest, members=[m for m in tf.getmembers() if m.name.count("/") == 1 and m.name.endswith(".py")])
    c["root"], c["_ready"] = os.path.join(dest, "backend"), True


def corpus_files(name):
    materialize(name)
    c = CORPORA[name]
    out = []
    for g in c["globs"]:
        for p in glob.glob(os.path.join(c["root"], g), recursive=True):
            rel = os.path.relpath(p, c["root"]).replace("\\", "/")
            if any(x in rel for x in c["exclude"]) or not os.path.isfile(p):
                continue
            out.append(rel)
    return sorted(set(out))


def _split(unit_lines, start, symbol):
    """A huge unit becomes windows of MAX_UNIT_LINES (20 lines overlap) with the same symbol."""
    n = len(unit_lines)
    if n <= MAX_UNIT_LINES:
        return [(start, start + n - 1, symbol)]
    out, i = [], 0
    while i < n:
        j = min(n, i + MAX_UNIT_LINES)
        out.append((start + i, start + j - 1, symbol))
        if j == n:
            break
        i = j - 20
    return out


def _py_spans(text):
    tree = ast.parse(text)
    spans = []

    def visit(node, parent):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{parent}.{ch.name}" if parent else ch.name
                first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                spans.append((first, ch.end_lineno, name))          # nested functions stay inside their parent
            elif isinstance(ch, ast.ClassDef):
                name = f"{parent}.{ch.name}" if parent else ch.name
                body_defs = [b for b in ch.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                head_end = (min(min([b.lineno] + [d.lineno for d in getattr(b, "decorator_list", [])]) for b in body_defs) - 1
                            if body_defs else ch.end_lineno)
                spans.append((ch.lineno, max(ch.lineno, head_end), name))
                visit(ch, name)
    visit(tree, "")
    return spans


def _js_spans(text):
    import repomap
    lines = text.splitlines()
    spans = []
    for d in repomap._js_defs(text):
        s = d["line"]
        depth, seen, end = 0, False, None
        for i in range(s - 1, min(len(lines), s - 1 + 2000)):
            ln = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1|//.*$", "", lines[i])   # strings and line comments out
            for ch in ln:
                if ch == "{":
                    depth += 1
                    seen = True
                elif ch == "}":
                    depth -= 1
            if seen and depth <= 0:
                end = i + 1
                break
            if not seen and i > s + 2:
                break
        if end:
            name = f"{d['parent']}.{d['name']}" if d.get("parent") else d["name"]
            spans.append((s, end, name))
    # keep outermost spans plus methods (a class span is reduced to its head like Python)
    return spans


def units_for(name):
    materialize(name)
    root = CORPORA[name]["root"]
    units, hashes = [], {}
    for rel in corpus_files(name):
        path = os.path.join(root, rel)
        text = open(path, encoding="utf-8", errors="replace").read()
        hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        lines = text.splitlines()
        ext = os.path.splitext(rel)[1].lower()
        try:
            spans = _py_spans(text) if ext == ".py" else _js_spans(text) if ext in (".js", ".mjs") else []
        except SyntaxError:
            spans = []
        # innermost-wins line ownership: methods own their lines, a class/function owns the rest of its span
        owner = [None] * (len(lines) + 2)
        for s, e, sym in sorted(spans, key=lambda x: -(x[1] - x[0])):
            for i in range(s, min(e, len(lines)) + 1):
                owner[i] = sym
        i = 1
        while i <= len(lines):
            sym = owner[i]
            j = i
            while j + 1 <= len(lines) and owner[j + 1] == sym and (sym is not None or j + 1 - i < 60):
                j += 1
            for s, e, sy in _split(lines[i - 1:j], i, sym):
                body = "\n".join(lines[s - 1:e])
                if body.strip():
                    units.append({"corpus": name, "file": rel, "symbol": sy, "start": s, "end": e,
                                  "text": body, "tokens": tok(body) + tok(f"# {rel}:{s}-{e}")})
            i = j + 1
    for k, u in enumerate(units):
        u["id"] = k
    return units, hashes


# ------------------------------------------------------------------------------------------------- retrieval
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def terms(text):
    out = []
    for w in _WORD.findall(text):
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", w.replace("_", " "))
        out.append(w.lower())
        if len(parts) > 1:
            out.extend(p.lower() for p in parts if len(p) > 1)
    return out


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = [Counter(d) for d in docs]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, q):
        qs = Counter(q)
        out = []
        for d, L in zip(self.docs, self.len):
            s = 0.0
            for t in qs:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out


_EMB = {}


def embedder(model):
    if model not in _EMB:
        from fastembed import TextEmbedding
        import paths
        _EMB[model] = TextEmbedding(model_name=model, cache_dir=os.path.join(paths.models_dir(), "fastembed"))
    return _EMB[model]


def embed(model, texts):
    import numpy as np
    vecs = np.array(list(embedder(model).embed(texts, batch_size=16)), dtype="float32")
    vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    return vecs


def rrf(*rankings, k=60):
    sc = defaultdict(float)
    for r in rankings:
        for pos, i in enumerate(r):
            sc[i] += 1.0 / (k + pos + 1)
    return sorted(sc, key=lambda i: -sc[i]), sc


class Index:
    def __init__(self, units, model="BAAI/bge-small-en-v1.5"):
        self.units = units
        self.bm25 = BM25([terms(f"{u['file']} {u['symbol'] or ''} {u['text']}") for u in units])
        t0 = time.time()
        self.vecs = embed(model, [f"{u['file']} {u['symbol'] or ''}\n{u['text'][:2000]}" for u in units])
        self.embed_s = time.time() - t0
        self.model = model
        self.defs = defaultdict(list)                     # short name and qualified name -> unit ids
        for u in units:
            if u["symbol"]:
                base = u["symbol"].split("#")[0]
                self.defs[base].append(u["id"])
                self.defs[base.split(".")[-1]].append(u["id"])

    def rank(self, query, mode="hybrid"):
        import numpy as np
        b = self.bm25.scores(terms(query))
        rb = [i for i in sorted(range(len(b)), key=lambda i: -b[i]) if b[i] > 0]
        if mode == "bm25":
            return rb
        qv = embed(self.model, [query])[0]
        e = self.vecs @ qv
        re_ = list(np.argsort(-e))
        if mode == "embed":
            return [int(i) for i in re_]
        order, _ = rrf(rb[:200], [int(i) for i in re_[:200]])
        return order


# ------------------------------------------------------------------------------------------------- packers
def pack(units, order, budget):
    chosen, used = [], 0
    for i in order:
        t = units[i]["tokens"]
        if used + t <= budget:
            chosen.append(i)
            used += t
        if used >= budget - 40:
            break
    return chosen, used


def p_whole_files(ix, q, budget):
    """Whole files ranked by their best unit (what read_file of the top hits gives); a file that doesn't fit is cut."""
    units = ix.units
    order = ix.rank(q)
    files = []
    for i in order:
        f = units[i]["file"]
        if f not in files:
            files.append(f)
    chosen, used = [], 0
    for f in files:
        for u in sorted((u for u in units if u["file"] == f), key=lambda u: u["start"]):
            if used + u["tokens"] > budget:
                return chosen, used
            chosen.append(u["id"])
            used += u["tokens"]
    return chosen, used


def p_chunks(mode):
    def f(ix, q, budget):
        return pack(ix.units, ix.rank(q, mode), budget)
    f.__name__ = f"chunks_{mode}"
    return f


def named_seeds(ix, q):
    """Code names the question or trace mentions that are defined in the corpus (exact, Class.method or file name)."""
    seeds = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", q):
        cands = ix.defs.get(w) or (ix.defs.get(w.split(".")[-1]) if "." in w else None)
        if not cands:
            continue
        plainword = w.islower() and "_" not in w and len(w) < 6
        if plainword or len(set(ix.units[i]["file"] for i in cands)) > 3:
            continue                                       # 'load', 'run': too ambiguous to be a seed
        for i in cands:
            if i not in seeds:
                seeds.append(i)
    return seeds


def expand(ix, seeds, rank_pos, max_callees=8, max_callers=4):
    """One hop of structure: callees (defined names used in the seed) and callers (units using the seed's name)."""
    units = ix.units
    out = []
    for s in seeds:
        text = units[s]["text"]
        callees = []
        for w in dict.fromkeys(_WORD.findall(text)):
            ids = ix.defs.get(w)
            if not ids or len(w) < 4 or len(set(units[i]["file"] for i in ids)) > 3:
                continue
            callees += [i for i in ids if i != s and units[i]["symbol"].split("#")[0].split(".")[-1] == w]
        out += [("callee", i) for i in callees[:max_callees]]
        name = (units[s]["symbol"] or "").split("#")[0].split(".")[-1]
        if name and len(name) >= 4:
            rx = re.compile(r"(?<![\w$])" + re.escape(name) + r"\s*\(")
            callers = [u["id"] for u in units if u["id"] != s and rx.search(u["text"])]
            callers.sort(key=lambda i: rank_pos.get(i, 10 ** 6))
            out += [("caller", i) for i in callers[:max_callers]]
    return out


def p_structural(seed_from_search=3, hybrid_fill=True):
    def f(ix, q, budget):
        order = ix.rank(q)
        pos = {i: k for k, i in enumerate(order)}
        seeds = named_seeds(ix, q)
        seeds += [i for i in order[:seed_from_search] if i not in seeds]
        prio = [(0, pos.get(s, 10 ** 6), s) for s in seeds]
        for kind, i in expand(ix, seeds, pos):
            prio.append((1 if kind == "callee" else 2, pos.get(i, 10 ** 6), i))
        if hybrid_fill:
            prio += [(3, k, i) for k, i in enumerate(order[:60])]
        seen, ordered = set(), []
        for _p, _r, i in sorted(prio):
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        return pack(ix.units, ordered, budget)
    f.__name__ = f"structural_s{seed_from_search}" + ("" if hybrid_fill else "_nofill")
    return f


# ------------------------------------------------------------------------------------------------- scoring
def covered(units, chosen, piece):
    lines = set()
    for i in chosen:
        u = units[i]
        if u["file"] == piece["file"]:
            lines.update(range(u["start"], u["end"] + 1))
    need = set(range(int(piece["start"]), int(piece["end"]) + 1))
    return len(need & lines) / max(1, len(need)) >= COVER


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budgets", type=int, nargs="+", default=[2000, 4000, 8000])
    ap.add_argument("--code-model", default="", help="also run the structural+hybrid packer on this fastembed model")
    ap.add_argument("--out", default=os.path.join(HERE, "results.json"))
    a = ap.parse_args()
    gold = []
    for p in sorted(glob.glob(os.path.join(HERE, "gold_*.jsonl"))):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                gold.append(json.loads(line))
    by_corpus = defaultdict(list)
    for g in gold:
        by_corpus[g["corpus"]].append(g)
    packers = [p_whole_files, p_chunks("bm25"), p_chunks("embed"), p_chunks("hybrid"), p_structural(3),
               p_structural(0, hybrid_fill=False), p_structural(5)]
    rows, meta = [], {"corpora": {}}
    for cname, qs in by_corpus.items():
        units, hashes = units_for(cname)
        bad = [(g["id"], p["file"]) for g in qs for p in g["gold"] if p["file"] not in hashes]
        if bad:
            print("WARNING gold pieces outside the corpus:", bad[:8])
        models = [("bge-small", "BAAI/bge-small-en-v1.5")] + ([("code", a.code_model)] if a.code_model else [])
        for mlabel, model in models:
            ix = Index(units, model)
            meta["corpora"][f"{cname}/{mlabel}"] = {"units": len(units), "files": len(hashes), "embed_s": round(ix.embed_s, 1),
                                                    "corpus_tokens": sum(u["tokens"] for u in units), "hashes": hashes}
            use = packers if mlabel == "bge-small" else [p_chunks("embed"), p_chunks("hybrid"), p_structural(3)]
            for g in qs:
                q = g["question"] + ("\n" + g["trace"] if g.get("trace") else "")
                for pk in use:
                    for B in a.budgets:
                        t0 = time.time()
                        chosen, used = pk(ix, q, B)
                        hit = [covered(units, chosen, p) for p in g["gold"]]
                        rows.append({"id": g["id"], "corpus": cname, "model": mlabel, "packer": pk.__name__,
                                     "budget": B, "pieces": len(hit), "covered": sum(hit), "all": all(hit),
                                     "multi": len({p["file"] for p in g["gold"]}) > 1, "used": used,
                                     "ms": round((time.time() - t0) * 1000)})
            print(f"{cname}/{mlabel}: {len(units)} units, embedded in {ix.embed_s:.0f}s", flush=True)
    json.dump({"meta": meta, "rows": rows}, open(a.out, "w", encoding="utf-8"), indent=0)
    report(rows)


def report(rows):
    keys = sorted({(r["model"], r["packer"]) for r in rows}, key=lambda k: (k[0] != "bge-small", k[1]))
    budgets = sorted({r["budget"] for r in rows})
    print("\nSET recall (all gold pieces in the window) / piece recall, all questions:")
    print("| packer | model | " + " | ".join(f"{b // 1000}K set | {b // 1000}K pieces" for b in budgets) + " |")
    for m, p in keys:
        cells = []
        for b in budgets:
            rs = [r for r in rows if r["model"] == m and r["packer"] == p and r["budget"] == b]
            cells.append(f"{sum(r['all'] for r in rs) / len(rs):.0%} | {sum(r['covered'] for r in rs) / sum(r['pieces'] for r in rs):.0%}")
        print(f"| {p} | {m} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
