"""Focus packs: the code pieces a question needs, from several files, side by side in one small block.

The context-packing benchmark (evals/context_pack, 2026-10-15; 50 cross-file questions over three codebases) found:
  - the window is not the limit: a perfect packer fits every question's pieces into 8K tokens;
  - whole files waste it: ranked whole files put all needed pieces together in 18% of questions at 8K, function-level
    slices ranked by hybrid keyword + meaning search in 34-42% — roughly double;
  - selection is the remaining bottleneck (the best rankers find about two thirds of the needed pieces).
So a pack is made of function/class-level slices (not files), ranked by BM25 fused with embeddings (RRF), filled to a
token budget, then shown grouped by file in line order with line numbers, so the model can edit from it directly.

Prototype: not wired into the agent loop yet. Natural plug-in points: after a failing test (query = the failure),
the working set at compaction (query = the current checklist item), or a tool the model calls with a goal.
"""
import ast
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict

CHARS_PER_TOKEN = 3.5
MAX_UNIT_LINES = 120
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_lock = threading.Lock()
_CACHE = {}          # root -> {"sig": ..., "units": [...], "bm25": BM25, "vecs": array|None}


def tokens(text):
    return int(len(text) / CHARS_PER_TOKEN) + 4


# --------------------------------------------------------------------------------------------- units
def _py_spans(text):
    spans = []

    def visit(node, parent):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{parent}.{ch.name}" if parent else ch.name
                first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                spans.append((first, ch.end_lineno, name))
            elif isinstance(ch, ast.ClassDef):
                name = f"{parent}.{ch.name}" if parent else ch.name
                inner = [b for b in ch.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                head_end = (min(min([b.lineno] + [d.lineno for d in getattr(b, "decorator_list", [])]) for b in inner) - 1
                            if inner else ch.end_lineno)
                spans.append((ch.lineno, max(ch.lineno, head_end), name))
                visit(ch, name)
    visit(ast.parse(text), "")
    return spans


def _js_spans(text):
    import repomap
    lines, spans = text.splitlines(), []
    for d in repomap._js_defs(text):
        s, depth, seen, end = d["line"], 0, False, None
        for i in range(s - 1, min(len(lines), s - 1 + 2000)):
            ln = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1|//.*$", "", lines[i])
            for c in ln:
                if c == "{":
                    depth, seen = depth + 1, True
                elif c == "}":
                    depth -= 1
            if seen and depth <= 0:
                end = i + 1
                break
            if not seen and i > s + 2:
                break
        if end:
            spans.append((s, end, f"{d['parent']}.{d['name']}" if d.get("parent") else d["name"]))
    return spans


def file_units(rel, text):
    """Function/class-level slices of one file: each line belongs to its innermost definition; code outside any
    definition is grouped in blocks of up to 60 lines; anything over MAX_UNIT_LINES is split with a 20-line overlap."""
    lines = text.splitlines()
    ext = os.path.splitext(rel)[1].lower()
    try:
        spans = _py_spans(text) if ext == ".py" else _js_spans(text) if ext in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx") else []
    except (SyntaxError, ValueError, RecursionError):
        spans = []
    owner = [None] * (len(lines) + 2)
    for s, e, sym in sorted(spans, key=lambda x: -(x[1] - x[0])):
        for i in range(s, min(e, len(lines)) + 1):
            owner[i] = sym
    out, i = [], 1
    while i <= len(lines):
        sym, j = owner[i], i
        while j + 1 <= len(lines) and owner[j + 1] == sym and (sym is not None or j + 1 - i < 60):
            j += 1
        k = i
        while k <= j:
            e = min(j, k + MAX_UNIT_LINES - 1)
            body = "\n".join(lines[k - 1:e])
            if body.strip():
                out.append({"file": rel, "symbol": sym, "start": k, "end": e, "text": body,
                            "tokens": tokens(body) + tokens(f"### {rel}:{k}-{e}")})
            if e == j:
                break
            k = e - 19
        i = j + 1
    return out


# --------------------------------------------------------------------------------------------- ranking
def terms(text):
    out = []
    for w in _WORD.findall(text):
        out.append(w.lower())
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", w.replace("_", " "))
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
        qs = set(q)
        out = []
        for d, L in zip(self.docs, self.len):
            s = 0.0
            for t in qs:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out


def _default_embed(texts, query=False):
    try:
        import memory_store
        return memory_store.store().embed(texts, query=query)
    except Exception:
        return None


def _index(root, files, embed):
    """Units + BM25 + embeddings for `files` under root, rebuilt only when a file changed (mtime/size)."""
    sig = []
    for rel in files:
        try:
            st = os.stat(os.path.join(root, rel))
            sig.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    sig = tuple(sig)
    key = (os.path.normcase(os.path.abspath(root)), bool(embed))
    with _lock:
        hit = _CACHE.get(key)
        if hit and hit["sig"] == sig:
            return hit
        units = []
        for rel, *_ in sig:
            try:
                with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                    units += file_units(rel, f.read())
            except OSError:
                continue
        bm = BM25([terms(f"{u['file']} {u['symbol'] or ''} {u['text']}") for u in units])
        vecs = embed([f"{u['file']} {u['symbol'] or ''}\n{u['text'][:2000]}" for u in units]) if embed and units else None
        entry = {"sig": sig, "units": units, "bm25": bm, "vecs": vecs, "built": time.time()}
        _CACHE[key] = entry
        return entry


def rank(ix, query, embed):
    b = ix["bm25"].scores(terms(query))
    by_bm = [i for i in sorted(range(len(b)), key=lambda i: -b[i]) if b[i] > 0][:200]
    rankings = [by_bm]
    if ix["vecs"] is not None and embed:
        qv = embed([query], query=True)
        if qv is not None:
            sims = ix["vecs"] @ qv[0]
            rankings.append([int(i) for i in sims.argsort()[::-1][:200]])
    score = defaultdict(float)
    for r in rankings:
        for pos, i in enumerate(r):
            score[i] += 1.0 / (61 + pos)
    return sorted(score, key=lambda i: -score[i])


# --------------------------------------------------------------------------------------------- the pack
def code_files(root, limit=4000):
    import repomap
    return [rel for rel, _p, _st in repomap._walk(os.path.abspath(root), code_only=True, max_files=limit)]


def focus_pack(root, query, budget_tokens=4000, files=None, embed=_default_embed):
    """-> (text, chosen units). The function-level slices of the code under `root` that best match `query` (a goal, a
    question or an error trace), filled to `budget_tokens`, shown grouped by file in line order with line numbers.
    embed=None: keyword ranking only (no embedding model)."""
    files = list(files) if files is not None else code_files(root)
    ix = _index(root, files, embed)
    if not ix["units"]:
        return "", []
    chosen, used = [], tokens("[Focus pack]") + 20
    for i in rank(ix, query, embed):
        u = ix["units"][i]
        if used + u["tokens"] <= budget_tokens:
            chosen.append(u)
            used += u["tokens"]
        if used >= budget_tokens - 40:
            break
    if not chosen:
        return "", []
    by_file = defaultdict(list)
    for u in chosen:
        by_file[u["file"]].append(u)
    order = [f for f in dict.fromkeys(u["file"] for u in chosen)]      # files in rank order of their best slice
    parts = [f"[Focus pack: {len(chosen)} code slices from {len(order)} file(s), ~{used:,} tokens, the best match for: "
             f"{' '.join(query.split())[:160]}. Line numbers as read_file shows them — edit from this text.]"]
    for f in order:
        for u in sorted(by_file[f], key=lambda u: u["start"]):
            label = f" ({u['symbol']})" if u["symbol"] else ""
            body = "\n".join(f"{n:>6}\t{ln}" for n, ln in enumerate(u["text"].split("\n"), u["start"]))
            parts.append(f"### {f}:{u['start']}-{u['end']}{label}\n{body}")
    return "\n\n".join(parts), chosen
