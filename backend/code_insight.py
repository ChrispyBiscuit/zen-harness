"""Checks that keep a growing codebase coherent, run by the harness (no model calls). All warn-only: a small model obeys
harness messages literally, so every check is narrow and high-precision rather than clever.

- contract_drift(path): the project's written contract (DESIGN.md / CONTRACT.md) vs the code — command-line flags
  and HTTP routes the contract names that the code doesn't have, and flags the code defines that the contract
  doesn't name (a run's tests called the project's own client with --user where the code said --person).
- signature_impact(path, old_text, new_text): when an edit changes a function's signature, the places that call it.
- regression(prev, cur): a test that newly fails while fewer tests pass than before.
- tests_map(root): which test file covers which module (by its imports), for the work-state map.
"""
import os
import re

_CONTRACT_SECTION = re.compile(r"^#+[^\n]*(contract|interface|api|endpoint|command|cli|usage|route)[^\n]*\n(.*?)(?=^#+ |\Z)",
                               re.I | re.M | re.S)
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*\b")
# "GET /api/x", "| GET | `/api/x` |" (method and path in separate table cells), or any backticked /api/… or /v1/… path
_ROUTE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s*\|?\s*`?(/[\w/.-]*)|`(/(?:api|v\d)/[\w/.-]*)")
_ADD_ARG = re.compile(r"add_argument\(\s*([^)]*)\)", re.S)
_COMMON_FLAGS = {"--help", "--version", "--verbose", "--quiet"}
_seen = {}                    # project root -> the last drift note given (each distinct note once)


def project_root(path):
    """The folder the project lives in: the nearest one above `path` with docs/, PRD.md, DESIGN.md or .git."""
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(5):
        if any(os.path.exists(os.path.join(d, m)) for m in ("docs", "PRD.md", "DESIGN.md", "CONTRACT.md", ".git")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.abspath(path))


def _contract_text(root):
    for rel in ("CONTRACT.md", os.path.join("docs", "CONTRACT.md")):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return open(p, encoding="utf-8", errors="replace").read(), rel
    for rel in ("DESIGN.md", os.path.join("docs", "DESIGN.md")):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            txt = open(p, encoding="utf-8", errors="replace").read()
            return "\n".join(m.group(0) for m in _CONTRACT_SECTION.finditer(txt)), rel
    return "", ""


def _code_files(root, limit=60):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith((".", "__")) and d not in ("venv", ".venv", "node_modules", "docs")]
        for f in fns:
            if f.endswith(".py"):
                out.append(os.path.join(dp, f))
                if len(out) >= limit:
                    return out
    return out


def contract_drift(path):
    """-> a short 'Contract check' note, or ''. Only Python projects, only exact tokens; each distinct note once."""
    if not str(path).endswith(".py"):
        return ""
    root = project_root(path)
    contract, rel = _contract_text(root)
    if not contract:
        return ""
    c_flags = set(_FLAG.findall(contract)) - _COMMON_FLAGS
    c_routes = {r.rstrip("/") or "/" for pair in _ROUTE.findall(contract) for r in pair
                if r and "{" not in r and "<" not in r}
    code, flags, files = "", set(), _code_files(root)
    for f in files:
        if os.sep + "tests" + os.sep in f or os.path.basename(f).startswith("test_"):
            continue                                       # tests are checked against the code, not the contract
        try:
            t = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        code += t + "\n"
        for args in _ADD_ARG.findall(t):
            flags |= set(re.findall(r"['\"](--[a-z][a-z0-9-]*)['\"]", args))
    notes = []
    if c_flags and flags:
        missing = sorted(c_flags - flags)
        extra = sorted(flags - c_flags - _COMMON_FLAGS)
        if missing:
            notes.append(f"{rel} names {', '.join(missing[:6])}, which no add_argument in the code defines")
        if extra:
            notes.append(f"the code defines {', '.join(extra[:6])}, which {rel} doesn't name")
    if c_routes:
        lost = sorted(r for r in c_routes if r != "/" and f"'{r}" not in code and f'"{r}' not in code)
        if lost:
            notes.append(f"{rel} lists route(s) {', '.join(lost[:6])} that appear nowhere in the code")
    if not notes:
        _seen.pop(root, None)
        return ""
    note = ("\nContract check (code vs the written contract — fix whichever is wrong, or ignore if intended): "
            + "; ".join(notes) + ".")
    if _seen.get(root) == note:
        return ""
    _seen[root] = note
    return note


def signature_impact(path, old_text, new_text, cap=5):
    """-> a note naming the callers of functions whose signature this change altered, or ''."""
    if not str(path).endswith(".py") or not old_text or not new_text:
        return ""
    try:
        import repomap
        old = {(d.get("parent") or "", d["name"]): d.get("sig", "") for d in repomap._extract(old_text, "python")[0]}
        new = {(d.get("parent") or "", d["name"]): d.get("sig", "") for d in repomap._extract(new_text, "python")[0]}
    except Exception:
        return ""
    changed = [k for k in old if k in new and " ".join(old[k].split()) != " ".join(new[k].split())]
    removed = [k for k in old if k not in new and not k[1].startswith("_")]
    if not changed and not removed:
        return ""
    root, me = project_root(path), os.path.abspath(path).lower()
    parts = []
    for k in (changed + removed)[:3]:
        try:
            refs = [r for r in repomap.find_references(root, k[1], limit=40)
                    if os.path.abspath(os.path.join(root, r["path"])).lower() != me]
        except Exception:
            refs = []
        if refs:
            where = ", ".join(f"{r['path']}:{r['line']}" for r in refs[:cap]) + (" …" if len(refs) > cap else "")
            what = "removed" if k in removed else "signature changed"
            parts.append(f"{k[1]} ({what}) is used at {where}")
    return ("\nCheck the callers: " + "; ".join(parts) + ".") if parts else ""


def failing_names(text):
    return sorted(set(re.findall(r"^FAILED\s+(\S+)", str(text or ""), re.M)))


def regression(prev, cur):
    """prev/cur: {'passed','failed','failing'} -> a note when fewer tests pass and a test newly fails, else ''."""
    if not prev or not cur or cur.get("passed", 0) >= prev.get("passed", 0):
        return ""
    new = [n for n in cur.get("failing") or [] if n not in (prev.get("failing") or [])]
    if not new:
        return ""
    return (f"\nRegression: {', '.join(new[:4])} now fail{'s' if len(new) == 1 else ''} — {prev['passed']} tests passed "
            f"before, {cur['passed']} now. Something you just changed broke it: changes_since_green shows what.")


def tests_map(root, limit=12):
    """'tests/test_db.py → db; tests/test_api.py → server, cli' from the test files' imports of project modules."""
    files = _code_files(root)
    mods = {os.path.splitext(os.path.basename(f))[0] for f in files}
    rows = []
    for f in files:
        b = os.path.basename(f)
        if not (b.startswith("test_") or b.endswith("_test.py")):
            continue
        try:
            t = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        hit = sorted({m for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", t, re.M)
                      if m.split(".")[0] in mods and not m.split(".")[0].startswith("test")})
        if hit:
            rows.append(f"{os.path.relpath(f, root)} → {', '.join(h.split('.')[0] for h in hit)}")
        if len(rows) >= limit:
            break
    return "; ".join(rows)
