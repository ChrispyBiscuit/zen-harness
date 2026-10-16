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
import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict

CHARS_PER_TOKEN = 3.5
MAX_UNIT_LINES = 40            # measured: 40-line windows for long definitions beat 120 (20 vs 16 of 50 at 8K)
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


def file_units(rel, text, max_lines=None):
    """Function/class-level slices of one file: each line belongs to its innermost definition; code outside any
    definition is grouped in blocks of up to 60 lines; anything over max_lines is split into windows that overlap by
    a sixth. The first window of each definition is its "head" (signature + start)."""
    max_lines = int(max_lines or MAX_UNIT_LINES)
    overlap = max(4, max_lines // 6)
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
            e = min(j, k + max_lines - 1)
            body = "\n".join(lines[k - 1:e])
            if body.strip():
                out.append({"file": rel, "symbol": sym, "start": k, "end": e, "text": body, "head": k == i,
                            "tokens": tokens(body) + tokens(f"### {rel}:{k}-{e}")})
            if e == j:
                break
            k = e - overlap + 1
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


_EMB_CACHE = {}      # sha1 of the embedded text -> vector: unchanged slices are never embedded twice
_EMB_LOADED = False
_EMB_DIRTY = False
STATUS = {"state": "idle"}     # what the index is doing, for the app's status chip: idle / indexing / ready / error


def _store_path():
    try:
        import paths
        return paths.data("code_index", "embeddings.pkl")
    except Exception:
        return ""


def _load_store():
    """The saved embeddings (one file for every project: vectors are keyed by the text they embed)."""
    global _EMB_LOADED
    if _EMB_LOADED:
        return
    _EMB_LOADED = True
    p = _store_path()
    if p and os.path.isfile(p):
        import pickle
        try:
            with open(p, "rb") as f:
                _EMB_CACHE.update(pickle.load(f))
        except Exception:
            pass


def _save_store():
    global _EMB_DIRTY
    p = _store_path()
    if not (p and _EMB_DIRTY):
        return
    import pickle
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(_EMB_CACHE, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, p)
    _EMB_DIRTY = False


def _embed_cached(embed, texts, persist=True, batch=32):
    """Vectors for `texts`, embedding only the ones never seen before (in batches, reporting progress in STATUS)."""
    global _EMB_DIRTY
    import hashlib
    import numpy as np
    if persist:
        _load_store()
    keys = [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest() for t in texts]
    todo = [i for i, k in enumerate(keys) if k not in _EMB_CACHE]
    if todo:
        STATUS.update(total=len(todo), done=0)
        for s in range(0, len(todo), batch):
            part = todo[s:s + batch]
            vecs = embed([texts[i] for i in part])
            if vecs is None:
                return None
            for i, v in zip(part, vecs):
                _EMB_CACHE[keys[i]] = v
            STATUS["done"] = min(len(todo), s + batch)
        _EMB_DIRTY = True
        if persist:
            try:
                _save_store()
            except OSError:
                pass
    return np.stack([_EMB_CACHE[k] for k in keys]) if keys else None


def _short(symbol):
    return (symbol or "").split("#")[0].split(".")[-1]


def _index(root, files, embed, max_lines=None):
    """Units + BM25 + embeddings for `files` under root, rebuilt only when a file changed (mtime/size)."""
    sig = []
    for rel in files:
        try:
            st = os.stat(os.path.join(root, rel))
            sig.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    sig = tuple(sig)
    key = (os.path.normcase(os.path.abspath(root)), bool(embed), int(max_lines or MAX_UNIT_LINES))
    with _lock:
        hit = _CACHE.get(key)
        if hit and hit["sig"] == sig:
            return hit
        t0 = time.time()
        STATUS.clear()
        STATUS.update(state="indexing", root=root, files=len(sig), started=t0, done=0, total=0)
        units = []
        for rel, *_ in sig:
            try:
                with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                    units += file_units(rel, f.read(), max_lines)
            except OSError:
                continue
        bm = BM25([terms(f"{u['file']} {u['symbol'] or ''} {u['text']}") for u in units])
        vecs = (_embed_cached(embed, [f"{u['file']} {u['symbol'] or ''}\n{u['text'][:2000]}" for u in units])
                if embed and units else None)
        defs = defaultdict(list)                 # short definition name -> unit ids (every window of it)
        for i, u in enumerate(units):
            if u["symbol"]:
                defs[_short(u["symbol"])].append(i)
        entry = {"sig": sig, "units": units, "bm25": bm, "vecs": vecs, "defs": defs, "built": time.time(),
                 "root": root, "files": len(sig), "seconds": round(time.time() - t0, 1)}
        _CACHE[key] = entry
        STATUS.clear()
        STATUS.update(state="ready", root=root, files=len(sig), slices=len(units), seconds=entry["seconds"],
                      built=entry["built"], embedded=vecs is not None)
        return entry


def start_index(root, files=None, embed=_default_embed, max_lines=None):
    """Build or refresh `root`'s index in the background (after the agent writes code, or when a project opens), so a
    focus_pack call later finds it ready. Unchanged slices reuse their saved embeddings; only new text is embedded."""
    def work():
        try:
            _index(root, list(files) if files is not None else code_files(root), embed, max_lines)
        except Exception as e:
            STATUS.clear()
            STATUS.update(state="error", root=root, error=repr(e)[:200])
    th = threading.Thread(target=work, daemon=True, name="code-index")
    th.start()
    return th


def index_status():
    return dict(STATUS)


def index_summary(root):
    """What is indexed for `root` (for the app's code-index view): files, slices, when, and saved-vector count."""
    key = next((k for k in _CACHE if k[0] == os.path.normcase(os.path.abspath(root))), None)
    e = _CACHE.get(key) if key else None
    if not e:
        return {"root": root, "indexed": False}
    per_file = Counter(u["file"] for u in e["units"])
    return {"root": root, "indexed": True, "files": e["files"], "slices": len(e["units"]), "built": e["built"],
            "seconds": e["seconds"], "embedded": e["vecs"] is not None, "saved_vectors": len(_EMB_CACHE),
            "largest": per_file.most_common(8)}


def indexed_roots():
    return sorted({e["root"] for e in _CACHE.values() if e.get("root")})


def forget(root):
    """Drop `root`'s in-memory index (its saved embeddings stay, so a rebuild only re-reads files)."""
    n = os.path.normcase(os.path.abspath(root))
    for k in [k for k in _CACHE if k[0] == n]:
        _CACHE.pop(k, None)


def named_units(ix, names):
    """Units for definitions named exactly (Class.method, function, class; a file name gives that file's units),
    head window first. -> (unit ids, names that matched nothing)."""
    units, defs = ix["units"], ix["defs"]
    out, missing = [], []
    for raw in names or []:
        name = str(raw).strip()
        if not name:
            continue
        base = os.path.basename(name.replace("\\", "/")).lower()
        by_file = [i for i, u in enumerate(units) if os.path.basename(u["file"]).lower() == base] if "." in base and \
            base.rsplit(".", 1)[-1] in ("py", "js", "mjs", "ts", "tsx", "jsx", "html", "css", "md") else []
        short = name.split(".")[-1]
        ids = defs.get(short) or []
        if "." in name and not by_file:
            ids = [i for i in ids if (units[i]["symbol"] or "").split("#")[0].endswith(name)] or ids
        ids = by_file or ids
        if not ids:
            missing.append(name)
            continue
        out += sorted(ids, key=lambda i: (units[i]["file"], not units[i].get("head"), units[i]["start"]))
    return list(dict.fromkeys(out)), missing


LINK_SEEDS = 0        # off: following the top hits' call links did not help (measure_focus.py); 5 to try it
LINK_BOOST = 0.005    # added to a linked slice's RRF score (rank 0 ~ 0.033, rank 50 ~ 0.018 with two rankers)


def _link_boost(ix, order, score, seeds, boost):
    """Most pieces the plain ranking missed were one call away from a top hit (benchmark: 47 of 68). Boost what the
    top `seeds` slices call (definitions named in their text; a callee's head window fully, its other windows by half)
    and what calls them (`name(` in another slice), skipping short and widely shared names."""
    units, defs = ix["units"], ix["defs"]
    for s in order[:seeds]:
        u = units[s]
        own = _short(u["symbol"])
        for w in set(_WORD.findall(u["text"])):
            if w == own or len(w) < 4 or w not in defs:
                continue
            ids = defs[w]
            if len({units[i]["file"] for i in ids}) > 3:
                continue
            for i in ids:
                if i != s:
                    score[i] += boost * (1.0 if units[i].get("head") else 0.5)
        if own and len(own) >= 4:
            rx = re.compile(r"(?<![\w$])" + re.escape(own) + r"\s*\(")
            for i, v in enumerate(units):
                if i != s and _short(v["symbol"]) != own and rx.search(v["text"]):
                    score[i] += boost * 0.5


def rank(ix, query, embed, link_seeds=None, link_boost=None):
    link_seeds = LINK_SEEDS if link_seeds is None else link_seeds
    link_boost = LINK_BOOST if link_boost is None else link_boost
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
    order = sorted(score, key=lambda i: -score[i])
    if link_seeds and link_boost:
        _link_boost(ix, order, score, link_seeds, link_boost)
        order = sorted(score, key=lambda i: -score[i])
    return order


# --------------------------------------------------------------------------------------------- the pack
def code_files(root, limit=4000):
    import repomap
    return [rel for rel, _p, _st in repomap._walk(os.path.abspath(root), code_only=True, max_files=limit)]


def unit_key(u):
    return (u["file"], u["start"], u["end"], hash(u["text"]))


def focus_pack(root, query, budget_tokens=4000, files=None, embed=_default_embed, max_lines=None, link_seeds=None,
               link_boost=None, names=None, skip=None):
    """-> (text, chosen units). The function-level slices of the code under `root` that best match `query` (a goal, a
    question or an error trace), filled to `budget_tokens`, shown grouped by file in line order with line numbers.
    names: definitions or files asked for exactly (a second round: what the model saw was missing) — they come first.
    skip: unit_key()s already shown (left out, so a follow-up call brings only new code).
    embed=None: keyword ranking only (no embedding model). max_lines: the slice size for long definitions;
    link_seeds / link_boost: how many top hits have their call links followed, and how strongly (0 = off)."""
    files = list(files) if files is not None else code_files(root)
    ix = _index(root, files, embed, max_lines)
    if not ix["units"]:
        return "", []
    named, missing = named_units(ix, names)
    seen = set(named)
    full_query = " ".join([query] + [str(n) for n in names or []])
    order = named + [i for i in rank(ix, full_query, embed, link_seeds, link_boost) if i not in seen]
    skip = skip or set()
    chosen, used, skipped = [], tokens("[Focus pack]") + 20, 0
    for i in order:
        u = ix["units"][i]
        if unit_key(u) in skip:
            skipped += 1
            continue
        if used + u["tokens"] <= budget_tokens:
            chosen.append(u)
            used += u["tokens"]
        if used >= budget_tokens - 40:
            break
    note = (f" Not found in the project: {', '.join(missing)}." if missing else "") + \
           (f" {skipped} slice(s) already shown earlier left out." if skipped else "")
    if not chosen:
        return ("[Focus pack: nothing new matched." + note + "]") if (missing or skipped) else "", []
    return render_pack(chosen, query, note), chosen


def render_pack(chosen, query, note=""):
    """One block: slices grouped by file (files in rank order of their best slice), each in line order with line
    numbers as read_file shows them."""
    used = sum(u["tokens"] for u in chosen) + tokens("[Focus pack]") + 20
    by_file = defaultdict(list)
    for u in chosen:
        by_file[u["file"]].append(u)
    order = [f for f in dict.fromkeys(u["file"] for u in chosen)]
    parts = [f"[Focus pack: {len(chosen)} code slices from {len(order)} file(s), ~{used:,} tokens, the best match for: "
             f"{' '.join(query.split())[:160]}.{note} Line numbers as read_file shows them — edit from this text.]"]
    for f in order:
        for u in sorted(by_file[f], key=lambda u: u["start"]):
            label = f" ({u['symbol']})" if u["symbol"] else ""
            body = "\n".join(f"{n:>6}\t{ln}" for n, ln in enumerate(u["text"].split("\n"), u["start"]))
            parts.append(f"### {f}:{u['start']}-{u['end']}{label}\n{body}")
    return "\n\n".join(parts)


# the measured second round (evals/context_pack/second_round.py: 68% -> 76% of needed pieces at 8K, Zazen's backend
# 54% -> 69%): the model reads a first pack of 60% of the budget and names what is missing; exact names come first
ASK_SHARE = 0.6
ASK_PROMPT = ("You are helping find the code needed to answer a question about a codebase. Below are the question and "
              "the code found so far.\n\nQUESTION:\n{q}\n\nCODE FOUND SO FAR:\n{pack}\n\n"
              "What code is still missing to answer the question fully? Name up to 6 items: exact function, method "
              "(Class.method) or class names you need to see, file names, or short search phrases for code you expect "
              "but cannot name. Do not list code that is already shown above. Reply with JSON only: "
              '{{"missing": ["name or phrase", ...]}}')


def parse_missing(reply):
    """The model's list of missing items (JSON, else `quoted` names) -> [str]."""
    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return [str(x) for x in json.loads(m.group(0)).get("missing", [])][:6]
        except (ValueError, AttributeError):
            pass
    return re.findall(r"`([^`]{2,80})`", text)[:6]


def names_in(items):
    """Code names inside the model's items ('db.py:add_expense function' -> ['db', 'py', 'add_expense', ...]); only
    the ones the index defines are used, so plain words fall away."""
    out = []
    for it in items:
        out += [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", it) if len(w) >= 3]
    return list(dict.fromkeys(out))


def two_round_pack(root, goal, budget_tokens, ask, files=None, embed=_default_embed, skip=None):
    """The measured two-round pack: 60% of the budget for the best matches, `ask(prompt) -> reply` (the model) names
    what is missing, and the other 40% brings those definitions first, then a search on its words.
    -> (text, chosen units, the model's items)."""
    files = list(files) if files is not None else code_files(root)
    skip = set(skip or ())
    text1, first = focus_pack(root, goal, int(budget_tokens * ASK_SHARE), files=files, embed=embed, skip=skip)
    if not first:
        return text1, first, []
    try:
        items = parse_missing(ask(ASK_PROMPT.format(q=goal, pack=text1)))
    except Exception:
        items = []
    if not items:
        return text1, first, []
    ix = _index(root, files, embed, None)
    named = [n for n in names_in(items) if n.split(".")[-1] in ix["defs"]]
    used = sum(u["tokens"] for u in first)
    _t, more = focus_pack(root, " ".join(items), max(400, budget_tokens - used), files=files, embed=embed,
                          names=named, skip=skip | {unit_key(u) for u in first})
    chosen = first + more
    note = f" Second round: the model asked for {', '.join(items)[:200]}."
    return render_pack(chosen, goal, note), chosen, items
