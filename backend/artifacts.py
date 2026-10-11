"""Artifacts: documents the agent creates and keeps editing during a conversation (reports, pages, diagrams,
code, tables). Each conversation has its own folder; every write is a new version, so nothing is lost.

  agent_artifacts/<conv>/<id>/meta.json     {id, title, type, language, versions: [{v, t, chars}]}
  agent_artifacts/<conv>/<id>/v<N>.<ext>
"""
import json
import os
import re
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
ROOT = paths.data("agent_artifacts")
TYPES = {"markdown": "md", "html": "html", "svg": "svg", "mermaid": "mmd", "chart": "vl.json", "code": "txt",
         "csv": "csv", "json": "json", "text": "txt"}
LANG_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "powershell": "ps1", "bash": "sh", "sql": "sql",
            "c": "c", "cpp": "cpp", "csharp": "cs", "java": "java", "go": "go", "rust": "rs", "yaml": "yaml",
            "css": "css", "tsx": "tsx", "jsx": "jsx", "r": "r", "latex": "tex"}
MAX_CHARS = 400_000
_lock = threading.Lock()


class ArtifactError(Exception):
    pass


def _safe(s):
    return re.sub(r"[^\w-]", "_", str(s or "misc"))[:80] or "misc"


def conv_dir(conv):
    return os.path.join(ROOT, _safe(conv))


def _meta_path(conv, aid):
    return os.path.join(conv_dir(conv), _safe(aid), "meta.json")


def _load(conv, aid):
    try:
        with open(_meta_path(conv, aid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise ArtifactError(f"no artifact '{aid}' in this conversation (artifact_list shows them)")


def _save_meta(conv, meta):
    p = _meta_path(conv, meta["id"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, p)


def _ext(meta):
    if meta["type"] == "code":
        return LANG_EXT.get((meta.get("language") or "").lower(), "txt")
    return TYPES.get(meta["type"], "txt")


def version_path(conv, aid, v):
    meta = _load(conv, aid)
    return os.path.join(conv_dir(conv), _safe(aid), f"v{int(v)}.{_ext(meta)}")


def list_artifacts(conv):
    d = conv_dir(conv)
    out = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            try:
                out.append(_load(conv, name))
            except ArtifactError:
                continue
    return sorted(out, key=lambda m: m.get("created", 0))


def read(conv, aid, v=None):
    meta = _load(conv, aid)
    v = int(v or meta["versions"][-1]["v"])
    if not any(x["v"] == v for x in meta["versions"]):
        raise ArtifactError(f"artifact {aid} has no version {v} (latest is v{meta['versions'][-1]['v']})")
    with open(version_path(conv, aid, v), encoding="utf-8") as f:
        return meta, f.read(), v


def _write_version(conv, meta, content, note=""):
    if len(content) > MAX_CHARS:
        raise ArtifactError(f"artifact too large ({len(content):,} characters; limit {MAX_CHARS:,})")
    v = (meta["versions"][-1]["v"] + 1) if meta["versions"] else 1
    meta["versions"].append({"v": v, "t": time.time(), "chars": len(content), "note": note[:200]})
    meta["updated"] = time.time()
    p = os.path.join(conv_dir(conv), _safe(meta["id"]), f"v{v}.{_ext(meta)}")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    _save_meta(conv, meta)
    return v


def create(conv, title, atype, content, language=""):
    atype = (atype or "markdown").lower().strip()
    atype = {"md": "markdown", "htm": "html", "mmd": "mermaid", "diagram": "mermaid", "txt": "text",
             "vegalite": "chart", "vega-lite": "chart", "vega": "chart", "plot": "chart", "graph": "chart"}.get(atype, atype)
    if atype not in TYPES:
        if atype in LANG_EXT:
            atype, language = "code", atype
        else:
            raise ArtifactError(f"unknown type '{atype}'; use one of: {', '.join(TYPES)}")
    if not str(content or "").strip():
        raise ArtifactError("content is empty")
    if atype == "chart":
        try:
            spec = json.loads(content) if isinstance(content, str) else content
            if not isinstance(spec, dict) or not any(k in spec for k in ("mark", "layer", "hconcat", "vconcat", "concat", "facet", "repeat")):
                raise ValueError("needs a 'mark' (or layer/concat/facet)")
            content = json.dumps(spec, indent=1)
        except ValueError as e:
            raise ArtifactError(f"a chart must be a Vega-Lite JSON spec with inline data ({e})")
    with _lock:
        os.makedirs(conv_dir(conv), exist_ok=True)
        n = 1 + max([int(m["id"][1:]) for m in list_artifacts(conv) if re.match(r"^a\d+$", m["id"])] or [0])
        aid = f"a{n}"
        os.makedirs(os.path.join(conv_dir(conv), aid), exist_ok=True)
        meta = {"id": aid, "title": (title or "Untitled").strip()[:120], "type": atype,
                "language": (language or "").lower(), "created": time.time(), "versions": []}
        v = _write_version(conv, meta, str(content), "created")
    return meta, v


def edit(conv, aid, content=None, old=None, new=None, title=None, note=""):
    """Full rewrite (content) or a targeted replacement (old -> new, old must match exactly once)."""
    with _lock:
        meta, cur, _v = read(conv, aid)
        if content is not None and str(content).strip():
            text = str(content)
        elif old is not None:
            n = cur.count(old)
            if n == 0:
                # the same forgiving match as edit_file: whitespace/indentation differences, then the closest region
                # in the error (it failed 6 of 9 times in one comparison task, ~2 minutes of retries)
                import agent_tools as _at
                found = _at._loose_match(cur, old)
                if not found:
                    raise ArtifactError("'old' text not found in the artifact; copy it exactly (artifact_read shows the "
                                        "current version)." + _at._closest_region(cur, old))
                start, end, shift = found
                old = cur[start:end]
                new = _at._reindent(new or "", shift, "\r\n" if "\r\n" in old else "\n")
                n = 1
            if n > 1:
                raise ArtifactError(f"'old' text appears {n} times; include more surrounding text so it is unique")
            text = cur.replace(old, new or "", 1)
        else:
            raise ArtifactError("give either the full new content, or old + new for a targeted edit")
        if title:
            meta["title"] = title.strip()[:120]
        if text == cur and not title:
            raise ArtifactError("no change")
        v = _write_version(conv, meta, text, note or ("edited" if old is not None else "rewritten"))
    return meta, v, cur, text
