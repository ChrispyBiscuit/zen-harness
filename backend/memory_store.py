"""Long-term memory and conversation search: one SQLite file, hybrid (keyword + meaning) retrieval.

  - keyword side: SQLite FTS5 (BM25 ranking, porter stemming)
  - meaning side: bge-small-en-v1.5 embeddings (384-d, ONNX on the CPU via fastembed; ~3 ms a text) kept in a
    NumPy matrix and searched brute force (a few ms for 100k rows, no vector-DB server)
  - the two rankings are fused with reciprocal-rank fusion; a small recency bonus breaks ties
If the embedding model is unavailable, search falls back to keywords only.

Kinds: "memory" (facts the agent saved; editable), "chat" (chunks of past conversations; indexed automatically).
"""
import json
import os
import re
import sqlite3
import threading
import time

import numpy as np

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
DB_PATH = paths.data("agent_memory.db")
OLD_MEMORY_MD = paths.data("agent_memory.md")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_CACHE = os.path.join(paths.models_dir(), "fastembed")
EMBED_SIZE = 70 << 20          # the quantized ONNX model + tokenizer, roughly (progress bar only)


def _embed_cached():
    """Is the embedding model already in the fastembed cache (any .onnx file under it)?"""
    for dp, _, files in os.walk(EMBED_CACHE) if os.path.isdir(EMBED_CACHE) else []:
        if any(f.endswith(".onnx") for f in files):
            return True
    return False
DIM = 384
RRF_K = 60
CHUNK = 900                         # characters per conversation chunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, tags TEXT DEFAULT '',
  source TEXT DEFAULT '', title TEXT DEFAULT '', role TEXT DEFAULT '', pos INTEGER DEFAULT 0,
  created REAL, updated REAL, vec BLOB);
CREATE INDEX IF NOT EXISTS items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS items_source ON items(source);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(text, tags, title, content='items', content_rowid='id',
  tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, text, tags, title) VALUES (new.id, new.text, new.tags, new.title); END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, text, tags, title) VALUES ('delete', old.id, old.text, old.tags, old.title); END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, text, tags, title) VALUES ('delete', old.id, old.text, old.tags, old.title);
  INSERT INTO items_fts(rowid, text, tags, title) VALUES (new.id, new.text, new.tags, new.title); END;
CREATE TABLE IF NOT EXISTS indexed(conv TEXT PRIMARY KEY, upto INTEGER, sig TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS filemeta(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, chunks INTEGER);
"""


_PSEUDO_RX = re.compile(r"<(update_tracker|tool_call|function[=\w]*|parameter[=\w]*)\b[\s\S]*?(/>|</\1>|$)")


class MemoryStore:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;" + _SCHEMA)
        self._model = None
        self._model_err = None
        self._model_lock = threading.Lock()
        self._mat = {}               # kind -> (ids ndarray, matrix ndarray) cache of normalized vectors
        self._migrate_markdown()

    # ------------------------------------------------------------------ embeddings
    def _embedder(self):
        if self._model is None and self._model_err is None:
            if not _embed_cached():
                self._ask_for_model()                     # first use: ask (in the background); keyword search meanwhile
                return None
            with self._model_lock:
                if self._model is None and self._model_err is None:
                    try:
                        from fastembed import TextEmbedding
                        self._model = TextEmbedding(EMBED_MODEL, cache_dir=EMBED_CACHE, threads=4)
                    except Exception as e:                    # no model: keyword search still works
                        self._model_err = repr(e)
        return self._model

    def _ask_for_model(self):
        """The embedding model isn't downloaded yet: ask the user once (downloads.py), then fetch it in the background.
        Until then (or if declined) memory search is keyword-only."""
        if getattr(self, "_asking", False):
            return
        self._asking = True

        def run():
            import downloads
            ok, rid = downloads.ask("embeddings", "Memory search model (bge-small, about 70 MB)",
                                    "Lets memory search find notes by meaning, not just matching words. Downloaded "
                                    "once; it runs on your CPU and nothing you store leaves this PC.",
                                    EMBED_SIZE, "huggingface.co (Qdrant/bge-small-en-v1.5-onnx-q)")
            if not ok:
                self._model_err = "the download was declined: keyword search only"
                return
            downloads.track(rid, EMBED_CACHE, EMBED_SIZE)
            try:
                from fastembed import TextEmbedding
                with self._model_lock:
                    self._model = TextEmbedding(EMBED_MODEL, cache_dir=EMBED_CACHE, threads=4)
                downloads.finish(rid, True)
            except Exception as e:
                self._model_err = repr(e)
                downloads.finish(rid, False, e)
        threading.Thread(target=run, daemon=True, name="memory-embedder-ask").start()

    def embed(self, texts, query=False):
        m = self._embedder()
        if m is None or not texts:
            return None
        vecs = list(m.query_embed(texts) if query else m.embed(texts, batch_size=64))
        arr = np.asarray(vecs, dtype=np.float32)
        arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
        return arr

    def warm(self):
        """Load the embedding model in the background (first load takes a few seconds)."""
        threading.Thread(target=self._embedder, daemon=True, name="memory-embedder").start()

    # ------------------------------------------------------------------ write
    def add(self, kind, text, tags="", source="", title="", role="", pos=0, created=None):
        text = text.strip()
        if not text:
            raise ValueError("empty text")
        vec = self.embed([text])
        now = time.time()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO items(kind,text,tags,source,title,role,pos,created,updated,vec) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, text, tags, source, title, role, pos, created or now, now,
                 vec[0].tobytes() if vec is not None else None))
            self.db.commit()
            self._mat.pop(kind, None)
            return cur.lastrowid

    def update(self, item_id, text=None, tags=None):
        with self.lock:
            row = self.get(item_id)
            if not row:
                raise KeyError(f"no memory with id {item_id}")
            text = row["text"] if text is None else text.strip()
            tags = row["tags"] if tags is None else tags
            vec = self.embed([text]) if text != row["text"] else None
            self.db.execute("UPDATE items SET text=?, tags=?, updated=?" + (", vec=?" if vec is not None else "")
                            + " WHERE id=?", (text, tags, time.time()) + ((vec[0].tobytes(),) if vec is not None else ())
                            + (item_id,))
            self.db.commit()
            self._mat.pop(row["kind"], None)
            return self.get(item_id)

    def delete(self, item_id):
        with self.lock:
            row = self.get(item_id)
            if not row:
                raise KeyError(f"no memory with id {item_id}")
            self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
            self.db.commit()
            self._mat.pop(row["kind"], None)
            return row

    def get(self, item_id):
        with self.lock:
            r = self.db.execute("SELECT id,kind,text,tags,source,title,role,pos,created,updated FROM items WHERE id=?",
                                (int(item_id),)).fetchone()
            return dict(r) if r else None

    def list(self, kind="memory", limit=200):
        with self.lock:
            rows = self.db.execute("SELECT id,kind,text,tags,source,title,created,updated FROM items WHERE kind=? "
                                   "ORDER BY updated DESC LIMIT ?", (kind, limit)).fetchall()
            return [dict(r) for r in rows]

    def count(self, kind):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM items WHERE kind=?", (kind,)).fetchone()[0]

    # ------------------------------------------------------------------ search
    @staticmethod
    def _fts_query(q):
        words = re.findall(r"[\w'-]+", q.lower())
        words = [w.strip("'-") for w in words if len(w.strip("'-")) > 1][:24]
        return " OR ".join(f'"{w}"' for w in words)

    def _matrix(self, kind):
        if kind not in self._mat:
            rows = self.db.execute("SELECT id, vec FROM items WHERE kind=? AND vec IS NOT NULL", (kind,)).fetchall()
            ids = np.array([r["id"] for r in rows], dtype=np.int64)
            mat = (np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(-1, DIM)
                   if rows else np.zeros((0, DIM), dtype=np.float32))
            self._mat[kind] = (ids, mat)
        return self._mat[kind]

    def search(self, query, kinds=("memory",), k=8, exclude_source=None, min_sim=None):
        """Hybrid search -> [dict(id, kind, text, title, source, score, sim, created, ...)] best first.
        min_sim: drop results whose meaning similarity is below this (when embeddings are available)."""
        query = (query or "").strip()
        if not query:
            return []
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        ph = ",".join("?" * len(kinds))
        pool = max(k * 5, 40)
        ranks, sims = {}, {}
        with self.lock:
            fq = self._fts_query(query)
            if fq:
                sql = (f"SELECT items.id FROM items_fts JOIN items ON items.id = items_fts.rowid "
                       f"WHERE items_fts MATCH ? AND items.kind IN ({ph})"
                       + (" AND items.source != ?" if exclude_source else "")
                       + " ORDER BY bm25(items_fts, 1.0, 0.6, 0.4) LIMIT ?")
                args = [fq] + kinds + ([exclude_source] if exclude_source else []) + [pool]
                try:
                    for rank, r in enumerate(self.db.execute(sql, args).fetchall()):
                        ranks.setdefault(r["id"], {})["kw"] = rank
                except sqlite3.OperationalError:
                    pass
            qv = self.embed([query], query=True)
            if qv is not None:
                for kind in kinds:
                    ids, mat = self._matrix(kind)
                    if not len(ids):
                        continue
                    s = mat @ qv[0]
                    top = np.argsort(-s)[:pool]
                    for idx in top:
                        sims[int(ids[idx])] = float(s[idx])
                order = sorted(sims, key=lambda i: -sims[i])
                for rank, i in enumerate(order):
                    ranks.setdefault(i, {})["vec"] = rank
            if not ranks:
                return []
            rows = {r["id"]: dict(r) for r in self.db.execute(
                f"SELECT id,kind,text,tags,source,title,role,pos,created,updated FROM items WHERE id IN "
                f"({','.join(str(i) for i in ranks)})").fetchall()}
        now = time.time()
        out = []
        for i, rk in ranks.items():
            r = rows.get(i)
            if not r or (exclude_source and r["source"] == exclude_source):
                continue
            sim = sims.get(i)
            if min_sim is not None and qv is not None and (sim is None or sim < min_sim):
                continue
            score = sum(1.0 / (RRF_K + v) for v in rk.values())
            age_days = (now - (r["updated"] or now)) / 86400
            score += 0.002 / (1 + age_days / 30)               # newer wins ties
            r.update(score=round(score, 5), sim=round(sim, 3) if sim is not None else None,
                     matched="+".join(sorted(rk)))
            out.append(r)
        out.sort(key=lambda r: -r["score"])
        seen, uniq = set(), []
        for r in out:                                          # the same text indexed twice: show it once
            key = " ".join(r["text"].split())[:200].lower()
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        return uniq[:k]

    # ------------------------------------------------------------------ conversations
    def index_conversations(self, convs, skip=None):
        """Index new messages of the given conversations (incremental). -> number of chunks added."""
        added = 0
        for c in convs:
            cid = c.get("id")
            if not cid or cid == skip:
                continue
            msgs = c.get("messages") or []
            with self.lock:
                row = self.db.execute("SELECT upto, sig FROM indexed WHERE conv=?", (cid,)).fetchone()
            upto = row["upto"] if row else 0
            first = (msgs[0].get("content") or "")[:80] if msgs else ""
            sig = f"{len(msgs)}|{first}"
            if row and row["sig"] == sig:
                continue
            if row and (upto > len(msgs) or not row["sig"].endswith("|" + first)):
                self.forget_conversation(cid)                  # edited / truncated: rebuild it
                upto = 0
            chunks = []
            for pos in range(upto, len(msgs)):
                m = msgs[pos]
                if m.get("kind") or m.get("streaming"):
                    continue                                   # tool cards, plans, dividers: not conversation
                text = _PSEUDO_RX.sub(" ", (m.get("content") or "")).strip()
                if len(text) < 12 or text.startswith(("(stopped", "(Paused", "{", "[", "<")):
                    continue
                for j in range(0, min(len(text), CHUNK * 6), CHUNK):
                    chunks.append((text[j:j + CHUNK + 100], m.get("role", ""), pos, m.get("t")))
            if chunks:
                vecs = self.embed([t for t, *_ in chunks])
                now = time.time()
                with self.lock:
                    self.db.executemany(
                        "INSERT INTO items(kind,text,source,title,role,pos,created,updated,vec) VALUES (?,?,?,?,?,?,?,?,?)",
                        [("chat", t, cid, c.get("title", ""), role, pos, ts or c.get("updated") or now,
                          c.get("updated") or now, vecs[n].tobytes() if vecs is not None else None)
                         for n, (t, role, pos, ts) in enumerate(chunks)])
                    self._mat.pop("chat", None)
                added += len(chunks)
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO indexed(conv, upto, sig, updated) VALUES (?,?,?,?)",
                                (cid, len(msgs), sig, time.time()))
                self.db.commit()
        return added

    def forget_conversation(self, cid):
        with self.lock:
            self.db.execute("DELETE FROM items WHERE kind='chat' AND source=?", (cid,))
            self.db.execute("DELETE FROM indexed WHERE conv=?", (cid,))
            self.db.commit()
            self._mat.pop("chat", None)

    def prune_conversations(self, live_ids):
        """Drop the index of conversations that were deleted."""
        with self.lock:
            known = [r["conv"] for r in self.db.execute("SELECT conv FROM indexed").fetchall()]
        for cid in known:
            if cid not in live_ids:
                self.forget_conversation(cid)

    # ------------------------------------------------------------------ files in the user's folders
    FILE_EXT = {".txt", ".md", ".markdown", ".rst", ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".tsv", ".html",
                ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".ps1", ".sql", ".yaml", ".yml", ".ini", ".toml", ".rtf",
                ".java", ".cs", ".go", ".rs", ".c", ".cpp", ".h", ".css"}
    SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "env", "__pycache__", "site-packages", "dist", "build", ".next",
                 "llama.cpp", "models", "web_dist", "agent_sandbox", "agent_artifacts", "agent_trash", "agent_trackers",
                 "agent_browser_profile", "lwsdata", ".cache", "AppData", "$RECYCLE.BIN"}
    FILE_CHARS = 12000          # read at most this much of each file (beginning: titles, summaries, imports)
    FILE_CHUNK = 1500           # -> at most 8 passages per file

    def _file_text(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".pdf", ".docx", ".pptx", ".xlsx"):
            import doc_extract
            info = doc_extract.extract(path, max_chars=self.FILE_CHARS)
            return (info or {}).get("text") or ""
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(self.FILE_CHARS)

    def index_files(self, roots, should_stop=lambda: False, max_files=5000, max_chunks=40000):
        """Index new/changed files under roots, drop deleted ones. -> (files indexed, chunks added)."""
        seen, todo = set(), []
        for root in roots or []:
            if not os.path.isdir(root):
                continue
            for d, dirs, files in os.walk(root):
                dirs[:] = [x for x in dirs if x not in self.SKIP_DIRS and not x.startswith(".")]
                for fn in files:
                    p = os.path.join(d, fn)
                    if os.path.splitext(fn)[1].lower() not in self.FILE_EXT:
                        continue
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_size > 15 * 1024 * 1024 or st.st_size == 0:
                        continue
                    seen.add(p)
                    todo.append((p, st.st_mtime, st.st_size))
                    if len(seen) >= max_files:
                        break
                if len(seen) >= max_files:
                    break
        with self.lock:
            known = {r["path"]: (r["mtime"], r["size"]) for r in self.db.execute("SELECT path, mtime, size FROM filemeta")}
            total = self.count("file")
        for p in [k for k in known if k not in seen]:                       # deleted (or no longer in the roots)
            self._forget_file(p)
        done = added = 0
        for p, mtime, size in todo:
            if should_stop():
                break
            if known.get(p) == (mtime, size):
                continue
            if total + added >= max_chunks:
                break
            try:
                text = self._file_text(p)
            except Exception:
                text = ""
            self._forget_file(p, keep_meta=True)
            chunks = []
            body = " ".join(text.split())
            for j in range(0, len(body), self.FILE_CHUNK):
                piece = body[j:j + self.FILE_CHUNK + 120]
                if len(piece.strip()) > 30:
                    chunks.append(piece)
            vecs = self.embed(chunks) if chunks else None
            now = time.time()
            with self.lock:
                if chunks:
                    self.db.executemany(
                        "INSERT INTO items(kind,text,source,title,pos,created,updated,vec) VALUES (?,?,?,?,?,?,?,?)",
                        [("file", c, p, os.path.basename(p), n, mtime, now, vecs[n].tobytes() if vecs is not None else None)
                         for n, c in enumerate(chunks)])
                self.db.execute("INSERT OR REPLACE INTO filemeta(path, mtime, size, chunks) VALUES (?,?,?,?)",
                                (p, mtime, size, len(chunks)))
                self.db.commit()
                self._mat.pop("file", None)
            done += 1
            added += len(chunks)
            time.sleep(0.02)                     # stay polite to the CPU the agent's model is using
        return done, added

    def _forget_file(self, path, keep_meta=False):
        with self.lock:
            self.db.execute("DELETE FROM items WHERE kind='file' AND source=?", (path,))
            if not keep_meta:
                self.db.execute("DELETE FROM filemeta WHERE path=?", (path,))
            self.db.commit()
            self._mat.pop("file", None)

    def file_stats(self):
        with self.lock:
            r = self.db.execute("SELECT COUNT(*), COALESCE(SUM(chunks),0) FROM filemeta").fetchone()
            return {"files": r[0], "chunks": r[1]}

    # ------------------------------------------------------------------ prompt helpers
    def prompt_block(self, max_chars=3500):
        """Saved memories for the system prompt (newest first, capped)."""
        out, used = [], 0
        for r in self.list("memory", 400):
            line = f"- [m{r['id']}] {r['text']}" + (f"  (tags: {r['tags']})" if r["tags"] else "")
            if used + len(line) > max_chars:
                out.append(f"- …and {self.count('memory') - len(out)} more: use memory_search.")
                break
            out.append(line)
            used += len(line) + 1
        return "\n".join(out)

    def _migrate_markdown(self):
        """Bring facts from the old agent_memory.md (one '- ' line each) into the store, once."""
        if not os.path.exists(OLD_MEMORY_MD) or self.count("memory"):
            return
        try:
            with open(OLD_MEMORY_MD, encoding="utf-8") as f:
                lines = [ln.strip()[2:].strip() for ln in f if ln.strip().startswith("- ")]
        except OSError:
            return
        for ln in lines:
            ln = re.sub(r"^\(?\d{4}-\d{2}-\d{2}[^:)]*[):]\s*", "", ln)
            if ln:
                self.add("memory", ln, tags="imported")
        if lines:
            os.replace(OLD_MEMORY_MD, OLD_MEMORY_MD + ".imported")


_STORE = None
_STORE_LOCK = threading.Lock()


def store():
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemoryStore()
        return _STORE


def fmt_age(ts):
    if not ts:
        return ""
    d = (time.time() - ts) / 86400
    return "today" if d < 1 else f"{int(d)}d ago" if d < 60 else time.strftime("%Y-%m-%d", time.localtime(ts))
