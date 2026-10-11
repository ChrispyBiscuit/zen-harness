"""Git operations for the engineering workspace (PRD section 4D, D1-D7) — a thin, testable layer over the git CLI.

Everything shells out to `git` (and `gh` for pull requests); no GitPython. Machine-readable formats only
(`status --porcelain=v2 -z`, `--format` with unit separators, `-z` lists) so file names with spaces, quotes or
non-ASCII letters survive. Output is read as bytes and decoded as UTF-8 with surrogateescape, so CRLF line endings
and odd bytes round-trip unchanged when a diff hunk is fed back into `git apply`.

`root` is the work-tree top level of the project; paths are relative to it, '/'-separated (absolute paths are
accepted and converted). Failures raise GitError carrying git's stderr. Credentials are never stored: clone and
push go through the system credential helper (Git Credential Manager on Windows), and URLs with an embedded
password are refused.
"""
import json
import os
import re
import shutil
import subprocess

GIT = "git"
GH = "gh"
TIMEOUT = 60                                          # seconds for local commands
NET_TIMEOUT = 600                                     # clone / push / pull / gh
_NOWINDOW = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW: no console flash from the GUI
_BASE = ["-c", "core.quotepath=false", "-c", "color.ui=false", "-c", "core.pager=cat",
         "-c", "protocol.ext.allow=never", "--literal-pathspecs"]   # pathspecs are file names, never globs/magic
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_STATUS_WORD = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "T": "typechange",
                "U": "unmerged"}
_CONFLICT_WORD = {"DD": "both deleted", "AU": "added by us", "UD": "deleted by them", "UA": "added by them",
                  "DU": "deleted by us", "AA": "both added", "UU": "both modified"}
DEFAULT_GITIGNORE = ("# added by Zen Harness on git init\n"
                     "node_modules/\nvenv/\n.venv/\n__pycache__/\n*.pyc\ndist/\nbuild/\n.env\n.DS_Store\nThumbs.db\n")


class GitError(Exception):
    def __init__(self, message, returncode=None, argv=None):
        super().__init__(message)
        self.stderr = message
        self.returncode = returncode
        self.argv = argv


# ---------------------------------------------------------------- process plumbing

def _env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"                  # no tty: fail instead of hanging on a username prompt
    env["GIT_OPTIONAL_LOCKS"] = "0"                   # status must not fight the user's own git for index.lock
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env.setdefault("LC_ALL", "C")                     # stable (English) error text
    return env


def _dec(b):
    return (b or b"").decode("utf-8", "surrogateescape")


def _enc(s):
    return s.encode("utf-8", "surrogateescape") if isinstance(s, str) else s


def _exec(argv, cwd, input=None, timeout=TIMEOUT, check=True):
    """-> (returncode, stdout, stderr) as str. Raises GitError on a missing binary, timeout, or (check) failure."""
    try:
        p = subprocess.run(argv, cwd=cwd, input=_enc(input), stdin=None if input is not None else subprocess.DEVNULL,
                           capture_output=True, timeout=timeout, creationflags=_NOWINDOW, env=_env())
    except FileNotFoundError:
        raise GitError(f"{argv[0]} not found on PATH", argv=argv) from None
    except NotADirectoryError:
        raise GitError(f"not a folder: {cwd}", argv=argv) from None
    except subprocess.TimeoutExpired:
        raise GitError(f"{' '.join(argv[:4])} timed out after {timeout}s", argv=argv) from None
    out, err = _dec(p.stdout), _dec(p.stderr)
    if check and p.returncode != 0:
        raise GitError((err.strip() or out.strip() or f"exit code {p.returncode}"), p.returncode, argv)
    return p.returncode, out, err


def _git(root, *args, input=None, timeout=TIMEOUT, check=True):
    return _exec([GIT, *_BASE, *args], root, input=input, timeout=timeout, check=check)


def _out(root, *args, **kw):
    return _git(root, *args, **kw)[1]


def _rel(root, path):
    """Project-relative '/' path; absolute paths are converted, paths outside the project are refused."""
    p = str(path)
    if os.path.isabs(p):
        p = os.path.relpath(p, root)
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p == ".." or p.startswith("../") or not p:
        raise GitError(f"path is outside the project: {path}")
    return p.rstrip("/") or p


def _paths(root, paths):
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    return [_rel(root, p) for p in paths]


def _has_head(root):
    return _git(root, "rev-parse", "--verify", "-q", "HEAD", check=False)[0] == 0


def is_git_internal_path(path):
    """True when any component of `path` is a .git folder/file — the agent's write tools refuse these (D4)."""
    return any(part.lower() == ".git" for part in re.split(r"[\\/]+", str(path)))


# ---------------------------------------------------------------- repo / init (D5)

def is_repo(root):
    if not root or not os.path.isdir(root):
        return False
    try:
        code, out, _ = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    except GitError:
        return False
    return code == 0 and out.strip() == "true"


def toplevel(root):
    return os.path.normpath(_out(root, "rev-parse", "--show-toplevel").strip()) if is_repo(root) else None


def init(root, first_commit=True, message="Initial commit", default_ignore=True):
    """`git init` (branch 'main' unless init.defaultBranch is configured); optionally commit everything present.
    A .gitignore for the usual heavy folders (node_modules, venv, ...) is written first when there is none.
    Already a repo -> no re-init; returns HEAD's sha (or None). -> sha of the first commit, or None."""
    os.makedirs(root, exist_ok=True)
    if is_repo(root):
        return _out(root, "rev-parse", "HEAD").strip() if _has_head(root) else None
    branch = _git(root, "config", "--get", "init.defaultBranch", check=False)[1].strip() or "main"
    _git(root, "init", "-q", f"--initial-branch={branch}")
    if not first_commit:
        return None
    gi = os.path.join(root, ".gitignore")
    if default_ignore and not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8", newline="\n") as f:
            f.write(DEFAULT_GITIGNORE)
    _git(root, "add", "-A")
    return commit(root, message, allow_empty=True)


# ---------------------------------------------------------------- status (D1)

def _empty_status(is_repo_=False):
    return {"is_repo": is_repo_, "branch": None, "detached": False, "head": None, "upstream": None,
            "ahead": 0, "behind": 0, "staged": [], "unstaged": [], "untracked": [], "conflicted": []}


def status(root):
    """Parsed `git status --porcelain=v2 --branch -z --untracked-files=all`."""
    if not is_repo(root):
        return _empty_status(False)
    out = _out(root, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all")
    st = _empty_status(True)
    recs = out.split("\0")
    i = 0
    while i < len(recs):
        r = recs[i]
        i += 1
        if not r:
            continue
        if r.startswith("# "):
            key, _, val = r[2:].partition(" ")
            if key == "branch.oid":
                st["head"] = None if val == "(initial)" else val
            elif key == "branch.head":
                st["detached"] = val == "(detached)"
                st["branch"] = None if st["detached"] else val
            elif key == "branch.upstream":
                st["upstream"] = val
            elif key == "branch.ab":
                m = re.match(r"\+(\d+) -(\d+)", val)
                if m:
                    st["ahead"], st["behind"] = int(m.group(1)), int(m.group(2))
            continue
        kind = r[0]
        if kind == "?":
            st["untracked"].append(r[2:])
        elif kind in "12":
            parts = r.split(" ", 8 if kind == "1" else 9)
            xy, path = parts[1], parts[-1]
            old = None
            if kind == "2":
                old = recs[i] if i < len(recs) else None     # -z: the original path is the next NUL field
                i += 1
            for side, code in (("staged", xy[0]), ("unstaged", xy[1])):
                if code != ".":
                    e = {"path": path, "status": _STATUS_WORD.get(code, code), "code": code}
                    if old is not None and code in "RC":
                        e["old_path"] = old
                    st[side].append(e)
        elif kind == "u":
            parts = r.split(" ", 10)
            st["conflicted"].append({"path": parts[-1], "status": _CONFLICT_WORD.get(parts[1], "conflict"),
                                     "code": parts[1]})
        # '!' (ignored) is never requested
    return st


def dirty_files(root):
    """Every path with staged, unstaged, untracked or conflicted changes (D3: commit the user's own edits first)."""
    st = status(root)
    seen = []
    for e in st["staged"] + st["unstaged"] + st["conflicted"]:
        for p in (e.get("old_path"), e["path"]):
            if p and p not in seen:
                seen.append(p)
    for p in st["untracked"]:
        if p not in seen:
            seen.append(p)
    return seen


# ---------------------------------------------------------------- diff + parser

def diff(root, path=None, staged=False, context=3):
    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", f"-U{max(0, int(context))}",
            "--src-prefix=a/", "--dst-prefix=b/"]
    if staged:
        args.append("--cached")
    args.append("--")
    if path is not None:
        args += _paths(root, path)
    return _out(root, *args)


def _unquote(s):
    """Undo git's C-style path quoting ("a/caf\\303\\251 \\"x\\"")."""
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    body, out, i = s[1:-1], bytearray(), 0
    esc = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, '"': 34, "\\": 92}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            if n in "01234567" and re.match(r"[0-7]{3}", body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(esc.get(n, ord(n)))
            i += 2
            continue
        out += c.encode("utf-8", "surrogateescape")
        i += 1
    return out.decode("utf-8", "surrogateescape")


def _quote(path):
    """Quote a path the way git does, so `git apply` reads it back exactly."""
    if not re.search(r'[\x00-\x1f"\\\x7f]', path):
        return path
    rep = {"\n": "\\n", "\t": "\\t", "\r": "\\r", '"': '\\"', "\\": "\\\\"}
    return '"' + "".join(rep.get(c, f"\\{ord(c):03o}" if ord(c) < 32 or ord(c) == 127 else c) for c in path) + '"'


def _strip_prefix(name, prefix):
    if name is None or name == "/dev/null":
        return None
    return name[len(prefix):] if name.startswith(prefix) else name


def _hdr_name(rest):
    """File name from a '--- x' / '+++ x' line: git appends a TAB after names containing spaces; difflib adds
    '\\tdate'. Quoted names are C-unquoted."""
    rest = rest.rstrip("\r")
    if rest.startswith('"'):
        m = re.match(r'("(?:[^"\\]|\\.)*")', rest)
        if m:
            return _unquote(m.group(1))
    return rest.split("\t", 1)[0]


def _git_line_names(rest):
    """'a/X b/Y' from a 'diff --git' line -> (X, Y), best effort (used only when no ---/+++ lines follow)."""
    rest = rest.rstrip("\r")
    if rest.startswith('"'):
        m = re.match(r'("(?:[^"\\]|\\.)*") (.*)$', rest)
        if m:
            return _strip_prefix(_unquote(m.group(1)), "a/"), _strip_prefix(_hdr_name(m.group(2)), "b/")
    n = len(rest)
    if (n - 5) % 2 == 0 and n >= 7:                   # 'a/P b/P' with the same P (mode change, binary)
        L = (n - 5) // 2
        if rest.startswith("a/") and rest[2 + L:5 + L] == " b/" and rest[2:2 + L] == rest[5 + L:]:
            return rest[2:2 + L], rest[2:2 + L]
    if rest.endswith('"'):
        m = re.match(r'(.*) ("(?:[^"\\]|\\.)*")$', rest)
        if m:
            return _strip_prefix(m.group(1), "a/"), _strip_prefix(_unquote(m.group(2)), "b/")
    a, _, b = rest.partition(" b/")
    return _strip_prefix(a, "a/"), b or None


def _new_file(git_header=None):
    f = {"path": None, "old_path": None, "status": "modified", "binary": False, "hunks": [],
         "_old": None, "_new": None, "_minus": False, "_flag": None}
    if git_header is not None:
        f["_old"], f["_new"] = _git_line_names(git_header)
        f["_gitnames"] = True
    return f


def _finish(f):
    old, new = f.pop("_old"), f.pop("_new")
    flag = f.pop("_flag")
    f.pop("_gitnames", None)
    f.pop("_minus", None)
    if flag == "added":
        old = None
    elif flag == "deleted":
        new = None
    f["path"] = new if new is not None else old
    f["old_path"] = old
    if flag in ("renamed", "copied"):
        f["status"] = flag
    elif old is None and new is not None:
        f["status"] = "added"
    elif new is None and old is not None:
        f["status"] = "deleted"
    return f


def parse_diff(text):
    """Unified diff text (git or difflib style) -> list of files with hunks and numbered lines.

    Each line: {"kind": "add|del|ctx", "text", "old", "new"} (old/new are 1-based line numbers, None on the side
    the line is absent from) plus "no_eol": True when followed by '\\ No newline at end of file'. Hunk bodies are
    read by their header counts, so content lines that look like headers ('--- x') are not misparsed."""
    files, f, h = [], None, None
    rem_old = rem_new = 0
    o = n = 0
    lines = (text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for raw in lines:
        if h is not None and (rem_old > 0 or rem_new > 0):
            c = raw[:1]
            if c == "\\":
                if h["lines"]:
                    h["lines"][-1]["no_eol"] = True
                continue
            if c in (" ", "") and rem_old > 0 and rem_new > 0:
                h["lines"].append({"kind": "ctx", "text": raw[1:], "old": o, "new": n})
                o, n, rem_old, rem_new = o + 1, n + 1, rem_old - 1, rem_new - 1
                continue
            if c == "-" and rem_old > 0:
                h["lines"].append({"kind": "del", "text": raw[1:], "old": o, "new": None})
                o, rem_old = o + 1, rem_old - 1
                continue
            if c == "+" and rem_new > 0:
                h["lines"].append({"kind": "add", "text": raw[1:], "old": None, "new": n})
                n, rem_new = n + 1, rem_new - 1
                continue
            h, rem_old, rem_new = None, 0, 0                 # malformed/truncated hunk: fall through as header text
        if raw.startswith("\\"):
            if h is not None and h["lines"]:
                h["lines"][-1]["no_eol"] = True
            continue
        if raw.startswith("diff --git "):
            f, h = _new_file(raw[len("diff --git "):]), None
            files.append(f)
            continue
        m = _HUNK.match(raw.rstrip("\r"))
        if m:
            if f is None:
                f = _new_file()
                files.append(f)
            os_, ol, ns, nl = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            h = {"header": raw.rstrip("\r"), "old_start": os_, "old_lines": ol, "new_start": ns, "new_lines": nl,
                 "section": m.group(5), "lines": []}
            f["hunks"].append(h)
            rem_old, rem_new = ol, nl
            o, n = (os_ if ol else os_ + 1), (ns if nl else ns + 1)
            continue
        if raw.startswith("--- "):
            if f is None or f["hunks"] or f["_minus"]:
                f = _new_file()
                files.append(f)
            f["_minus"] = True
            f["_old"] = _strip_prefix(_hdr_name(raw[4:]), "a/")
            if f["_old"] is None and f["_flag"] is None:
                f["_flag"] = "added"
            continue
        if raw.startswith("+++ ") and f is not None and not f["hunks"]:
            f["_new"] = _strip_prefix(_hdr_name(raw[4:]), "b/")
            if f["_new"] is None:
                f["_flag"] = "deleted"
            continue
        if f is None or f["hunks"]:
            continue                                         # noise between files
        s = raw.rstrip("\r")
        if s.startswith("new file mode"):
            f["_flag"] = "added"
        elif s.startswith("deleted file mode"):
            f["_flag"] = "deleted"
        elif s.startswith("rename from "):
            f["_flag"], f["_old"] = "renamed", _unquote(s[12:])
        elif s.startswith("rename to "):
            f["_new"] = _unquote(s[10:])
        elif s.startswith("copy from "):
            f["_flag"], f["_old"] = "copied", _unquote(s[10:])
        elif s.startswith("copy to "):
            f["_new"] = _unquote(s[8:])
        elif s.startswith(("old mode", "new mode")) and f["_flag"] is None:
            f["_flag"] = "mode"
        elif s.startswith("Binary files") or s == "GIT binary patch":
            f["binary"] = True
    return [_finish(x) for x in files]


# ---------------------------------------------------------------- stage / unstage / discard (D1)

def stage(root, paths):
    ps = _paths(root, paths)
    if ps:
        _git(root, "add", "-A", "--", *ps)               # -A: deletions are staged too


def unstage(root, paths):
    ps = _paths(root, paths)
    if not ps:
        return
    if _has_head(root):
        _git(root, "restore", "--staged", "--", *ps)
    else:
        _git(root, "rm", "--cached", "-r", "-q", "--", *ps)   # nothing committed yet: just drop from the index


def _backup_target(backup_dir, rel):
    dest = os.path.join(backup_dir, *rel.split("/"))
    base, k = dest, 1
    while os.path.exists(dest):
        dest = f"{base}.{k}"
        k += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    return dest


def discard(root, paths, backup_dir):
    """Throw away local changes (index and work tree back to HEAD) for `paths`, keeping a copy first:
    tracked files are copied into backup_dir before `git restore`; untracked and newly added files are moved
    there instead of deleted. A folder path covers every dirty file under it.
    -> [{"path", "action": "restored|moved", "backup": path or None}]"""
    ps = _paths(root, paths)
    st = status(root)
    untracked = set(st["untracked"])
    dirty = dirty_files(root)
    expanded = []
    for p in ps:
        if os.path.isdir(os.path.join(root, p)):
            expanded += [d for d in dirty if d.startswith(p + "/")]
        else:
            expanded.append(p)
    has_head = _has_head(root)
    done = []
    for rel in dict.fromkeys(expanded):
        full = os.path.join(root, *rel.split("/"))
        if rel in untracked:
            dest = _backup_target(backup_dir, rel)
            shutil.move(full, dest)
            done.append({"path": rel, "action": "moved", "backup": dest})
            continue
        in_head = has_head and _git(root, "cat-file", "-e", f"HEAD:{rel}", check=False)[0] == 0
        if not in_head:                                    # staged new file: unstage, then park it in the backup
            _git(root, "rm", "--cached", "-q", "-f", "--", rel)
            dest = None
            if os.path.lexists(full):
                dest = _backup_target(backup_dir, rel)
                shutil.move(full, dest)
            done.append({"path": rel, "action": "moved", "backup": dest})
            continue
        dest = None
        if os.path.isfile(full):
            dest = _backup_target(backup_dir, rel)
            shutil.copy2(full, dest)
        _git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", rel)
        done.append({"path": rel, "action": "restored", "backup": dest})
    return done


# ---------------------------------------------------------------- per-hunk apply / reject

def _counts(hunk):
    ol = sum(1 for ln in hunk["lines"] if ln["kind"] != "add")
    nl = sum(1 for ln in hunk["lines"] if ln["kind"] != "del")
    return ol, nl


def build_patch(path, hunk, old_path=None):
    """A minimal one-hunk git patch for `path` (counts recomputed from the lines)."""
    old = old_path or path
    ol, nl = _counts(hunk)
    tab = lambda p: "\t" if " " in p else ""           # noqa: E731 — git's marker for names containing spaces
    a, b = _quote("a/" + old), _quote("b/" + path)
    out = [f"diff --git {a} {b}", f"--- {a}{tab(old)}", f"+++ {b}{tab(path)}",
           f"@@ -{hunk['old_start']},{ol} +{hunk['new_start']},{nl} @@"]
    mark = {"add": "+", "del": "-", "ctx": " "}
    for ln in hunk["lines"]:
        out.append(mark[ln["kind"]] + ln["text"])
        if ln.get("no_eol"):
            out.append("\\ No newline at end of file")
    return "\n".join(out) + "\n"


def apply_hunk(root, path, hunk, reverse=True, staged=False, old_path=None):
    """Apply ONE hunk (from parse_diff) with `git apply`. reverse=True rejects it (the per-hunk "reject" in the
    UI); staged=True works on the index instead of the work tree (reverse+staged = unstage that hunk,
    forward+staged = stage that hunk)."""
    rel = _rel(root, path)
    patch = build_patch(rel, hunk, old_path=_rel(root, old_path) if old_path else None)
    args = ["apply", "-p1", "--whitespace=nowarn"]
    if reverse:
        args.append("-R")
    if staged:
        args.append("--cached")
    if not any(ln["kind"] == "ctx" for ln in hunk["lines"]):
        args.append("--unidiff-zero")
    args.append("-")
    _git(root, *args, input=patch)
    return {"ok": True, "path": rel}


def _split_keep(text):
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def revert_hunk_in_file(file_path, hunk, reverse=True):
    """apply_hunk without git, for plain folders: edits the file in place. reverse=True turns the hunk's new side
    back into its old side. The block is looked for at the hunk's line number first, then at the nearest place
    it matches exactly (line endings ignored); the file's own line-ending style is kept."""
    with open(file_path, "rb") as fh:
        text = _dec(fh.read())
    lines = _split_keep(text)
    have, want = (("ctx", "add"), ("ctx", "del")) if reverse else (("ctx", "del"), ("ctx", "add"))
    find = [ln for ln in hunk["lines"] if ln["kind"] in have]
    repl = [ln for ln in hunk["lines"] if ln["kind"] in want]
    start = hunk["new_start"] if reverse else hunk["old_start"]
    guess = start - 1 if find else start                # a 0-line side's start is the line *before* the gap
    key = lambda s: s.rstrip("\r\n")                    # noqa: E731
    if find:
        hits = [i for i in range(0, len(lines) - len(find) + 1)
                if all(key(lines[i + k]) == key(find[k]["text"]) for k in range(len(find)))]
        if not hits:
            raise GitError(f"hunk does not match the current contents of {file_path}")
        at = min(hits, key=lambda i: abs(i - guess))
    else:
        at = max(0, min(guess, len(lines)))
    crlf = sum(1 for s in lines if s.endswith("\r\n"))
    nl = "\r\n" if crlf and crlf >= len(lines) / 2 else "\n"
    new = [key(r["text"]) + nl for r in repl]
    end = at + len(find)
    if new and end == len(lines):                       # block reaches EOF: get the final newline right
        last = repl[-1]
        if last.get("no_eol"):
            new[-1] = new[-1][:-len(nl)]
        elif find and find[-1]["kind"] == "ctx" and last is find[-1] and not lines[-1].endswith("\n"):
            new[-1] = new[-1][:-len(nl)]
    if new and at == len(lines) and lines and not lines[-1].endswith("\n"):
        lines[-1] += nl                                 # appending after a last line that had no newline
    lines[at:end] = new
    with open(file_path, "wb") as fh:
        fh.write(_enc("".join(lines)))
    return {"ok": True, "line": at + 1}


# ---------------------------------------------------------------- commit / log (D2)

def commit(root, message, trailer=None, allow_empty=False):
    """Commit what is staged. trailer: 'Key: value' or a list of them (e.g. an attribution line). -> sha."""
    if not (message or "").strip():
        raise GitError("commit message is empty")
    args = ["commit", "-q", "-F", "-"]
    for t in ([trailer] if isinstance(trailer, str) else (trailer or [])):
        if t and t.strip():
            args += ["--trailer", t.strip()]
    if allow_empty:
        args.append("--allow-empty")
    _git(root, *args, input=message if message.endswith("\n") else message + "\n")
    return _out(root, "rev-parse", "HEAD").strip()


def log(root, n=20):
    if not is_repo(root) or not _has_head(root):
        return []
    out = _out(root, "log", f"-n{int(n)}", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e")
    res = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, short, author, email, date, subject = (rec.split("\x1f") + [""] * 6)[:6]
        res.append({"sha": sha, "short": short, "author": author, "email": email, "date": date, "subject": subject})
    return res


def staged_summary(root, cap_chars=6000):
    """`git diff --cached --stat` plus as much of the staged diff as fits in cap_chars — the model's input for
    drafting a commit message. Never longer than cap_chars."""
    stat = _out(root, "diff", "--cached", "--no-color", "--stat=120").rstrip()
    body = _out(root, "diff", "--cached", "--no-color", "--no-ext-diff", "-M", "-U2",
                "--src-prefix=a/", "--dst-prefix=b/")
    if not stat and not body:
        return ""
    text = stat + "\n\n" + body
    if len(text) <= cap_chars:
        return text
    marker = "\n[... diff truncated: {} more chars]"
    room = cap_chars - len(marker.format(len(text)))
    if room <= 0:
        return text[:cap_chars]
    return text[:room] + marker.format(len(text) - room)


# ---------------------------------------------------------------- branches (D4)

def branches(root):
    """-> {"current": name or None, "local": [names], "detail": [{"name","sha","upstream","current"}]}"""
    cur = _git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False)[1].strip() or None
    out = _out(root, "for-each-ref", "--format=%(refname:short)%1f%(objectname:short)%1f%(upstream:short)",
               "refs/heads")
    detail = []
    for line in out.splitlines():
        name, sha, up = (line.split("\x1f") + ["", ""])[:3]
        if name:
            detail.append({"name": name, "sha": sha, "upstream": up or None, "current": name == cur})
    return {"current": cur, "local": [d["name"] for d in detail], "detail": detail}


def _check_branch(root, name):
    if not name or name.startswith("-"):
        raise GitError(f"invalid branch name: {name!r}")
    _git(root, "check-ref-format", "--branch", name)


def create_branch(root, name, switch=True):
    _check_branch(root, name)
    if switch:
        _git(root, "switch", "-q", "-c", name)
    else:
        _git(root, "branch", name)
    return name


def switch_branch(root, name):
    _check_branch(root, name)
    _git(root, "switch", "-q", name)
    return name


# ---------------------------------------------------------------- remotes: clone / push / pull (D6, D7)

_URL_CRED = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://([^/@]*)@")


def _scrub(url):
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/@:]*):[^/@]*@", r"\1\2@", url) if url else url


def clone(url, dest_parent, name=None):
    """`git clone url <dest_parent>/<name>`; name defaults to the repo name in the URL. Authentication goes
    through the system credential helper; a password embedded in the URL is refused (it would be saved in
    .git/config). -> absolute path of the new checkout."""
    url = (url or "").strip()
    if not url or url.startswith("-"):
        raise GitError(f"invalid clone URL: {url!r}")
    m = _URL_CRED.match(url)
    if m and ":" in m.group(1):
        raise GitError("the URL contains a password; remove it and let the credential manager sign in")
    if not name:
        name = re.split(r"[/\\:]", url.rstrip("/\\"))[-1]
        name = name[:-4] if name.lower().endswith(".git") else name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "repo"
    os.makedirs(dest_parent, exist_ok=True)
    dest = os.path.abspath(os.path.join(dest_parent, name))
    if os.path.exists(dest) and os.listdir(dest):
        raise GitError(f"destination already exists and is not empty: {dest}")
    _exec([GIT, *_BASE, "clone", "--", url, dest], dest_parent, timeout=NET_TIMEOUT)
    return dest


def remote_url(root, name="origin"):
    """URL of `name` (else the first remote), with any password scrubbed; None when there is no remote."""
    if not is_repo(root):
        return None
    code, out, _ = _git(root, "remote", "get-url", name, check=False)
    if code != 0:
        remotes = _out(root, "remote").split()
        if not remotes:
            return None
        code, out, _ = _git(root, "remote", "get-url", remotes[0], check=False)
        if code != 0:
            return None
    return _scrub(out.strip()) or None


def push(root, remote="origin", set_upstream=True):
    """Push the current branch (setting its upstream on the first push)."""
    args = ["push", "-q"] + (["-u"] if set_upstream else []) + [remote, "HEAD"]
    _git(root, *args, timeout=NET_TIMEOUT)
    return status(root)


def pull(root):
    """Fast-forward-only pull: never creates merge commits or conflicts behind the user's back."""
    _git(root, "pull", "-q", "--ff-only", timeout=NET_TIMEOUT)
    return status(root)


# ---------------------------------------------------------------- pull requests via gh (D7)

def _gh_path():
    return shutil.which(GH)


def pr_create(root, title, body, base=None, dry_run=False):
    """`gh pr create --title T --body-file - [--base B]`, body fed on stdin (no command-line length or quoting
    limits). dry_run=True -> the argv list, nothing is run. Otherwise -> {"url", "output"}."""
    title = (title or "").strip()
    if not title:
        raise GitError("pull request title is empty")
    if "\n" in title or "\r" in title:
        raise GitError("pull request title must be one line")
    if base is not None and (not base.strip() or base.startswith("-")):
        raise GitError(f"invalid base branch: {base!r}")
    argv = [GH, "pr", "create", "--title", title, "--body-file", "-"]
    if base:
        argv += ["--base", base.strip()]
    if dry_run:
        return argv
    if not is_repo(root):
        raise GitError(f"not a git repository: {root}")
    exe = _gh_path()
    if not exe:
        raise GitError("GitHub CLI (gh) not found on PATH")
    _, out, _ = _exec([exe] + argv[1:], root, input=body or "", timeout=NET_TIMEOUT)
    urls = re.findall(r"https://\S+", out)
    return {"url": urls[-1] if urls else None, "output": out.strip()}


def _summarize_pr(data):
    checks = []
    for c in data.get("statusCheckRollup") or []:
        if c.get("__typename") == "StatusContext" or "context" in c:
            state = (c.get("state") or "").upper()
            checks.append({"name": c.get("context") or "", "status": "COMPLETED" if state not in ("PENDING", "EXPECTED")
                           else "IN_PROGRESS", "conclusion": {"SUCCESS": "SUCCESS", "FAILURE": "FAILURE",
                                                              "ERROR": "FAILURE"}.get(state)})
        else:
            checks.append({"name": c.get("name") or c.get("workflowName") or "", "status": c.get("status") or "",
                           "conclusion": c.get("conclusion") or None})
    bad = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
    if not checks:
        summary = "none"
    elif any((c["conclusion"] or "").upper() in bad for c in checks):
        summary = "failing"
    elif any((c["status"] or "").upper() != "COMPLETED" for c in checks):
        summary = "pending"
    else:
        summary = "passing"
    return {"url": data.get("url"), "state": data.get("state"), "title": data.get("title"),
            "checks": checks, "checks_summary": summary}


def pr_status(root):
    """The current branch's PR: {"url","state","title","checks":[...],"checks_summary"}; None when there is no PR,
    gh is missing or not usable here. Never raises."""
    exe = _gh_path()
    if not exe or not is_repo(root):
        return None
    try:
        code, out, _ = _exec([exe, "pr", "view", "--json", "url,state,title,statusCheckRollup"], root,
                             timeout=NET_TIMEOUT, check=False)
        if code != 0:
            return None
        return _summarize_pr(json.loads(out))
    except (GitError, ValueError, AttributeError, TypeError):
        return None
