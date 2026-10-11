"""Projects: a folder the agent works in, its commands, and its instruction files (PRD milestone M1, sections 4A/4B).

A project is just a folder. Its root is found by walking up to the nearest marker (.git, package.json, AGENTS.md…),
so opening any file or subfolder lands on the same project. Commands (setup/run/test/lint/build) are inferred from
the repo's own manifests so the agent never has to guess "npm or pnpm?"; the user confirms them once and they are
saved in <root>/.zen/project.json, which then wins over detection. Instruction files (AGENTS.md, nearest wins, with
CLAUDE.md / Cursor / Copilot fallbacks) are read here and capped, because small models lose the task when a long
rules file floods the prompt. The recent-projects list is app state, not project state, so it lives in
    <data>/projects.json
No model calls, no network: everything here is file reads, cheap enough to run on every turn.
"""
import datetime
import glob
import json
import os
import re
import threading

try:
    import tomllib                                     # 3.11+; only used to read pyproject.toml
except ImportError:                                    # pragma: no cover
    tomllib = None

_lock = threading.Lock()

MARKERS = (".git", ".zen", "AGENTS.md", "package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod",
           "Makefile")
DEFAULT_IGNORE = [".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".zen", ".next",
                  "target", "coverage"]
COMMAND_KEYS = ("setup", "run", "test", "lint", "build")
_NPM_PLACEHOLDER = "no test specified"                 # the `npm init` default test script, which only fails
_RECENT_MAX = 50


# ---------------------------------------------------------------- helpers

def _data_dir():
    import paths
    return paths.data_dir()


def _key(path):
    """Comparison key for a path: Windows paths differ only in case/slashes, so C:/Dev and c:\\dev are one project."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _read(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read() if limit is None else f.read(limit)
    except OSError:
        return ""
    return text.lstrip("\ufeff")


def _read_json(path):
    try:
        return json.loads(_read(path) or "null")
    except ValueError:
        return None


def _has(root, *names):
    return any(os.path.exists(os.path.join(root, n)) for n in names)


def _is_marked(folder):
    return _has(folder, *MARKERS) or bool(glob.glob(os.path.join(glob.escape(folder), "*.sln")))


# ---------------------------------------------------------------- root + detection

def find_root(path):
    """Nearest folder at or above `path` holding a project marker; None if the walk reaches the drive root empty."""
    if not path:
        return None
    cur = os.path.abspath(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while True:
        if os.path.isdir(cur) and _is_marked(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _makefile_targets(root):
    text = _read(os.path.join(root, "Makefile"))
    return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.M))


def _node(root, cmds):
    pkg = _read_json(os.path.join(root, "package.json")) or {}
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    if os.path.exists(os.path.join(root, "pnpm-lock.yaml")):
        pm = "pnpm"
    elif os.path.exists(os.path.join(root, "yarn.lock")):
        pm = "yarn"
    else:
        pm = "npm"
    cmds["setup"] = f"{pm} install"

    def script(*names):
        for n in names:
            s = scripts.get(n)
            if isinstance(s, str) and s.strip() and _NPM_PLACEHOLDER not in s:
                return f"{pm} run {n}"
        return ""

    cmds["run"] = script("dev", "start", "serve", "preview")
    cmds["test"] = script("test", "test:unit")
    cmds["lint"] = script("lint", "typecheck", "check")
    cmds["build"] = script("build")


def _python(root, cmds):
    pyproject = {}
    if tomllib and os.path.exists(os.path.join(root, "pyproject.toml")):
        try:
            pyproject = tomllib.loads(_read(os.path.join(root, "pyproject.toml")))
        except (ValueError, tomllib.TOMLDecodeError):
            pyproject = {}
    if os.path.exists(os.path.join(root, "requirements.txt")):
        cmds["setup"] = "pip install -r requirements.txt"
    elif _has(root, "pyproject.toml", "setup.py"):
        cmds["setup"] = "pip install -e ."
    has_tests = (_has(root, "tests", "test") or glob.glob(os.path.join(glob.escape(root), "test_*.py"))
                 or glob.glob(os.path.join(glob.escape(root), "*_test.py")))
    if has_tests or "pytest" in pyproject.get("tool", {}):
        cmds["test"] = "pytest"
    if "ruff" in pyproject.get("tool", {}) or _has(root, "ruff.toml", ".ruff.toml"):
        cmds["lint"] = "ruff check ."
    for entry in ("main.py", "app.py", "manage.py"):
        if os.path.isfile(os.path.join(root, entry)):
            cmds["run"] = f"python {entry}" + (" runserver" if entry == "manage.py" else "")
            break
    else:
        # A package with __main__.py is runnable as `python -m pkg` — only when there is exactly one, else it's a guess.
        mains = [os.path.basename(os.path.dirname(p)) for pat in ("*/__main__.py", "src/*/__main__.py")
                 for p in glob.glob(os.path.join(glob.escape(root), pat))]
        mains = [m for m in mains if m not in DEFAULT_IGNORE]
        if len(mains) == 1:
            cmds["run"] = f"python -m {mains[0]}"
    if _has(root, "pyproject.toml") and pyproject.get("build-system"):
        cmds["build"] = "python -m build"


def detect(root):
    """Infer kind and commands from the repo's manifests. Cheap and offline; the result is a proposal, not truth."""
    root = os.path.abspath(root)
    cmds = {k: "" for k in COMMAND_KEYS}
    if _has(root, "package.json"):
        kind = "node"
        _node(root, cmds)
    elif _has(root, "pyproject.toml", "setup.py", "requirements.txt", "setup.cfg"):
        kind = "python"
        _python(root, cmds)
    elif _has(root, "Cargo.toml"):
        kind = "rust"
        cmds.update(setup="cargo fetch", run="cargo run", test="cargo test", lint="cargo clippy",
                    build="cargo build")
    elif _has(root, "go.mod"):
        kind = "go"
        cmds.update(setup="go mod download", run="go run .", test="go test ./...", lint="go vet ./...",
                    build="go build ./...")
    elif glob.glob(os.path.join(glob.escape(root), "*.sln")) or glob.glob(os.path.join(glob.escape(root), "*.csproj")):
        kind = "dotnet"
        cmds.update(setup="dotnet restore", run="dotnet run", test="dotnet test", build="dotnet build")
    elif _has(root, "index.html"):
        kind = "web"
        cmds["run"] = "open index.html"
    else:
        kind = "unknown"
    if _has(root, "Makefile"):
        # Make targets only fill gaps: a repo's package.json/pyproject commands are more specific than `make`.
        targets = _makefile_targets(root)
        wanted = {"setup": ("install", "setup", "deps"), "run": ("run", "serve", "dev", "start"),
                  "test": ("test", "check"), "lint": ("lint",), "build": ("build", "all")}
        for slot, names in wanted.items():
            if not cmds[slot]:
                hit = next((n for n in names if n in targets), None)
                if hit:
                    cmds[slot] = f"make {hit}"
    return {"name": os.path.basename(root.rstrip("\\/")) or root, "root": root, "kind": kind,
            "commands": cmds, "ignore": list(DEFAULT_IGNORE)}


# ---------------------------------------------------------------- per-project config

def _cfg_path(root):
    return os.path.join(os.path.abspath(root), ".zen", "project.json")


def load(root):
    """Saved config merged over detection, so keys added in later versions (or never saved) still have values."""
    cfg = detect(root)
    saved = _read_json(_cfg_path(root)) if os.path.exists(_cfg_path(root)) else None
    if not isinstance(saved, dict):
        cfg["saved"] = False
        return cfg
    for k, v in saved.items():
        if k == "commands" and isinstance(v, dict):
            cfg["commands"].update({ck: (cv if isinstance(cv, str) else "") for ck, cv in v.items()})
        elif k not in ("root", "saved"):
            cfg[k] = v
    cfg["root"] = os.path.abspath(root)                # the folder it was found in wins: projects get moved/cloned
    cfg["saved"] = True
    return cfg


def save(root, cfg):
    """Write .zen/project.json; the root path is left out so the file stays valid after the folder moves."""
    path = _cfg_path(root)
    data = {k: v for k, v in dict(cfg).items() if k not in ("root", "saved")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- recent projects

def _recent_path():
    return os.path.join(_data_dir(), "projects.json")


def _recent_load():
    data = _read_json(_recent_path())
    items = data.get("recent") if isinstance(data, dict) else None
    return [e for e in (items or []) if isinstance(e, dict) and e.get("root")]


def _recent_save(items):
    os.makedirs(_data_dir(), exist_ok=True)
    tmp = _recent_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"recent": items[:_RECENT_MAX]}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _recent_path())


def add_recent(root, name=None):
    """Move `root` to the top of the recent list (one entry per folder, however its path was spelled)."""
    root = os.path.abspath(root)
    with _lock:
        items = [e for e in _recent_load() if _key(e["root"]) != _key(root)]
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        items.insert(0, {"root": root, "name": name or os.path.basename(root.rstrip("\\/")) or root,
                         "last_opened": stamp})
        _recent_save(items)
    return items[0]


def recent(limit=20):
    """Most recent first; folders deleted since are dropped (and pruned from the file) rather than shown dead."""
    with _lock:
        items = _recent_load()
        seen, alive = set(), []
        for e in items:
            k = _key(e["root"])
            if k not in seen and os.path.isdir(e["root"]):
                seen.add(k)
                alive.append({"root": e["root"], "name": e.get("name") or os.path.basename(e["root"]),
                              "last_opened": e.get("last_opened", "")})
        if len(alive) != len(items):
            _recent_save(alive)
    return alive[:max(0, int(limit))]


def remove_recent(root):
    with _lock:
        items = _recent_load()
        kept = [e for e in items if _key(e["root"]) != _key(root)]
        if len(kept) != len(items):
            _recent_save(kept)
    return len(kept) != len(items)


# ---------------------------------------------------------------- instruction files

def _strip_front_matter(text):
    m = re.match(r"\A---\s*\n.*?\n---\s*(\n|\Z)", text, re.S)
    return text[m.end():] if m else text


def _nearest(root, near, filename):
    """Nearest `filename` walking up from `near` to root, plus root's own copy if different — nearest first."""
    root_k = _key(root)
    start = os.path.abspath(near) if near else os.path.abspath(root)
    if not os.path.isdir(start):
        start = os.path.dirname(start)
    if not (_key(start) + os.sep).startswith(root_k.rstrip(os.sep) + os.sep):
        start = os.path.abspath(root)                  # `near` outside the project: only the root's file applies
    found, cur = [], start
    while True:
        p = os.path.join(cur, filename)
        if os.path.isfile(p):
            found.append(p)
            break
        if _key(cur) == root_k:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    root_file = os.path.join(root, filename)
    if os.path.isfile(root_file) and not any(_key(f) == _key(root_file) for f in found):
        found.append(root_file)
    return found


def instructions(root, near=None, cap_chars=6000):
    """-> (text, sources). AGENTS.md (nearest wins, root's also included) else the first fallback family found."""
    root = os.path.abspath(root)
    files = _nearest(root, near, "AGENTS.md") or _nearest(root, near, "CLAUDE.md")
    if not files:
        mdc = sorted(glob.glob(os.path.join(glob.escape(root), ".cursor", "rules", "*.mdc")))
        if mdc:
            files = mdc
        else:
            files = [p for p in (os.path.join(root, ".cursorrules"),
                                 os.path.join(root, ".github", "copilot-instructions.md")) if os.path.isfile(p)][:1]
    parts, sources = [], []
    for p in files:
        body = _read(p, limit=cap_chars * 4)
        if p.endswith(".mdc"):
            body = _strip_front_matter(body)
        body = body.strip()
        if not body:
            continue
        rel = os.path.relpath(p, root).replace("\\", "/")
        sources.append(rel)
        parts.append(f"## {rel}\n{body}")
    if not parts:
        return "", []
    text = "\n\n".join(parts)
    if len(text) > cap_chars:
        note = f"\n[... truncated {len(text) - cap_chars} chars; read {', '.join(sources)} for the rest]"
        text = text[:max(0, cap_chars - len(note))].rstrip() + note
    return text, sources


def summary(root):
    """A few lines for the agent prompt: what the project is and which commands to use, under ~600 chars."""
    cfg = load(root)
    lines = [f"Project: {cfg.get('name')} ({cfg.get('kind')})", f"Root: {cfg.get('root')}"]
    for k in COMMAND_KEYS:
        v = (cfg.get("commands") or {}).get(k)
        if v:
            lines.append(f"{k}: {v}")
    _, sources = instructions(root)
    if sources:
        lines.append("Instructions: " + ", ".join(sources))
    text = "\n".join(lines)
    return text if len(text) <= 600 else text[:597] + "..."
