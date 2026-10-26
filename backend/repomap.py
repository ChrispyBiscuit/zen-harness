"""Code context for small models — an Aider-style repo map plus symbol and search tools (PRD M6, E1–E3).

Every code file under a project root is parsed once for its definitions (classes, functions, methods, constants,
components…) and the identifiers it references. Files become nodes of a directed graph (A → B weighted by how often
A uses names defined in B); personalised PageRank, boosted towards the files in play and the identifiers named in
the request, ranks them; the best files' most-referenced definitions are rendered as a compact outline that fits a
token budget. No tree-sitter: Python uses `ast`, everything else pygments tokens plus light regexes.

The index is cached per root as JSON and refreshed by file mtime, so only edited files are re-parsed:
    <data>/repomap/<sha1(root.lower())[:12]>.json
"""
import ast
import bisect
import fnmatch
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict

_VERSION = 4                                         # 4: tree-sitter definitions
_MAX_FILES = 4000
_MAX_BYTES = 1_000_000
_MAX_SEARCH_FILES = 20000
_SIG_LEN = 160
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000            # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS (OneDrive)
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".icns", ".pdf", ".zip", ".gz", ".7z",
               ".tar", ".rar", ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".npy", ".npz", ".db", ".sqlite",
               ".db-wal", ".db-shm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".wav", ".ogg",
               ".webm", ".mov", ".avi", ".gguf", ".bin", ".safetensors", ".pt", ".onnx", ".jar", ".class", ".docx",
               ".xlsx", ".pptx", ".psd", ".blend", ".lock-wal"}
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target",
               "coverage", ".zen", ".hg", ".svn", ".idea", ".mypy_cache", ".pytest_cache", ".tox", ".cache"}

_LANG = {
    ".py": "python", ".pyw": "python",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "jsx",
    ".ts": "ts", ".mts": "ts", ".cts": "ts", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".php": "php", ".rb": "ruby", ".lua": "lua", ".gd": "gdscript", ".swift": "swift",
    ".css": "css", ".scss": "css", ".less": "css", ".html": "html", ".htm": "html",
    ".vue": "html", ".svelte": "html",
}
_PYG_NAME = {"js": "javascript", "jsx": "jsx", "ts": "typescript", "tsx": "tsx", "go": "go", "rust": "rust",
             "java": "java", "kotlin": "kotlin", "csharp": "csharp", "c": "c", "cpp": "cpp", "php": "php",
             "ruby": "ruby", "lua": "lua", "gdscript": "gdscript", "swift": "swift", "css": "css", "html": "html"}

_lock = threading.Lock()
_lexers = {}
LAST_INDEX_STATS = {}                                   # {"files", "parsed", "removed", "seconds"} of the last index()
LAST_SEARCH_STATS = {}                                  # {"files", "truncated"} of the last search()
SEARCH_SECONDS = 15.0                                   # wall-clock cap for one search()
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_WORD = re.compile(r"[A-Za-z_$][\w$]*")


# ── paths, ignore rules, cache ────────────────────────────────────────────────────────────────────────────────────
def _data_dir():
    import paths
    base = paths.data_dir()
    return os.path.join(base, "repomap")


def _norm_root(root):
    return os.path.normpath(os.path.abspath(str(root)))


def _cache_path(root):
    rid = hashlib.sha1(_norm_root(root).lower().encode("utf-8")).hexdigest()[:12]
    return os.path.join(_data_dir(), rid + ".json")


def _gitignore(root):
    """Simple root .gitignore rules -> [(pattern, dir_only, anchored)]. Negations are ignored (approximate)."""
    rules = []
    try:
        with open(os.path.join(root, ".gitignore"), encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("!"):
                    continue
                dir_only = s.endswith("/")
                s = s.strip("/")
                if not s:
                    continue
                rules.append((s, dir_only, "/" in s))
    except OSError:
        pass
    return rules


def _ignored(rel, name, is_dir, rules):
    for pat, dir_only, anchored in rules:
        if dir_only and not is_dir:
            continue
        if anchored:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*"):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


def _walk(root, code_only=True, max_files=_MAX_FILES):
    """Yield (relpath with '/', abspath, stat) for files under root, honouring IGNORE_DIRS, .gitignore and size caps.
    Uses scandir so the stat comes free with the directory listing on Windows."""
    rules = _gitignore(root)
    n = 0
    stack = [(root, "")]
    while stack:
        dirpath, reld = stack.pop()
        try:
            with os.scandir(dirpath) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subdirs = []
        for e in entries:
            fn = e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if fn not in IGNORE_DIRS and not fn.endswith(".egg-info") and \
                            not _ignored(reld + fn, fn, True, rules):
                        subdirs.append((e.path, reld + fn + "/"))
                    continue
                if not e.is_file():
                    continue
            except OSError:
                continue
            if code_only and os.path.splitext(fn)[1].lower() not in _LANG:
                continue
            if fn.endswith((".min.js", ".min.css", ".bundle.js")) or _ignored(reld + fn, fn, False, rules):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            if st.st_size > _MAX_BYTES or getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY:
                continue                             # too big, or a cloud placeholder that reading would download
            yield reld + fn, e.path, st
            n += 1
            if n >= max_files:
                return
        stack.extend(reversed(subdirs))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _minified(text):
    lines = text.count("\n") + 1
    return len(text) > 2000 and len(text) / lines > 300


def _sig(line):
    s = line.strip()
    return s if len(s) <= _SIG_LEN else s[:_SIG_LEN - 1] + "…"


# ── extraction ────────────────────────────────────────────────────────────────────────────────────────────────────
def _lexer(lang):
    if lang not in _lexers:
        from pygments.lexers import get_lexer_by_name
        try:
            lx = get_lexer_by_name(_PYG_NAME[lang], stripnl=False, ensurenl=False)
        except Exception:
            lx = None
            if lang in ("jsx", "tsx"):
                try:
                    lx = get_lexer_by_name("typescript" if lang == "tsx" else "javascript", stripnl=False, ensurenl=False)
                except Exception:
                    lx = None
        _lexers[lang] = lx
    return _lexers[lang]


def _tokens(text, lang):
    """-> [(offset, ttype, value)] of name / function / class tokens."""
    lx = _lexer(lang)
    if lx is None:
        return []
    from pygments.token import Name
    out = []
    try:
        for off, tt, val in lx.get_tokens_unprocessed(text):
            if tt in Name:
                out.append((off, tt, val))
    except Exception:
        pass
    return out


def _py_extract(text):
    tree = ast.parse(text)
    lines = text.splitlines()
    defs, refs = [], Counter()

    def line_of(n):
        return lines[n - 1] if 0 < n <= len(lines) else ""

    def visit_body(body, parent):
        for node in body:
            if isinstance(node, ast.ClassDef):
                d = {"name": node.name, "kind": "class", "line": node.lineno, "sig": _sig(line_of(node.lineno))}
                if parent:
                    d["parent"] = parent
                defs.append(d)
                visit_body(node.body, (parent + "." if parent else "") + node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                d = {"name": node.name, "kind": "method" if parent else "function", "line": node.lineno,
                     "sig": _sig(line_of(node.lineno))}
                if parent:
                    d["parent"] = parent
                defs.append(d)
            elif not parent and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for nm in ([t] if isinstance(t, ast.Name) else getattr(t, "elts", [])):
                        if isinstance(nm, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", nm.id) and len(nm.id) > 1:
                            defs.append({"name": nm.id, "kind": "const", "line": node.lineno,
                                         "sig": _sig(line_of(node.lineno))})
            elif isinstance(node, (ast.If, ast.Try)) and not parent:   # `if TYPE_CHECKING:` / try-import blocks
                visit_body(node.body, parent)

    visit_body(tree.body, None)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs[node.id] += 1
        elif isinstance(node, ast.Attribute):
            refs[node.attr] += 1
        elif isinstance(node, ast.alias):
            refs[(node.asname or node.name).split(".")[-1]] += 1
            if node.asname:
                refs[node.name.split(".")[-1]] += 1
    return defs, refs


_JS_KW = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else", "do", "try", "new",
          "typeof", "await", "constructor_", "super", "import", "export", "case", "throw", "delete", "void", "yield"}
_JS_FUNC = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]")
_JS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_TYPE = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?(interface|type|enum)\s+([A-Za-z_$][\w$]*)")
_JS_VAR = re.compile(r"^(\s*)(export\s+)?(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(.*)")
_JS_ARROW = re.compile(r"^(?:async\s+)?(?:<[^>]*>\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?=>|^(?:async\s+)?function\b")
_JS_COMP = re.compile(r"^(?:React\.)?(?:memo|forwardRef)\s*[(<]")
_JS_METHOD = re.compile(r"^(\s+)(?:(?:public|private|protected|static|async|get|set|readonly|override|abstract)\s+)*"
                        r"\*?([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^)]*\)?\s*(?::\s*[^{=]+)?\{\s*$")
_JS_PROP_FN = re.compile(r"^(\s+)([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?=>")


def _js_defs(text, line_offset=0):
    defs = []
    stack = []                                       # [(indent, kind, name)] — open class / interface blocks
    for i, line in enumerate(text.splitlines(), 1 + line_offset):
        s = line.strip()
        if not s or s.startswith(("//", "*", "/*")):
            continue
        ind = len(line) - len(line.lstrip())
        while stack and ind <= stack[-1][0]:         # dedent closes the open class / interface block
            stack.pop()
            if s.startswith("}"):
                break
        sig = _sig(line)
        m = _JS_CLASS.match(line)
        if m:
            defs.append({"name": m.group(1), "kind": "class", "line": i, "sig": sig})
            if not s.endswith("}"):
                stack.append((ind, "class", m.group(1)))
            continue
        m = _JS_TYPE.match(line)
        if m:
            defs.append({"name": m.group(2), "kind": m.group(1), "line": i, "sig": sig})
            if s.endswith("{"):
                stack.append((ind, "type", m.group(2)))
            continue
        m = _JS_FUNC.match(line)
        if m:
            nm = m.group(1)
            defs.append({"name": nm, "kind": "component" if nm[0].isupper() else "function", "line": i, "sig": sig})
            continue
        m = _JS_VAR.match(line)
        if m:
            indent, exported, nm, rhs = m.groups()
            if _JS_ARROW.match(rhs):
                kind = "component" if nm[0].isupper() else "function"
            elif _JS_COMP.match(rhs) and nm[0].isupper():
                kind = "component"
            else:
                kind = "const"
            if exported or not indent:               # top-level only; locals inside components are noise
                defs.append({"name": nm, "kind": kind, "line": i, "sig": sig})
            continue
        in_type = stack and stack[-1][1] == "type"
        if in_type:
            continue
        m = _JS_METHOD.match(line)
        if m and m.group(2) not in _JS_KW:
            d = {"name": m.group(2), "kind": "method", "line": i, "sig": sig}
            if stack and stack[-1][1] == "class":
                d["parent"] = stack[-1][2]
            defs.append(d)
            continue
        m = _JS_PROP_FN.match(line)
        if m and m.group(2) not in _JS_KW:
            d = {"name": m.group(2), "kind": "method", "line": i, "sig": sig}
            if stack and stack[-1][1] == "class":
                d["parent"] = stack[-1][2]
            defs.append(d)
    return defs


def _token_refs(text, lang):
    from pygments.token import Name
    refs = Counter()
    for _off, tt, val in _tokens(text, lang):
        if tt in Name.Attribute:                     # JSX/HTML attribute names: className, onClick…
            continue
        v = val.rstrip("?!")
        if _IDENT.match(v):
            refs[v] += 1
    return refs


_GENERIC_DEFS = {
    "go": [(re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "function"),
           (re.compile(r"^type\s+([A-Za-z_]\w*)"), "type")],
    "rust": [(re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)"), "function"),
             (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_]\w*)"), "type")],
    "lua": [(re.compile(r"^\s*(?:local\s+)?function\s+([\w.:]+)\s*\("), "function"),
            (re.compile(r"^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*function\b"), "function")],
    "ruby": [(re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)"), "function"),
             (re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)"), "class")],
    "php": [(re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+([A-Za-z_]\w*)"), "function"),
            (re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+([A-Za-z_]\w*)"), "class")],
    "gdscript": [(re.compile(r"^\s*(?:static\s+)?func\s+([A-Za-z_]\w*)"), "function"),
                 (re.compile(r"^\s*(?:class_name|class)\s+([A-Za-z_]\w*)"), "class"),
                 (re.compile(r"^\s*signal\s+([A-Za-z_]\w*)"), "signal"),
                 (re.compile(r"^const\s+([A-Z][A-Z0-9_]*)"), "const")],
    "java": [(re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final|sealed)\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"), "class")],
    "kotlin": [(re.compile(r"^\s*(?:\w+\s+)*fun\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)"), "function"),
               (re.compile(r"^\s*(?:\w+\s+)*(?:class|interface|object)\s+([A-Za-z_]\w*)"), "class")],
    "csharp": [(re.compile(r"^\s*(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)"), "class")],
    "c": [(re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)\s*\{"), "type")],
    "cpp": [(re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct|enum(?:\s+class)?|union)\s+([A-Za-z_]\w*)\s*(?:[:{]|$)"), "class")],
    "swift": [(re.compile(r"^\s*(?:\w+\s+)*func\s+([A-Za-z_]\w*)"), "function"),
              (re.compile(r"^\s*(?:\w+\s+)*(?:class|struct|enum|protocol)\s+([A-Za-z_]\w*)"), "class")],
}
_CSS_SEL = re.compile(r"^\s*([.#][A-Za-z_-][\w-]*)[^;{}]*(?:\{|,\s*$|$)")
_CSS_VAR = re.compile(r"^\s*(--[\w-]+)\s*:")
_HTML_ID = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""")
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def _line_index(text):
    return [i for i, c in enumerate(text) if c == "\n"]


def _generic_extract(text, lang):
    from pygments.token import Name
    defs, seen = [], set()
    lines = text.splitlines()

    def add(name, kind, line):
        if (name, line) not in seen and name:
            seen.add((name, line))
            defs.append({"name": name, "kind": kind, "line": line, "sig": _sig(lines[line - 1] if line <= len(lines) else "")})

    refs = Counter()
    if lang == "css":
        for i, ln in enumerate(lines, 1):
            m = _CSS_SEL.match(ln)
            if m:
                add(m.group(1), "selector", i)
            m = _CSS_VAR.match(ln)
            if m:
                add(m.group(1), "variable", i)
        for w in re.findall(r"var\((--[\w-]+)", text):
            refs[w] += 1
        return defs, refs
    if lang == "html":
        for i, ln in enumerate(lines, 1):
            for m in _HTML_ID.finditer(ln):
                add(m.group(1), "id", i)
        for m in _SCRIPT.finditer(text):
            body = m.group(1)
            off = text.count("\n", 0, m.start(1))
            for d in _js_defs(body, off):
                add(d["name"], d["kind"], d["line"])
            refs.update(_token_refs(body, "js"))
        return defs, refs
    for rx, kind in _GENERIC_DEFS.get(lang, []):
        for i, ln in enumerate(lines, 1):
            m = rx.match(ln)
            if m:
                add(m.group(1).split(":")[-1].split(".")[-1] if lang == "lua" else m.group(1), kind, i)
    nl = _line_index(text)
    for off, tt, val in _tokens(text, lang):
        v = val.rstrip("?!")
        if not _IDENT.match(v):
            continue
        if tt in Name.Function or tt in Name.Class:
            line = bisect.bisect_right(nl, off) + 1
            if (v, line) not in seen:
                add(v, "class" if tt in Name.Class else "function", line)
                continue
        refs[v] += 1
    return defs, refs


_ES_MODULE = re.compile(r"^\s*(?:import|export)\b", re.M)
_EXPORT_LIST = re.compile(r"\bexport\s*(?:type\s*)?\{([^}]*)\}")
_EXPORT_DEFAULT = re.compile(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$", re.M)


def _mark_module_locals(text, defs):
    """In an ES module, top-level names that are not exported cannot be referenced from other files."""
    if not _ES_MODULE.search(text):
        return
    exported = set(_EXPORT_DEFAULT.findall(text))
    for grp in _EXPORT_LIST.findall(text):
        for part in grp.split(","):
            nm = part.strip().split(" as ")[0].strip()
            if nm:
                exported.add(nm)
    for d in defs:
        if d["kind"] != "method" and not d.get("parent") and not d["sig"].startswith("export") \
                and d["name"] not in exported:
            d["local"] = True


def _extract(text, lang):
    """-> (defs, refs) for one file's text."""
    if lang == "python":
        try:
            return _py_extract(text)
        except (SyntaxError, ValueError, RecursionError):
            defs = []
            for i, ln in enumerate(text.splitlines(), 1):
                m = re.match(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", ln)
                if m:
                    kind = "class" if m.group(2) == "class" else ("method" if m.group(1) else "function")
                    defs.append({"name": m.group(3), "kind": kind, "line": i, "sig": _sig(ln)})
            return defs, Counter(_WORD.findall(text))
    if lang in ("js", "jsx", "ts", "tsx"):
        defs = _ts_defs(text, lang)                  # tree-sitter (plans/code-graph-prd.md P3), else the regexes
        if defs is None:
            defs = _js_defs(text)
        _mark_module_locals(text, defs)
        refs = _token_refs(text, lang)
        for d in defs:                               # a definition is not a reference to itself
            if refs.get(d["name"], 0) > 0:
                refs[d["name"]] -= 1
                if not refs[d["name"]]:
                    del refs[d["name"]]
        return defs, refs
    defs, refs = _generic_extract(text, lang)
    ts = _ts_defs(text, lang) if lang in ("c", "cpp", "go", "rust", "csharp", "java") else None
    if ts:                                           # tree-sitter's definitions; the token references stay
        for d in ts:
            if refs.get(d["name"], 0) > 0:
                refs[d["name"]] -= 1
                if not refs[d["name"]]:
                    del refs[d["name"]]
        return ts, refs
    return defs, refs


def _ts_defs(text, lang):
    """Definitions from tree-sitter (code_graph.ts_defs), or None (no grammar here, or the file doesn't parse)."""
    try:
        import code_graph
        return code_graph.ts_defs(text, lang)
    except Exception:
        return None


def _parse_file(path):
    lang = _LANG.get(os.path.splitext(path)[1].lower())
    if not lang:
        return None
    text = _read(path)
    if text is None or "\x00" in text[:4096] or _minified(text):
        return None
    defs, refs = _extract(text, lang)
    if lang == "html":                               # element ids and inline-script names are page-local
        for d in defs:
            d["local"] = True
    return {"lang": lang, "defs": defs, "refs": dict(refs)}


# ── E1: index + repo map ──────────────────────────────────────────────────────────────────────────────────────────
def index(root, refresh=False):
    """Index every code file under root -> {relpath: {"mtime","defs":[...],"refs":{name: count}}}.
    Cached as JSON; only files whose mtime/size changed are re-parsed (refresh=True re-parses everything)."""
    t0 = time.perf_counter()
    root = _norm_root(root)
    cp = _cache_path(root)
    with _lock:
        cache = {}
        if not refresh and os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == _VERSION and data.get("root") == root:
                    cache = data.get("files", {})
            except (OSError, ValueError):
                cache = {}
        files, parsed = {}, 0
        for rel, p, st in _walk(root):
            old = cache.get(rel)
            if old and old.get("mtime") == st.st_mtime and old.get("size") == st.st_size:
                files[rel] = old
                continue
            rec = _parse_file(p)
            parsed += 1
            if rec is None:
                rec = {"lang": None, "defs": [], "refs": {}, "skipped": True}
            rec["mtime"], rec["size"] = st.st_mtime, st.st_size
            files[rel] = rec
        removed = len(set(cache) - set(files))
        if parsed or removed or not os.path.exists(cp):
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            tmp = cp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": _VERSION, "root": root, "files": files}, f, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(tmp, cp)
    LAST_INDEX_STATS.clear()
    LAST_INDEX_STATS.update(files=len(files), parsed=parsed, removed=removed,
                            seconds=round(time.perf_counter() - t0, 4))
    return {k: v for k, v in files.items() if not v.get("skipped")}


def _identifier_like(nm):
    """camelCase / PascalCase / snake_case / digits — clearly a code name rather than an English word."""
    return bool(re.search(r"[A-Z_0-9$]", nm[1:])) or nm[:1].isupper()


def _mention_words(mentions):
    words = set()
    for m in mentions or ():
        words.update(w for w in _WORD.findall(str(m)) if len(w) >= 2)
    return words


def _rel(root, p):
    p = str(p)
    if os.path.isabs(p):
        try:
            p = os.path.relpath(_norm_root(p), root)
        except ValueError:                            # another drive: cannot be inside root
            return p.replace("\\", "/")
    p = p.replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def _pagerank(nodes, edges, personalization):
    try:
        import networkx as nx
        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        for (a, b), w in edges.items():
            g.add_edge(a, b, weight=w)
        return nx.pagerank(g, alpha=0.85, personalization=personalization, weight="weight", max_iter=200, tol=1e-8)
    except Exception:                                 # networkx/scipy missing or failed to converge: plain power iteration
        n = len(nodes)
        tot = sum(personalization.values()) or 1.0
        pv = {k: personalization.get(k, 0) / tot for k in nodes}
        out = defaultdict(float)
        for (a, _b), w in edges.items():
            out[a] += w
        r = dict(pv) if n else {}
        for _ in range(100):
            nr = {k: 0.15 * pv[k] for k in nodes}
            dangling = sum(r[k] for k in nodes if not out[k])
            for k in nodes:
                nr[k] += 0.85 * dangling * pv[k]
            for (a, b), w in edges.items():
                nr[b] += 0.85 * r[a] * w / out[a]
            r = nr
        return r


def rank(root, focus_files=(), mentions=()):
    """-> (files index, {file: pagerank}, {(file, def_idx): score}) — the ranking behind build_map."""
    root = _norm_root(root)
    files = index(root)
    words = _mention_words(mentions)
    lwords = {w.lower() for w in words}
    focus = {_rel(root, f) for f in focus_files or ()}
    defines = defaultdict(set)
    member_only = defaultdict(lambda: True)            # name defined only as object/class members
    for f, rec in files.items():
        for d in rec["defs"]:
            if not d.get("local"):
                defines[d["name"]].add(f)
            if d["kind"] != "method":
                member_only[d["name"]] = False

    def ambiguous(nm):                                # `open`, `send`, `file`: plain-word members collide with everything
        return member_only[nm] and nm.islower() and "_" not in nm

    def mention_boost(nm):
        if nm in words:
            if _identifier_like(nm):
                return 10.0
            return 1.0 if ambiguous(nm) else 3.0
        if nm.lower() in lwords and _identifier_like(nm):
            return 3.0
        return 1.0

    def name_weight(nm):
        w = mention_boost(nm)
        if nm.startswith("_"):
            w *= 0.1
        if len(defines[nm]) > 5:
            w *= 0.1
        if len(nm) < 2:
            w *= 0.1
        if ambiguous(nm):
            w *= 0.1
        return w

    py_modules = defaultdict(set)                     # `import history_archive` / `history_archive.add(…)` -> that file
    for f, rec in files.items():
        if rec.get("lang") == "python":
            stem = os.path.splitext(os.path.basename(f))[0]
            py_modules[os.path.basename(os.path.dirname(f)) if stem == "__init__" else stem].add(f)

    edges = defaultdict(float)
    xref = defaultdict(float)                         # (definer file, name) -> weighted uses from other files
    for a, rec in files.items():
        for nm, c in rec["refs"].items():
            mods = py_modules.get(nm) if rec.get("lang") == "python" else None
            if mods and len(mods) <= 5:
                for b in mods:
                    if b != a:
                        edges[(a, b)] += 0.5 * math.sqrt(c) / math.sqrt(len(mods))
            targets = defines.get(nm)
            if not targets:
                continue
            w = name_weight(nm) * math.sqrt(c) / math.sqrt(len(targets))   # copies/duplicates share the vote
            for b in targets:
                if b != a:
                    edges[(a, b)] += w
                    xref[(b, nm)] += w / max(1, len(targets))
    pers = {}
    for f, rec in files.items():
        p = 1.0
        if f in focus:
            p *= 20
        stem = os.path.splitext(os.path.basename(f))[0].lower()
        if any(mention_boost(d["name"]) > 1 for d in rec["defs"]) or stem in lwords or                 any(len(w) >= 5 and w in stem for w in lwords):
            p *= 10
        pers[f] = p
    pr = _pagerank(list(files), edges, pers) if files else {}
    scores = {}
    for f, rec in files.items():
        local = []
        for i, d in enumerate(rec["defs"]):
            nm = d["name"]
            own = 0.05 * math.sqrt(rec["refs"].get(nm, 0)) * (0.1 if ambiguous(nm) else 1)
            s = pr.get(f, 0) * (0.1 + xref.get((f, nm), 0) + own) * mention_boost(nm)
            if d["kind"] in ("class", "component", "interface", "type"):
                s *= 1.3
            if nm.startswith("_"):
                s *= 0.3
            local.append((s, i))
        local.sort(key=lambda t: -t[0])
        for k, (s, i) in enumerate(local):            # diminishing returns within one file favours breadth
            scores[(f, i)] = s / math.sqrt(1 + k)
    return files, pr, scores


def _render(files, pr, chosen):
    by_file = defaultdict(list)
    for f, i in chosen:
        by_file[f].append(files[f]["defs"][i])
    out = []
    for f in sorted(by_file, key=lambda x: (-pr.get(x, 0), x)):
        out.append(f + ":")
        for d in sorted(by_file[f], key=lambda d: d["line"]):
            out.append(f"  {d['line']}: {d['sig']}")
    return "\n".join(out)


def _tokens_of(s):
    return len(s) / 3.5


def build_map(root, focus_files=(), mentions=(), budget_tokens=1500):
    """Ranked, token-budgeted outline of the repo: `path:` then indented `line: signature` rows, best file first."""
    files, pr, scores = rank(root, focus_files, mentions)
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    if not ranked:
        return ""
    lo, hi, best = 1, len(ranked), ""
    while lo <= hi:                                   # largest prefix of ranked definitions that fits the budget
        mid = (lo + hi) // 2
        txt = _render(files, pr, ranked[:mid])
        if _tokens_of(txt) <= budget_tokens:
            best, lo = txt, mid + 1
        else:
            hi = mid - 1
    return best


# ── E2: symbol tools ──────────────────────────────────────────────────────────────────────────────────────────────
def outline(path):
    """Definitions of one file -> [{"name","kind","line","sig"}] (plus "parent" for methods)."""
    rec = _parse_file(str(path))
    return rec["defs"] if rec else []


def find_definition(root, name):
    """Where `name` (or a qualified `Class.method`) is defined -> [{"path","line","kind","sig"}]."""
    name = str(name).strip()
    out = []
    for f, rec in sorted(index(root).items()):
        for d in rec["defs"]:
            q = (d.get("parent") + "." + d["name"]) if d.get("parent") else d["name"]
            if d["name"] == name or q == name or ("." in name and q.endswith("." + name)):
                out.append({"path": f, "line": d["line"], "kind": d["kind"], "sig": d["sig"]})
    kinds = {"class": 0, "component": 0, "interface": 1, "type": 1, "function": 2, "method": 3}
    out.sort(key=lambda r: (kinds.get(r["kind"], 4), r["path"], r["line"]))
    return out


def find_references(root, name, limit=100):
    """Word-boundary uses of `name` across indexed code files, definition lines excluded -> [{"path","line","text"}]."""
    root = _norm_root(root)
    word = str(name).strip().split(".")[-1]
    if not word:
        return []
    rx = re.compile(r"(?<![\w$])" + re.escape(word) + r"(?![\w$])")
    out = []
    for f, rec in sorted(index(root).items()):
        def_lines = {d["line"] for d in rec["defs"] if d["name"] == word}
        text = _read(os.path.join(root, f))
        if not text or word not in text:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if i in def_lines or not rx.search(ln):
                continue
            out.append({"path": f, "line": i, "text": _sig(ln)})
            if len(out) >= limit:
                return out
    return out


# ── E3: search ────────────────────────────────────────────────────────────────────────────────────────────────────
def _expand_braces(pat):
    m = re.search(r"\{([^{}]*)\}", pat)
    if not m:
        return [pat]
    return [x for alt in m.group(1).split(",") for x in _expand_braces(pat[:m.start()] + alt + pat[m.end():])]


def _glob_match(rel, globs):
    base = rel.rsplit("/", 1)[-1]
    for g in globs:
        g = g.replace("\\", "/")
        if "/" in g:
            if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.replace("**/", "")):
                return True
        elif fnmatch.fnmatch(base, g):
            return True
    return False


def search(root, pattern, glob=None, regex=True, context=1, limit=200):
    """ripgrep-style search -> [{"path","line","text","before":[...],"after":[...]}]; skips ignored and binary files."""
    root = _norm_root(root)
    try:
        rx = re.compile(pattern if regex else re.escape(str(pattern)), re.M)
    except re.error as e:
        raise ValueError(f"bad regex: {e}") from None
    globs = None
    if glob:
        globs = [x for g in (glob if isinstance(glob, (list, tuple)) else [glob]) for x in _expand_braces(str(g))]
    ctx = max(0, int(context or 0))
    out = []
    t0 = time.perf_counter()
    LAST_SEARCH_STATS.clear()
    LAST_SEARCH_STATS.update(files=0, truncated=False)
    for rel, p, _st in _walk(root, code_only=False, max_files=_MAX_SEARCH_FILES):
        if globs and not _glob_match(rel, globs):
            continue
        if os.path.splitext(rel)[1].lower() in _BINARY_EXT:
            continue
        if time.perf_counter() - t0 > SEARCH_SECONDS:     # huge trees: return what we have rather than hang the agent
            LAST_SEARCH_STATS["truncated"] = True
            break
        LAST_SEARCH_STATS["files"] += 1
        try:
            with open(p, "rb") as fh:
                head = fh.read(8192)
                if b"\x00" in head:
                    continue
                raw = head + fh.read()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        if not rx.search(text) or _minified(text):   # whole-file check first; most files have no match
            continue
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if rx.search(ln):
                out.append({"path": rel, "line": i + 1, "text": ln[:400],
                            "before": [x[:400] for x in lines[max(0, i - ctx):i]],
                            "after": [x[:400] for x in lines[i + 1:i + 1 + ctx]]})
                if len(out) >= limit:
                    return out
    return out
