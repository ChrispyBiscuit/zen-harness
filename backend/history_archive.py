"""Per-chat archive of everything compaction moves out of the context — restorable compression.

When the context is compacted, the full text of what leaves it (tool results, the arguments of old write/edit calls,
the messages that get summarised) is appended here as numbered entries (E1, E2, …). The stubs left in the context
name their entry, and the history_search tool finds entries again by keyword (BM25, no model calls — keyword search
recovers most facts a summary drops). Stored as JSONL in the chat's own folder:
    <data>/agent_attachments/<chat>/archive/entries.jsonl
"""
import json
import math
import os
import re
import threading

_lock = threading.Lock()
_WORD = re.compile(r"[A-Za-z_]{2,}|\d+")              # words of 2+ letters, and every number (ids, lines, years)


def _dir(conv):
    import paths
    base = paths.data_dir()
    return os.path.join(base, "agent_attachments", re.sub(r"[^A-Za-z0-9_-]", "_", str(conv or "misc"))[:64], "archive")


def _path(conv):
    return os.path.join(_dir(conv), "entries.jsonl")


def entries(conv):
    p = _path(conv)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def add(conv, kind, label, text):
    """Archive one item; -> its id ('E12')."""
    with _lock:
        os.makedirs(_dir(conv), exist_ok=True)
        n = len(entries(conv)) + 1
        e = {"id": f"E{n}", "kind": kind, "label": str(label)[:200], "text": str(text)}
        with open(_path(conv), "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return e["id"]


def get(conv, entry_id):
    eid = str(entry_id).strip().lstrip("#").upper()
    for e in entries(conv):
        if e["id"] == eid:
            return e
    return None


def search(conv, query, limit=5):
    """BM25 over the archive -> [(score, entry, snippet)]."""
    es = entries(conv)
    q = [w.lower() for w in _WORD.findall(query or "")]
    if not es or not q:
        return []
    docs = [[w.lower() for w in _WORD.findall(e["label"] + " " + e["text"])] for e in es]
    n, avg = len(docs), sum(len(d) for d in docs) / max(1, len(docs))
    df = {t: sum(1 for d in docs if t in d) for t in set(q)}
    res = []
    for e, d in zip(es, docs):
        if not d:
            continue
        tf = {}
        for w in d:
            if w in df:
                tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for t in set(q):
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        low = (e["label"] + " " + e["text"]).lower()
        for chunk in (query or "").lower().split():      # exact identifiers / phrases as typed: strong signal
            if len(chunk) >= 3 and chunk in low:
                s += 3.0
        if (query or "").strip().lower() in low:
            s += 5.0
        if s > 0:
            res.append((s, e, _snippet(e["text"], q, exact=(query or "").lower().split())))
    res.sort(key=lambda r: -r[0])
    return res[:max(1, int(limit))]


def _snippet(text, q, width=320, exact=()):
    low = text.lower()
    pos = next((low.find(c) for c in exact if len(c) >= 3 and low.find(c) >= 0), None)
    if pos is None:
        pos = min((low.find(t) for t in q if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    s = " ".join(text[start:start + width].split())
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")
