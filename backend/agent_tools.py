"""Tools for Agent mode: web, files, shell, screen/keyboard/mouse, and MCP servers.

Every tool has a KIND, which decides whether the user must approve it:
  read   inspect files / windows           (auto-allowed by default)
  net    web search / fetch                 (auto-allowed by default)
  write  create / change / move / trash     (asks)
  exec   run a shell command                (asks, every time unless allowed for the session)
  input  mouse / keyboard / focus window    (asks)
  mcp    a tool on an MCP server            (asks, per the server's policy)
Nothing here ever deletes permanently: "delete" moves to <app>/agent_trash.
"""
import ctypes
import ctypes.wintypes as wt
import difflib
import fnmatch
import hashlib
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import tools as webtools
from mcp_client import MCPError

READ, NET, WRITE, EXEC, INPUT, MCP = "read", "net", "write", "exec", "input", "mcp"
# auto-allowed bookkeeping kinds: the tracker / memory (app files), controlling the agent's own
# background processes and threads. CLOUD (sending context to the OpenAI advisor) asks.
NOTE, CONTROL, CLOUD = "note", "control", "cloud"
import paths
TRACKERS = paths.data("agent_trackers")
MEMORY_FILE = paths.data("agent_memory.md")
SCREEN_TOOLS = {"screen_size", "list_windows", "screenshot", "mouse_move", "mouse_click", "mouse_drag",
                "scroll", "type_text", "press_keys", "focus_window"}
MAX_RESULT = 8000
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TRASH = paths.data("agent_trash")
BACKUPS = os.path.join(TRASH, "backups")       # pre-change copies, used by "Revert" in the chat
_bk_seq = itertools.count(1)

# commands that can wreck the machine: refused outright, approval or not
BLOCKED = [r"\bformat\s+[a-z]:", r"\bformat-volume\b", r"\bclear-disk\b", r"\bdiskpart\b", r"\bbcdedit\b", r"\bcipher\s+/w",
           r"\bshutdown\b", r"\b(restart|stop)-computer\b", r"\bset-executionpolicy\b",
           r"\breg\s+(delete|add)\s+hk(lm|cr)", r"\bremove-item\b.*[a-z]:\\\s*($|-)",
           r"\brd\s+/s\b.*\s[a-z]:\\\s*$", r"\bdel\b.*\s/[fsq]+.*\s[a-z]:\\\s*$",
           r"\bnet\s+user\b", r"\bnew-localuser\b", r"\bvssadmin\b.*delete", r"\bwevtutil\s+cl\b"]


APP_PORTS = (8765, 8099, 8100, 8101)        # the app's backend and its model servers — never for the user's projects
_KILL_RX = re.compile(r"\b(stop-process|spps|taskkill|kill)\b", re.I)


def protected_reason(command):
    """A command that would stop the app's OWN processes (its model server, backend or window) -> why, else None.
    A 9B building a server picked port 8099, found "a stray server" there — its own llama-server — and killed it,
    which cut off its own model mid-task."""
    if not _KILL_RX.search(command or ""):
        return None
    low = command.lower()
    why = ("That would stop this app's own {what} (it runs this conversation). Don't stop it — if a port you want is "
           f"taken, use another one (the app uses ports {', '.join(map(str, APP_PORTS))}).")
    if re.search(r"llama-server|app_web|web_backend", low):
        return why.format(what="model server or backend")
    if re.search(r"(get-process|gps|ps)\s+(-name\s+)?['\"]?pythonw?\b|/im\s+pythonw?(\.exe)?\b", low):
        return why.format(what="backend (it is a Python process)")
    pids = {int(x) for x in re.findall(r"\b\d{2,7}\b", low)}
    if not pids:
        return None
    protected = {os.getpid(), os.getppid()}
    try:
        import glob
        import llama_local
        for pf in glob.glob(os.path.join(llama_local._state_dir(), "swarm_server*.pid")):
            try:
                protected.add(int(open(pf).read().strip()))
            except (OSError, ValueError):
                pass
    except Exception:
        pass
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cl = " ".join(p.info.get("cmdline") or []).lower()
            if "llama-server" in (p.info.get("name") or "").lower() or "app_web.py" in cl or "web_backend" in cl:
                protected.add(p.info["pid"])
    except Exception:
        pass
    if pids & protected:
        return why.format(what="model server, backend or window")
    return None


class ToolError(Exception):
    pass


class Ctx:
    """Per-run state shared by the tools."""

    def __init__(self, roots, emit=None, mcp=None):
        self.roots = [os.path.abspath(os.path.expanduser(r)) for r in (roots or []) if r]
        self.emit = emit or (lambda *a, **k: None)
        self.mcp = mcp
        self.shot_scale = 1.0          # real pixels per screenshot pixel
        self.shot_size = None
        self.cwd = None                # PowerShell working folder, carried between commands
        self.workspace = None          # this chat's own folder (made on first use): the default working folder
        self.call_id = None            # id of the tool call running now (for live output)
        self.display = None            # rich view of the last call for the chat (terminal/diff/file)
        self.changed = {}              # path -> what happened to it during the current turn
        self.turn_diffs = []           # diff displays of this turn, oldest first (for "undo turn")
        self.should_stop = lambda: False
        self.conv_id = None            # the chat thread this agent belongs to
        self.title = ""
        self.tracker = None            # {"goal", "steps": [{step, status}], "notes": [], "next"}
        self.tracker_path = None
        self.host = None               # delegation host (GUI or headless): delegate/task_info/wait
        self.advisor = None            # callable(question) -> advice text, set by the engine
        self.gopher = None             # callable(briefs) -> (text, results): fan-out workers, set by the engine
        self.gopher_wait = None        # callable() -> text: block until the fan-out workers finish
        self.go_extensive = None       # callable(reason) -> text: switch a basic task to the extensive path
        self.asked = ""                # ask_user's questions: the engine shows them and ends the turn
        self.evidence_check = None     # callable(rows) -> verdicts: check evidence-table rows against what was read
        self.vision = False
        self.enable_screen = None      # callable() that adds the screen tools to the registry
        self.is_subagent = False

    def inside(self, p):
        return any(os.path.splitdrive(r)[0].lower() == os.path.splitdrive(p)[0].lower()
                   and os.path.commonpath([p, r]) == r for r in self.roots)

    def resolve(self, path, must_exist=False):
        path = str(path or "").strip().strip('"')
        if path.lower().startswith("file:///"):
            path = path[8:]
        if path and not os.path.isabs(os.path.expanduser(path)) and (self.cwd or self.workspace or self.roots):
            path = os.path.join(self.cwd or work_dir(self) or self.roots[0], path)   # relative: the shell's folder
        p = os.path.abspath(os.path.expanduser(path))
        if not self.inside(p):
            raise ToolError(f"'{p}' is outside the folders the agent may use "
                            f"({'; '.join(self.roots) or 'none set'}). Ask the user to add it in "
                            f"Agent settings.")
        if must_exist and not os.path.exists(p):
            raise ToolError(f"'{p}' does not exist")
        return p


def clip(s, n=MAX_RESULT):
    s = str(s)
    return s if len(s) <= n else s[:n] + f"\n...[truncated, {len(s) - n} more characters]"


# ----------------------------------------------------------------------------- files
def t_list_dir(a, c):
    p = c.resolve(a.get("path") or c.roots[0], True)
    rows, kinds, nfiles, ndirs = [], {}, 0, 0
    names = sorted(os.listdir(p), key=str.lower)
    for name in names[:400]:
        fp = os.path.join(p, name)
        try:
            if os.path.isdir(fp):
                rows.append(f"{name}/    (folder)")
                ndirs += 1
            else:
                rows.append(f"{name}    ({os.stat(fp).st_size:,} bytes)")
                nfiles += 1
                ext = os.path.splitext(name)[1].lower() or "(no extension)"
                kinds[ext] = kinds.get(ext, 0) + 1
        except OSError:
            rows.append(f"{name}    (unreadable)")
    summary = (f"{nfiles} file{'s' if nfiles != 1 else ''}"
               + (" (" + ", ".join(f"{n} {e}" for e, n in sorted(kinds.items(), key=lambda x: -x[1])) + ")" if kinds else "")
               + f", {ndirs} folder{'s' if ndirs != 1 else ''}"
               + (f" — showing the first 400 of {len(names)}" if len(names) > 400 else ""))
    return f"{p}\n{summary}\n" + "\n".join(rows)


def _read_text(p):
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def _numbered(lines, start):
    """Lines prefixed with their 1-based line number, like `cat -n` (what the model edits against)."""
    return "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines, start))


def _backup(p):
    """Copy p aside before it changes; returns the copy's path (None if p did not exist)."""
    if not os.path.isfile(p):
        return None
    os.makedirs(BACKUPS, exist_ok=True)
    dst = os.path.join(BACKUPS, f"{time.strftime('%Y%m%d_%H%M%S')}_{next(_bk_seq)}_{os.path.basename(p)}")
    shutil.copy2(p, dst)
    return dst


def make_diff(old, new, path, context=3, max_lines=400):
    """-> (unified diff text, lines added, lines removed)."""
    rows = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=path, tofile=path,
                                     lineterm="", n=context))[2:]          # drop the ---/+++ header
    added = sum(1 for r in rows if r.startswith("+"))
    removed = sum(1 for r in rows if r.startswith("-"))
    if len(rows) > max_lines:
        rows = rows[:max_lines] + [f"… {len(rows) - max_lines} more diff lines"]
    return "\n".join(rows), added, removed


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def _png_bytes(img, max_w=1280):
    """PIL image -> PNG bytes, scaled down to max_w wide."""
    import io
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)))
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), img.width, img.height


def _read_image(p, c):
    """Images come back as pixels (the model sees them if it has vision), scaled to <=1280 wide."""
    from PIL import Image
    try:
        img = Image.open(p)
        img.load()
    except Exception as e:
        raise ToolError(f"could not decode that image: {e}")
    w, h = img.width, img.height
    png, _w, _h = _png_bytes(img)
    c.display = {"type": "image", "path": p, "width": w, "height": h}
    note = "" if c.vision else " (this model has vision turned off, so it cannot see the pixels)"
    return (f"image {p}: {w}x{h} pixels{note}", png)


READ_WHOLE, READ_MIN = 600, 200
# characters of numbered lines one read may show: results are clipped at MAX_RESULT, and the outline and notes
# need room. A file that fits is shown whole; a longer one is paged, never silently cut.
READ_BUDGET = MAX_RESULT - 1800
OUTLINE_BUDGET = 1200        # of the 1,800 left: the outline, the size header and the continue note


def t_read_file(a, c):
    p = c.resolve(a.get("path"), True)
    if os.path.isdir(p):
        raise ToolError("that is a folder; use list_dir")
    if os.path.splitext(p)[1].lower() in IMAGE_EXT:
        return _read_image(p, c)
    if os.path.getsize(p) > 20_000_000:
        raise ToolError("file is larger than 20 MB; read part of it with powershell (Get-Content -TotalCount)")
    with open(p, "rb") as f:
        if b"\0" in f.read(4096):
            raise ToolError("this looks like a binary file, not text")
    lines = _read_text(p).splitlines()
    start = max(1, int(a.get("offset") or 1))
    limit = max(1, min(int(a.get("limit") or 400), 2000))
    # Small models ask for 10-60 line slices and then never see two related functions at once (a 9B chased a
    # deadlock for an hour: the lock in _logout and the second lock in _auth were never on screen together).
    # A file up to READ_WHOLE lines is always shown whole; a bigger one at least READ_MIN lines at a time.
    asked = (start, limit) if (a.get("offset") or a.get("limit")) else None
    cut = [ln if len(ln) <= 2000 else ln[:2000] + "…" for ln in lines]
    fits = len(_numbered(cut, 1)) <= READ_BUDGET
    if len(lines) <= READ_WHOLE and fits:
        start, limit = 1, max(len(lines), 1)
        if asked and asked[0] > 1:
            a["_whole_note"] = (f"(The whole file is shown — {len(lines)} lines is small enough. The lines you asked "
                                f"for, {asked[0]}-{min(len(lines), asked[0] + asked[1] - 1)}, are in it; no need to "
                                f"read it again.)\n")
    else:
        limit = max(limit, READ_MIN)
    # page by characters too: a 330-line file of long lines used to be "shown whole" and then clipped at
    # MAX_RESULT — the model was told it had the whole file, asked for the rest, and got line 1 again, forever
    seg, used = [], 0
    for ln in cut[start - 1:start - 1 + limit]:
        used += len(ln) + 8                            # the line-number prefix and newline
        if seg and used > READ_BUDGET:
            break
        seg.append(ln)
    end = start + len(seg) - 1
    c.display = {"type": "file", "path": p, "start": start, "end": end, "total": len(lines)}
    if not lines:
        return f"{p} is empty"
    # the file's full size and the exact part shown, on every read: the model must know whether it has seen it all
    whole = start == 1 and end == len(lines)
    head = (f"[{os.path.basename(p)}: {len(lines)} lines, {sum(len(x) + 1 for x in lines):,} characters — "
            + ("showing the whole file]\n" if whole else
               f"showing lines {start}-{end}; {start - 1} line(s) before, {len(lines) - end} after"
               + (f" — continue with offset={end + 1}" if end < len(lines) else "") + "]\n"))
    out = a.pop("_whole_note", "") + _numbered(seg, start)
    if p.lower().endswith(TABLE_EXT):
        out = _table_hint(p).lstrip("\n") + "\n" + out
    if end < len(lines):
        out += f"\n…[lines {start}-{end} of {len(lines)}; call again with offset={end + 1} for more]"
        if start == 1:
            ol = _outline(p, lines)
            if ol and len(ol) > OUTLINE_BUDGET:          # it must fit beside the page, or the clip cuts the page
                ol = ol[:OUTLINE_BUDGET].rsplit("\n", 1)[0] + "\n… (more definitions: search_text for a name)"
            if ol:
                out = (f"OUTLINE of {os.path.basename(p)} ({len(lines)} lines) — jump with offset=, or "
                       f"search_text(pattern, root=this file, context='function'):\n{ol}\n\n" + out)
    return head + out


_OUTLINE_RX = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|struct|impl|fn|func|"
                         r"(?:public|private|protected|static)\s+[\w<>\[\]]+\s+\w+\s*\()\b|"
                         r"^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>")


def _outline(p, lines, limit=80):
    """Classes and functions of a source file with their line ranges (Python: exact; others: a regex scan)."""
    rows = []
    if p.lower().endswith(".py"):
        try:
            import ast
            tree = ast.parse("\n".join(lines))

            def walk(nodes, depth):
                for n in nodes:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        kind = "class" if isinstance(n, ast.ClassDef) else "def"
                        rows.append(f"{'  ' * depth}{n.lineno}-{n.end_lineno or n.lineno}  {kind} {n.name}")
                        walk(n.body, depth + 1)
            walk(tree.body, 0)
        except Exception:
            rows = []
    if not rows:
        rows = [f"{i}  {ln.strip()[:100]}" for i, ln in enumerate(lines, 1) if _OUTLINE_RX.match(ln)]
    if len(rows) > limit:
        rows = rows[:limit] + [f"… {len(rows) - limit} more"]
    return "\n".join(rows)


TABLE_EXT = (".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".parquet", ".json")


def _table_hint(path, kind=None):
    """A data table being read: totals, counts and averages come from code, not from rows picked out by eye (a model
    adding up rows it selected by reading misses some — 407 instead of 422 in one test)."""
    if not (str(path).lower().endswith(TABLE_EXT) or kind in ("table", "spreadsheet")):
        return ""
    return ("\nFor any total, count, average, maximum or filter over these rows, compute it with query_data (SQL) or "
            "python_sandbox on the file — never by picking rows out by reading and adding them up.")


def t_read_document(a, c):
    import doc_extract
    p = c.resolve(a.get("path"), True)
    if doc_extract.kind_of(p) == "image":
        return _read_image(p, c)
    try:
        info = doc_extract.extract(p, a.get("start"), a.get("end"), a.get("sheet"),
                                   max(2000, min(int(a.get("max_chars") or 30000), 60000)))
    except doc_extract.DocError as e:
        raise ToolError(str(e))
    c.display = {"type": "document", "path": p, "kind": info["kind"], "unit": info["unit"],
                 "first": info["first"], "last": info["last"], "total": info["total"]}
    head = f"{doc_extract.summary_line(p, info)}"
    if info["unit"] in ("page", "slide"):
        head += f", showing {info['unit']}s {info['first']}–{info['last']}"
    return (head + (f"\nNote: {info['note']}" if info["note"] else "") + _table_hint(p, info.get("kind")) + "\n\n"
            + info["text"])


def _fstamp(p):
    try:
        st = os.stat(p)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def note_seen(c, p):
    """The agent now knows this file's current content (it read, wrote or edited it)."""
    try:
        known = c.__dict__.setdefault("_known_files", {})
        known[os.path.abspath(p).lower()] = _fstamp(p)
    except Exception:
        pass


def _seen_current(c, p):
    known = getattr(c, "_known_files", {}) or {}
    k = known.get(os.path.abspath(p).lower())
    return k is not None and k == _fstamp(p)


def t_write_file(a, c):
    p = c.resolve(a.get("path"))
    content = str(a.get("content", ""))
    if len(content) > 1_000_000:
        raise ToolError("content too large (1 MB limit)")
    if os.path.isdir(p):
        raise ToolError("that path is a folder")
    existed = os.path.isfile(p)
    old = _read_text(p) if existed else ""
    new = old + content if a.get("append") else content
    if existed and new.replace("\r\n", "\n") == old.replace("\r\n", "\n"):
        raise ToolError(f"{p} already has exactly this content, so nothing was written. Run or test it instead of "
                        f"rewriting it.")
    if existed and not a.get("append"):
        if not _seen_current(c, p):
            # overwriting from memory loses work (a model rewrote a PRD section by section and wiped its own earlier
            # sections); the agent must know the file as it is now
            raise ToolError(f"{os.path.basename(p)} already exists and you haven't read its current version, so "
                            f"overwriting it could lose content. read_file it first — or use append=true to add to "
                            f"it, or edit_file for a targeted change.")
        old_n, new_n = len(old.splitlines()), len(new.splitlines())
        sig = (os.path.abspath(p).lower(), hashlib.md5(new.encode("utf-8", "replace")).hexdigest())
        ok = c.__dict__.setdefault("_shrink_ok", set())
        if old_n >= 20 and new_n <= max(3, old_n * 0.15):
            # almost nothing left: a cut-off or placeholder write, not a rewrite. Resending must NOT confirm it (a model
            # resent a truncated 1-line write and wiped a 206-line file)
            raise ToolError(f"this would replace {old_n} lines of {os.path.basename(p)} with {new_n} — that looks like a "
                            f"cut-off or incomplete write, so it was not done (resending it won't change that). Change "
                            f"part of the file with edit_file, add with append=true, or write the new version in parts "
                            f"(first part, then append=true). To really start the file over, move the old one to the "
                            f"trash first (trash_path), then write the new file.")
        if old_n >= 20 and new_n < old_n * 0.4 and sig not in ok:
            ok.add(sig)                                  # sending the same write again confirms it
            raise ToolError(f"this would replace {old_n} lines of {os.path.basename(p)} with {new_n} — most of the "
                            f"file would be deleted. To add a section use append=true; to change part of it use "
                            f"edit_file. If replacing it really is intended, send exactly the same write_file again.")
    bk = _backup(p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    diff, add, rem = make_diff(old, new, p)
    c.display = {"type": "diff", "path": p, "diff": diff, "added": add, "removed": rem,
                 "backup": bk, "created": not existed}
    c.turn_diffs.append(c.display)
    c.changed[p] = "created" if not existed or c.changed.get(p) == "created" else "edited"
    verb = "appended to" if a.get("append") else ("overwrote" if existed else "created")
    prd_note = ""
    if os.path.basename(p).lower() == "prd.md" and not re.search(r"acceptance|done when|success criteria", new, re.I):
        prd_note = ("\nNote: this PRD has no acceptance criteria. Add a short 'Acceptance criteria' list of testable "
                    "statements (each feature, invalid input, who can see or change what) — the plan's last milestone "
                    "and the tests check each one.")
    big_note = ""
    if not a.get("append") and len(new.splitlines()) > 250 and re.search(r"\.(py|js|ts|tsx|jsx|go|rs|java|cs|cpp|c)$", p, re.I):
        # long single writes are where defects slip in (a 469-line test file came with a duplicate method and a
        # syntax error); the next file of this size is better written and checked in parts
        big_note = ("\nNote: that was a long file in one write. Run it or its tests now, and write the next big file in "
                    "parts (one feature or test group, then append=true), checking each part.")
    return (f"{verb} {p} ({len(new.splitlines())} lines, +{add} -{rem})" + _note_change(c, p, old, new, existed)
            + prd_note + big_note)


def _loose_match(data, old):
    """Find `old` in `data` comparing whole lines with whitespace runs ignored (indentation, trailing spaces, doubled
    spaces inside a line). -> (start, end, (old's base indent, the file's base indent)) for exactly one match, else None."""
    sq = lambda ln: " ".join(ln.split())
    olines = [sq(ln) for ln in old.replace("\r\n", "\n").strip("\n").split("\n")]
    if not any(olines):
        return None
    dlines = data.split("\n")
    k = len(olines)
    hits = [i for i in range(len(dlines) - k + 1)
            if all(sq(dlines[i + j]) == olines[j] for j in range(k))]
    if len(hits) != 1:
        return None
    i = hits[0]
    start = sum(len(ln) + 1 for ln in dlines[:i])
    end = start + sum(len(ln) + 1 for ln in dlines[i:i + k]) - 1
    if dlines[i + k - 1].endswith("\r"):
        end -= 1
    ind = {}                                             # old's indentation -> the file's, from the matched lines
    for j, ol in enumerate(old.replace("\r\n", "\n").strip("\n").split("\n")):
        fl = dlines[i + j].rstrip("\r")
        if ol.strip():
            ind.setdefault(ol[:len(ol) - len(ol.lstrip())], fl[:len(fl) - len(fl.lstrip())])
    return start, end, ind


def _reindent(new, ind, nl):
    """Re-indent `new` with the map from _loose_match. An indentation not in the map (a new nesting level) keeps its
    offset from the deepest known level below it, scaled by the file's indent unit when that can be told."""
    known = sorted(ind, key=len)
    out = []
    for ln in new.replace("\r\n", "\n").strip("\n").split("\n"):
        if not ln.strip():
            out.append("")
            continue
        cur = ln[:len(ln) - len(ln.lstrip())]
        if cur in ind:
            out.append(ind[cur] + ln.lstrip())
            continue
        base = max((w for w in known if cur.startswith(w)), key=len, default=None)
        if base is None:
            out.append(ln)
            continue
        extra = cur[len(base):]
        steps = [(len(a), len(ind[a])) for a in known]  # pairs of (old width, file width) to find the unit ratio
        ratio = next(((fb - fa) / (ob - oa) for (oa, fa), (ob, fb) in zip(steps, steps[1:]) if ob > oa and fb > fa), 1)
        fill = ("\t" if "\t" in "".join(ind.values()) else " ")
        out.append(ind[base] + (fill * round(len(extra) * ratio) if ratio != 1 and fill == " " else extra) + ln.lstrip())
    return nl.join(out)


def _closest_region(data, old):
    """The file region most like `old`, as a short diff, so the model sees what is actually there."""
    import difflib
    olines = old.replace("\r\n", "\n").strip("\n").split("\n")
    dlines = data.replace("\r\n", "\n").split("\n")
    k = max(1, len(olines))
    if len(dlines) > 20000:
        return ""
    best, bi = 0.0, -1
    key = "\n".join(ln.strip() for ln in olines)
    for i in range(0, max(1, len(dlines) - k + 1)):
        r = difflib.SequenceMatcher(None, key, "\n".join(ln.strip() for ln in dlines[i:i + k])).quick_ratio()
        if r > best:
            best, bi = r, i
    if bi < 0 or best < 0.5:
        return ""
    region = dlines[bi:bi + k]
    if difflib.SequenceMatcher(None, key, "\n".join(ln.strip() for ln in region)).ratio() < 0.5:
        return ""
    diff = list(difflib.unified_diff(olines, region, "your old", f"file lines {bi + 1}-{bi + k}", lineterm="", n=1))
    return "\nClosest match in the file (lines %d-%d):\n%s" % (bi + 1, bi + k, "\n".join(diff[:40]))


def t_edit_file(a, c):
    p = c.resolve(a.get("path"), True)
    old, new = str(a.get("old", "")), str(a.get("new", ""))
    if not old:
        raise ToolError("'old' must be the exact text to replace (read_file first; leave out the "
                        "line-number prefix)")
    if old == new:
        raise ToolError("'old' and 'new' are identical, so this edit would change nothing. If you suspect a typo, "
                        "compare the two spellings character by character; for code, the automatic code check after "
                        "each write already reports misspelled (undefined) names. If it reported none, there is "
                        "probably no typo: run the program and fix what actually fails.")
    data = _read_text(p)
    n = data.count(old)
    if n == 0 and "\r\n" in data and "\r\n" not in old:      # model sent \n for a CRLF file
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        n = data.count(old)
    loose = ""
    if n == 0:
        # small models often get indentation or trailing spaces wrong: match line by line ignoring those, and
        # re-indent `new` by the same shift. Only a single unambiguous match is applied.
        found = _loose_match(data, old)
        if not found:
            raise ToolError("the text to replace was not found; it must match the file exactly, including spaces "
                            "and indentation." + _closest_region(data, old) + "\nread_file the region again and retry.")
        start, end, shift = found
        old = data[start:end]
        new = _reindent(new, shift, "\r\n" if "\r\n" in old else "\n")
        n = 1
        loose = " (matched ignoring indentation/trailing spaces)"
    if n > 1 and not a.get("replace_all"):
        raise ToolError(f"the text appears {n} times; include more surrounding lines to make it "
                        f"unique, or pass replace_all=true")
    result = data.replace(old, new) if a.get("replace_all") else data.replace(old, new, 1)
    bk = _backup(p)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(result)
    diff, add, rem = make_diff(data, result, p)
    c.display = {"type": "diff", "path": p, "diff": diff, "added": add, "removed": rem,
                 "backup": bk, "created": False}
    c.turn_diffs.append(c.display)
    c.changed[p] = "created" if c.changed.get(p) == "created" else "edited"
    # show the edited region with line numbers so the model can check its work
    first = result[:result.find(new)].count("\n") + 1 if new and new in result else 1
    lines = result.splitlines()
    lo, hi = max(1, first - 3), min(len(lines), first + new.count("\n") + 3)
    return (f"edited {p}: replaced {n if a.get('replace_all') else 1} occurrence(s){loose}, +{add} -{rem}. "
            f"Lines {lo}-{hi} now:\n" + _numbered(lines[lo - 1:hi], lo) + _note_change(c, p, data, result, True))


def t_make_dir(a, c):
    p = c.resolve(a.get("path"))
    os.makedirs(p, exist_ok=True)
    c.changed[p] = "created folder"
    return f"created {p}"


def t_move_path(a, c):
    s, d = c.resolve(a.get("src"), True), c.resolve(a.get("dst"))
    if os.path.exists(d) and not os.path.isdir(d):
        raise ToolError(f"'{d}' already exists; trash it first or pick another name")
    os.makedirs(os.path.dirname(d), exist_ok=True)
    final = shutil.move(s, d)
    c.changed[os.path.abspath(final)] = f"moved from {s}"
    return f"moved {s} -> {final}"


def t_trash_path(a, c):
    p = c.resolve(a.get("path"), True)
    if any(os.path.abspath(p) == r for r in c.roots):
        raise ToolError("refusing to trash an entire allowed folder")
    os.makedirs(TRASH, exist_ok=True)
    dst = os.path.join(TRASH, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(p))
    shutil.move(p, dst)
    c.changed[p] = "moved to agent trash"
    return f"moved {p} to the agent trash ({dst}); nothing was permanently deleted"


def revert(display):
    """Undo a write/edit shown in the chat. -> message for the user. Raises ToolError."""
    p = display.get("path")
    if display.get("created"):
        if not os.path.isfile(p):
            raise ToolError(f"{p} no longer exists")
        os.makedirs(TRASH, exist_ok=True)
        dst = os.path.join(TRASH, time.strftime("%Y%m%d_%H%M%S_reverted_") + os.path.basename(p))
        shutil.move(p, dst)
        return f"Removed the new file {os.path.basename(p)} (moved to agent_trash)"
    bk = display.get("backup")
    if not bk or not os.path.isfile(bk):
        raise ToolError("the backup copy for this change is missing")
    _backup(p)                      # keep the current version too, so the revert can be undone
    shutil.copy2(bk, p)
    return f"Restored {os.path.basename(p)} to how it was before this change"


def t_find_files(a, c):
    root = c.resolve(a.get("root") or c.roots[0], True)
    pat = a.get("pattern") or "*"
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__")]
        for fn in fns:
            if fnmatch.fnmatch(fn.lower(), pat.lower()):
                out.append(os.path.join(dp, fn))
                if len(out) >= 200:
                    return "\n".join(out) + "\n...[200 results shown]"
    return "\n".join(out) or "(no matches)"


def _py_spans(src):
    """(start, end) line spans of every function/method in Python source (innermost wins when looked up)."""
    try:
        import ast
        return [(n.lineno, n.end_lineno or n.lineno) for n in ast.walk(ast.parse(src))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    except Exception:
        return []


def _search_windows(fp, hits, context, label=None, outside=12):
    """The matches of one file shown in place: the whole enclosing function (Python, context='function') or
    +-N lines, overlapping windows merged, matching lines marked '>'. outside: lines around a match that is in no
    function (module level, or a language without the exact parse)."""
    text = _read_text(fp)
    lines = text.splitlines()
    spans = _py_spans(text) if context == "function" and fp.lower().endswith(".py") else []
    n = outside if context == "function" else max(1, min(int(context), 60))
    if spans and context == "function":
        n = min(n, 3)                     # a Python module-level line (an import, a constant): its neighbours only
    wins = []
    for ln in hits:
        inner = [s for s in spans if s[0] <= ln <= s[1]]
        if inner:
            lo, hi = max(inner, key=lambda s: s[0])
            if hi - lo > 120:                                   # a very long function: the part around the match
                lo, hi = max(lo, ln - 40), min(hi, ln + 40)
        else:
            lo, hi = max(1, ln - n), min(len(lines), ln + n)
        if wins and lo <= wins[-1][1] + 1:
            wins[-1][1] = max(wins[-1][1], hi)
        else:
            wins.append([lo, hi])
    hit = set(hits)
    parts = []
    for lo, hi in wins:
        parts.append(f"{label or fp}  lines {lo}-{hi}:\n" + "\n".join(
            f"{'>' if i in hit else ' '}{i:>5}\t{lines[i - 1][:300]}" for i in range(lo, hi + 1)))
    return parts


def _callees(path, line):
    """Names called inside the Python function defined at `line` (in order, unique)."""
    try:
        import ast
        tree = ast.parse(_read_text(path))
    except Exception:
        return []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno == line:
            out = []
            for m in ast.walk(n):
                if isinstance(m, ast.Call):
                    f = m.func
                    # plain calls and self./cls. methods only: obj.get() on an unknown object would be matched to
                    # whatever project function happens to share the name
                    nm = f.id if isinstance(f, ast.Name) else (
                        f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id in ("self", "cls") else "")
                    if nm and nm not in out:
                        out.append(nm)
            return out
    return []


def t_trace_symbol(a, c):
    """A function/class/variable and everything connected to it, across files: its definition and every place that
    uses it, each shown inside its whole enclosing function, plus what it calls. Python names are resolved the way
    Python resolves them (jedi): same-named functions in other files are kept apart."""
    import repomap
    name = str(a.get("name") or "").strip()
    if not name:
        raise ToolError("give the name of a function, class, method or variable")
    root = c.resolve(a.get("root") or (c.cwd or c.roots[0]), True)
    prefer = ""
    if os.path.isfile(root):
        prefer, root = os.path.abspath(root), os.path.dirname(root)
    if a.get("file"):
        prefer = os.path.abspath(c.resolve(a.get("file"), True))
    word = name.split(".")[-1]
    defs = repomap.find_definition(root, name)
    for d in defs:
        d["abs"] = os.path.abspath(os.path.join(root, d["path"]))
    if prefer:
        defs.sort(key=lambda d: d["abs"].lower() != prefer.lower())
    budget = max(100, min(int(a.get("max_lines") or 400), 1200))
    parts, used, more = [], 0, []
    shown = set()                                         # (path, line) of reference lines already accounted for

    def add_block(text, oneliner):
        nonlocal used
        n = text.count("\n") + 1
        if used + n > budget:
            more.append(oneliner)
        else:
            parts.append(text)
            used += n

    def windows(path, lines):
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            rel = path
        return _search_windows(path, sorted(set(lines)), "function", label=rel)

    if not defs:
        refs = repomap.find_references(root, name, limit=60)
        if not refs:
            return f"No definition of or reference to '{name}' under {root}."
        parts.append(f"No definition of '{name}' found (it may come from a library) — {len(refs)} use(s) by name:")
        byfile = {}
        for r in refs:
            byfile.setdefault(r["path"], []).append(r["line"])
        for f, lines in byfile.items():
            for w in windows(os.path.join(root, f), lines):
                add_block(w, f"{f}: lines {', '.join(map(str, lines))}")
    resolved_any = False
    for d in defs[:4]:
        path, line = d["abs"], d["line"]
        head = f"=== {d['kind']} {name} — defined in {d['path']}:{line}"
        refs, how = [], "by name"
        if path.endswith(".py"):
            try:
                import jedi
                text = _read_text(path).splitlines()
                m = re.search(r"(?<![\w$])" + re.escape(word) + r"(?![\w$])", text[line - 1]) if line <= len(text) else None
                if m:
                    found = jedi.Script(path=path, project=jedi.Project(root)).get_references(
                        line, m.start(), scope="project")
                    refs = [(str(r.module_path), r.line) for r in found
                            if r.module_path and not (os.path.abspath(str(r.module_path)) == path and r.line == line)]
                    how, resolved_any = "resolved (jedi)", True
            except Exception:
                refs, how = [], "by name"
        if how == "by name":
            refs = [(os.path.join(root, r["path"]), r["line"]) for r in repomap.find_references(root, name, limit=60)]
        parts.append(head + f" · {len(refs)} use(s), {how}")
        used += 1
        for w in windows(path, [line]):
            add_block("DEFINITION:\n" + w, f"definition {d['path']}:{line}")
        byfile = {}
        for p, ln in refs:
            byfile.setdefault(os.path.abspath(p), []).append(ln)
            shown.add((os.path.abspath(p).lower(), ln))
        for p, lines in byfile.items():
            rel = os.path.relpath(p, root)
            for w in windows(p, lines):
                add_block("USED IN:\n" + w, f"{rel}: lines {', '.join(map(str, sorted(lines)))}")
        calls = _callees(path, line) if path.endswith(".py") else []
        sigs = []
        for nm in calls[:25]:
            hit = repomap.find_definition(root, nm)
            if hit:
                h = hit[0]
                sigs.append(f"  {nm} — {h['path']}:{h['line']}  {h['sig']}")
        if sigs:
            parts.append("IT CALLS (defined in this project):\n" + "\n".join(sigs))
            used += len(sigs) + 1
    if resolved_any:
        # same word, not resolved to any definition above: another function of the same name, or a dynamic call
        # (obj.method with an unknown type) — listed, not mixed in
        defined_at = {(d["abs"].lower(), d["line"]) for d in defs}
        other = [r for r in repomap.find_references(root, name, limit=200)
                 if (os.path.abspath(os.path.join(root, r["path"])).lower(), r["line"]) not in shown | defined_at]
        if other:
            parts.append(f"SAME NAME, NOT RESOLVED to the definition(s) above ({len(other)}) — another '{word}' or a "
                         f"call on an object of unknown type; check before assuming they are related:\n"
                         + "\n".join(f"  {r['path']}:{r['line']}  {r['text']}" for r in other[:15])
                         + (f"\n  … {len(other) - 15} more" if len(other) > 15 else ""))
    if len(defs) > 4:
        parts.append(f"{len(defs) - 4} more definitions named '{name}': " + ", ".join(
            f"{d['path']}:{d['line']}" for d in defs[4:12]) + " — pass file= to pick one")
    if more:
        parts.append("NOT SHOWN (line budget) — read them with read_file or raise max_lines:\n  " + "\n  ".join(more[:30]))
    return "\n\n".join(parts)


def t_search_text(a, c):
    root = c.resolve(a.get("root") or c.roots[0], True)
    try:
        rx = re.compile(a.get("pattern") or "", re.I)
    except re.error as e:
        raise ToolError(f"bad regular expression: {e}")
    glob = a.get("glob") or "*"
    context = a.get("context")
    if context not in (None, "", 0, "0", False):
        context = "function" if str(context).lower() in ("function", "func", "def", "true") else (
            int(context) if str(context).isdigit() else "function")
        files = [root] if os.path.isfile(root) else []
        if not files:
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__")]
                files += [os.path.join(dp, fn) for fn in fns if fnmatch.fnmatch(fn.lower(), glob.lower())]
        parts, total = [], 0
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="strict") as f:
                    hits = [i for i, line in enumerate(f, 1) if rx.search(line)]
            except (UnicodeDecodeError, OSError):
                continue
            if hits:
                for p in _search_windows(fp, hits, context):
                    parts.append(p)
                    total += p.count("\n")
                    if total > 600 or len(parts) >= 30:
                        return "\n\n".join(parts) + "\n...[more matches: narrow the pattern, root or glob]"
        return "\n\n".join(parts) or "(no matches)"
    out = []
    if os.path.isfile(root):
        try:
            with open(root, "r", encoding="utf-8", errors="strict") as f:
                out = [f"{root}:{i}: {line.strip()[:200]}" for i, line in enumerate(f, 1) if rx.search(line)][:100]
        except (UnicodeDecodeError, OSError):
            pass
        return "\n".join(out) or "(no matches)"
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__")]
        for fn in fns:
            if not fnmatch.fnmatch(fn.lower(), glob.lower()):
                continue
            fp = os.path.join(dp, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="strict") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            out.append(f"{fp}:{i}: {line.strip()[:200]}")
                            if len(out) >= 100:
                                return "\n".join(out) + "\n...[100 matches shown]"
            except (UnicodeDecodeError, OSError):
                continue
    return "\n".join(out) or "(no matches)"


# ----------------------------------------------------------------------------- shell
def blocked_reason(command):
    low = command.lower()
    for pat in BLOCKED:
        if re.search(pat, low):
            return pat
    return None


CWD_MARK = "<<__AGENT_CWD__>>"
PS_HEAD = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8\n"
           "$ProgressPreference = 'SilentlyContinue'\n"
           "$__e0 = $Error.Count; $global:LASTEXITCODE = $null\n"
           "& {\n")
# The user's command runs in the block above. Its output is rendered to text right there: left to
# PowerShell's deferred table formatting, object output is lost when the script exits. Afterwards:
# remember where it ended up (cd sticks) and pass on a meaningful exit code.
PS_TAIL = ("\n} | Out-String -Stream -Width 220\n"
           "$__ec = $LASTEXITCODE; $__ok = ($Error.Count -eq $__e0)\n"
           f"Write-Output ('{CWD_MARK}' + (Get-Location).ProviderPath)\n"
           "if ($__ec) { exit $__ec } elseif (-not $__ok) { exit 1 } else { exit 0 }\n")


def work_dir(c, create=True):
    """This chat's own folder (under the workspace root), made on first use -> path or None."""
    w = getattr(c, "workspace", None)
    if not w:
        return None
    if create and not os.path.isdir(w):
        try:
            os.makedirs(w, exist_ok=True)
        except OSError:
            return None
    return w


def shell_cwd(c):
    if c.cwd and os.path.isdir(c.cwd) and c.inside(c.cwd):
        return c.cwd
    w = work_dir(c)
    if w and c.inside(w):
        return w
    return c.roots[0]


def _proc_env():
    try:
        import stackdump
        return stackdump.env()
    except Exception:
        return dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")


def _kill_tree(proc):
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       timeout=10, creationflags=0x08000000)
    except Exception:
        proc.kill()


_WAITS_ON_PURPOSE = re.compile(r"Start-Sleep|\bsleep\b|Wait-(Process|Job|Event)|Read-Host|\btimeout\s+/t|pause\b", re.I)


def _owned_bg(c):
    """The agent's own running background processes (its servers) for this chat."""
    try:
        import bg_tasks
        return [p for p in bg_tasks.MANAGER.procs.values()
                if p.running and p.proc is not None and p.owner == getattr(c, "conv_id", None)]
    except Exception:
        return []


def _stall_watch(proc, c, cmd, a):
    """A blocked-command detector for this run, or None (asked to wait, or the command waits on purpose)."""
    if a.get("allow_idle") or _WAITS_ON_PURPOSE.search(cmd or ""):
        return None
    try:
        import stackdump
        return stackdump.StallWatch(proc.pid, lambda: [p.proc.pid for p in _owned_bg(c)], quiet=10.0)
    except Exception:
        return None


def _blocked_report(proc, c, cwd, watch):
    """Where a blocked command — and every server the agent is running — is waiting (Python stacks)."""
    parts = []
    try:
        import stackdump
        s = stackdump.dump(proc.pid, project_hint=cwd)
        if s and not s.startswith("(no stack"):
            parts.append("THE COMMAND — " + s)
        for p in _owned_bg(c):
            s = stackdump.dump(p.proc.pid, project_hint=p.cwd or cwd)
            if s and not s.startswith("(no stack"):
                parts.append(f"YOUR BACKGROUND TASK {p.id} ({p.name}) — " + s)
    except Exception:
        pass
    if parts:
        parts.append("Read the top '>' line of each stack: that is the line each program is waiting on. A client "
                     "waiting on a socket + a server thread waiting on a lock = a deadlock in the server.")
    return "\n\n".join(parts)


def t_powershell(a, c):
    cmd = str(a.get("command", "")).strip()
    if not cmd:
        raise ToolError("no command given")
    if blocked_reason(cmd):
        raise ToolError("that command is blocked by the safety policy (it can damage the system)")
    cwd = c.resolve(a.get("cwd"), True) if a.get("cwd") else shell_cwd(c)
    timeout = max(1, min(int(a.get("timeout") or 120), 600))
    shell = (a.get("shell") or "powershell").lower()
    script = None
    if shell == "cmd":
        argv = ["cmd", "/d", "/s", "/c", cmd]
    else:
        exe = "pwsh" if shell == "pwsh" and shutil.which("pwsh") else "powershell"
        fd, script = tempfile.mkstemp(suffix=".ps1", prefix="agent_")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:      # BOM: PS 5.1 reads it as UTF-8
            f.write(PS_HEAD + cmd + PS_TAIL)
        argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script]
    c.display = {"type": "terminal", "shell": shell, "cwd": cwd, "command": cmd,
                 "output": "", "exit": None}
    t0 = time.time()
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=0x08000000, env=_proc_env())
    q = queue.Queue()

    def pump():
        for raw in iter(proc.stdout.readline, b""):
            q.put(raw)
        q.put(None)
    threading.Thread(target=pump, daemon=True).start()

    out, new_cwd, pending, last_emit, why_stopped = [], None, [], 0.0, None
    done, exited_at, killed_at, held = False, None, None, False
    watch = _stall_watch(proc, c, cmd, a)
    port_check_at = time.time() + 1.0
    while not done:
        try:
            raw = q.get(timeout=0.1)
        except queue.Empty:
            raw = ""
            # The shell has exited but the output pipe never closes: a program it launched (a GUI app like winver or
            # notepad, a detached server) inherited the pipe and is still running. Reading until EOF waited for that
            # window to be closed — 44 minutes in one test. Stop after a quiet second, and 3 s after a timeout kill.
            if proc.poll() is not None:
                exited_at = exited_at or time.time()
                if time.time() - exited_at > 1.0:
                    done, held = True, True
            if killed_at and time.time() - killed_at > 3.0:
                done = True
        if raw is None:
            done = True
        elif raw:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(CWD_MARK):
                new_cwd = line[len(CWD_MARK):].strip()
            else:
                out.append(line)
                pending.append(line)
                if watch:
                    watch.output()
        if pending and (done or time.time() - last_emit > 0.15):
            c.emit("tool_output", id=c.call_id, text="\n".join(pending) + "\n")
            pending, last_emit = [], time.time()
        if not done and why_stopped is None and proc.poll() is None and time.time() >= port_check_at:
            port_check_at = time.time() + 1.0
            grabbed = app_port_grab(proc.pid)
            if grabbed:
                why_stopped = APP_PORT_MSG.format(port=grabbed)
                _kill_tree(proc)
                killed_at = time.time()
        if not done and why_stopped is None and watch and proc.poll() is None and watch.blocked():
            # blocked, not working: hand it back now (with where everything is waiting) instead of at the timeout
            report = _blocked_report(proc, c, cwd, watch)
            try:
                import bg_tasks
                bp = bg_tasks.MANAGER.adopt(proc, q, cmd, cwd, owner=getattr(c, "conv_id", None),
                                            prior=list(out), script=script)
                script = None                          # the bg task removes it when the command ends
                kept = (f"It is STILL RUNNING as background task {bp.id}: bg_output('{bp.id}') shows what it prints "
                        f"next, bg_stop('{bp.id}') ends it.")
            except Exception:
                _kill_tree(proc)
                kept = "It was stopped."
            text = "\n".join(out).strip("\n")
            c.display.update(output=clip(text, 30000), exit=None, cwd_after=c.cwd or cwd,
                             seconds=round(time.time() - t0, 1), stopped="blocked")
            return clip(text or "(no output)") + (
                f"\n[BLOCKED after {round(time.time() - t0)}s: no output and no CPU, disk or network activity for "
                f"{watch.idle_for()}s — it is WAITING on something (a lock, a socket, input, a server), not working, "
                f"so waiting longer won't help. {kept}" + (f"\n{report}" if report else "") + "]")
        if not done and why_stopped is None:
            if c.should_stop():
                why_stopped = "stopped by the user"
            elif time.time() - t0 > timeout:
                why_stopped = f"timed out after {timeout}s"
                try:                                  # before killing it: where was it stuck?
                    import stackdump
                    stuck_at = stackdump.dump(proc.pid, project_hint=cwd)
                except Exception:
                    stuck_at = ""
            if why_stopped:
                _kill_tree(proc)
                killed_at = time.time()
    try:
        rc = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        rc = None
    if held and why_stopped is None:
        why_stopped = None
        out.append("(A program this command started is still running on its own — if it opened a window, it is on "
                   "the user's screen. Don't open programs to read information; use commands that print it.)")
    if script:
        try:
            os.remove(script)
        except OSError:
            pass
    text = "\n".join(out).strip("\n")
    note = ""
    if new_cwd and os.path.isdir(new_cwd):
        if c.inside(new_cwd):
            c.cwd = new_cwd
        else:
            note = f"\n(note: the folder {new_cwd} is outside the allowed folders; staying in {cwd})"
    timed_out = bool(why_stopped and why_stopped.startswith("timed out"))
    if timed_out and locals().get("stuck_at"):
        note += "\n[" + locals()["stuck_at"] + "]"
    if timed_out:
        crashed = re.search(r"Traceback \(most recent call last\)|\bError\b|Exception", text)
        # a process killed by the timeout did not "exit 1": it was still running. For a program with a main loop
        # (a game, a GUI, a server) that is normal — reporting it as a silent crash sent agents hunting ghosts
        note += (f"\n[{why_stopped}: the program was STILL RUNNING when it was stopped"
                 + (". The output above shows an error from before that." if crashed else
                    " — no crash or error was seen. A program with a main loop (game, GUI, server, watcher) never "
                    "exits on its own, so a timeout is NOT a failure. To test one: run it headless with a frame or "
                    "time limit and scripted input (see read_guide('software')), test its logic with run_tests, or "
                    "start it with bg_start and read its output with bg_output. To SHOW it to the user, use show_user "
                    "(no timeout; it runs until they close it).") + "]")
    elif why_stopped:
        note += f"\n[{why_stopped}; the process was killed]"
    c.display.update(output=clip(text, 30000), exit=rc, cwd_after=c.cwd or cwd,
                     seconds=round(time.time() - t0, 1), stopped=why_stopped)
    if not text.strip():
        text = ("(no output before it was stopped)" if timed_out else
                "(the command printed nothing. If you expected data, the command is wrong: fix it "
                "and run it again. Never report results that did not appear here.)")
    head = f"still running after {timeout}s (stopped by the timeout, not an exit)" if timed_out else f"exit code {rc}"
    # not clipped here: Registry.call keeps the start AND the end (the error is usually at the end)
    return f"{head}  (cwd: {shell_cwd(c)})\n{text}{note}"


# ----------------------------------------------------------------------------- web
def _browser_rows(q):
    """Fallback when the keyless engines find nothing: search in the real (off-screen) browser."""
    import browser_agent
    _engine, rows = browser_agent.MANAGER.search(q.replace('"site:', 'site:'), 8)
    return webtools.clean_results(q, rows or [])


def t_web_search(a, c):
    """Research in one call: several phrasings searched, top pages read, the relevant passages returned."""
    import deep_search
    qs = a.get("queries") or []
    if isinstance(qs, str):
        qs = [x for x in re.split(r"\n|;|\|", qs) if x.strip()]
    if not (a.get("query") or a.get("question")) and qs:      # only 'queries' given: the first is the main one
        a = dict(a, query=qs[0])
        qs = qs[1:]
    pages = a.get("pages", a.get("sources"))
    info = {}
    try:
        out = deep_search.deep_search(a.get("query") or a.get("question") or "", qs,
                                      6 if pages in (None, "") else int(pages),
                                      should_stop=getattr(c, "should_stop", None) or (lambda: False),
                                      info=info, fallback=_browser_rows, browser=None,
                                      memory=c.__dict__.setdefault("search_memory", {}))
    except ValueError as e:
        raise ToolError(str(e))
    c.display = dict(info, type="web_hits")
    return out


def _browser_read_page(url, max_chars):
    import browser_agent
    return browser_agent.MANAGER.read_page(url, max_chars)


def t_encyclopedia(a, c):
    """Offline Wikipedia lookup (encyclopedia.py)."""
    import encyclopedia
    q = str(a.get("query") or a.get("title") or a.get("topic") or "").strip()
    if not q:
        raise ToolError("give query='topic or question'")
    if not encyclopedia.available():
        raise ToolError("offline Wikipedia is not installed here" + ("" if getattr(c, "web_off", False) else " — use web_search"))
    arts = encyclopedia.search(q, max(1, min(int(a.get("n") or 3), 6)))
    if not arts:
        return f"No offline Wikipedia article matched '{q}'. Try other words (the topic's usual name), or web_search."
    return encyclopedia.render(arts)


def t_web_fetch(a, c):
    asked = [u for u in ([a.get("url")] if isinstance(a.get("url"), str) else list(a.get("url") or []))
             + ([a["urls"]] if isinstance(a.get("urls"), str) else list(a.get("urls") or [])) if u]
    down = [u for u in asked if local_down_hint(u)]
    if asked and len(down) == len(asked):       # the user's own server isn't running: say so now, not after a timeout
        raise ToolError(f"Could not read {', '.join(down[:3])}: connection refused." + local_down_hint(down[0]))
    urls = a.get("urls")
    if isinstance(a.get("url"), list):
        urls = list(a["url"]) + list(urls or [])
    if isinstance(urls, str):
        urls = [u for u in re.split(r"[\s,;]+", urls) if u]
    if urls:                                       # several pages at once
        import deep_search
        if a.get("url") and isinstance(a.get("url"), str):
            urls = [a["url"]] + list(urls)
        base = paths.data_dir()
        save = os.path.join(base, "agent_attachments", _safe_name(getattr(c, "conv_id", None) or "misc"), "pages")
        info = {}
        try:
            out = deep_search.fetch_many(urls, str(a.get("question") or ""), int(a.get("max_chars") or 7000),
                                         browser=_browser_read_page, save_dir=save,
                                         should_stop=getattr(c, "should_stop", None) or (lambda: False), info=info)
        except ValueError as e:
            raise ToolError(str(e))
        if os.path.isdir(save) and not c.inside(save) and os.path.dirname(save) not in c.roots:
            c.roots.append(os.path.dirname(save))   # the saved pages are readable with read_file
        c.display = info
        return out
    url = str(a.get("url", ""))
    if not re.match(r"^https?://", url, re.I):
        raise ToolError("only http(s) URLs can be fetched (or give 'urls': a list)")
    n = int(a.get("max_chars") or 6000)
    out = webtools.web_fetch(url, n)
    if out.startswith(("(no extractable text", "(could not fetch")):
        # PDFs (and pages trafilatura can't parse): the reader web_search uses handles PDF text too
        import deep_search
        text, _err = deep_search._fetch(url)
        if text:
            return text if len(text) <= n else text[:n] + "\n...[truncated]..."
    if out.startswith(("[blocked]", "(no extractable text", "(could not fetch")):
        # blocked or empty for a plain fetch (bot checks, pages built by JavaScript): try the real browser
        import deep_search
        text, err = deep_search.read_with_browser(url, _browser_read_page, n + 200)
        if text:
            return "(read in the browser: a plain fetch was blocked or empty)\n" + (
                text if len(text) <= n else text[:n] + "\n...[truncated]...")
        return out + f"\n(The real browser also could not read it: {err}.)"
    return out


# ----------------------------------------------------------------------------- screen / input
user32 = ctypes.windll.user32 if os.name == "nt" else None
gdi32 = ctypes.windll.gdi32 if os.name == "nt" else None


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _IU(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("i", _IU)]


VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
      "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25,
      "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def _vk(name):
    n = name.strip().lower()
    if n in VK:
        return VK[n]
    if len(n) == 1:
        r = user32.VkKeyScanW(ord(n)) & 0xFF
        return r if r != 0xFF else ord(n.upper())
    raise ToolError(f"unknown key '{name}'")


def _send_key(vk, up=False):
    inp = _INPUT(type=1)
    inp.i.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=2 if up else 0, time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _send_unicode(ch):
    for up in (False, True):
        inp = _INPUT(type=1)
        inp.i.ki = _KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=0x0004 | (2 if up else 0), time=0,
                               dwExtraInfo=0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def real_screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _to_real(c, x, y):
    w, h = real_screen_size()
    rx, ry = int(round(float(x) * c.shot_scale)), int(round(float(y) * c.shot_scale))
    if not (0 <= rx < w and 0 <= ry < h):
        raise ToolError(f"({x}, {y}) is off the screen (the screenshot area is "
                        f"{int(w / c.shot_scale)}x{int(h / c.shot_scale)})")
    return rx, ry


def grab_screen(max_w=1280):
    """-> (png_bytes, shot_w, shot_h, scale) of the primary screen."""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    w = img.width
    png, sw, sh = _png_bytes(img, max_w)
    return png, sw, sh, w / sw


def t_screen_size(a, c):
    w, h = real_screen_size()
    return f"primary screen: {w}x{h} pixels"


def t_screenshot(a, c):
    png, w, h, scale = grab_screen(int(a.get("max_width") or 1280))
    c.shot_scale, c.shot_size = scale, (w, h)
    return (f"screenshot {w}x{h}. Mouse tools use coordinates in THIS image "
            f"(x 0-{w - 1}, y 0-{h - 1}).", png)


def t_mouse_move(a, c):
    rx, ry = _to_real(c, a.get("x"), a.get("y"))
    user32.SetCursorPos(rx, ry)
    return f"mouse moved to ({a.get('x')}, {a.get('y')})"


def t_mouse_click(a, c):
    rx, ry = _to_real(c, a.get("x"), a.get("y"))
    btn = (a.get("button") or "left").lower()
    down, up = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}.get(btn, (0x2, 0x4))
    user32.SetCursorPos(rx, ry)
    time.sleep(0.05)
    for _ in range(2 if a.get("double") else 1):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)
    return f"{'double-' if a.get('double') else ''}clicked {btn} at ({a.get('x')}, {a.get('y')})"


def t_mouse_drag(a, c):
    x1, y1 = _to_real(c, a.get("x1"), a.get("y1"))
    x2, y2 = _to_real(c, a.get("x2"), a.get("y2"))
    user32.SetCursorPos(x1, y1)
    time.sleep(0.05)
    user32.mouse_event(0x2, 0, 0, 0, 0)
    for i in range(1, 11):
        user32.SetCursorPos(x1 + (x2 - x1) * i // 10, y1 + (y2 - y1) * i // 10)
        time.sleep(0.02)
    user32.mouse_event(0x4, 0, 0, 0, 0)
    return "dragged"


def t_scroll(a, c):
    amt = int(a.get("amount") or 0)
    user32.mouse_event(0x0800, 0, 0, amt * 120, 0)
    return f"scrolled {amt} notches ({'up' if amt > 0 else 'down'})"


_SENSITIVE_WINDOW = re.compile(r"payment|checkout|billing|credit card|pay with|place order", re.I)


def _foreground_title():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def t_type_text(a, c):
    text = str(a.get("text", ""))
    if len(text) > 2000:
        raise ToolError("too much text (2000 characters max)")
    title = _foreground_title()
    if _SENSITIVE_WINDOW.search(title):
        raise ToolError(f"refused: the focused window ('{title[:80]}') looks like a payment or checkout "
                        f"screen. The agent never enters payment details or buys anything.")
    for ch in text:
        if ch == "\n":
            _send_key(0x0D)
            _send_key(0x0D, up=True)
        else:
            _send_unicode(ch)
        time.sleep(0.004)
    return f"typed {len(text)} characters"


def t_press_keys(a, c):
    keys = [k for k in str(a.get("keys", "")).replace(" ", "").split("+") if k]
    if not keys:
        raise ToolError("no keys given (example: 'ctrl+c' or 'enter')")
    vks = [_vk(k) for k in keys]
    for v in vks:
        _send_key(v)
    for v in reversed(vks):
        _send_key(v, up=True)
    return f"pressed {'+'.join(keys)}"


def _windows():
    out = []
    cb_t = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _l):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True
    user32.EnumWindows(cb_t(cb), 0)
    return out


def t_list_windows(a, c):
    return "\n".join(t for _h, t in _windows()[:60]) or "(no visible windows)"


def t_focus_window(a, c):
    want = str(a.get("title", "")).lower()
    for h, t in _windows():
        if want and want in t.lower():
            user32.ShowWindow(h, 9)
            user32.SetForegroundWindow(h)
            return f"focused '{t}'"
    raise ToolError(f"no visible window has '{a.get('title')}' in its title")


def t_wait(a, c):
    s = max(0.1, min(float(a.get("seconds") or 1), 30))
    time.sleep(s)
    return f"waited {s:g}s"


# ----------------------------------------------------------------------------- tracker
# The tracker is the agent's working memory for a task: goal, steps with status, notes (findings,
# decisions, dead ends) and the next step. It is a real markdown file and is put back into the
# system prompt on every step, so the agent keeps its place across long tasks, context
# compaction, app restarts and new turns.
STATUS_ICON = {"pending": "☐", "in_progress": "◐", "done": "☑", "blocked": "⚠", "skipped": "–"}
_STATUS_ALIASES = {"todo": "pending", "not_started": "pending", "open": "pending", "active": "in_progress",
                   "in progress": "in_progress", "doing": "in_progress", "started": "in_progress",
                   "wip": "in_progress", "complete": "done", "completed": "done", "finished": "done",
                   "x": "done", "failed": "blocked", "stuck": "blocked", "cancelled": "skipped",
                   "canceled": "skipped"}
_TRACKER_JSON = re.compile(r"<!-- tracker-json: (\{.*\}) -->", re.S)


def _norm_status(s):
    s = str(s or "pending").strip().lower().replace("-", "_")
    s = _STATUS_ALIASES.get(s.replace("_", " "), _STATUS_ALIASES.get(s, s))
    return s if s in STATUS_ICON else "pending"


_NUMBERING = re.compile(r"^\s*(?:(?:step|milestone|phase|task)\s*)?(?:\d+(?:\.\d+)*|[a-z])\s*[.):\-]\s*|^\s*[-*•]\s+", re.I)


def _clean(text):
    t = str(text or "").strip()
    for _ in range(2):                  # "1. 1.1: x" -> "x"
        t = _NUMBERING.sub("", t, count=1).strip()
    return t


def _norm_steps(steps):
    out = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict):
            text = s.get("step") or s.get("text") or s.get("title") or s.get("content") or s.get("task") or s.get("description") or s.get("name") or ""
            out.append({"step": _clean(text)[:300], "status": _norm_status(s.get("status") or s.get("state"))})
        elif str(s).strip():
            out.append({"step": _clean(s)[:300], "status": "pending"})
    return [s for s in out if s["step"]][:30]


def default_tracker_path(conv_id):
    return os.path.join(TRACKERS, f"{conv_id or 'scratch'}.md")


def _derive(ms):
    """A milestone's status follows its subtasks unless it has none."""
    subs = ms.get("subtasks") or []
    if not subs:
        return ms.get("status", "pending")
    st = [s["status"] for s in subs]
    if all(x in ("done", "skipped") for x in st):
        return "done"
    if any(x == "blocked" for x in st):
        return "blocked"
    if any(x in ("in_progress", "done") for x in st):
        return "in_progress"
    return "pending"


def _norm_milestones(ms):
    out = []
    for m in ms if isinstance(ms, list) else []:
        if isinstance(m, str):
            m = {"title": m}
        if not isinstance(m, dict):
            continue
        title = m.get("title") or m.get("milestone") or m.get("name") or m.get("step") or m.get("text") or ""
        subs = _norm_steps(m.get("subtasks") or m.get("steps") or m.get("tasks") or m.get("items") or [])
        item = {"title": _clean(title)[:200], "status": _norm_status(m.get("status")), "subtasks": subs}
        dw = m.get("done_when") or m.get("check") or m.get("acceptance") or m.get("evidence")
        if dw:
            item["done_when"] = _clean(" ".join(dw) if isinstance(dw, list) else str(dw))[:300]
        item["status"] = _derive(item)
        if item["title"]:
            out.append(item)
    return out[:12]


def upgrade_tracker(t):
    """Old flat trackers (steps) become one milestone."""
    if t and "milestones" not in t:
        t = dict(t)
        steps = t.pop("steps", []) or []
        t["milestones"] = [{"title": "Steps", "status": "pending", "subtasks": steps}] if steps else []
        for m in t["milestones"]:
            m["status"] = _derive(m)
    return t


def tracker_items(t):
    """[(id, text, status, is_milestone)] in order, ids like '2' and '2.3'."""
    out = []
    for i, m in enumerate((t or {}).get("milestones") or [], 1):
        out.append((str(i), m["title"], m["status"], True))
        for j, s in enumerate(m.get("subtasks") or [], 1):
            out.append((f"{i}.{j}", s["step"], s["status"], False))
    return out


def tracker_progress(t):
    """(done, total) counting subtasks (or milestones that have none)."""
    done = total = 0
    for m in (t or {}).get("milestones") or []:
        units = m.get("subtasks") or [m]
        total += len(units)
        done += sum(1 for u in units if u["status"] in ("done", "skipped"))
    return done, total


def tracker_current(t):
    items = [x for x in tracker_items(t) if not x[3]] or tracker_items(t)
    for want in ("in_progress", "pending", "blocked"):
        for iid, text, st, _m in items:
            if st == want:
                return iid, text
    return None


def tracker_open(t):
    """Unfinished subtasks (and milestones without subtasks)."""
    out = []
    for i, m in enumerate((t or {}).get("milestones") or [], 1):
        units = [(f"{i}.{j}", s) for j, s in enumerate(m.get("subtasks") or [], 1)] or [(str(i), m)]
        for iid, u in units:
            if u["status"] not in ("done", "skipped"):
                out.append((iid, u.get("step") or u.get("title"), u["status"]))
    return out


def tracker_markdown(t, title=""):
    done, total = tracker_progress(t)
    lines = [f"# Checklist{': ' + title if title else ''}", "",
             f"_updated {time.strftime('%Y-%m-%d %H:%M')} · {done}/{total} done_", "", "## Goal",
             t.get("goal") or "(not set)"] + (["", "## Design", t["design"]] if t.get("design") else []) + \
            ["", "## Milestones"]
    for iid, text, st, ms in tracker_items(t):
        if ms:
            lines.append(f"\n### {iid}. {STATUS_ICON[st]} {text}")
            dw = (t.get("milestones") or [])[int(iid) - 1].get("done_when")
            if dw:
                lines.append(f"_Done when: {dw}_")
        else:
            lines.append(f"- {STATUS_ICON[st]} {iid} {text}" + (f"  _({st.replace('_', ' ')})_" if st not in ("pending", "done") else ""))
    if not t.get("milestones"):
        lines.append("(none yet)")
    lines += ["", "## Next", t.get("next") or "(not set)", "", "## Notes"]
    lines += [f"- {n}" for n in t.get("notes") or []] or ["(none yet)"]
    lines += ["", f"<!-- tracker-json: {json.dumps(t, ensure_ascii=False)} -->", ""]
    return "\n".join(lines)


def load_tracker(path):
    try:
        with open(path, encoding="utf-8") as f:
            m = _TRACKER_JSON.search(f.read())
        return upgrade_tracker(json.loads(m.group(1))) if m else None
    except (OSError, ValueError):
        return None


def tracker_prompt(t):
    """Compact form of the checklist for the model."""
    if not t:
        return ""
    t = upgrade_tracker(t)
    done, total = tracker_progress(t)
    rows = []
    mss = t.get("milestones") or []
    for iid, text, st, ms in tracker_items(t):
        rows.append(("" if ms else "    ") + f"{STATUS_ICON[st]} {iid}{'.' if ms else ''} {text}"
                    + (f" [{st.replace('_', ' ')}]" if st in ("in_progress", "blocked", "skipped") else ""))
        if ms and mss[int(iid) - 1].get("done_when"):
            rows.append(f"    done when: {mss[int(iid) - 1]['done_when']}")
    cur = tracker_current(t)
    notes = "\n".join(f"  - {n}" for n in (t.get("notes") or [])[-10:])
    return (f"Goal: {t.get('goal') or '(not set)'}\n"
            + (f"Design: {t['design']}\n" if t.get("design") else "")
            + f"Progress: {done}/{total} done\n"
            + ("\n".join(rows) or "  (no milestones yet)")
            + (f"\nCurrent: {cur[0]} {cur[1]}" if cur else "\nAll items are done.")
            + f"\nNext action: {t.get('next') or '(not set)'}"
            + (("\nSettled — decisions and verified facts (don't re-check one unless new evidence contradicts it):\n"
                + "\n".join(f"  - {d}" for d in t["decisions"])) if t.get("decisions") else "")
            + (f"\nNotes (latest):\n{notes}" if notes else ""))


PROGRESS_FIELD = {"type": "string", "description": "optional checklist progress, e.g. '1.2 done, 1.3 started'"}
PROGRESS_EXEMPT = {"update_tracker", "available_tools"}
_PROGRESS_RX = re.compile(r"(\d+(?:\.\d+)?)\s*[:=-]?\s*(done|completed?|finished|in[ _-]?progress|started|start|doing|"
                          r"working(?: on)?|skipped|skip|blocked|pending)", re.I)


def parse_progress(text):
    """'1.2 done, 1.3 started' -> [{'id': '1.2', 'status': 'done'}, {'id': '1.3', 'status': 'in_progress'}]"""
    out = []
    for iid, st in _PROGRESS_RX.findall(str(text or "")):
        s = st.lower()
        s = ("done" if s.startswith(("done", "complet", "finish")) else "skipped" if s.startswith("skip")
             else "blocked" if s == "blocked" else "pending" if s == "pending" else "in_progress")
        out.append({"id": iid, "status": s})
    return out


def t_update_tracker(a, c):
    if a.get("new") and c.tracker and not a.get("milestones") and not a.get("steps") and tracker_open(c.tracker):
        # an empty "new" checklist over one with open items (a 9B sent {"new": true} mid-build and was told
        # "everything is done")
        open_items = tracker_open(c.tracker)
        raise ToolError(f"Not replaced: your checklist still has {len(open_items)} open item(s) (current: "
                        f"{open_items[0][0]} {open_items[0][1]}) and new=true came with no milestones. To update it, "
                        f"send updates (id + status) or notes without new=true; to replace it, send new=true WITH the "
                        f"full milestones.")
    fresh = bool(a.get("new")) or c.tracker is None
    t = {"goal": "", "milestones": [], "notes": [], "next": ""} if fresh else dict(upgrade_tracker(c.tracker))
    t.setdefault("notes", [])
    if fresh and c.tracker and c.tracker.get("goal"):           # keep a trace of the previous checklist
        d, n = tracker_progress(upgrade_tracker(c.tracker))
        t["notes"].append(f"{time.strftime('%H:%M')} previous checklist '{c.tracker.get('goal')}' ended at {d}/{n}")
    if a.get("path"):                                  # a project-level checklist, e.g. PROGRESS.md
        p = c.resolve(a["path"])
        # only a FILE: models that fill every optional field send the workspace folder here, and writing the
        # checklist "into" a folder failed every call (PermissionError) — keep the default file then
        if not os.path.isdir(p) and re.search(r"\.(md|txt|markdown)$", p, re.I):
            # never over an existing file of the user's (an eval lost response_times.txt that way): only a new file
            # or one that already is a checklist
            head = ""
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        head = f.read(200)
                except OSError:
                    head = "?"
            if not os.path.exists(p) or head.lstrip().startswith("# Checklist"):
                c.tracker_path = p
    if a.get("goal"):
        t["goal"] = str(a["goal"])[:600]
    if a.get("design"):
        t["design"] = " ".join(str(a["design"]).split())[:900]
    ms = a.get("milestones")
    if isinstance(ms, str):
        try:
            ms = json.loads(ms)
        except ValueError:
            ms = [ln.strip("-*☐☑ #").strip() for ln in ms.splitlines() if ln.strip()]
    resent = False
    if ms is not None and not fresh and t.get("milestones"):
        new_ms = _norm_milestones(ms)
        if [m.get("title", "").strip().lower() for m in new_ms] == [m.get("title", "").strip().lower() for m in t["milestones"]]:
            resent = True                              # the same plan again: keep it, take only the statuses
            extra = []
            for i, m in enumerate(new_ms, 1):
                if m.get("done_when"):
                    t["milestones"][i - 1]["done_when"] = m["done_when"]
                subs = m.get("subtasks") or []
                if subs:
                    extra += [{"id": f"{i}.{j}", "status": s["status"]} for j, s in enumerate(subs, 1)]
                else:
                    extra.append({"id": str(i), "status": m.get("status")})
            ups0 = a.get("updates") or []
            a = dict(a, updates=(ups0 if isinstance(ups0, list) else []) + extra)
            ms = None
    if ms is not None:
        t["milestones"] = _norm_milestones(ms)
        if fresh:
            for m in t["milestones"]:                 # nothing is done before the work starts
                for sub in m.get("subtasks") or []:
                    if sub["status"] in ("done", "skipped"):
                        sub["status"] = "pending"
                if not m.get("subtasks") and m["status"] in ("done", "skipped"):
                    m["status"] = "pending"
                m["status"] = _derive(m)
    elif a.get("steps") is not None:                   # flat list → one milestone
        steps = a["steps"]
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except ValueError:
                steps = [ln.strip("-*☐☑ ").strip() for ln in steps.splitlines() if ln.strip()]
        t["milestones"] = [{"title": t.get("goal") or "Steps", "status": "pending", "subtasks": _norm_steps(steps)}]
        t["milestones"][0]["status"] = _derive(t["milestones"][0])
    ups = a.get("updates") or a.get("set_status") or []
    if isinstance(ups, str):
        try:
            ups = json.loads(ups)
        except ValueError:
            ups = []
    if isinstance(ups, dict):
        ups = [{"id": k, "status": v} for k, v in ups.items()]
    bad = []
    for u in ups if isinstance(ups, list) else []:
        iid = str((u or {}).get("id", "")).strip().rstrip(".")
        st = _norm_status((u or {}).get("status"))
        parts = iid.split(".")
        try:
            m = t["milestones"][int(parts[0]) - 1]
            if len(parts) == 1:
                for sub in m.get("subtasks") or []:
                    if st in ("done", "skipped") and sub["status"] not in ("done", "skipped"):
                        sub["status"] = st
                m["status"] = st if not m.get("subtasks") else _derive(m)
            else:
                m["subtasks"][int(parts[1]) - 1]["status"] = st
                m["status"] = _derive(m)
        except (ValueError, IndexError):
            bad.append(iid)
    notes = a.get("notes") if a.get("notes") is not None else a.get("note")
    if notes:
        for n in (notes if isinstance(notes, list) else [notes]):
            if str(n).strip():
                t["notes"].append(f"{time.strftime('%H:%M')} {str(n).strip()[:500]}")
        t["notes"] = t["notes"][-60:]
    dec = a.get("decisions")
    if isinstance(dec, str):
        dec = [dec]
    if dec:
        have = list(t.get("decisions") or [])
        for d in dec:
            d = " ".join(str(d).split())[:300]
            if d and d.lower() not in {x.lower() for x in have}:
                have.append(d)
        t["decisions"] = have[-15:]
    if a.get("next") is not None:
        t["next"] = str(a["next"])[:400]
    path = c.tracker_path or default_tracker_path(c.conv_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(tracker_markdown(t, c.title))
    c.tracker, c.tracker_path = t, path
    done, total = tracker_progress(t)
    c.display = {"type": "tracker", "path": path, "done": done, "total": total, **t}
    cur = tracker_current(t)
    msg = (f"checklist saved: {done}/{total} done"
           + (f"; current: {cur[0]} {cur[1]}" if cur else "; everything is done")
           + f"; next: {t.get('next') or '(not set)'}")
    if bad:
        msg += f". Unknown item ids (ignored): {', '.join(bad)} - ids look like '2' or '2.3'."
    if not t["milestones"]:
        msg += ". The checklist has no milestones yet: add them, each with subtasks."
    elif not cur and t.get("next") and not re.search(r"\b(none|nothing|n/a|report|answer|done)\b", t["next"], re.I):
        # every item ticked while "next" names more work: the checklist only covered the first stage
        msg += (f". But your next step ('{t['next'][:120]}') is work the checklist doesn't contain — add the "
                f"remaining milestones now (update_tracker with milestones=[...], each with subtasks and a "
                f"done_when you will test), so the plan covers the whole task.")
    return ("(Next time send only 'updates', 'notes' and 'next' — re-sending the whole checklist takes long to write. Or add progress='1.2 done' to your next tool call.) " if resent else "") + msg


def _mem():
    import memory_store
    return memory_store.store()


def _mem_id(v):
    m = re.match(r"^\s*m?(\d+)\s*$", str(v or ""), re.I)
    if not m:
        raise ToolError("id must be a memory id like 12 or m12 (from memory_search or the memory list)")
    return int(m.group(1))


def t_playbook_note(a, c):
    import playbooks
    name = str(a.get("pattern") or a.get("playbook") or getattr(c, "playbook", None) or "").strip()
    books = playbooks.load_all()
    if name not in books:                        # given by title, or loosely: map it, else use this task's playbook
        low = name.lower()
        name = next((n for n, b in books.items() if b["title"].lower() == low or low in b["title"].lower()),
                    getattr(c, "playbook", None) or "")
    if not name:
        raise ToolError("no playbook is active for this task, so there is nothing to attach the note to")
    try:
        nid = playbooks.add_note(_mem(), name, a.get("note") or a.get("content") or a.get("lesson") or "")
    except ValueError as e:
        raise ToolError(str(e))
    return f"Noted for the '{name}' playbook (n{nid}): it will be shown the next time this kind of task comes up."


def t_memory_save(a, c):
    text = str(a.get("content") or a.get("note") or a.get("fact") or "").strip()
    if not text:
        raise ToolError("nothing to save")
    if len(text) > 1500:
        raise ToolError("too long for one memory: save one fact (under ~1500 characters) per call")
    st = _mem()
    dup = [r for r in st.search(text, ("memory",), k=3) if (r.get("sim") or 0) >= 0.93]
    if dup:
        d = dup[0]
        return (f"Not saved: a nearly identical memory already exists — m{d['id']}: {d['text']}\n"
                f"Use memory_update with id m{d['id']} if it needs changing.")
    mid = st.add("memory", text, tags=str(a.get("tags") or ""), source=getattr(c, "conv_id", None) or "", title=getattr(c, "title", "") or "")
    c.display = {"type": "note", "text": f"m{mid}: {text[:500]}", "action": "saved"}
    return f"saved as memory m{mid}"


def t_memory_update(a, c):
    mid = _mem_id(a.get("id"))
    text = a.get("content")
    try:
        row = _mem().update(mid, text=str(text) if text not in (None, "") else None,
                            tags=str(a["tags"]) if a.get("tags") is not None else None)
    except KeyError as e:
        raise ToolError(str(e))
    c.display = {"type": "note", "text": f"m{mid}: {row['text'][:500]}", "action": "updated"}
    return f"memory m{mid} updated: {row['text']}"


def t_memory_delete(a, c):
    mid = _mem_id(a.get("id"))
    try:
        row = _mem().delete(mid)
    except KeyError as e:
        raise ToolError(str(e))
    c.display = {"type": "note", "text": f"m{mid}: {row['text'][:500]}", "action": "deleted"}
    return f"memory m{mid} deleted ({row['text'][:120]})"


def t_history_search(a, c):
    """This chat's archive: what compaction moved out of the context (tool results, code written, earlier steps)."""
    import history_archive as ha
    conv = getattr(c, "conv_id", None)
    eid = str(a.get("entry") or "").strip()
    if eid:
        e = ha.get(conv, eid)
        if not e:
            raise ToolError(f"no archived entry {eid} in this chat")
        return clip(f"#{e['id']} [{e['kind']}] {e['label']}\n{e['text']}")
    q = str(a.get("query") or "").strip()
    if not q:
        n = len(ha.entries(conv))
        return f"{n} archived entries in this chat. Give a query (keywords) or entry='E12'."
    hits = ha.search(conv, q, int(a.get("limit") or 5))
    if not hits:
        return "No archived entries match. (Only what compaction moved out of the context is archived.)"
    return "\n".join(f"#{e['id']} [{e['kind']}] {e['label']}\n   {snip}" for _s, e, snip in hits) + \
        "\n(history_search entry='E…' returns an entry in full)"


def t_memory_search(a, c):
    import memory_store
    q = str(a.get("query") or "").strip()
    scope = str(a.get("scope") or "all").lower()
    k = max(1, min(int(a.get("limit") or 8), 25))
    kinds = {"memories": ("memory",), "memory": ("memory",), "chats": ("chat",), "chat": ("chat",),
             "conversations": ("chat",)}.get(scope, ("memory", "chat"))
    st = _mem()
    if q in ("", "*", "all") and kinds == ("memory",):
        rows = st.list("memory", k)
        return "\n".join(f"m{r['id']}: {r['text']}" for r in rows) or "No saved memories yet."
    if not q:
        raise ToolError("give a query")
    rows = st.search(q, kinds, k=k, exclude_source=getattr(c, "conv_id", None) if "chat" in kinds else None)
    if not rows:
        return f"Nothing found for '{q}' in {'/'.join(kinds)}."
    out = []
    for r in rows:
        if r["kind"] == "memory":
            out.append(f"m{r['id']} (memory, {memory_store.fmt_age(r['updated'])}): {r['text']}")
        else:
            who = "user" if r["role"] == "user" else "assistant"
            out.append(f"[chat \"{r['title'][:60]}\", {memory_store.fmt_age(r['created'])}, {who}]: "
                       f"{' '.join(r['text'].split())[:600]}")
    return "\n".join(out)


def read_memory(max_chars=3500):
    try:
        return _mem().prompt_block(max_chars)
    except Exception:
        return ""


def _art_conv(c):
    return getattr(c, "conv_id", None) or "misc"


def _art_display(meta, v, action):
    return {"type": "artifact", "id": meta["id"], "title": meta["title"], "atype": meta["type"],
            "language": meta.get("language", ""), "version": v, "action": action}


def t_artifact_create(a, c):
    import artifacts
    try:
        meta, v = artifacts.create(_art_conv(c), a.get("title"), a.get("type"), a.get("content"), a.get("language") or "")
    except artifacts.ArtifactError as e:
        raise ToolError(str(e))
    c.display = _art_display(meta, v, "created")
    return (f"created artifact {meta['id']} \"{meta['title']}\" ({meta['type']}, v{v}); it is open in the "
            f"Artifacts pane. Use artifact_edit with id {meta['id']} to change it.")


def t_artifact_edit(a, c):
    import artifacts
    try:
        meta, v, before, after = artifacts.edit(_art_conv(c), str(a.get("id") or ""), content=a.get("content"),
                                                old=a.get("old"), new=a.get("new"), title=a.get("title"))
    except artifacts.ArtifactError as e:
        raise ToolError(str(e))
    c.display = _art_display(meta, v, "edited")
    _diff, added, removed = make_diff(before, after, meta["title"])
    return f"artifact {meta['id']} updated to v{v} (+{added} -{removed} lines)"


def t_artifact_read(a, c):
    import artifacts
    try:
        meta, text, v = artifacts.read(_art_conv(c), str(a.get("id") or ""), a.get("version"))
    except artifacts.ArtifactError as e:
        raise ToolError(str(e))
    return clip(f"artifact {meta['id']} \"{meta['title']}\" ({meta['type']}) v{v} of {meta['versions'][-1]['v']}:\n{text}")


def t_artifact_list(a, c):
    import artifacts
    rows = artifacts.list_artifacts(_art_conv(c))
    if not rows:
        return "No artifacts in this conversation yet."
    return "\n".join(f"{m['id']}: \"{m['title']}\" ({m['type']}{'/' + m['language'] if m.get('language') else ''}, "
                     f"v{m['versions'][-1]['v']}, {m['versions'][-1]['chars']:,} chars)" for m in rows)


def _live_out(c):
    cid = getattr(c, "call_id", None)
    return (lambda text: c.emit("tool_output", id=cid, text=text)) if cid and getattr(c, "emit", None) else None


def _py_display(code, res, sandbox):
    return {"type": "python", "code": code[:20000], "output": res["text"][:20000], "images": res.get("images") or [],
            "files": res.get("files") or [], "sandbox": sandbox, "error": res.get("error", False)}


_WINPATH_RX = re.compile(r"""[A-Za-z]:(?:\\\\|\\|/)[^'"\n]*?\.\w{1,6}(?=['"])""")


def _sandbox_paths(code, files, c):
    """The sandbox only sees /work. Models keep writing the Windows path of a file in their code (they passed it in
    'files', or forgot to): copy every allowed file the code names into /work and point the code at /work/<name>.
    -> (code, files to copy)."""
    wanted = [c.resolve(p) for p in (files or [])]
    for raw in set(_WINPATH_RX.findall(code)):
        p = raw.replace("\\\\", "\\").replace("/", "\\")
        try:
            p = os.path.abspath(p)
        except (TypeError, ValueError):
            continue
        if os.path.isfile(p) and c.inside(p):
            if p not in wanted:
                wanted.append(p)
            code = code.replace(raw, "/work/" + os.path.basename(p))
    return code, wanted


def t_python_sandbox(a, c):
    _code = str(a.get("code") or "")
    if re.search(r"(localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d+\.\d+|\b10\.\d+\.\d+\.\d+)(:\d+)?", _code):
        raise ToolError("python_sandbox runs isolated (WebAssembly, no network) — it is NOT on this PC and cannot reach "
                        "servers here. To call your server use http_request; to run Python on this PC use python_host "
                        "or powershell.")
    import sandbox
    code = str(a.get("code") or "")
    if not code.strip():
        raise ToolError("no code given")
    code, wanted = _sandbox_paths(code, a.get("files"), c)
    try:
        names = sandbox.copy_inputs(getattr(c, "conv_id", None) or "misc", wanted, c.inside)
        res = sandbox.run(getattr(c, "conv_id", None) or "misc", code, timeout=int(a.get("timeout") or 120),
                          reset=bool(a.get("reset")), on_output=_live_out(c))
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    if names:
        res["text"] = f"(copied into /work: {', '.join(names)})\n" + res["text"]
    # deliver files: save={"report.md": "C:\\...\\report.md"} copies /work files into the allowed folders. Without
    # it, models wrote the requested file in /work, couldn't get it out, and the user never got it.
    save = a.get("save") or {}
    if isinstance(save, list):
        save = {os.path.basename(str(p)): p for p in save}
    saved = []
    for src, dst in (save.items() if isinstance(save, dict) else []):
        s = os.path.join(sandbox.work_dir(getattr(c, "conv_id", None) or "misc"),
                         str(src).replace("/work/", "").lstrip("/\\"))
        try:
            d = c.resolve(str(dst))                    # (inside the allowed folders; may not exist yet)
            if os.path.isdir(d):
                d = os.path.join(d, os.path.basename(s))
            if os.path.isfile(s):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                saved.append(d)
            else:
                res["text"] += f"\n(not saved: /work/{os.path.basename(s)} does not exist)"
        except ToolError as e:
            res["text"] += f"\n(not saved to {dst}: {e})"
    if saved:
        res["text"] += "\nSaved to the user's folder: " + ", ".join(saved)
    elif "Files written:" in res["text"]:
        res["text"] += ("\n(These files are only in the sandbox. To deliver one to the user's folder, run again with "
                        "save={\"<name in /work>\": \"<full target path>\"}, or write it with write_file.)")
    c.display = _py_display(code, res, True)
    return res["text"]


def t_python(a, c):
    import py_kernel
    code = str(a.get("code") or "")
    if not code.strip():
        raise ToolError("no code given")
    try:
        res = py_kernel.run(getattr(c, "conv_id", None) or "misc", code, cwd=shell_cwd(c),
                            timeout=int(a.get("timeout") or 120), reset=bool(a.get("reset")), on_output=_live_out(c))
    except Exception as e:
        raise ToolError(f"Python session failed: {type(e).__name__}: {e}")
    c.display = _py_display(code, res, False)
    return res["text"]


_SQL_FORBIDDEN = re.compile(r"\b(copy|attach|detach|install|load|export|import|create|insert|update|delete|drop|alter|"
                            r"pragma|set|call|checkpoint|vacuum)\b", re.I)


def t_query_data(a, c):
    import duckdb
    sql = str(a.get("sql") or "").strip().rstrip(";")
    files = a.get("files") or {}
    if isinstance(files, list):
        files = {re.sub(r"\W", "_", os.path.splitext(os.path.basename(str(p)))[0]).lower() or f"t{i}": p
                 for i, p in enumerate(files)}
    max_rows = max(1, min(int(a.get("max_rows") or 100), 1000))
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)                   # ignore text inside string literals
    if sql and (_SQL_FORBIDDEN.search(stripped) or ";" in stripped):
        raise ToolError("only read-only queries are allowed (SELECT / WITH / DESCRIBE / SUMMARIZE), one per call")
    for lit in re.findall(r"'((?:[^']|'')*)'", sql):                 # paths written inside the SQL
        if re.search(r"[\\/]|\.(csv|tsv|parquet|json|xlsx?)$", lit, re.I):
            p = c.resolve(lit.replace("''", "'"))
            if not c.inside(p):
                raise ToolError(f"{lit} is outside the allowed folders")
            sql = sql.replace(f"'{lit}'", "'" + p.replace("\\", "/").replace("'", "''") + "'")
    con = duckdb.connect()
    tables = []
    try:
        for name, path in files.items():
            # models often quote the table name inside the key ('"evidence"'): strip quotes, or the table would be
            # called _evidence_ and every query against "evidence" would fail
            name = re.sub(r"\W", "_", str(name).strip().strip("\"'`[] ")) or "t"
            p = c.resolve(str(path))
            if not c.inside(p):
                raise ToolError(f"{path} is outside the allowed folders")
            if not os.path.isfile(p):
                raise ToolError(f"{p} does not exist")
            ext = os.path.splitext(p)[1].lower()
            q = p.replace("\\", "/").replace("'", "''")
            if ext in (".xlsx", ".xlsm", ".xls"):
                import pandas as pd
                sheets = pd.read_excel(p, sheet_name=None)
                for i, (sheet, df) in enumerate(sheets.items()):
                    tn = name if len(sheets) == 1 else f"{name}_{re.sub(chr(92) + 'W', '_', str(sheet)).lower()}"
                    con.register(tn, df)
                    tables.append(tn)
                continue
            reader = {".parquet": "read_parquet", ".json": "read_json_auto", ".jsonl": "read_json_auto",
                      ".ndjson": "read_json_auto"}.get(ext, "read_csv_auto")
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM {reader}('{q}')")
            tables.append(name)
        if not sql:
            if not tables:
                raise ToolError("give sql, or files to describe")
            parts = []
            for t in tables:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                cols = con.execute(f"DESCRIBE {t}").fetchall()
                sample = con.execute(f"SELECT * FROM {t} LIMIT 5").df().to_string(max_colwidth=40)
                parts.append(f"table {t}: {n:,} rows\ncolumns: " + ", ".join(f"{r[0]} ({r[1]})" for r in cols)
                             + f"\nfirst rows:\n{sample}")
            return clip("\n\n".join(parts))
        df = con.execute(sql).df()
    except duckdb.Error as e:
        raise ToolError(f"SQL error: {str(e).splitlines()[0][:400]}" + (f" (tables: {', '.join(tables)})" if tables else ""))
    finally:
        con.close()
    total = len(df)
    shown = df.head(max_rows)
    c.display = {"type": "table", "sql": sql, "columns": [str(x) for x in shown.columns],
                 "rows": shown.head(200).astype(str).values.tolist(), "total": total}
    text = shown.to_string(max_colwidth=60, index=False)
    return clip(f"{total:,} row(s)" + (f", first {max_rows} shown" if total > max_rows else "") + f"\n{text}")


# groups shown by available_tools (a tool not listed falls under "other")
TOOL_GROUPS = [
    ("files", "read, find, write and edit files in the allowed folders; search their contents by meaning",
     ["list_dir", "find_files", "search_text", "trace_symbol", "search_documents", "read_file", "read_document", "write_file", "edit_file", "make_dir",
      "move_path", "trash_path"]),
    ("documents", "create professionally styled Word, Excel, PowerPoint and PDF files (the only way to make them)",
     ["create_document"]),
    ("shell", "PowerShell commands, long-running background processes, and launching your work for the user to see",
     ["powershell", "bg_start", "bg_output", "bg_stop", "bg_list", "http_request", "show_user"]),
    ("web", "offline Wikipedia (instant facts), web search (reads the top pages) and page fetch",
     ["encyclopedia", "web_search", "web_fetch"]),
    ("browser", "a real browser you operate like a person (sites, forms, logins)",
     ["browser_open", "browser_read", "browser_click", "browser_type", "browser_press", "browser_select",
      "browser_scroll", "browser_nav", "browser_tabs", "browser_screenshot", "browser_wait", "browser_links",
      "browser_eval", "browser_devtools"]),
    ("math", "exact calculator: arithmetic, algebra, calculus, units, dates; read formulas from images",
     ["calculate", "read_formula"]),
    ("code", "run Python (isolated sandbox, or on the PC), SQL over data files, run tests, debug scripts",
     ["python_sandbox", "python_host", "query_data", "record", "run_tests", "debug_run", "trace_call",
      "changes_since_green", "undo_file", "port_info", "stack_dump"]),
    ("artifacts", "documents in the Artifacts pane you create and keep revising",
     ["artifact_create", "artifact_edit", "artifact_read", "artifact_list"]),
    ("memory", "long-term memory and search over past conversations",
     ["memory_search", "memory_save", "memory_update", "memory_delete", "playbook_note"]),
    ("delegation", "hand sub-tasks to background agent threads; fan-out research workers",
     ["delegate_task", "wait_task", "task_status", "gather", "wait_for_workers"]),
    ("schedule", "run agent tasks on a schedule (daily, weekdays, chosen days, several times a day, every N days, "
     "monthly, every N hours, once)",
     ["schedule_task", "list_schedules", "update_schedule", "delete_schedule"]),
    ("screen", "see and operate the desktop (mouse, keyboard, windows)",
     ["enable_screen_control", "screen_size", "list_windows", "screenshot", "mouse_move", "mouse_click", "mouse_drag",
      "scroll", "type_text", "press_keys", "focus_window", "wait"]),
    ("advisor", "ask a stronger cloud model when stuck", ["consult_advisor"]),
]
ALWAYS_ON = {"go_extensive", "available_tools", "update_tracker", "read_guide", "history_search", "ask_user"}


def _first_sentence(text, n=150):
    t = " ".join(str(text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    t = m.group(1) if m else t
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def tool_groups(reg):
    """[(group, blurb, [tool names present])] including MCP servers and leftovers."""
    seen, out = set(), []
    for g, blurb, names in TOOL_GROUPS:
        present = [n for n in names if n in reg.by_name]
        if g == "web" and present and not any(is_web_tool(n) for n in present):
            blurb = "offline Wikipedia (instant facts); web access is turned off by the user"
        if present:
            out.append((g, blurb, present))
            seen.update(present)
    servers = {}
    for n, t in reg.by_name.items():
        if t["kind"] == MCP:
            servers.setdefault(t.get("server") or "mcp", []).append(n)
            seen.add(n)
    for srv, names in sorted(servers.items()):
        out.append((f"mcp:{srv}", f"tools from the MCP server '{srv}'", sorted(names)))
    rest = [n for n in reg.by_name if n not in seen and n not in ALWAYS_ON]
    if rest:
        out.append(("other", "other tools", rest))
    return out


def tool_catalog(reg, enabled, query="", gate=True):
    q = str(query or "").strip().lower()
    lines = []
    for g, blurb, names in tool_groups(reg):
        rows = []
        for n in names:
            desc = _first_sentence(reg.by_name[n]["description"])
            if q and q not in n.lower() and q not in desc.lower() and q not in g and q not in blurb.lower():
                continue
            mark = "✓ " if (not gate or n in enabled) else ""
            rows.append(f"  - {mark}{n}: {desc}")
        if rows:
            lines.append(f"[{g}] {blurb}")
            lines.extend(rows)
    if not lines:
        return f"No tools match '{query}'. Call available_tools with no query for the full catalog."
    head = ("Tool catalog (✓ = enabled now). Enable what the task needs with available_tools(enable=[tool or group "
            "names]); enabled tools stay on for this chat and become callable on your next step."
            if gate else "Tool catalog (all tools are enabled).")
    return head + "\n" + "\n".join(lines)


def t_available_tools(a, c):
    gate = getattr(c, "tool_gate", None)
    if gate is None:
        raise ToolError("the tool catalog is not available here")
    return gate(a)


GUIDES_DIR = paths.resource("guides")
GUIDE_ALIASES = {"pptx": "powerpoint", "ppt": "powerpoint", "slides": "powerpoint", "deck": "powerpoint",
                 "presentation": "powerpoint", "docx": "word", "doc": "word", "document": "word", "xlsx": "excel",
                 "xls": "excel", "spreadsheet": "excel", "csv": "pdf", "research": "web_research", "web": "web_research",
                 "search": "web_research", "browser": "web_research", "writing": "clear_writing",
                 "style": "clear_writing", "ste": "clear_writing", "communication": "clear_writing",
                 "simplified_english": "clear_writing", "acw": "clear_writing", "hard": "hard_search",
                 "hard_target": "hard_search", "playbook": "hard_search", "search_playbook": "hard_search",
                 "deep_search": "hard_search", "hard_search_playbook": "hard_search",
                 "operators": "hard_search_operators", "search_operators": "hard_search_operators",
                 "archive": "hard_search_archives", "archives": "hard_search_archives", "wayback": "hard_search_archives",
                 "paywall": "hard_search_archives", "blocked": "hard_search_archives",
                 "specialist_indexes": "hard_search_sources", "databases": "hard_search_sources",
                 "sources": "hard_search_sources", "site_excavation": "hard_search_sites", "sitemap": "hard_search_sites",
                 "wikidata": "hard_search_sites", "sparql": "hard_search_sites", "structured_queries": "hard_search_sites",
                 "techniques": "hard_search_techniques", "verification": "hard_search_techniques",
                 "verify": "hard_search_techniques"}


def t_read_guide(a, c):
    topic = str(a.get("topic") or "").strip().lower().replace(" ", "_").replace(".md", "")
    topic = GUIDE_ALIASES.get(topic, topic)
    available = sorted(f[:-3] for f in os.listdir(GUIDES_DIR) if f.endswith(".md")) if os.path.isdir(GUIDES_DIR) else []
    if topic not in available:
        return "Guides: " + ", ".join(available) + ". Call read_guide with one of these topics."
    with open(os.path.join(GUIDES_DIR, topic + ".md"), encoding="utf-8") as f:
        return f.read()


def t_create_document(a, c):
    import doc_create
    path = c.resolve(str(a.get("path") or ""))
    if not c.inside(path):
        raise ToolError(f"{path} is outside the allowed folders")
    if os.path.exists(path) and not a.get("overwrite"):
        raise ToolError(f"{path} already exists; pass overwrite=true to replace it (or choose another name)")
    try:
        out, kind, info = doc_create.create(path, a.get("content") or "", a.get("type"), a.get("title"),
                                            a.get("theme"), a.get("subtitle"))
    except doc_create.DocError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"could not create the document: {type(e).__name__}: {e}")
    # an empty result is a failure, not a success with a number in it (a model reported "done" on "0 data rows")
    if re.search(r"\b0 (?:data rows|slides?|paragraphs?|pages?)\b", str(info)):
        try:
            os.remove(out)
        except OSError:
            pass
        hint = {"xlsx": 'JSON {"sheets": [{"name": "Sheet1", "rows": [["Header A", "Header B"], ["value", "value"]]}]} '
                        "(the first row is the header; every further row is data) or CSV text with a header line",
                "pptx": 'JSON {"slides": [{"layout": "title", "title": "..."}, {"layout": "bullets", "title": "...", '
                        '"bullets": ["..."]}]}'}.get(kind, "Markdown text with headings and paragraphs")
        raise ToolError(f"the {kind} came out EMPTY ({info}) — the content was not in a format it reads, so nothing was "
                        f"saved. Use: {hint}. read_guide('{ {'xlsx': 'excel', 'pptx': 'powerpoint'}.get(kind, 'word') }') "
                        f"has the full format.")
    c.changed[out] = "created"
    c.display = {"type": "document_created", "path": out, "kind": kind, "info": info}
    return f"created {out} ({kind}: {info})"


def t_read_formula(a, c):
    import formula_ocr
    p = c.resolve(str(a.get("path") or ""))
    if not c.inside(p) or not os.path.isfile(p):
        raise ToolError(f"{p} is not an image file in the allowed folders")
    r = formula_ocr.read(p)
    if r.get("error"):
        raise ToolError("could not read the formula: " + r["error"])
    c.display = {"type": "math", "expression": os.path.basename(p), "result": r["latex"], "latex": r["latex"]}
    return f"LaTeX read from the image (check it; small symbols can be misread):\n{r['latex']}"


def t_schedule_task(a, c):
    import scheduler
    try:
        it = scheduler.add(str(a.get("name") or ""), str(a.get("prompt") or ""), str(a.get("when") or ""), "agent")
    except scheduler.ScheduleError as e:
        raise ToolError(str(e))
    nxt = time.strftime("%a %Y-%m-%d %H:%M", time.localtime(it["next_run"]))
    return (f"scheduled task {it['id']} \"{it['name']}\" ({it['when']}); next run {nxt}. Each run happens in its own "
            f"new chat; the user can see, pause or delete it under Scheduled at the bottom of the sidebar.")


def t_list_schedules(a, c):
    import scheduler
    rows = scheduler.all_tasks()
    if not rows:
        return "No scheduled tasks."
    out = []
    for it in rows:
        nxt = time.strftime("%a %m-%d %H:%M", time.localtime(it["next_run"])) if it.get("next_run") else "—"
        out.append(f"{it['id']}: \"{it['name']}\" {it['when']} {'(paused)' if not it.get('enabled') else ''} next {nxt}, "
                   f"{it.get('runs', 0)} runs — {it['prompt'][:120]}")
    return "\n".join(out)


def t_update_schedule(a, c):
    """Pause / resume, rename, reword or reschedule an existing scheduled task."""
    import scheduler
    changes = {k: a[k] for k in ("name", "prompt", "when", "enabled") if a.get(k) is not None and a.get(k) != ""}
    if not changes:
        raise ToolError("give at least one of: name, prompt, when, enabled")
    if "enabled" in changes:
        changes["enabled"] = bool(changes["enabled"]) if not isinstance(changes["enabled"], str) \
            else changes["enabled"].strip().lower() in ("true", "yes", "1", "on")
    try:
        it = scheduler.update(str(a.get("id") or ""), **changes)
    except scheduler.ScheduleError as e:
        raise ToolError(str(e))
    nxt = time.strftime("%a %Y-%m-%d %H:%M", time.localtime(it["next_run"])) if it.get("next_run") else "—"
    return (f"updated scheduled task {it['id']} \"{it['name']}\": {it['when']}, "
            f"{'active' if it.get('enabled') else 'paused'}, next run {nxt if it.get('enabled') else '— (paused)'}")


def t_delete_schedule(a, c):
    import scheduler
    try:
        scheduler.delete(str(a.get("id") or ""))
    except scheduler.ScheduleError as e:
        raise ToolError(str(e))
    return f"deleted scheduled task {a.get('id')}"


def t_search_documents(a, c):
    import memory_store
    q = str(a.get("query") or "").strip()
    if not q:
        raise ToolError("give a query")
    st = memory_store.store()
    if not st.file_stats()["files"]:
        raise ToolError("no files are indexed yet (indexing runs in the background while the agent is idle, over the "
                        "allowed folders); use find_files / search_text meanwhile")
    folder = c.resolve(str(a["folder"])) if a.get("folder") else None
    k = max(1, min(int(a.get("limit") or 8), 20))
    rows = st.search(q, ("file",), k=k * 4)
    best = {}
    for r in rows:                                  # best passage per file
        if folder and not os.path.abspath(r["source"]).lower().startswith(folder.lower()):
            continue
        if r["source"] not in best:
            best[r["source"]] = r
    hits = list(best.values())[:k]
    if not hits:
        return f"No indexed document matches '{q}'."
    c.display = {"type": "doc_hits", "query": q, "hits": [{"path": h["source"], "snippet": " ".join(h["text"].split())[:240],
                                                           "matched": h.get("matched")} for h in hits]}
    return "\n".join(f"{h['source']}  [{h.get('matched')}]\n    {' '.join(h['text'].split())[:400]}" for h in hits)


def t_calculate(a, c):
    import calc
    expr = str(a.get("expression") or a.get("expr") or a.get("query") or "").strip()
    try:
        # 'digits' is significant digits, but models pass 2 meaning "cents" and got 16881 for 16,881.03: never
        # fewer than 15 (rounding for display is the model's job, done from the full value)
        res = calc.calculate(expr, max(15, int(a.get("digits") or 15)))
    except calc.CalcError as e:
        raise ToolError(f"{e}. Examples: '2^64', 'x^2-5x+6=0', 'diff(sin(x)*x^2, x)', '65 mph to m/s', "
                        f"'days between 2026-03-03 and 2026-06-09'")
    c.display = {"type": "math", "expression": expr, **res}
    return calc.render(res) + _mean_note(expr) + _scope_note(expr, c)


def _scope_note(expr, c):
    """An average whose terms are evidence-table figures: list what each one measures (its scope), so figures of
    different kinds (base vs total, median vs mean, one sector vs national) are not blended without noticing."""
    try:
        import evidence
        rows = [r for r in evidence.read_rows(evidence.table_path(c)) if r.get("verified") == "yes" and r.get("num")]
    except Exception:
        return ""
    out = []
    for m in _MEAN_RX.finditer(expr.replace(",", "")):
        lines = []
        for t in [t.strip() for t in m.group(1).split("+") if t.strip()]:
            hit = next((r for r in rows if abs(float(r["num"]) - float(t)) < 0.5), None) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None
            if hit:
                lines.append(f"{t} = {hit['id']}: {hit['metric']}, {hit['scope']}")
        if len(lines) >= 2 and len({ln.split(": ", 1)[1] for ln in lines}) > 1:
            out.append("What each term measures (evidence table):\n" + "\n".join("- " + ln for ln in lines)
                       + "\nAverage only figures that measure the same thing; report different kinds separately.")
    return ("\n" + "\n".join(out)) if out else ""


_MEAN_RX = re.compile(r"\(\s*([-\d.,\s+]+?)\s*\)\s*/\s*(\d+)")


def _mean_note(expr):
    """An average written as (a + b + …) / n: say when a term repeats (the same figure counted twice is the usual
    slip when it was copied from two places) or when n is not the number of terms."""
    notes = []
    for m in _MEAN_RX.finditer(expr.replace(",", "")):
        terms = [t.strip() for t in m.group(1).split("+") if t.strip()]
        if len(terms) < 2:
            continue
        rep = sorted({t for t in terms if terms.count(t) > 1})
        if rep:
            notes.append(f"{', '.join(rep)} appears more than once in this average — if that is one figure from one "
                         f"source, it is counted twice; if two sources really report it, keep it.")
        if int(m.group(2)) != len(terms):
            notes.append(f"divided by {m.group(2)} but there are {len(terms)} terms.")
    return ("\nCheck: " + " ".join(notes)) if notes else ""


# ----------------------------------------------------------------------------- debugging toolkit
# Built from an analysis of a 9B's failed builds: it was rarely blocked by not knowing how to code, but by not SEEING —
# which process answered a request, what values its code computed, what changed since things last worked.
def _hist(c):
    """Per-conversation file history: original content, recent versions (undo), state hashes (edit loops) and the
    snapshot taken when tests last passed (changes_since_green)."""
    h = getattr(c, "_filehist", None)
    if h is None:
        h = {"orig": {}, "versions": {}, "states": {}, "green": None, "green_edits": 0}
        try:                                          # an app restart must not lose "what worked" (the baseline)
            saved = json.load(open(_filehist_path(c), encoding="utf-8"))
            for k in ("orig", "green", "green_edits", "green_t", "green_partial", "best_pass", "last_tests"):
                if k in saved:
                    h[k] = saved[k]
        except (OSError, ValueError, TypeError):
            pass
        try:
            c._filehist = h
        except Exception:
            pass
    return h


def _filehist_path(c):
    conv = getattr(c, "conv_id", None) or "misc"
    base = paths.data_dir()
    return os.path.join(base, "agent_attachments", _safe_name(conv), "filehist.json")


def _save_filehist(c):
    """Persist the baseline (snapshot at the last/best passing test run) and the originals for this chat."""
    h = _hist(c)
    try:
        p = _filehist_path(c)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({k: h.get(k) for k in ("orig", "green", "green_edits", "green_t", "green_partial", "best_pass",
                                             "last_tests")}, f)
    except (OSError, TypeError, ValueError):
        pass


def _code_path(p):
    return bool(re.search(r"\.(py|js|ts|tsx|jsx|mjs|cjs|html?|css|java|cs|go|rs|rb|php|ps1|sh|sql)$", str(p), re.I))


def _note_change(c, p, old, new, existed):
    """Record a write/edit -> a note for the model (an edit loop, or tests not re-run since they last passed)."""
    h, key = _hist(c), os.path.abspath(p)
    h["orig"].setdefault(key, old if existed else None)
    v = h["versions"].setdefault(key, [])
    v.append(old if existed else None)
    del v[:-20]
    states = h["states"].setdefault(key, [hashlib.md5((old if existed else "").encode("utf-8", "replace")).hexdigest()])
    now = hashlib.md5(new.encode("utf-8", "replace")).hexdigest()
    notes = []
    if now in states[:-1]:
        back = len(states) - states.index(now) - 1 if now in states else 0
        back = len(states) - 1 - max(i for i, s in enumerate(states[:-1]) if s == now)
        notes.append(f"NOTE: this puts {os.path.basename(p)} back exactly as it was {back} edit(s) ago — the last "
                     f"{back} change(s) cancelled out. Don't toggle the same change: find the real cause "
                     f"(run it and read the actual error; changes_since_green shows what really changed).")
    states.append(now)
    del states[:-30]
    if h["green"] is not None and _code_path(p):
        h["green_edits"] += 1
        if h["green_edits"] in (1, 4, 8):
            notes.append(f"Tests last passed {h['green_edits']} code edit(s) ago — re-run them now (run_tests) so a "
                         f"break shows up right where it happened; changes_since_green lists every change since.")
    return ("\n" + "\n".join(notes)) if notes else ""


def mark_green(c):
    """Tests just passed: snapshot every file this chat touched (the baseline for changes_since_green)."""
    h = _hist(c)
    files = set(h["orig"]) | {os.path.abspath(k) for k in (getattr(c, "changed", {}) or {})}
    snap = {}
    for p in files:
        if os.path.isfile(p) and os.path.getsize(p) < 2_000_000:
            try:
                snap[p] = _read_text(p)
            except Exception:
                pass
    h["green"], h["green_edits"], h["green_t"] = snap, 0, time.strftime("%H:%M:%S")
    h["green_partial"] = ""
    _save_filehist(c)


_PASS_RX = re.compile(r"\b(\d+ passed|ALL TESTS PASSED|^OK\b|tests: PASSED)", re.M | re.I)
_FAILISH_RX = re.compile(r"\b(\d+ failed|FAILED|FAIL\b|Traceback|AssertionError|\d+ errors?)\b")


def test_counts(text):
    """(passed, failed) from a test run's output (pytest, unittest, or PASS/FAIL lines) — (0, 0) when none."""
    t = str(text or "")
    p = max([int(x) for x in re.findall(r"\b(\d+) passed", t)] or [0])     # the same summary can appear twice
    f = max([int(x) for x in re.findall(r"\b(\d+) (?:failed|errors?)\b", t)] or [0])
    m = re.search(r"Ran (\d+) tests?", t)
    if m and not p:                                   # unittest: "Ran 7 tests ... OK" / "FAILED (failures=2)"
        bad = sum(int(x) for x in re.findall(r"(?:failures|errors)=(\d+)", t))
        p, f = int(m.group(1)) - bad, bad
    if not p and not f:
        p = len(re.findall(r"^\s*(?:PASS(?:ED)?\b|✓|\[PASS\]|ok\b)", t, re.M | re.I))
        f = len(re.findall(r"^\s*(?:FAIL(?:ED)?\b|✗|\[FAIL\]|ERROR\b)", t, re.M | re.I))
    return p, f


def mark_progress(c, name, args, text):
    """A test run with MORE passing tests than ever in this chat (e.g. 5 of 7) becomes the baseline for
    changes_since_green too — a suite that never fully passes would otherwise leave nothing to compare against."""
    cmd = json.dumps(args or {}).lower()
    if name not in ("run_tests", "powershell", "python_host") or (name != "run_tests" and not re.search(r"test|pytest|unittest", cmd)):
        return
    p, f = test_counts(text)
    h = _hist(c)
    if p and p > h.get("best_pass", 0):
        h["best_pass"] = p
        if f:                                          # all-passing runs are handled by mark_green
            mark_green(c)
            h["green_partial"] = f"{p} passed, {f} failed"
            _save_filehist(c)


def looks_green(name, args, text):
    if name == "run_tests":
        return text.startswith("tests: PASSED")
    if name in ("powershell", "python_host"):
        cmd = json.dumps(args or {}).lower()
        return (bool(re.search(r"test|pytest|unittest", cmd)) and "exit code 0" in text[:200]
                and bool(_PASS_RX.search(text)) and not _FAILISH_RX.search(text))
    return False


def t_changes_since_green(a, c):
    """Every change since tests last passed (or since this chat first touched the files)."""
    h = _hist(c)
    base = h["green"]
    label = (f"since tests last {'did best (' + h['green_partial'] + ')' if h.get('green_partial') else 'passed'} "
             f"(at {h.get('green_t')}, {h['green_edits']} code edit(s) ago)" if base is not None
             else "since this chat first changed the files (no passing test run yet)")
    if base is None:
        base = {k: v for k, v in h["orig"].items() if v is not None}
    only = c.resolve(a["path"]) if a.get("path") else None
    files = sorted(set(base) | set(h["orig"]))
    out, total = [], 0
    for p in files:
        if only and os.path.abspath(p) != os.path.abspath(only):
            continue
        before = base.get(p, h["orig"].get(p) or "")
        now = _read_text(p) if os.path.isfile(p) else ""
        if before.replace("\r\n", "\n") == now.replace("\r\n", "\n"):
            continue
        diff, add, rem = make_diff(before, now, p, context=2, max_lines=160)
        total += 1
        out.append(f"=== {p}  (+{add} -{rem})\n{diff}")
    if not out:
        return f"No changes {label}."
    return f"{total} file(s) changed {label}:\n\n" + "\n\n".join(out)


def t_undo_file(a, c):
    """Put a file back as it was before your last change(s) to it."""
    p = c.resolve(a.get("path"), True) if os.path.exists(c.resolve(a.get("path"))) else c.resolve(a.get("path"))
    h, key = _hist(c), os.path.abspath(p)
    v = h["versions"].get(key) or []
    n = max(1, min(int(a.get("steps") or 1), len(v) or 1))
    if not v:
        raise ToolError(f"no earlier version of {os.path.basename(p)} from this chat to go back to")
    target = v[-n]
    cur = _read_text(p) if os.path.isfile(p) else ""
    del v[-n:]
    if target is None:                                  # the file did not exist before: move it to the trash
        os.makedirs(TRASH, exist_ok=True)
        shutil.move(p, os.path.join(TRASH, time.strftime("%Y%m%d_%H%M%S_undone_") + os.path.basename(p)))
        return f"undid {n} change(s): {p} did not exist before, so it was moved to agent_trash"
    _backup(p)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(target)
    diff, add, rem = make_diff(cur, target, p)
    c.display = {"type": "diff", "path": p, "diff": diff, "added": add, "removed": rem, "backup": None, "created": False}
    h["states"].setdefault(key, []).append(hashlib.md5(target.encode("utf-8", "replace")).hexdigest())
    return f"undid {n} change(s) to {p} (+{add} -{rem}); it is back as it was before them"


def _app_pids():
    """Processes of this app: its backend/window and its model servers (never the user's project)."""
    out = {os.getpid(), os.getppid()}
    try:
        import glob
        import llama_local
        for pf in glob.glob(os.path.join(llama_local._state_dir(), "swarm_server*.pid")):
            try:
                out.add(int(open(pf).read().strip()))
            except (OSError, ValueError):
                pass
    except Exception:
        pass
    try:
        import psutil
        for q in psutil.process_iter(["pid", "name", "cmdline"]):
            cl = " ".join(q.info.get("cmdline") or []).lower()
            if "llama-server" in (q.info.get("name") or "").lower() or "app_web.py" in cl or "web_backend" in cl:
                out.add(q.info["pid"])
    except Exception:
        pass
    return out


def port_owners(port):
    """-> [{pid, name, cmd, cwd, started, whose}] for every process listening on port (whose: app / yours bgN / other)."""
    try:
        import psutil
        import bg_tasks
    except Exception:
        return []
    mine = {}
    for i in bg_tasks.MANAGER.list():
        try:
            p = bg_tasks.MANAGER.get(i["id"])
            live = []
            if p.running and p.proc:                  # its tree right now (the sampled tree may lag a new child)
                try:
                    live = [q.pid for q in psutil.Process(p.proc.pid).children(recursive=True)]
                except Exception:
                    pass
            for pid in list(p.tree) + live + ([p.proc.pid] if p.proc else []):
                mine[pid] = f"yours ({i['id']}, '{i['name']}'{', still running' if i['running'] else ', stopped'})"
        except Exception:
            pass
    app = _app_pids()
    rows = []
    for cn in psutil.net_connections(kind="inet"):
        if cn.status == psutil.CONN_LISTEN and cn.laddr and cn.laddr.port == int(port) and cn.pid:
            try:
                q = psutil.Process(cn.pid)
                rows.append({"pid": cn.pid, "name": q.name(), "addr": f"{cn.laddr.ip}:{cn.laddr.port}",
                             "cmd": " ".join(q.cmdline())[:200], "cwd": (lambda: q.cwd())() if True else "",
                             "started": time.strftime("%H:%M:%S", time.localtime(q.create_time())),
                             "whose": "THIS APP (never stop it)" if cn.pid in app else mine.get(cn.pid, "another program")})
            except Exception:
                rows.append({"pid": cn.pid, "name": "?", "addr": f"{cn.laddr.ip}:{cn.laddr.port}", "cmd": "",
                             "cwd": "", "started": "?", "whose": "another program"})
    return rows


def t_stack_dump(a, c):
    """Where a running Python process of yours is stuck right now: every thread's current line, your code marked."""
    import bg_tasks
    import stackdump
    pid = None
    if a.get("id"):
        try:
            p = bg_tasks.MANAGER.get(a["id"])
        except KeyError as e:
            raise ToolError(str(e))
        if not p.running:
            raise ToolError(f"{p.id} is not running (exit code {p.exit}); read bg_output for what it printed")
        pid = p.proc.pid
    elif a.get("pid"):
        pid = int(a["pid"])
    else:
        raise ToolError("give id (a background process, e.g. bg2) or pid")
    return stackdump.dump(pid, project_hint=getattr(c, "cwd", "") or "") or "no Python process found in that process tree"


def t_port_info(a, c):
    """Who is listening on a port: every process, with its command line, folder, start time and whose it is."""
    port = int(a.get("port") or 0)
    if not port:
        raise ToolError("give the port number")
    rows = port_owners(port)
    if not rows:
        return f"Nothing is listening on port {port}."
    lines = [f"{len(rows)} process(es) listening on port {port}:"]
    for r in rows:
        lines.append(f"- pid {r['pid']} {r['name']} on {r['addr']}, started {r['started']} — {r['whose']}\n"
                     f"  command: {r['cmd']}\n  folder: {r['cwd']}")
    if len(rows) > 1:
        lines.append("MORE THAN ONE process holds this port (Windows allows it): requests may reach any of them — "
                     "usually an OLD instance with old code. Stop the ones that are yours and not current (bg_stop).")
    return "\n".join(lines)


TRACE_SCRIPT = r"""
import importlib.util, os, reprlib, sys
path, expr, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
folder = os.path.dirname(os.path.abspath(path))
sys.path.insert(0, folder)
os.chdir(folder)
spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
r = reprlib.Repr(); r.maxstring = 80; r.maxother = 80; r.maxlist = 6; r.maxdict = 6; r.maxlevel = 2
n = [0]
src = {}
prev = {}
def code_line(fn, ln):
    if fn not in src:
        try: src[fn] = open(fn, encoding="utf-8", errors="replace").read().splitlines()
        except Exception: src[fn] = []
    return src[fn][ln - 1].strip() if 0 < ln <= len(src[fn]) else ""
def tracer(frame, event, arg):
    if frame.f_code.co_filename.startswith("<"):
        return tracer if event == "call" else None
    fn = os.path.abspath(frame.f_code.co_filename)
    if not fn.startswith(folder) or "site-packages" in fn:
        return None
    if n[0] >= limit:
        return None
    key = id(frame)
    loc = {k: v for k, v in frame.f_locals.items() if not k.startswith("__")}
    if event == "call":
        args = ", ".join(f"{k}={r.repr(v)}" for k, v in loc.items())
        print(f"-> {frame.f_code.co_name}({args})  [{os.path.basename(fn)}:{frame.f_lineno}]", flush=True)
        prev[key] = {}
        n[0] += 1
    elif event == "line":
        old = prev.get(key, {})
        changed = {k: v for k, v in loc.items() if k not in old or r.repr(old[k]) != r.repr(v)}
        ch = "   | " + ", ".join(f"{k}={r.repr(v)}" for k, v in changed.items()) if changed else ""
        print(f"   {os.path.basename(fn)}:{frame.f_lineno}  {code_line(fn, frame.f_lineno)}{ch}", flush=True)
        prev[key] = dict(loc)
        n[0] += 1
    elif event == "return":
        print(f"<- {frame.f_code.co_name} returned {r.repr(arg)}", flush=True)
    elif event == "exception":
        print(f"!! {frame.f_code.co_name} raised {arg[0].__name__}: {r.repr(arg[1])}", flush=True)
    return tracer
g = dict(vars(mod))
sys.settrace(tracer)
try:
    result = eval(expr, g)
finally:
    sys.settrace(None)
print(f"== {expr} -> {r.repr(result)}" + (f"   (trace stopped after {limit} lines)" if n[0] >= limit else ""))
"""


def t_trace_call(a, c):
    """Run ONE call in a Python file and print every line it executes in your project's code, with the variables
    that changed on that line, each function's arguments and its return value."""
    path = c.resolve(a.get("file"), True)
    expr = str(a.get("call") or "").strip()
    if not expr:
        raise ToolError("give call, e.g. call=\"register_user('alice', 'pw')\" (an expression using the file's names)")
    import tempfile as _tf
    fd, script = _tf.mkstemp(suffix=".py", prefix="trace_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(TRACE_SCRIPT)
    try:
        r = subprocess.run([*_python_exe(), script, path, expr, str(int(a.get("max_lines") or 300))],
                           capture_output=True, text=True, timeout=int(a.get("timeout") or 60), encoding="utf-8",
                           errors="replace", creationflags=0x08000000,
                           env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
        out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + "\n(stopped by the timeout — the call did not return)"
    finally:
        try:
            os.remove(script)
        except OSError:
            pass
    return out or "(no output — the call ran no lines in this folder's code)"


def _python_exe():
    import paths
    return paths.python_cmd()          # a command prefix (list): system Python or the bundled one


# ----------------------------------------------------------------------------- background processes
def listening(pid):
    """Ports the process AND its children listen on -> ['0.0.0.0:8766 (python.exe)']. A background command runs
    inside PowerShell, so the server is a child: asking Windows about the reported pid alone finds nothing."""
    try:
        import psutil
        root = psutil.Process(int(pid))
        out = []
        for p in [root] + root.children(recursive=True):
            try:
                for cn in p.net_connections(kind="inet"):
                    if cn.status == psutil.CONN_LISTEN and cn.laddr:
                        out.append(f"{cn.laddr.ip}:{cn.laddr.port} ({p.name()})")
            except (psutil.Error, OSError):
                pass
        return sorted(set(out))
    except Exception:
        return []


def app_port_grab(pid):
    """An app port (8765, 8099…) that the process tree listens on -> the port, else None. On Windows Python's
    http.server binds a port that is already in use, so an agent's test server on 8765 silently shared the app's
    port, outlived the app and blocked its restart."""
    for entry in listening(pid):
        m = re.search(r":(\d+) ", entry + " ")
        if m and int(m.group(1)) in APP_PORTS:
            return int(m.group(1))
    return None


APP_PORT_MSG = ("it started listening on port {port}, which belongs to this app (its own server or model server), "
                "so it was stopped. Use another port for your project — e.g. 8790 or higher — and make the port "
                "configurable (an env var or --port).")


def _listen_note(p):
    if not p.running:
        return ""
    ports = listening(p.proc.pid)
    return ("listening on " + ", ".join(ports)) if ports else "running, not listening on any port (yet)"


def t_bg_start(a, c):
    import bg_tasks
    cmd = str(a.get("command", "")).strip()
    if not cmd:
        raise ToolError("no command given")
    if blocked_reason(cmd):
        raise ToolError("that command is blocked by the safety policy (it can damage the system)")
    cwd = c.resolve(a.get("cwd"), True) if a.get("cwd") else shell_cwd(c)
    want = int(a.get("wait_port") or 0)

    def holders(port):
        """Your other background processes already listening on this port. On Windows a second server can bind a
        port that is in use (Python's http.server sets allow_reuse_address), and requests keep reaching the OLD one."""
        out = []
        for i in bg_tasks.MANAGER.list():
            if i["running"] and i.get("pid") and any(x.rsplit(":", 1)[-1].split(" ")[0] == str(port)
                                                     for x in listening(i["pid"])):
                out.append(f"{i['id']} ('{i['name']}')")
        return out
    if want:
        foreign = [r for r in port_owners(want) if not r["whose"].startswith("yours")]
        if foreign:
            r = foreign[0]
            raise ToolError(f"port {want} is already taken by {r['whose']}: pid {r['pid']} {r['name']} ({r['cmd'][:120]}). "
                            f"Pick another free port for your server" + ("" if "APP" in r["whose"] else
                            " (or stop that program if it is a leftover of yours)") + ".")
    if want and holders(want):
        raise ToolError(f"port {want} is already served by your background process {', '.join(holders(want))} — "
                        f"requests would keep reaching that old instance (on Windows a second server can bind the same "
                        f"port). bg_stop it first, then start again.")
    try:
        p = bg_tasks.MANAGER.start(cmd, cwd, a.get("name"), owner=c.conv_id, keep=bool(a.get("keep")))
    except RuntimeError as e:
        raise ToolError(str(e))
    t0 = time.time()
    limit = float(a.get("wait") or (20 if want else 3))
    while time.time() - t0 < limit and p.running:      # catch startup errors (and wait for the port, if asked)
        if want and any(x.rsplit(":", 1)[-1].split(" ")[0] == str(want) for x in listening(p.proc.pid)):
            break
        time.sleep(0.25)
    c.display = {"type": "bg", "id": p.id, "name": p.name, "command": cmd, "cwd": cwd}
    state = (_listen_note(p) if p.running else f"already exited with code {p.exit}")
    if want and p.running and not any(x.rsplit(":", 1)[-1].split(" ")[0] == str(want) for x in listening(p.proc.pid)):
        state += f" — NOT listening on {want} after {limit:.0f}s: read its output below (an error or a different port)"
    if p.running:                                  # the same port served twice: the old instance answers requests
        for port in {x.rsplit(":", 1)[-1].split(" ")[0] for x in listening(p.proc.pid)}:
            others = [h for h in holders(port) if not h.startswith(p.id + " ")]
            if others:
                state += (f" — WARNING: port {port} is also served by your older process {', '.join(others)}; requests "
                          f"may reach the old code. bg_stop it.")
    return (f"started background process {p.id} ('{p.name}'): {state}. "
            f"Use bg_output('{p.id}') to read its output, http_request to call it, bg_stop('{p.id}') to stop it.\n"
            f"First output:\n" + ("\n".join(p.tail(30)) or "(none yet)"))


_LOCAL_HOST_RX = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
                            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$", re.I)


def t_http_request(a, c):
    """One HTTP request to a server on this PC or the local network (for testing what you built)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    url = str(a.get("url") or "").strip()
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("give a full url, e.g. http://127.0.0.1:8766/expenses")
    if not _LOCAL_HOST_RX.match(u.hostname):
        raise ToolError("http_request is for servers on this PC or the local network (localhost, 127.x, 192.168.x, "
                        "10.x); use web_fetch for websites")
    if u.port in APP_PORTS:
        raise ToolError(f"port {u.port} belongs to this app, not to your project")
    method = str(a.get("method") or ("POST" if a.get("body") is not None else "GET")).upper()
    body = a.get("body")
    headers = dict(a.get("headers") or {})
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    import http.cookiejar
    if a.get("fresh_session") or getattr(c, "_cookie_jar", None) is None:   # cookies persist across calls (logins)
        try:
            c._cookie_jar = http.cookiejar.CookieJar()
        except Exception:
            pass
    jar = getattr(c, "_cookie_jar", None)
    if jar is None:                               # (an EMPTY CookieJar is falsy: `jar or CookieJar()` reset it each call)
        jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    t0 = time.time()
    try:
        with opener.open(req, timeout=float(a.get("timeout") or 10)) as r:
            status, hdrs, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ToolError(f"no response from {u.hostname}:{u.port or 80} — {reason}. Is the server running and listening "
                        f"on that port? (bg_list shows what your background processes listen on)")
    text = raw.decode("utf-8", "replace")
    ctype = hdrs.get("Content-Type") or hdrs.get("content-type") or ""
    keep = {k: v for k, v in hdrs.items() if k.lower() in ("location", "set-cookie", "www-authenticate", "content-length",
                                                           "server", "allow")}
    cookies = ", ".join(f"{ck.name}={ck.value[:24]}" for ck in jar) if jar is not None else ""
    return (f"HTTP {status} in {int((time.time() - t0) * 1000)} ms  ({ctype or 'no content-type'}, {len(raw)} bytes)\n"
            + ("".join(f"{k}: {v}\n" for k, v in keep.items()))
            + (f"(cookies kept for the next request: {cookies})\n" if cookies else "")
            + (text[:8000] + ("\n… (truncated)" if len(text) > 8000 else "") if text else "(empty body)"))


def t_show_user(a, c):
    """Present something to the user: launch a program, game or app with NO timeout (it runs until the user closes
    it), open a page in the Browser panel, or open a file in its default app. For showing, never for testing."""
    import bg_tasks
    target = str(a.get("path") or a.get("target") or "").strip()
    cmd = str(a.get("command") or "").strip()
    if not target and not cmd:
        raise ToolError("give 'path' (a program, script, page or file to show) or 'command' (how to launch it)")
    if target and re.match(r"^https?://", target, re.I):
        st = _bwrap(lambda: _bm().goto(target), target)
        _bdisplay(c, "open", target, st)
        return f"Opened {target} in the Browser panel for the user."
    if target and not cmd:
        p = c.resolve(target, True)
        ext = os.path.splitext(p)[1].lower()
        folder = os.path.dirname(p)
        if ext in (".html", ".htm", ".svg"):
            url = "file:///" + p.replace("\\", "/")
            st = _bwrap(lambda: _bm().goto(url), url)
            _bdisplay(c, "open", url, st)
            return f"Opened {p} in the Browser panel for the user."
        if ext == ".py":
            cmd = f'python "{p}"'
        elif ext in (".bat", ".cmd"):
            cmd = f'cmd /c "{p}"'
        elif ext in (".exe", ".ps1"):
            cmd = f'& "{p}"'
        else:                                         # documents, images, media: the user's default app
            os.startfile(p)
            return f"Opened {p} in its default app on the user's screen."
        cwd = folder
    else:
        if blocked_reason(cmd):
            raise ToolError("that command is blocked by the safety policy (it can damage the system)")
        cwd = c.resolve(a.get("cwd"), True) if a.get("cwd") else shell_cwd(c)
    name = "shown: " + (os.path.basename(target) if target else cmd[:40])
    try:
        proc = bg_tasks.MANAGER.start(cmd, cwd, name, owner=c.conv_id, keep=True)   # the user's to close
    except RuntimeError as e:
        raise ToolError(str(e))
    t0 = time.time()
    while time.time() - t0 < 4 and proc.running:          # catch a crash at startup, then let it run
        time.sleep(0.2)
    c.display = {"type": "bg", "id": proc.id, "name": proc.name, "command": cmd, "cwd": cwd}
    first = "\n".join(proc.tail(20)) or "(no output yet)"
    if not proc.running:
        return (f"It exited right away (code {proc.exit}), so the user is not seeing it. Output:\n{first}\n"
                f"Fix the problem, then show it again.")
    return (f"Launched for the user ({proc.id}, pid {proc.proc.pid}); it keeps running with no timeout until the user "
            f"closes it or presses Stop in the Tasks panel. It is on the user's screen — you cannot see it, so don't "
            f"wait on it or test with it: tell the user it's open and how to use it. bg_output('{proc.id}') shows its "
            f"console output later.\nFirst output:\n{first}")


def t_bg_output(a, c):
    import bg_tasks
    try:
        p = bg_tasks.MANAGER.get(a.get("id"))
    except KeyError as e:
        raise ToolError(str(e))
    n = max(1, min(int(a.get("lines") or 60), 400))
    state = _listen_note(p) if p.running else f"exited with code {p.exit}"
    lines = list(p.lines)
    mark = getattr(p, "_read_mark", 0)
    total = p.total_lines
    if a.get("new_only"):                         # only what arrived since you last read this process's output
        fresh = total - mark
        lines = lines[-fresh:] if 0 < fresh <= len(lines) else ([] if fresh <= 0 else lines)
    p._read_mark = total
    if a.get("grep"):
        try:
            rx = re.compile(str(a["grep"]), re.I)
        except re.error as e:
            raise ToolError(f"bad grep pattern: {e}")
        lines = [ln for ln in lines if rx.search(ln)]
    shown = lines[-n:]
    what = ("new lines" if a.get("new_only") else "lines") + (f" matching /{a['grep']}/" if a.get("grep") else "")
    return (f"{p.id} '{p.name}': {state}, {total} lines of output so far. Last {len(shown)} {what}:\n"
            + ("\n".join(shown) or "(none)"))


def t_bg_stop(a, c):
    import bg_tasks
    try:
        p = bg_tasks.MANAGER.stop(a.get("id"))
    except KeyError as e:
        raise ToolError(str(e))
    return f"stopped {p.id} '{p.name}'"


def t_bg_list(a, c):
    import bg_tasks
    rows = []
    for i in bg_tasks.MANAGER.list():
        state = "exit " + str(i["exit"])
        if i["running"]:
            ports = listening(i["pid"]) if i.get("pid") else []
            state = "running, " + (("listening on " + ", ".join(ports)) if ports else "not listening")
        rows.append(f"{i['id']}  {state}  {i['name']}  (cwd {i['cwd']})")
    return "\n".join(rows) or "(no background processes)"


# ----------------------------------------------------------------------------- delegation
def _host(c):
    if c.is_subagent:
        raise ToolError("sub-agents cannot delegate further; do the work yourself")
    if c.host is None:
        raise ToolError("delegation is not available here")
    return c.host


def _task_text(info):
    head = f"task {info['id']} '{info['title']}': {info['status']}"
    if info.get("needs_approval"):
        head += " (waiting for the user to approve an action in that thread)"
    body = ""
    if info.get("tracker"):
        body += "\nIts tracker:\n" + tracker_prompt(info["tracker"])
    if info.get("result"):
        body += "\nResult:\n" + info["result"]
    return head + body


def t_delegate_task(a, c):
    task = str(a.get("task") or "").strip()
    if not task:
        raise ToolError("describe the task to delegate")
    title = str(a.get("title") or task.split("\n")[0])[:60]
    info = _host(c).delegate(task=task, title=title, context=str(a.get("context") or ""),
                             parent=c.conv_id)
    c.display = {"type": "delegate", "task_id": info["id"], "title": title, "task": task,
                 "conv_id": info.get("conv_id")}
    return (f"started task {info['id']} in a new thread '{title}'. It runs in the background with its "
            f"own context. Keep working; call wait_task('{info['id']}') when you need its result, or "
            f"task_status to check on it.")


_PASSIVE_MINE = re.compile(r"\b(monitor|wait|await|watch|review|merge|compile|synthes|cross[- ]?reference|combine|"
                           r"consolidat|check (on |in on )?(their|the workers'?|the gather)|their (results|findings|"
                           r"reports))", re.I)
_ACTIVE_MINE = re.compile(r"\b(search|fetch|read|open|browse|find|look up|lookup|query|calculate|compute|verify|"
                          r"check (the |a |local|files)|list|scan)\b", re.I)


def t_gather(a, c):
    """Fan-out: hand 1-4 independent research briefs to the gopher workers (run in parallel, results come back)."""
    if getattr(c, "gopher", None) is None:
        raise ToolError("fan-out workers are not available (Agent settings: fan-out)")
    raw = a.get("tasks") or a.get("briefs") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    briefs = []
    for t in raw:
        if isinstance(t, dict):
            if t.get("objective"):                 # structured brief: an objective the worker owns, not a procedure
                parts = [("OBJECTIVE", t.get("objective")), ("WHY IT MATTERS / CONTEXT", t.get("context")),
                         ("DONE WHEN", t.get("done_when")),
                         ("STARTING POINTS (suggestions only — use your judgment)", t.get("hints")),
                         ("NOT YOURS (other workers cover this)", t.get("avoid"))]
                b = "\n".join(f"{k}: {str(v).strip()}" for k, v in parts if v and str(v).strip())
            else:
                b = str(t.get("brief") or t.get("task") or t.get("description") or "").strip()
            title = str(t.get("title") or b.split("\n")[0])[:60]
        else:
            b, title = str(t).strip(), str(t).strip().split("\n")[0][:60]
        if b:
            import gopher
            mc = gopher.clamp_calls(t.get("max_calls") if isinstance(t, dict) else None)   # 3-6, the main agent's pick
            briefs.append({"title": title, "brief": b + f"\nCALL BUDGET: at most {mc} tool calls.", "max_calls": mc})
    if not briefs:
        raise ToolError("give tasks=[{title, brief}, ...] — 1 to 4 independent research briefs")
    mine = str(a.get("mine") or "").strip()
    if not mine or _PASSIVE_MINE.search(mine) and not _ACTIVE_MINE.search(mine):
        raise ToolError("'mine' must be real work you do with your own tools while the workers run — e.g. search "
                        "or read a source they are not covering, check local files, or verify a key fact. "
                        "Monitoring, waiting for or reviewing their results is not a part.")
    text, _ = c.gopher(briefs, mine)
    c.display = {"type": "gather", "running": True, "mine": str(a.get("mine") or "")[:300],
                 "workers": [{"title": b["title"], "steps": 0, "seconds": 0, "error": "", "report": ""} for b in briefs]}
    return text


def t_record(a, c):
    """Add facts to the chat's evidence table (CSV), each checked against what was read in this chat."""
    import evidence
    rows = a.get("rows") or a.get("facts") or []
    if isinstance(rows, dict):
        rows = [rows]
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        raise ToolError("give rows=[{entity, metric, value, unit, scope, period, source_url, quote}, …]")
    checker = getattr(c, "evidence_check", None)
    verdicts = checker(rows) if checker else ["unchecked"] * len(rows)
    for r, v in zip(rows, verdicts):
        r["verified"] = v
    path, out = evidence.append(c, rows, by="lead")
    dups = [r for r in out if r.get("duplicate_of")]
    new = [r for r in out if not r.get("duplicate_of")]
    total = len(evidence.read_rows(path))
    bad = [(r["id"], r["verified"]) for r in new if r["verified"] != "yes"]
    text = (f"Recorded {len(new)} rows in the evidence table ({total} rows in all): {path}\n"
            f"{len(new) - len(bad)} verified against what you read.")
    if dups:
        text += ("\nNot added — already in the table (same source, same value), so it is not counted twice: "
                 + "; ".join(f"{r['value']} from {r['source_url'][:60]} = {r['duplicate_of']}" for r in dups[:8]))
    if bad:
        text += "\nNOT verified — fix (record a corrected row) or don't use them:\n" + "\n".join(
            f"- {i}: {why}" for i, why in bad)
    text += (f"\nCompute counts, averages, medians and ranges from the table, not in your head: query_data with "
             f"files={{\"evidence\": \"{path}\"}}, e.g. SELECT scope, count(*), median(num) FROM evidence WHERE "
             f"verified = 'yes' GROUP BY scope. Keep different scopes (base vs total, median vs average) apart.")
    return text


def t_run_tests(a, c):
    import debug_tools as dbg
    return dbg.run_tests(a, c, lambda p, must=False: c.resolve(p, must), ToolError)


def t_debug_run(a, c):
    import debug_tools as dbg
    return dbg.debug_run(a, c, lambda p, must=False: c.resolve(p, must), ToolError)


def t_browser_eval(a, c):
    expr = str(a.get("expression") or a.get("js") or "").strip()
    if not expr:
        raise ToolError("give 'expression': JavaScript to evaluate in the current page")
    st = _bwrap(lambda: _bm().state())
    url = str(st.get("url") or "")
    if not re.match(r"^(file:|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$))", url, re.I):
        raise ToolError("browser_eval only runs on your own local pages (file:// or localhost), not on websites; "
                        f"the current page is {url or 'empty'}")
    return _bwrap(lambda: _bm().evaluate(expr))


_LOCAL_PAGE = re.compile(r"^(file:|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$))", re.I)
DEVTOOLS_LOCAL_ONLY = {"request", "storage", "breakpoint", "exceptions"}   # secrets / running code: own pages only


def t_browser_devtools(a, c):
    action = str(a.get("action") or "").strip().lower()
    acts = ("network", "request", "console", "element", "listeners", "storage", "performance", "breakpoint",
            "exceptions")
    if action not in acts:
        raise ToolError("action must be one of: " + ", ".join(acts))
    if action in DEVTOOLS_LOCAL_ONLY:
        url = str(_bwrap(lambda: _bm().state()).get("url") or "")
        if not _LOCAL_PAGE.match(url):
            raise ToolError(f"action '{action}' only runs on your own local pages (file:// or localhost), not on "
                            f"websites; the current page is {url or 'empty'}")
    kw = {}
    if action == "network":
        kw = {"filter": a.get("filter"), "failed": bool(a.get("failed_only")), "limit": a.get("limit") or 40}
    elif action == "request":
        if a.get("id") in (None, ""):
            raise ToolError("give 'id': the request number from action='network'")
        kw = {"rid": a.get("id")}
    elif action == "console":
        kw = {"level": a.get("level"), "filter": a.get("filter"), "limit": a.get("limit") or 60}
    elif action == "element":
        if not a.get("selector"):
            raise ToolError("give 'selector': a CSS selector, e.g. '#startBtn' or '.menu button'")
        kw = {"selector": str(a["selector"]), "styles": a.get("styles")}
    elif action == "listeners":
        kw = {"target": str(a.get("selector") or "window")}
    elif action == "performance":
        kw = {"seconds": a.get("seconds") or 2}
    elif action == "breakpoint":
        if not a.get("location"):
            raise ToolError("give 'location': 'file.js:LINE'")
        kw = {"location": a["location"], "condition": a.get("condition"), "watch": a.get("watch"),
              "trigger": a.get("trigger"), "timeout": min(60, float(a.get("timeout") or 15))}
    elif action == "exceptions":
        kw = {"trigger": a.get("trigger"), "timeout": min(60, float(a.get("timeout") or 15)),
              "caught": bool(a.get("caught")), "watch": a.get("watch")}
    return clip(_bwrap(lambda: _bm().devtools(action, **kw)), 12000)


def t_ask_user(a, c):
    """Clarifying questions before broad work: the engine shows them and ends the turn to wait for the answers."""
    qs = a.get("questions")
    if isinstance(qs, str):
        qs = [ln.strip("-*• ").strip() for ln in qs.splitlines() if ln.strip()]
    rows = []
    for q in qs if isinstance(qs, list) else []:
        if isinstance(q, dict):
            text, dflt = str(q.get("question") or "").strip(), str(q.get("default") or "").strip()
        else:
            text, dflt = str(q).strip(), ""
        if text:
            rows.append(f"{len(rows) + 1}. {text}" + (f" *(default: {dflt})*" if dflt else ""))
    if not getattr(c, "may_ask", True):
        raise ToolError("the user has already answered your questions (or this is a sub-task): go ahead with their "
                        "answers and your stated defaults")
    if not rows:
        raise ToolError("give 'questions': a list of {question, default}")
    intro = str(a.get("intro") or "").strip() or "Before I start, a few questions so I head in the right direction:"
    c.asked = (intro + "\n\n" + "\n".join(rows[:6]) + "\n\nAnswer any or all, or say **go with the defaults**.")
    return "Questions shown to the user. The turn ends here; their answer comes as the next message."


def t_go_extensive(a, c):
    """Basic -> extensive path for this message (a plan, the checklist and parallel workers)."""
    if getattr(c, "go_extensive", None) is None:
        return "Not available here."
    return c.go_extensive(str(a.get("reason") or ""))


def t_wait_for_workers(a, c):
    """Block until the background fan-out workers finish (their results join the chat right after)."""
    if getattr(c, "gopher_wait", None) is None:
        raise ToolError("no fan-out workers are available")
    return c.gopher_wait()


def t_wait_task(a, c):
    timeout = max(5, min(int(a.get("timeout") or 300), 1800))
    info = _host(c).wait(str(a.get("id")), timeout, c.should_stop)
    if info is None:
        raise ToolError(f"no task '{a.get('id')}'")
    return _task_text(info)


def t_task_status(a, c):
    h = _host(c)
    if a.get("id"):
        info = h.task_info(str(a["id"]))
        if info is None:
            raise ToolError(f"no task '{a.get('id')}'")
        return _task_text(info)
    rows = [f"{i['id']}  {i['status']}  {i['title']}" for i in h.tasks_of(c.conv_id)]
    return "\n".join(rows) or "(no delegated tasks in this chat)"


# ----------------------------------------------------------------------------- advisor / screen
def t_consult_advisor(a, c):
    if c.advisor is None:
        raise ToolError("the advisor is not configured (Agent settings → advisor)")
    q = str(a.get("question") or "").strip()
    if not q:
        raise ToolError("ask a specific question")
    advice, meta = c.advisor(q)
    c.display = {"type": "advisor", "question": q, "advice": advice, **meta}
    return "Advisor says:\n" + advice


def t_enable_screen(a, c):
    if c.enable_screen is None:
        raise ToolError("screen control is not available")
    names = c.enable_screen()
    return "screen control is on for this chat. New tools: " + ", ".join(sorted(names))


# ----------------------------------------------------------------------------- in-app browser
def _bm():
    import browser_agent
    return browser_agent.MANAGER


def local_down_hint(url):
    """'' unless url is on this computer and nothing listens on its port -> a note on what that means."""
    m = re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::(\d+))?", str(url or ""), re.I)
    if not m:
        return ""
    port = int(m.group(2) or (443 if str(url).lower().startswith("https") else 80))
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return ""
    except OSError:
        pass
    return (f"\nNothing is running on port {port}: the server for {m.group(0)} is not up (it was never started, it "
            f"crashed, or it stopped — background processes end when the app restarts). bg_list shows what is running. "
            f"Start the server again (bg_start, e.g. the project's dev/serve command in its folder), check bg_output "
            f"until it says it is ready (and which port it uses), then open the page. Retrying the same URL will not "
            f"help until the server runs.")


def _bwrap(fn, url=None):
    import browser_agent
    try:
        return fn()
    except browser_agent.BrowserError as e:
        msg = str(e)
        if url and ("ERR_CONNECTION_REFUSED" in msg or "ERR_CONNECTION_RESET" in msg or "ERR_EMPTY_RESPONSE" in msg):
            msg += local_down_hint(url)
        raise ToolError(msg)


def _bdisplay(c, action, detail, st=None, shot=True):
    st = st or {}
    d = {"type": "browser", "action": action, "detail": str(detail)[:200], "url": st.get("url", ""),
         "title": st.get("title", "")}
    m = _bm()
    if shot and m.frame:
        try:
            d["shot"] = m.save_shot(m.frame, action)
        except OSError:
            pass
    c.display = d


def _page_view(snap, max_els=150):
    rows = []
    for e in snap["elements"][:max_els]:
        bits = [f"[{e['ref']}]", e["role"]]
        if e.get("name"):
            bits.append(f"\"{e['name']}\"")
        if e.get("placeholder") and e.get("placeholder") != e.get("name"):
            bits.append(f"placeholder=\"{e['placeholder']}\"")
        if e.get("value"):
            bits.append(f"value=\"{e['value']}\"")
        if e.get("checked"):
            bits.append("(checked)")
        if e.get("disabled"):
            bits.append("(disabled)")
        if e.get("href") and e["role"] == "link":
            bits.append("-> " + e["href"][:120])
        if not e.get("inView"):
            bits.append("(below)")
        rows.append(" ".join(bits))
    sc = snap.get("scroll") or {}
    where = f"scrolled {sc.get('y', 0)}px of {sc.get('h', 0)}px" if sc else ""
    con = snap.get("console")
    if con is not None:
        where += ("\nJavaScript console (errors and warnings since the page was opened):\n" + "\n".join(con)
                  if con else "\nJavaScript console: no errors or warnings since the page was opened.")
    return (f"Page: {snap.get('title', '')}\nURL: {snap.get('url', '')}\n{where}\n\n"
            f"Elements you can use (numbers for browser_click / browser_type / browser_select):\n"
            + ("\n".join(rows) or "(none)") + "\n\nPage text:\n" + snap.get("text", ""))


def t_browser_open(a, c):
    url = str(a.get("url") or a.get("query") or "").strip()
    if not url:
        raise ToolError("give a URL (or words to search for)")
    st = _bwrap(lambda: _bm().goto(url), url)
    _bdisplay(c, "open", url, st)
    snap = _bwrap(lambda: _bm().snapshot(int(a.get("max_chars") or 5000)))
    return _page_view(snap)


def t_browser_read(a, c):
    snap = _bwrap(lambda: _bm().snapshot(int(a.get("max_chars") or 6000)))
    _bdisplay(c, "read", snap.get("title", ""), snap, shot=False)
    return _page_view(snap)


def _after(action_text, max_chars=2500):
    """Short page view returned after an action, so the agent sees the result immediately."""
    try:
        snap = _bm().snapshot(max_chars)
        return action_text + "\n\n" + _page_view(snap, max_els=70)
    except Exception:
        return action_text + " (call browser_read to see the page)"


def t_browser_click(a, c):
    kw = {k: a[k] for k in ("ref", "text", "x", "y") if a.get(k) is not None}
    st = _bwrap(lambda: _bm().click(**kw))
    _bdisplay(c, "click", a.get("text") or (f"[{a.get('ref')}]" if a.get("ref") is not None else f"({a.get('x')}, {a.get('y')})"), st)
    note = getattr(_bm(), "last_note", "") or ""
    _bm().last_note = ""
    return _after("Clicked." + (" " + note if note else ""))


def t_browser_type(a, c):
    text = str(a.get("text", ""))
    kw = {"ref": a.get("ref"), "text": text, "submit": bool(a.get("submit")), "clear": a.get("clear", True) is not False}
    st = _bwrap(lambda: _bm().type(**kw))
    _bdisplay(c, "type", f"{text[:80]}" + (" ⏎" if a.get("submit") else ""), st)
    return _after((f"Typed into [{a.get('ref')}]" if a.get("ref") is not None else "Typed")
                  + (" and pressed Enter." if a.get("submit") else "."))


def t_browser_press(a, c):
    st = _bwrap(lambda: _bm().press(str(a.get("keys") or "Enter")))
    _bdisplay(c, "press", a.get("keys") or "Enter", st)
    return _after(f"Pressed {a.get('keys') or 'Enter'}.")


def t_browser_select(a, c):
    st = _bwrap(lambda: _bm().select(a.get("ref"), a.get("option")))
    _bdisplay(c, "select", a.get("option"), st)
    return f"selected '{a.get('option')}' in [{a.get('ref')}]"


def t_browser_scroll(a, c):
    pos = _bwrap(lambda: _bm().scroll(a.get("direction") or "down", a.get("amount")))
    _bdisplay(c, "scroll", a.get("direction") or "down", None)
    return _after(f"Scrolled {a.get('direction') or 'down'}: now at {pos.get('y')}px of {pos.get('h')}px.")


def t_browser_nav(a, c):
    which = str(a.get("action") or "back").lower()
    if which not in ("back", "forward", "reload"):
        raise ToolError("action must be back, forward or reload")
    st = _bwrap(lambda: _bm().history(which))
    _bdisplay(c, which, st.get("url", ""), st)
    return f"{which}: {st.get('title', '')} ({st.get('url', '')})"


def t_browser_tabs(a, c):
    act = str(a.get("action") or "list").lower()
    m = _bm()
    st = _bwrap(lambda: m.state()) if act == "list" else _bwrap(lambda: m.tab(act, a.get("index"), a.get("url")))
    if act != "list":
        _bdisplay(c, f"tab {act}", a.get("url") or a.get("index") or "", st)
    return "\n".join(f"{'*' if t['current'] else ' '} [{t['i']}] {t['title'][:70]} — {t['url'][:100]}"
                     for t in st.get("tabs", [])) or "(no tabs)"


def t_browser_screenshot(a, c):
    png = _bwrap(lambda: _bm().screenshot(bool(a.get("full_page"))))
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png))
    data, w, h = _png_bytes(img)
    _bdisplay(c, "screenshot", "", _bwrap(lambda: _bm().state()))
    return (f"browser screenshot {w}x{h}" + ("" if c.vision else " (vision is off, so you cannot see it; use browser_read)"), data)


def t_browser_wait(a, c):
    st = _bwrap(lambda: _bm().wait(a.get("seconds"), a.get("text")))
    return f"waited. Page: {st.get('title', '')} ({st.get('url', '')})"


def t_browser_links(a, c):
    links = _bwrap(lambda: _bm().links(a.get("contains")))
    return "\n".join(f"- {l['text']} -> {l['href']}" for l in links) or "(no matching links)"


# ----------------------------------------------------------------------------- approval policy
# Commands that only look at things run without asking. Anything that could change state (or that
# this simple check can't vouch for) still asks.
_SAFE_CMDS = {
    "get-childitem", "gci", "dir", "ls", "get-content", "gc", "cat", "type", "get-item", "gi",
    "get-itemproperty", "get-location", "gl", "pwd", "set-location", "sl", "cd", "chdir", "push-location",
    "pushd", "pop-location", "popd", "get-process", "gps", "ps", "get-service", "gsv", "get-ciminstance",
    "get-wmiobject", "gwmi", "get-computerinfo", "get-date", "get-command", "gcm", "get-help", "help",
    "get-module", "get-host", "get-history", "get-psdrive", "get-volume", "get-disk", "get-partition",
    "get-netipaddress", "get-netadapter", "get-nettcpconnection", "get-dnsclientcache", "get-filehash",
    "get-acl", "get-hotfix", "get-package", "get-appxpackage", "get-member", "gm", "get-variable",
    "get-culture", "get-uptime", "get-counter", "get-eventlog", "get-winevent", "get-printer",
    "get-scheduledtask", "get-localuser", "get-timezone", "get-clipboard", "get-random",
    "test-path", "test-connection", "test-netconnection", "resolve-path", "rvpa", "split-path",
    "join-path", "select-object", "select", "where-object", "where", "?", "sort-object", "sort",
    "measure-object", "measure", "group-object", "group", "compare-object", "compare", "diff",
    "format-table", "ft", "format-list", "fl", "format-wide", "fw", "format-custom", "out-string",
    "out-host", "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv",
    "convertto-html", "convertto-xml", "select-string", "sls", "foreach-object", "foreach", "%",
    "write-output", "echo", "write-host", "write-verbose", "measure-command", "tee-object",
    "whoami", "hostname", "ipconfig", "systeminfo", "tasklist", "where.exe", "findstr", "nslookup",
    "ping", "tracert", "netstat", "ver", "nvidia-smi", "driverquery", "wmic", "getmac", "route",
}
_PS_KEYWORDS = {"if", "else", "elseif", "foreach", "for", "while", "do", "switch", "try", "catch",
                "finally", "return", "break", "continue", "in"}
_GIT_SAFE = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame",
             "describe", "tag", "shortlog", "reflog", "config", "grep", "show-ref", "--version"}
_TOOL_VERSION = {"python", "python3", "py", "node", "npm", "pip", "pip3", "dotnet", "java", "go",
                 "cargo", "rustc", "ollama", "uv", "winget", "code", "pwsh", "powershell"}
_SAFE_METHODS = {"tostring", "trim", "trimstart", "trimend", "split", "replace", "substring", "tolower",
                 "toupper", "contains", "startswith", "endswith", "indexof", "padleft", "padright",
                 "getvalue", "gettype", "tostring", "count", "length", "round", "format", "join",
                 "equals", "compareto", "normalize", "totalseconds", "totalminutes", "tolongdatestring",
                 "toshortdatestring", "getfilename", "getextension", "getdirectoryname", "adddays"}


def is_safe_command(cmd):
    """True if every statement in a PowerShell command only reads (conservative: unknown = unsafe)."""
    c = str(cmd or "").strip()
    if not c or len(c) > 2000:
        return False
    low = c.lower()
    if re.search(r"(?<![-=!<])>|>>|`|\binvoke-|\biex\b|-encodedcommand|\bstart-process\b|\bsaps\b", low):
        return False
    if re.search(r"(?<!\[math\])(?<!\[datetime\])(?<!\[environment\])::", low):
        return False
    for m in re.finditer(r"\.(\w+)\s*\(", low):                 # .Method( calls
        if m.group(1) not in _SAFE_METHODS:
            return False
    if re.search(r"\[(environment)\]::(set|exit)", low):
        return False
    for seg in re.split(r"&&|\|\||[;|\n{}()]", c):
        seg = seg.strip()
        seg = re.sub(r"^\$[\w:.]+\s*[+\-*/]?=\s*", "", seg)          # $x = ..., $x += ...
        seg = re.sub(r"^[A-Za-z_]\w*\s*=\s*", "", seg)                # hashtable key = value
        if not seg or seg[0] in "$'\"[@-+!,0123456789.":           # expression / literal / argument
            if seg.startswith((".\\", "./", ". ")):
                return False                                       # running a script / dot-sourcing
            continue
        if seg.startswith("&"):
            return False
        toks = seg.split()
        first = toks[0].lower()
        if first in _PS_KEYWORDS:          # if/else/foreach…: the code inside the braces is checked separately
            continue
        if first in _SAFE_CMDS:
            continue
        if first in ("git", "git.exe") and len(toks) > 1 and toks[1].lower() in _GIT_SAFE:
            if toks[1].lower() == "branch" and any(t in ("-d", "-D", "-m", "-M", "--delete") for t in toks):
                return False
            if toks[1].lower() in ("config", "tag", "remote") and len(toks) > 2 and not toks[2].startswith("-"):
                return False                                       # config x y / tag x / remote add
            continue
        if first in _TOOL_VERSION and len(toks) == 2 and toks[1].lower() in ("--version", "-v", "-V".lower(), "version"):
            continue
        if first in ("pip", "pip3", "npm") and len(toks) >= 2 and toks[1].lower() in ("list", "show", "freeze", "ls", "view"):
            continue
        if first in ("python", "py") and toks[1:3] and toks[1] == "-m" and len(toks) > 2 and toks[2] == "pip" \
                and len(toks) > 3 and toks[3] in ("list", "show", "freeze"):
            continue
        return False
    return True


def rule_target(name, args):
    a = args or {}
    if name in ("powershell", "bg_start"):
        return " ".join(str(a.get("command", "")).split())
    if a.get("path"):
        return os.path.abspath(str(a["path"]))
    return ""


def rule_matches(rules, name, args):
    """The first allow rule (dict tool/pattern) that covers this call, or None."""
    target = rule_target(name, args).lower()
    multi = name in ("powershell", "bg_start") and ("\n" in str((args or {}).get("command", "")))
    for r in rules or []:
        if r.get("tool") not in (name, "*"):
            continue
        pat = str(r.get("pattern", "*")).lower()
        if multi and pat != "*":
            continue                       # multi-line scripts only match a blanket rule
        if name in ("powershell", "bg_start") and re.search(r"[;&|]", target) and "*" in pat and pat != "*":
            continue                       # 'git *' must not wave through 'git status; rm -r x'
        if fnmatch.fnmatchcase(target, pat):
            return r
    return None


def suggest_rule(name, args):
    a = args or {}
    if name in ("powershell", "bg_start"):
        cmd = " ".join(str(a.get("command", "")).split())
        toks = cmd.split()
        if not toks or "\n" in str(a.get("command", "")) or re.search(r"[;&|]", cmd):
            return {"tool": name, "pattern": cmd}
        multi = {"git", "npm", "pip", "pip3", "python", "py", "node", "dotnet", "cargo", "docker", "winget",
                 "choco", "uv", "yarn", "pnpm", "npx", "go", "ollama"}
        head = toks[:2] if toks[0].lower() in multi and len(toks) > 1 else toks[:1]
        return {"tool": name, "pattern": " ".join(head).lower() + (" *" if len(toks) > len(head) else "")}
    if a.get("path"):
        return {"tool": name, "pattern": os.path.join(os.path.dirname(os.path.abspath(str(a["path"]))), "*")}
    return {"tool": name, "pattern": "*"}


def rule_label(r):
    return f"{r.get('tool')}: {r.get('pattern')}"


# ----------------------------------------------------------------------------- registry
def _obj(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
N = {"type": "number"}

BUILTIN = [
    ("encyclopedia", READ, "Offline English Wikipedia — instant (under a second), every article's intro and infobox "
     "(population, area, length, height, dates, founders, capitals…). Use it FIRST to check any fact you would "
     "otherwise state from memory: names, figures, dates, who/what/where, and the figures behind a ranking (look up "
     "each candidate). 'query' = a topic or question; 'n' = articles (default 3). Snapshot from a recent month: for "
     "anything current (prices, news, this year's figures) use web_search instead.",
     _obj({"query": S, "n": I}, ["query"]), t_encyclopedia),
    ("web_search", NET, "Search the web and read the results in one call: searches your query plus 2-4 "
     "differently-worded 'queries' in parallel (synonyms, the words a good source would use, a narrower angle), "
     "reads the top pages (HTML and PDF) and returns the passages most relevant to the query, each with its numbered "
     "source URL, plus other results worth a look. 'pages' = pages to read (default 6; 0 = just the result list, "
     "fast, e.g. to find one URL). Up to 3 extra queries are used. Takes ~15-35 s when reading pages.",
     _obj({"query": S, "queries": {"type": "array", "items": S}, "pages": I}), t_web_search),
    ("web_fetch", NET, "Read web pages (HTML or PDF). One page: 'url' returns its text. Several pages at once: "
     "'urls' (a list, up to 8) reads them all in parallel; add 'question' to get each page's passages most relevant "
     "to it (otherwise the start of each page). Full texts are saved to files you can read_file.",
     _obj({"url": S, "urls": {"type": "array", "items": S}, "question": S, "max_chars": I}), t_web_fetch),
    ("list_dir", READ, "List the files in a folder (inside the allowed folders).",
     _obj({"path": S}), t_list_dir),
    ("read_file", READ, "Read a text file. Returns lines prefixed with line numbers (the prefix is "
     "not part of the file). offset = first line (1-based), limit = number of lines (default 400).",
     _obj({"path": S, "offset": I, "limit": I}, ["path"]), t_read_file),
    ("read_document", READ, "Read a document's text: PDF, Word (.docx/.doc/.rtf), Excel "
     "(.xlsx/.xls, tab-separated rows), PowerPoint (.pptx/.ppt, with speaker notes), CSV, or text. "
     "start/end select pages, slides or rows; sheet picks an Excel sheet. Use this for files the "
     "user attached.", _obj({"path": S, "start": I, "end": I, "sheet": S, "max_chars": I}, ["path"]),
     t_read_document),
    ("find_files", READ, "Find files by name pattern, e.g. '*.py'.",
     _obj({"pattern": S, "root": S}, ["pattern"]), t_find_files),
    ("search_text", READ, "Search inside files (root = a folder or one file) for a regular expression. "
     "context='function' shows each match inside its whole enclosing function (Python; others +-12 lines), "
     "context=N shows N lines around each match — use it to see related code together instead of reading slices.",
     _obj({"pattern": S, "root": S, "glob": S, "context": S}, ["pattern"]), t_search_text),
    ("trace_symbol", READ, "Everything connected to one function, method, class or variable, across all files of a "
     "project: its definition, every place that uses it — each shown inside its whole function — and what it calls. "
     "Python names are resolved like Python does, so a same-named function in another file is kept apart (listed "
     "separately as unresolved). Use it before changing a function, and when a bug may span two places (a lock taken "
     "in a caller and a callee, a renamed field the tests still expect). root = project folder (or a file in it); "
     "file = which definition when several share the name.",
     _obj({"name": S, "root": S, "file": S, "max_lines": I}, ["name"]), t_trace_symbol),
    ("write_file", WRITE, "Create a new text file, or overwrite one with the full new content "
     "(append=true adds to the end). Folders are created as needed. For small changes to an "
     "existing file use edit_file instead.",
     _obj({"path": S, "content": S, "append": B}, ["path", "content"]), t_write_file),
    ("edit_file", WRITE, "Change part of an existing file: replaces the exact text 'old' with 'new'. "
     "'old' must match the file exactly (copy it from read_file without the line-number prefix) "
     "and be unique, unless replace_all=true. Returns the edited lines.",
     _obj({"path": S, "old": S, "new": S, "replace_all": B}, ["path", "old", "new"]), t_edit_file),
    ("make_dir", WRITE, "Create a folder.", _obj({"path": S}, ["path"]), t_make_dir),
    ("move_path", WRITE, "Move or rename a file or folder.",
     _obj({"src": S, "dst": S}, ["src", "dst"]), t_move_path),
    ("trash_path", WRITE, "Delete a file/folder by moving it to the agent trash (recoverable).",
     _obj({"path": S}, ["path"]), t_trash_path),
    ("powershell", EXEC, "Run PowerShell on the user's Windows PC and get its output (stdout and "
     "errors) and exit code. This is your terminal: use it for processes (Get-Process), system "
     "info, git, python, pip, npm, builds, tests, network checks, anything a terminal can do. "
     "Multi-line scripts are fine. The working folder carries over between calls (cd sticks). "
     "The user watches the command and its output live. Not for interactive programs that wait "
     "for input. timeout in seconds (default 120, max 600); shell='cmd' or 'pwsh' to switch. "
     "This is PowerShell, not bash: no heredocs (<<EOF), no &&-chains in Windows PowerShell 5 (use ;). "
     "To run more than a line of Python, write a .py file with write_file and run `python file.py`. "
     "A command that sits silent and idle for 10s (blocked on a lock, socket or server) comes back early with "
     "where it is waiting, and keeps running as a background task; allow_idle=true waits for exit or the timeout.",
     _obj({"command": S, "cwd": S, "timeout": I, "shell": S, "allow_idle": B}, ["command"]), t_powershell),
    ("screen_size", READ, "Screen resolution.", _obj({}), t_screen_size),
    ("list_windows", READ, "Titles of the visible windows.", _obj({}), t_list_windows),
    ("screenshot", READ, "Take a screenshot of the screen (the model sees it if it has vision). "
     "Mouse tools then use that image's coordinates.", _obj({"max_width": I}), t_screenshot),
    ("mouse_move", INPUT, "Move the mouse (coordinates from the last screenshot).",
     _obj({"x": N, "y": N}, ["x", "y"]), t_mouse_move),
    ("mouse_click", INPUT, "Click at screenshot coordinates (button left/right/middle, double=true).",
     _obj({"x": N, "y": N, "button": S, "double": B}, ["x", "y"]), t_mouse_click),
    ("mouse_drag", INPUT, "Drag from one point to another.",
     _obj({"x1": N, "y1": N, "x2": N, "y2": N}, ["x1", "y1", "x2", "y2"]), t_mouse_drag),
    ("scroll", INPUT, "Scroll the mouse wheel: positive = up, negative = down.",
     _obj({"amount": I}, ["amount"]), t_scroll),
    ("type_text", INPUT, "Type text into the focused window.", _obj({"text": S}, ["text"]), t_type_text),
    ("press_keys", INPUT, "Press a key or combo, e.g. 'enter', 'ctrl+c', 'alt+tab'.",
     _obj({"keys": S}, ["keys"]), t_press_keys),
    ("focus_window", INPUT, "Bring a window to the front by (part of) its title.",
     _obj({"title": S}, ["title"]), t_focus_window),
    ("wait", READ, "Wait a few seconds (for a page or program to load).",
     _obj({"seconds": N}), t_wait),
    ("browser_open", NET, "Open a URL in the in-app browser (a real Edge browser the user can watch). Use it "
     "like a person would for searching job boards, sites that block simple fetches, logins, forms and "
     "anything interactive. Given plain words, it searches them. Returns the page: numbered elements "
     "plus the text.", _obj({"url": S, "max_chars": I}, ["url"]), t_browser_open),
    ("browser_read", READ, "Read the current browser page again: numbered clickable/typeable elements "
     "(numbers change after the page changes) and the visible text.", _obj({"max_chars": I}), t_browser_read),
    ("browser_click", NET, "Click an element in the browser: by its number from browser_read (ref), by its "
     "visible text, or by x/y in the screenshot. The agent cannot buy anything.",
     _obj({"ref": I, "text": S, "x": N, "y": N}), t_browser_click),
    ("browser_type", NET, "Type into a field in the browser (ref from browser_read; omit ref to type into the "
     "focused field). submit=true presses Enter afterwards; clear=false appends. Never for payment details.",
     _obj({"ref": I, "text": S, "submit": B, "clear": B}, ["text"]), t_browser_type),
    ("browser_press", NET, "Press a key or combo in the browser, e.g. 'Enter', 'Tab', 'Escape', 'PageDown', "
     "'Control+A'.", _obj({"keys": S}, ["keys"]), t_browser_press),
    ("browser_select", NET, "Choose an option in a dropdown (ref from browser_read, option = its visible text).",
     _obj({"ref": I, "option": S}, ["ref", "option"]), t_browser_select),
    ("browser_scroll", NET, "Scroll the page: direction down / up / top / bottom (optional amount in pixels).",
     _obj({"direction": S, "amount": I}), t_browser_scroll),
    ("browser_nav", NET, "Browser back, forward or reload.", _obj({"action": S}, ["action"]), t_browser_nav),
    ("browser_tabs", NET, "Browser tabs: action list / new (with url) / switch (index) / close (index).",
     _obj({"action": S, "index": I, "url": S}), t_browser_tabs),
    ("browser_screenshot", READ, "Screenshot of the browser page (you see it if vision is on).",
     _obj({"full_page": B}), t_browser_screenshot),
    ("browser_wait", READ, "Wait for the page: a number of seconds, or until some text appears.",
     _obj({"seconds": N, "text": S}), t_browser_wait),
    ("browser_links", READ, "List the links on the current page (optionally only those containing some text).",
     _obj({"contains": S}), t_browser_links),
    ("update_tracker", NOTE, "Your task checklist (it keeps you on track). Create it FIRST for every "
     "request: 'goal'; 'design' = the decisions the result depends on (the hard parts and how you will handle "
     "each; for a fix, the possible causes and what tells them apart); and 'milestones' = the major phases, each "
     "with 'done_when' (the observable evidence that it works: a test that passes, output or a screenshot showing "
     "X) and 'subtasks' (concrete steps). "
     "Set new=true when starting a new request. Then keep it current with SHORT calls (or, quicker, add "
     "progress='1.2 done, 1.3 started' to your next tool call instead): mark progress with 'updates' "
     "(e.g. [{id:'1.2', status:'done'}, {id:'1.3', status:'in_progress'}]; statuses pending / "
     "in_progress / done / blocked / skipped), add 'notes' (findings, decisions, paths, what failed) "
     "and set 'next' (the very next action). When you settle a question — what the contract or PRD requires, "
     "which approach, how a library behaved in a probe — add it to 'decisions' (one short line each): they stay in "
     "view on every step, so you never have to re-read or re-decide them. "
     "Send 'milestones' again only to restructure the plan. "
     "Optional 'path' keeps it in a project folder (e.g. PROGRESS.md).",
     _obj({"goal": S, "design": S, "new": B,
           "milestones": {"type": "array", "items": {"type": "object", "properties": {
               "title": S, "done_when": S, "status": S, "subtasks": {"type": "array", "items": {"type": "object", "properties": {
                   "step": S, "status": S}, "required": ["step"]}}}, "required": ["title"]}},
           "updates": {"type": "array", "items": {"type": "object", "properties": {"id": S, "status": S},
                                                  "required": ["id", "status"]}},
           "notes": {"type": "array", "items": S}, "decisions": {"type": "array", "items": S}, "next": S,
           "path": S}), t_update_tracker),
    ("calculate", READ, "Exact calculator (SymPy + units + dates). Use it for EVERY calculation instead of "
     "doing math yourself: arithmetic of any size or precision ('2^100/3', digits=50), percentages "
     "('15% of 240'), equations ('x^2-5x+6=0', 'solve([x+y-3, x-y-1], [x,y])'), calculus ('diff(sin(x)*x^2, x)', "
     "'integrate(exp(-x^2), (x, -oo, oo))', 'limit(sin(x)/x, x, 0)'), simplify/factor/expand, matrices "
     "('Matrix([[1,2],[3,4]]).det()'), statistics ('mean([3,5,9])', 'stdev([...])'), unit conversion "
     "('65 mph to m/s', '72 degF to degC'), and dates ('days between 2026-03-03 and 2026-06-09', "
     "'business days between ...', '2026-10-07 + 90 days', 'now in Tokyo'). Returns the exact result, "
     "a decimal and LaTeX.", _obj({"expression": S, "digits": I}, ["expression"]), t_calculate),
    ("artifact_create", NOTE, "Create an artifact: a document shown in the Artifacts pane beside the chat that you "
     "keep improving during the conversation. Use it for substantial deliverables the user will read, reuse or "
     "iterate on (reports, plans, articles, HTML pages, diagrams, code files, tables), not for short answers. "
     "type: markdown (supports $LaTeX$), html (a full self-contained page; scripts allowed), svg, mermaid (diagram "
     "source), chart (a Vega-Lite JSON spec with the data inline under data.values — interactive charts), code (set "
     "language), csv, json or text. Returns its id (a1, a2, …).",
     _obj({"title": S, "type": S, "content": S, "language": S}, ["title", "type", "content"]), t_artifact_create),
    ("artifact_edit", NOTE, "Change an artifact (each change is saved as a new version). Targeted edit: old (exact "
     "text that appears once) + new. Full rewrite: content. title renames it.",
     _obj({"id": S, "old": S, "new": S, "content": S, "title": S}, ["id"]), t_artifact_edit),
    ("artifact_read", READ, "Read an artifact's current text (or an older version) before editing it.",
     _obj({"id": S, "version": I}, ["id"]), t_artifact_read),
    ("artifact_list", READ, "List this conversation's artifacts (id, title, type, latest version).", _obj({}),
     t_artifact_list),
    ("available_tools", READ, "The tool catalog. Call it (no arguments) to see all available tools grouped "
     "by purpose with what each does; query filters by keyword; enable turns tools on by tool or group name (e.g. "
     "[\"files\", \"web\", \"calculate\"]) — they become callable on your next step and stay on for this chat. "
     "Use it when planning to decide which tools you will use, and whenever you need a capability you don't see.",
     _obj({"query": S, "enable": {"type": "array", "items": S}}), t_available_tools),
    ("schedule_task", EXEC, "Schedule a recurring or one-time agent task (the user approves it): name, prompt (full "
     "instructions; the run starts with no chat history), when: 'daily 08:00', 'weekdays 8:30am', 'every monday at 9am', "
     "'mon,thu 18:00', 'daily 08:00 and 17:30', 'every 3 days at 07:00', 'monthly 1 09:00', '15th of every month at "
     "9am', 'monthly last 17:00', 'every 2h', 'every 45m', 'hourly' or 'once 2026-10-09 14:00' (local time). Each run "
     "happens in a new chat and the user is notified.",
     _obj({"name": S, "prompt": S, "when": S}, ["prompt", "when"]), t_schedule_task),
    ("list_schedules", READ, "List scheduled tasks (id, schedule, next run).", _obj({}), t_list_schedules),
    ("update_schedule", CONTROL, "Change a scheduled task by id (from list_schedules): pause or resume it "
     "(enabled false/true), rename it, reword its prompt, or give it a new 'when' (same forms as schedule_task).",
     _obj({"id": S, "name": S, "prompt": S, "when": S, "enabled": B}, ["id"]), t_update_schedule),
    ("delete_schedule", CONTROL, "Delete a scheduled task by id.", _obj({"id": S}, ["id"]), t_delete_schedule),
    ("search_documents", READ, "Search the contents of the user's files (in the allowed folders) by meaning and keywords — "
     "e.g. 'my notes on the Q3 budget', 'the script that renames photos'. Returns the best matching passage per file "
     "with its path (then read_file / read_document it). folder narrows the search. For exact text or regex use "
     "search_text instead.", _obj({"query": S, "folder": S, "limit": I}, ["query"]), t_search_documents),
    ("create_document", WRITE, "THE way to make Office files — PowerPoint decks, Word documents, Excel workbooks and "
     "PDFs — professionally styled (themes: corporate, modern, minimal, warm, forest). pptx: JSON slides with layouts "
     "(title, bullets, two_column, stats, quote, section, image, closing); docx/pdf: Markdown; xlsx: JSON sheets with "
     "number formats and native charts, or CSV. Read the format first with read_guide('powerpoint' | 'word' | 'excel' "
     "| 'pdf'). Never pip-install libraries or use python_sandbox for Office files. Only when the user asks for a file "
     "or document: a table, list or comparison asked for in chat goes in your reply, not in a file.",
     _obj({"path": S, "type": S, "content": S, "title": S, "subtitle": S, "theme": S, "overwrite": B}, ["path", "content"]),
     t_create_document),
    ("show_user", EXEC, "Show your work to the user: launch a program, game or app for them with NO timeout (it runs "
     "until they close it), open a page (.html or URL) in the Browser panel, or open a file in its default app. "
     "Use it to PRESENT a result, never to test it (it returns at once and you can't see the window): test with "
     "run_tests, a headless run or debug_run first. path = what to show; or command = how to launch it.",
     _obj({"path": S, "command": S, "cwd": S}), t_show_user),
    ("run_tests", EXEC, "Run a project's tests and get a pass/fail summary plus the failures with their tracebacks "
     "and the exact file:line where each error was raised. path = a test file or folder (default: the working "
     "folder). Python uses pytest (test_*.py files with test_* functions and plain asserts); JavaScript uses node's "
     "test runner or the package's npm test. Runs headless (no windows open). Prefer this over a hand-written "
     "runner: it never hides a traceback.",
     _obj({"path": S, "framework": {"type": "string", "enum": ["auto", "pytest", "node", "npm"]},
           "filter": {"type": "string", "description": "run only tests whose name matches (pytest -k)"},
           "max_failures": I, "timeout": I}), t_run_tests),
    ("debug_run", EXEC, "Run a Python script as a debugger would. On a crash: the traceback plus the LOCAL VARIABLES "
     "of the failing frame (post-mortem). With probe='file.py:LINE': prints the local variables (and any 'watch' "
     "expressions) each time that line is reached, up to max_hits — to see what the data really is instead of "
     "guessing. Runs headless.",
     _obj({"script": S, "args": {"type": "array", "items": S}, "probe": S,
           "watch": {"type": "array", "items": S, "description": "expressions to evaluate at the probe or the crash"},
           "max_hits": I, "cwd": S, "timeout": I}, ["script"]), t_debug_run),
    ("browser_eval", READ, "Evaluate JavaScript in the current browser page and get the result as JSON — inspect "
     "a web app's state while debugging (variables, DOM, game objects), e.g. 'player.x' or "
     "'document.querySelectorAll(\".item\").length'. Only on your own local pages (file:// or localhost).",
     _obj({"expression": S}, ["expression"]), t_browser_eval),
    ("browser_devtools", READ, "Chrome DevTools for the current browser page, as text. action: "
     "'network' (every request: status, type, size, time; filter / failed_only), 'request' (one request by id: "
     "headers, timing, response body), 'console' (all messages; level error|warning|log|all), 'element' (selector: "
     "HTML, box, computed styles, whether something covers it, and the EVENT LISTENERS attached — the check for "
     "'clicking does nothing'), 'listeners' (on 'window', 'document' or a selector), 'storage' (cookies, "
     "localStorage), 'performance' (FPS, JS heap, DOM nodes), 'breakpoint' (location 'file.js:LINE': pause when it "
     "runs and show the call stack, local variables and 'watch' expressions; 'trigger' = JS that makes it run, e.g. "
     "\"document.querySelector('#start').click()\"), 'exceptions' (pause at the next thrown error with its stack "
     "and locals). request/storage/breakpoint/exceptions only on your own local pages.",
     _obj({"action": {"type": "string", "enum": ["network", "request", "console", "element", "listeners", "storage",
                                                  "performance", "breakpoint", "exceptions"]},
           "filter": S, "failed_only": {"type": "boolean"}, "id": I, "level": S, "selector": S,
           "styles": {"type": "array", "items": S}, "location": S, "condition": S,
           "watch": {"type": "array", "items": S}, "trigger": S, "caught": {"type": "boolean"},
           "seconds": I, "timeout": I, "limit": I}, ["action"]), t_browser_devtools),
    ("ask_user", NOTE, "Ask the user 2-5 short clarifying questions before broad or open-ended work, when their "
     "answers would change what you make (purpose, scope, platform or format, look and feel, must-haves). Give each "
     "question the default you would use. The turn ends after this call; their answer is the next message.",
     _obj({"intro": S, "questions": {"type": "array", "items": {"type": "object", "properties": {
         "question": S, "default": S}, "required": ["question"]}}}, ["questions"]), t_ask_user),
    ("read_guide", READ, "Read a how-to guide before specialised work: 'powerpoint', 'word', 'excel', 'pdf' (also CSV) "
     "— exact formats, design rules, how to edit existing files — 'web_research' — how to search, verify and "
     "report — and 'clear_writing' — the communication standard for answers, reports and instructions. Call with "
     "no topic to list guides.", _obj({"topic": S}), t_read_guide),
    ("read_formula", READ, "Read a picture of a math formula (png/jpg) into LaTeX.", _obj({"path": S}, ["path"]),
     t_read_formula),
    ("python_sandbox", NOTE, "Run Python in an isolated sandbox (WebAssembly): it cannot touch the rest of the PC, "
     "the network or other programs, so it runs without asking. Use it to process and analyze data, run scripts, "
     "compute and make charts. numpy, pandas, scipy, matplotlib, scikit-learn, sympy, etc. load automatically on "
     "import. Variables persist between calls in this chat. Its folder is /work (current directory): pass host files "
     "in 'files' to copy them there first; files you save in /work stay available. matplotlib figures are shown to "
     "the user automatically. reset=true starts fresh. Keep each call's code short: put large data in files (write_file, "
     "then pass them in files) instead of pasting it into the code. Long results of read_document / web_fetch / "
     "browser_read etc. are saved automatically ('Full result … saved at <path>'): pass those paths in files. Results stay in variables between calls — reuse "
     "them; never copy printed output back into new code. Don't print huge results (long lists, big numbers): print a "
     "summary (count, first items) and write the full data to a file in /work.",
     _obj({"code": S, "files": {"type": "array", "items": S}, "timeout": I, "reset": B,
           "save": {"type": "object", "description": "deliver files from /work to the user's folders: {\"report.md\": \"C:\\...\\report.md\"}"}}, ["code"]), t_python_sandbox),
    ("python_host", EXEC, "Run Python on the PC itself (full access, needs approval) in a persistent session for this chat, "
     "starting in the working folder. Use it only when the code must read/write real files elsewhere, use installed "
     "packages or the network; otherwise use python_sandbox.",
     _obj({"code": S, "timeout": I, "reset": B}, ["code"]), t_python),
    ("query_data", READ, "Query data files with SQL (DuckDB): CSV/TSV, Excel (each sheet a table), Parquet, JSON. "
     "files maps table names to paths, e.g. {\"sales\": \"C:/data/sales.xlsx\"}; omit sql to see each table's "
     "columns, row count and sample rows. Exact aggregates over millions of rows without reading them yourself. "
     "Read-only (SELECT / WITH / DESCRIBE / SUMMARIZE).",
     _obj({"sql": S, "files": {"type": "object"}, "max_rows": I}), t_query_data),
    ("memory_save", NOTE, "Save one durable fact to long-term memory (kept across all chats): the user's "
     "preferences, details about them, their setup and projects, decisions, things they asked you to remember. "
     "One fact per call, written so it makes sense on its own later. Not for task progress (that is "
     "update_tracker).", _obj({"content": S, "tags": S}, ["content"]), t_memory_save),
    ("playbook_note", NOTE, "Record ONE short reusable lesson for the current KIND of task (the playbook shown at "
     "the start): a type of source that worked, a dead end to avoid, a check that caught an error. General, not "
     "task-specific: no names, paths or figures. Shown under the playbook next time such a task comes up.",
     _obj({"note": S, "pattern": S}, ["note"]), t_playbook_note),
    ("history_search", READ, "Search THIS chat's archive: when the conversation was compacted, earlier tool results, "
     "code you wrote and earlier steps were moved here in full (stubs in the context name them, e.g. #E12). Give "
     "keywords to find them, or entry='E12' for one entry in full. Use it before redoing work you did earlier.",
     _obj({"query": S, "entry": S, "limit": I}), t_history_search),
    ("memory_search", READ, "Search long-term memory and past conversations by meaning and keywords (hybrid). "
     "scope: 'memories' (saved facts), 'chats' (earlier conversations, not this one) or 'all' (default). Use it "
     "when the user refers to something from before ('like last time', 'my usual', 'what did we decide about…') "
     "or before asking the user something you may already know. query '*' with scope 'memories' lists them.",
     _obj({"query": S, "scope": S, "limit": I}, ["query"]), t_memory_search),
    ("memory_update", NOTE, "Correct or replace a saved memory by its id (e.g. m12): new content and/or tags. "
     "Use it when a fact changed instead of saving a contradicting one.",
     _obj({"id": S, "content": S, "tags": S}, ["id"]), t_memory_update),
    ("memory_delete", NOTE, "Delete a saved memory by its id (e.g. m12) when it is wrong, outdated or the user "
     "asks you to forget it.", _obj({"id": S}, ["id"]), t_memory_delete),
    ("bg_start", EXEC, "Start a long-running PowerShell command in the background (dev server, "
     "watcher, long build) and return with an id. It keeps running after your turn; "
     "the user sees it in the Tasks pane. Use powershell instead for commands that finish. For a server, pass "
     "wait_port=<its port>: it waits until the server listens (or reports that it doesn't), and the result says "
     "which ports the process listens on. It is stopped automatically when you finish your reply; pass keep=true "
     "only for something the user will keep using after you're done (e.g. a server they asked to leave running).",
     _obj({"command": S, "name": S, "cwd": S, "wait_port": I, "wait": I, "keep": B}, ["command"]), t_bg_start),
    ("http_request", NET, "Send one HTTP request to a server on this PC or the local network — to test an API or "
     "web server you built. method (GET/POST/PUT/PATCH/DELETE), url (http://127.0.0.1:PORT/path), headers (object), "
     "body (an object is sent as JSON; text as-is). Returns the status, key headers and body. Cookies are kept between "
     "calls like a browser (log in once, then call protected routes); fresh_session=true starts over. For websites "
     "use web_fetch.",
     _obj({"method": S, "url": S, "headers": {"type": "object"}, "body": {}, "timeout": I, "fresh_session": B}, ["url"]),
     t_http_request),
    ("stack_dump", READ, "Where a running Python process of yours is stuck RIGHT NOW: every thread's current line, "
     "your own code marked. Use it when a server or command hangs (a request that never returns, a test that "
     "times out). id = a background process (bg2) or pid. (Commands and test runs that time out show this "
     "automatically.)", _obj({"id": S, "pid": I}), t_stack_dump),
    ("port_info", READ, "Who is listening on a port: every process, with its command line, folder, start time and whose "
     "it is (yours / this app / another program). Use it when a server answers strangely (old code, 404s you didn't "
     "write) or won't start — two processes can hold one port on Windows.",
     _obj({"port": I}, ["port"]), t_port_info),
    ("changes_since_green", READ, "Show every change to your files since your tests last passed (or since this chat "
     "first changed them): the quickest way to find the edit that broke something. Optional path = one file.",
     _obj({"path": S}), t_changes_since_green),
    ("undo_file", WRITE, "Put a file back as it was before your last change to it (steps=N to go back further). For "
     "undoing an experiment that made things worse.", _obj({"path": S, "steps": I}, ["path"]), t_undo_file),
    ("trace_call", EXEC, "Run ONE call in a Python file and print every line it executes in your project's code with "
     "the variables that changed on each line, plus arguments and return values — see exactly where a value goes "
     "wrong. file = the .py file, call = an expression using its names, e.g. \"verify_password('ann', 'pw1')\" or "
     "\"register_user('ann', 'pw1') and verify_password('ann', 'pw1')\".",
     _obj({"file": S, "call": S, "max_lines": I, "timeout": I}, ["file", "call"]), t_trace_call),
    ("bg_output", READ, "Read the latest output lines of a background process (a server's log). grep = only lines "
     "matching a pattern (e.g. 'Traceback|Error|POST'); new_only=true = only lines since you last read it.",
     _obj({"id": S, "lines": I, "grep": S, "new_only": B}, ["id"]), t_bg_output),
    ("bg_stop", CONTROL, "Stop a background process you started.", _obj({"id": S}, ["id"]), t_bg_stop),
    ("bg_list", READ, "List background processes and whether they are still running.", _obj({}), t_bg_list),
    ("delegate_task", CONTROL, "Hand a self-contained sub-task to a new agent thread that works "
     "in the background with its own context (it appears in the sidebar and Tasks pane). Good for "
     "independent research, investigating a separate folder, or long jobs while you continue. Give "
     "a complete task description with all paths and the expected deliverable: it cannot see this "
     "chat.", _obj({"task": S, "title": S, "context": S}, ["task"]), t_delegate_task),
    ("gather", NET, "Fan-out: helper agents work in the BACKGROUND while you keep working; their reports plus the "
     "raw results they read arrive here by themselves when done. They are agents, not tool calls: give each one an "
     "OBJECTIVE to own — a sub-question of the task — not a procedure. Split the work by sub-question or angle (e.g. "
     "the core figure, a related measure, how it varies, an independent cross-check), never one website per worker; "
     "each picks its own sources and adapts if one is blocked. Give exactly one objective per worker (the system prompt says how many). For each: objective (what to "
     "establish), context (the user's overall question and why this part matters to the final answer), done_when "
     "(what a good result contains — e.g. figures with their exact scope, date and source), hints (optional starting "
     "points, explicitly suggestions), avoid (what the other workers cover). They cannot see this chat. Put the part "
     "YOU do meanwhile in 'mine' and start it right after this call; do not write the final answer before their "
     "results arrive. Workers search and read the web and local files and calculate; they cannot write, edit or run "
     "commands. Give each a max_calls budget from 3 (quick lookup) to 6 (hard objective). When their results "
     "arrive, check them against the raw results and merge them with yours; if they are not enough, call gather "
     "again with sharper objectives and keep searching yourself.",
     _obj({"tasks": {"type": "array", "minItems": 1, "maxItems": 4, "items": _obj(
               {"title": S, "objective": S, "context": S, "done_when": S, "hints": S, "avoid": S,
                "max_calls": {"type": "integer", "minimum": 3, "maximum": 6,
                              "description": "tool-call budget: 3 for a quick lookup, up to 6 for a hard objective"}},
               ["title", "objective", "context", "done_when", "max_calls"])},
           "mine": {"type": "string", "description": "the part you do yourself while the workers run"}},
          ["tasks", "mine"]), t_gather),
    ("record", NOTE, "Record facts in this chat's evidence table (a CSV) instead of keeping them in your head: one row "
     "per figure or fact, with what exactly it covers (scope: base or total, median or average, place, group) and the "
     "sentence from the source that states it. Each row is checked against what you read. Use it whenever a task "
     "collects several figures or data points; then compute with query_data over the table and write the answer "
     "from it. Several rows per call.",
     _obj({"rows": {"type": "array", "items": __import__("evidence").ROW_SCHEMA}}, ["rows"]), t_record),
    ("go_extensive", CONTROL, "Switch this task from the basic path to the extensive one (a plan, a checklist and "
     "parallel research workers) when it turns out bigger than it looked: several sources or items to cover, a "
     "comparison, a document to build. What you found so far is kept.", _obj({"reason": S}, ["reason"]),
     t_go_extensive),
    ("wait_for_workers", READ, "Call this when your own part is done while the fan-out workers still run. If a worker "
     "has stalled (no progress for a minute) with several calls to go, you take over its objective (it is stopped, and you get its brief and what it "
     "already found — continue it with your own tools, you are faster). Otherwise it sleeps up to 10 seconds while "
     "they finish; their reports and raw results are added right after.", _obj({}), t_wait_for_workers),
    ("wait_task", READ, "Wait for a delegated task to finish (up to timeout seconds) and get its result.",
     _obj({"id": S, "timeout": I}, ["id"]), t_wait_task),
    ("task_status", READ, "Status, tracker and result of a delegated task (or all tasks if no id).",
     _obj({"id": S}), t_task_status),
    ("consult_advisor", CLOUD, "Ask a stronger cloud model (OpenAI) for advice when you are stuck: "
     "repeated failures, an error you don't understand, or a plan you are unsure about. It sees a "
     "summary of this chat. Ask one specific question.", _obj({"question": S}, ["question"]),
     t_consult_advisor),
    ("enable_screen_control", INPUT, "Ask the user to turn on screenshot / mouse / keyboard tools for "
     "this chat. Only when the task truly needs operating an app's GUI.",
     _obj({"reason": S}, ["reason"]), t_enable_screen),
]
GROUP_DELEGATE = {"delegate_task", "wait_task", "task_status"}


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:64]


# names models trained on other harnesses tend to use (Claude Code, OpenAI, etc.) -> ours
ALIASES = {"run_command": "powershell", "bash": "powershell", "shell": "powershell",
           "terminal": "powershell", "cmd": "powershell", "execute_command": "powershell",
           "run_shell": "powershell", "exec": "powershell", "run_powershell": "powershell",
           "read": "read_file", "view": "read_file", "cat": "read_file", "open_file": "read_file",
           "write": "write_file", "create_file": "write_file", "edit": "edit_file",
           "str_replace": "edit_file", "replace_in_file": "edit_file", "ls": "list_dir",
           "list_files": "list_dir", "glob": "find_files", "grep": "search_text",
           "websearch": "web_search", "deep_search": "web_search", "deepsearch": "web_search", "webfetch": "web_fetch", "fetch": "web_fetch",
           "update_plan": "update_tracker", "todowrite": "update_tracker", "todo_write": "update_tracker",
           "plan": "update_tracker", "tracker": "update_tracker", "write_todos": "update_tracker",
           "task": "delegate_task", "spawn_agent": "delegate_task", "delegate": "delegate_task",
           "memory": "memory_save", "save_memory": "memory_save", "remember": "memory_save",
           "add_memory": "memory_save", "search_memory": "memory_search", "recall": "memory_search",
           "update_memory": "memory_update", "edit_memory": "memory_update", "delete_memory": "memory_delete",
           "forget": "memory_delete", "calc": "calculate", "calculator": "calculate", "math": "calculate",
           "compute": "calculate", "guide": "read_guide", "get_guide": "read_guide",
           "document_guide": "read_guide", "make_document": "create_document", "create_presentation": "create_document",
           "make_powerpoint": "create_document", "create_pptx_file": "create_document",
           "create_docx": "create_document", "create_xlsx": "create_document", "create_pptx": "create_document",
           "write_docx": "create_document", "python": "python_sandbox", "py": "python_sandbox", "list_tools": "available_tools", "tools": "available_tools",
           "tool_catalog": "available_tools", "enable_tools": "available_tools", "run_python": "python_sandbox", "python_exec": "python_sandbox",
           "execute_python": "python_sandbox", "code_interpreter": "python_sandbox", "sandbox": "python_sandbox",
           "sql": "query_data", "query_csv": "query_data", "duckdb": "query_data", "create_artifact": "artifact_create", "edit_artifact": "artifact_edit",
           "update_artifact": "artifact_edit", "artifact_update": "artifact_edit", "read_artifact": "artifact_read",
           "list_artifacts": "artifact_list", "ask_advisor": "consult_advisor",
           "browse": "browser_open", "open_url": "browser_open", "navigate": "browser_open", "goto": "browser_open",
           "click": "browser_click", "type": "browser_type", "fill": "browser_type", "scroll_page": "browser_scroll",
           "read_page": "browser_read", "get_page": "browser_read", "page_text": "browser_read"}
ARG_ALIASES = {"note": "content", "fact": "content", "memory_id": "id", "expr": "expression", "file_path": "path", "filepath": "path", "filename": "path", "file": "path",
               "old_string": "old", "old_str": "old", "new_string": "new", "new_str": "new",
               "text": "content", "cmd": "command", "script": "command", "directory": "path",
               "dir": "path", "folder": "path", "line_offset": "offset", "num_lines": "limit",
               "todos": "steps", "plan": "milestones", "items": "steps", "tasks": "steps",
               "phases": "milestones", "checklist": "milestones", "status_updates": "updates",
               "objective": "goal", "next_step": "next", "prompt": "task", "description": "task",
               "task_id": "id", "process_id": "id", "question": "query", "sources": "pages", "lesson": "note"}


_SAVE_LOCK = threading.Lock()


# the composer's Web switch (settings agent.web): off = these tools are never offered and every call is refused
WEB_TOOL_RX = re.compile(r"^(web_|browser_)")
WEB_TOOLS = {"gather"}
WEB_OFF_MSG = ("web access is turned off by the user (the globe switch by the message box): web search, page fetch "
               "and the browser are unavailable. Use offline Wikipedia (encyclopedia), local files and memory, or "
               "tell the user the task needs web access.")


def is_web_tool(name):
    low = str(name or "").strip().lower().replace("-", "_")
    alias = ALIASES.get(low) or ""                      # "fetch", "browse", "navigate"… are web tools too
    return bool(WEB_TOOL_RX.match(low) or WEB_TOOL_RX.match(alias)) or low in WEB_TOOLS or alias in WEB_TOOLS


# the placeholder agentic._slim_call_args leaves in history for old bulky args — never real content
_HISTORY_STUB = re.compile(r"\[\d[\d,]* chars omitted [—-]+ already applied")
_STUB_TOOLS = {"write_file", "edit_file", "run_python", "run_command", "write_artifact"}


class Registry:
    def __init__(self, ctx, mcp=None, enabled=None, screen=False, delegate=False, advisor=False, fanout=False):
        """screen: offer the screen/mouse/keyboard tools (otherwise only enable_screen_control).
        delegate: offer delegate_task & co (never for sub-agents). advisor: offer consult_advisor."""
        self.ctx = ctx
        self.mcp = mcp
        self.by_name = {}
        self.enabled = enabled
        for name, kind, desc, params, fn in BUILTIN:
            if enabled is not None and name not in enabled and name not in (
                    "update_tracker", "enable_screen_control", "calculate", "memory_save",
                    "memory_search", "memory_update", "memory_delete", "artifact_create", "artifact_edit",
                    "artifact_read", "artifact_list", "python_sandbox", "python_host", "query_data",
                    "available_tools", "read_guide"):
                continue
            if (name in SCREEN_TOOLS and not screen) or (name == "enable_screen_control" and screen):
                continue
            if name in GROUP_DELEGATE and (not delegate or ctx.is_subagent):
                continue
            if name == "consult_advisor" and not advisor:
                continue
            if name in ("gather", "wait_for_workers") and (not fanout or ctx.is_subagent):
                continue
            if getattr(ctx, "web_off", False) and is_web_tool(name):
                continue
            self._add(name, kind, desc, params, fn)
        ctx.enable_screen = self.add_screen
        if mcp:
            for server, t in mcp.tools():
                full = _safe_name(f"mcp__{server}__{t.get('name', '')}")
                self.by_name[full] = {
                    "name": full, "kind": MCP, "server": server, "tool": t.get("name"),
                    "description": f"[{server}] " + (t.get("description") or t.get("name") or ""),
                    "parameters": t.get("inputSchema") or _obj({}),
                    "read_only": bool((t.get("annotations") or {}).get("readOnlyHint")),
                    "fn": None}

    def _add(self, name, kind, desc, params, fn):
        self.by_name[name] = {"name": name, "kind": kind, "description": desc, "parameters": params,
                              "fn": fn}

    def add_screen(self):
        added = set()
        for name, kind, desc, params, fn in BUILTIN:
            if name in SCREEN_TOOLS and name not in self.by_name and not (getattr(self.ctx, "web_off", False)
                                                                          and is_web_tool(name)):
                self._add(name, kind, desc, params, fn)
                added.add(name)
        self.by_name.pop("enable_screen_control", None)
        return added

    def schemas(self, names=None):
        out = []
        for t in self.by_name.values():
            if names is not None and t["name"] not in names:
                continue
            params = t["parameters"]
            if t["name"] not in PROGRESS_EXEMPT and isinstance(params, dict) and params.get("type") == "object":
                params = dict(params, properties={**(params.get("properties") or {}), "progress": PROGRESS_FIELD})
            out.append({"type": "function", "function": {"name": t["name"], "description": t["description"][:900],
                                                         "parameters": params}})
        return out

    def canonical(self, name):
        """The registered name for what the model asked for (exact, case-insensitive, or alias)."""
        if name in self.by_name:
            return name
        low = str(name or "").strip().lower()
        for n in self.by_name:
            if n.lower() == low:
                return n
        alias = ALIASES.get(low.replace("-", "_"))
        return alias if alias in self.by_name else name

    def normalize_args(self, name, args):
        if not isinstance(args, dict):
            return args
        t = self.by_name.get(name)
        props = set(((t or {}).get("parameters") or {}).get("properties") or {})
        out = {}
        for k, v in args.items():
            out.setdefault(ARG_ALIASES.get(k, k) if k not in props else k, v)
        return out

    def get(self, name):
        return self.by_name.get(self.canonical(name))

    def call(self, name, args):
        """-> (text, png_bytes|None). Raises ToolError for problems the model can fix."""
        name = self.canonical(name)
        if getattr(self.ctx, "web_off", False) and is_web_tool(name):
            raise ToolError(WEB_OFF_MSG)
        t = self.by_name.get(name)
        if not t:
            raise ToolError(f"unknown tool '{name}'")
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        args = self.normalize_args(name, args)
        self.ctx.display = None
        missing = [k for k in (t["parameters"].get("required") or []) if k not in args]
        if missing:
            raise ToolError(f"missing required argument(s): {', '.join(missing)}")
        if t["kind"] == MCP:
            try:
                text, is_err = self.mcp.call(t["server"], t["tool"], args)
            except MCPError as e:
                raise ToolError(str(e))
            return clip(("[the tool reported an error]\n" if is_err else "") + text), None
        appending = name == "write_file" and args.get("append") and args.get("path")
        if appending:          # an append doesn't show the agent the file: it stays "known" only if it already was
            try:
                appending = (_seen_current(self.ctx, self.ctx.resolve(args.get("path")))
                             or not os.path.exists(self.ctx.resolve(args.get("path"))))
            except Exception:
                appending = False
        if name == "web_fetch" and re.search(r"^\s*(https?://)?(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])\b",
                                             " ".join(str(u) for u in ([args.get("url")] + list(args.get("urls") or []))
                                                      if u), re.I):
            raise ToolError("web_fetch reads internet pages (it extracts text and caches by URL). For a server on "
                            "this PC use http_request(method, url, json=...) — it shows the real status, headers and "
                            "body, and never returns a cached answer.")
        if name in _STUB_TOOLS and any(isinstance(v, str) and _HISTORY_STUB.search(v) for v in args.values()):
            raise ToolError("this call contains '[N chars omitted — already applied …]'. That is a placeholder the "
                            "harness puts in your conversation history in place of a long earlier tool argument; it "
                            "is not file content, and writing it would replace the real text. Nothing was changed. "
                            "Read the file with read_file to see its current text, then send the actual content.")
        res = t["fn"](args, self.ctx)
        if (name in ("read_file", "edit_file", "undo_file") or (name == "write_file" and
                (not args.get("append") or appending))) and args.get("path"):
            try:
                note_seen(self.ctx, self.ctx.resolve(args.get("path")))
            except Exception:
                pass
        text, image = (res[0], res[1]) if isinstance(res, tuple) else (res, None)
        try:
            if looks_green(name, args, str(text)):
                mark_green(self.ctx)                  # tests passed: the baseline for changes_since_green
            else:
                mark_progress(self.ctx, name, args, str(text))   # or more of them pass than ever before
            cmd_blob = json.dumps(args or {}).lower()
            if name == "run_tests" or (name in ("powershell", "python_host") and re.search(r"test|pytest|unittest", cmd_blob)):
                p_, f_ = test_counts(str(text))
                if p_ or f_:                                     # the last test run, for the work-state panel
                    _hist(self.ctx)["last_tests"] = {"passed": p_, "failed": f_, "t": time.strftime("%H:%M:%S"),
                                                     "cmd": str(args.get("command") or args.get("path") or name)[:80]}
        except Exception:
            pass
        text = self._debug_aids(name, args, str(text))
        return self._keep_result(name, text), image

    RUN_TOOLS = {"powershell", "python_host", "python_sandbox", "bg_output", "trace_call", "debug_run"}
    HEAD_TAIL = {"run_tests", "http_request", "browser_devtools", "browser_eval"}   # the end matters too

    def _debug_aids(self, name, args, text):
        """Command output: an '[Error located]' note for a traceback/stack in project code, and the long-output clip
        keeps the start AND the end. File writes: an automatic code check (syntax, undefined names)."""
        try:
            import debug_tools as dbg
            if name in self.RUN_TOOLS:
                return dbg.clip_ht(dbg.enrich(text, getattr(self.ctx, "cwd", None)))
            if name in ("write_file", "edit_file") and not text.startswith("Error"):
                p = self.ctx.resolve(args.get("path"))
                d = getattr(self.ctx, "display", None) or {}
                bk = d.get("backup") if d.get("type") == "diff" and d.get("path") == p else None
                return text + dbg.lint_file(p, before=bk)        # with the backup: only problems NEW in this change
        except Exception as e:                              # a helper must never break the tool itself
            print("debug aids failed:", repr(e))
        return text

    SAVE_RESULTS = {"gather", "read_document", "web_fetch", "browser_read", "browser_links", "query_data", "search_documents",
                    "memory_search", "web_search", "artifact_read", "bg_output", "task_status", "wait_task"}

    # curated reference text the model asks for on purpose: a guide cut at MAX_RESULT lost its last sections
    # (software.md is 11 KB, hard_search_techniques.md 15 KB) and the tool catalog lost its last groups
    RESULT_LIMITS = {"read_guide": 16000, "available_tools": 12000}

    def _keep_result(self, name, text):
        """Long results of reading tools are saved whole to the chat's folder, so the model can hand them to code
        (python_sandbox files=[...]) instead of re-typing them — a model has no copy & paste."""
        if name in self.RESULT_LIMITS:
            return clip(text, self.RESULT_LIMITS[name])
        if name in self.HEAD_TAIL and len(text) > MAX_RESULT:
            try:
                import debug_tools as dbg
                return dbg.clip_ht(text)
            except Exception:
                pass
        if name not in self.SAVE_RESULTS or len(text) < 3000 or text.startswith(("Error", "[blocked]")):
            return clip(text)
        conv = getattr(self.ctx, "conv_id", None) or "misc"
        base = paths.data_dir()
        folder = os.path.join(base, "agent_attachments", _safe_name(conv), "results")
        try:
            os.makedirs(folder, exist_ok=True)
            with _SAVE_LOCK:                                # fan-out workers save at the same time
                n = len([f for f in os.listdir(folder) if f.endswith(".txt")]) + 1
                path = os.path.join(folder, f"r{n}_{name}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            parent = os.path.dirname(folder)
            if not self.ctx.inside(path) and parent not in self.ctx.roots:
                self.ctx.roots.append(parent)              # the chat's own folder is always usable
        except OSError:
            return clip(text)
        note = (f"[Full result ({len(text):,} characters) saved at {path} — to use this data in code, pass that path "
                f"in python_sandbox files=[...] instead of copying text.]\n")
        return note + clip(text)

    def preview(self, name, args):
        """What the chat card shows before the call runs (or while it waits for approval)."""
        name = self.canonical(name)
        a = self.normalize_args(name, args or {})
        if name in ("python_sandbox", "python_host"):
            return {"type": "python", "code": str(a.get("code", ""))[:20000], "output": "", "images": [], "files": [],
                    "sandbox": name == "python_sandbox", "running": True}
        if name == "powershell":
            return {"type": "terminal", "shell": (a.get("shell") or "powershell").lower(),
                    "cwd": a.get("cwd") or shell_cwd(self.ctx), "command": str(a.get("command", "")),
                    "output": "", "exit": None}
        if name in ("write_file", "edit_file"):
            try:
                p = self.ctx.resolve(a.get("path"))
                old = _read_text(p) if os.path.isfile(p) else ""
                if name == "write_file":
                    new = old + str(a.get("content", "")) if a.get("append") else str(a.get("content", ""))
                else:
                    o, n = str(a.get("old", "")), str(a.get("new", ""))
                    if o and o not in old and "\r\n" in old:
                        o, n = o.replace("\n", "\r\n"), n.replace("\n", "\r\n")
                    if not o or o not in old:
                        return {"type": "pending_file", "path": p}
                    new = old.replace(o, n) if a.get("replace_all") else old.replace(o, n, 1)
                diff, add, rem = make_diff(old, new, p)
                return {"type": "diff", "pending": True, "path": p, "diff": diff, "added": add,
                        "removed": rem, "created": not os.path.isfile(p)}
            except Exception:
                return {"type": "pending_file", "path": str(a.get("path", ""))}
        if name in ("read_file", "read_document"):
            return {"type": "pending_file", "path": str(a.get("path", ""))}
        if name == "bg_start":
            return {"type": "bg", "id": None, "name": a.get("name") or str(a.get("command", ""))[:60],
                    "command": str(a.get("command", "")), "cwd": a.get("cwd") or shell_cwd(self.ctx)}
        return None

    def describe(self, name, args):
        """Human-readable summary of what a call will do (shown in the approval dialog)."""
        name = self.canonical(name)
        t = self.by_name.get(name) or {}
        a = self.normalize_args(name, args or {})
        if name == "powershell":
            prompt = "C:\\>" if (a.get("shell") or "").lower() == "cmd" else "PS"
            return f"{prompt} {a.get('cwd') or shell_cwd(self.ctx)}> {a.get('command')}"
        if name in ("write_file", "edit_file"):
            try:
                p = self.ctx.resolve(a.get("path"))
            except ToolError as e:
                return f"{name}: {a.get('path')}\n\n✗ {e}"
            old = _read_text(p) if os.path.isfile(p) else ""
            if name == "write_file":
                new = old + str(a.get("content", "")) if a.get("append") else str(a.get("content", ""))
                head = f"{'Create' if not os.path.isfile(p) else 'Overwrite' if not a.get('append') else 'Append to'} {p}"
            else:
                o, n = str(a.get("old", "")), str(a.get("new", ""))
                if o and o not in old and "\r\n" in old:
                    o, n = o.replace("\n", "\r\n"), n.replace("\n", "\r\n")
                if not o or o not in old:
                    return f"Edit {p}\n\n(the text to replace was not found; this will fail)"
                new = old.replace(o, n) if a.get("replace_all") else old.replace(o, n, 1)
                head = f"Edit {p}"
            diff, add, rem = make_diff(old, new, p, max_lines=80)
            return f"{head}   (+{add} -{rem})\n\n{diff}"
        if name in ("move_path",):
            return f"Move {a.get('src')}\n  to {a.get('dst')}"
        if name == "trash_path":
            return f"Move to the agent trash: {a.get('path')}"
        if name == "make_dir":
            return f"Create folder {a.get('path')}"
        if name == "type_text":
            return f"Type into the focused window:\n{clip(a.get('text', ''), 500)}"
        if name == "press_keys":
            return f"Press keys: {a.get('keys')}"
        if name in ("mouse_click", "mouse_move", "mouse_drag", "scroll", "focus_window"):
            return f"{name.replace('_', ' ')}: {json.dumps(a)}  (this controls your real mouse/windows)"
        if t.get("kind") == MCP:
            return f"MCP {t.get('server')} → {t.get('tool')}\n{clip(json.dumps(a, ensure_ascii=False, indent=2), 900)}"
        return f"{name}: {json.dumps(a, ensure_ascii=False)}"
