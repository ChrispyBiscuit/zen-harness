"""Debugging help for the agent: errors located, code checked on write, tests run, failures inspected.

- clip_ht: long command output keeps its start AND its end (tracebacks and compiler errors are at the end).
- enrich: a Python traceback or a Node stack in some output gets an "[Error located]" note: the innermost frame in
  the project's own code (not the standard library or installed packages) with the source lines around it.
- lint_file: a static check after every write/edit — Python syntax + ruff's serious-error rules (undefined names,
  syntax errors, misused statements), JavaScript syntax (node --check, also inline <script> blocks in HTML), JSON.
- run_tests / debug_run: the run_tests and debug_run tools (pytest / node test runners; a script run with a
  post-mortem of the failing frame's local variables, or a probe that prints locals each time a line is reached).
"""
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

NO_WINDOW = 0x08000000
HEADLESS_ENV = {"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "QT_QPA_PLATFORM": "offscreen",
                "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8", "PYGAME_HIDE_SUPPORT_PROMPT": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}


# ----------------------------------------------------------------------------- output clipping
def clip_ht(s, n=7600, head=0.3):
    """Keep the first ~30% and the last ~70% of a long output: the command and its setup at the top, the result
    and any error at the bottom."""
    s = str(s)
    if len(s) <= n:
        return s
    h = int(n * head)
    t = n - h
    cut = len(s) - h - t
    return s[:h] + f"\n...[{cut:,} characters of output omitted from the middle]...\n" + s[-t:]


# ----------------------------------------------------------------------------- locating errors
_PY_TB = "Traceback (most recent call last):"
_PY_FRAME = re.compile(r'^\s*File "(?P<f>[^"]+)", line (?P<l>\d+)(?:, in (?P<fn>[^\n]+))?', re.M)
_JS_AT = re.compile(r"^\s*at (?:(?P<fn>[^\n(]+?) \()?(?:file:///)?(?P<f>(?:[A-Za-z]:)?[\\/][^\n():]+?):(?P<l>\d+):\d+\)?\s*$",
                    re.M)
_NODE_CHECK = re.compile(r"^(?P<f>(?:[A-Za-z]:)?[\\/][^\n]+?\.(?:m?js|cjs)):(?P<l>\d+)\s*$", re.M)
_EXC_LINE = re.compile(r"^(?:[\w.]+\.)?\w*(?:Error|Exception|Exit|Interrupt|Warning|Failure)\b.*$|^\w+: .+$", re.M)


def _is_lib(path):
    p = (path or "").replace("\\", "/").lower()
    if not p or p.startswith(("<", "node:")) or "/node_modules/" in p or "site-packages" in p or "dist-packages" in p:
        return True
    for base in {sys.base_prefix, sys.prefix, os.path.dirname(shutil.which("python") or "") or "~"}:
        b = base.replace("\\", "/").lower().rstrip("/")
        if b and p.startswith(b + "/lib/"):
            return True
    return "/lib/python3" in p or p.endswith("/runpy.py")


def _resolve(path, cwd):
    p = path.strip()
    if p.startswith("<"):                        # <frozen runpy>, <string>, <debug_run>: not a file
        return p
    if p.startswith("file:///"):
        p = p[8:]
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def _source(path, line, ctx=3):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    lo, hi = max(1, line - ctx), min(len(lines), line + ctx)
    w = len(str(hi))
    return "\n".join(f"{'>' if i == line else ' '} {i:>{w}} | {lines[i - 1]}" for i in range(lo, hi + 1))


_PYTEST_FRAME = re.compile(r"^(?P<f>[^\s:][^:\n]*?\.py):(?P<l>\d+): in (?P<fn>\S+)", re.M)
_PYTEST_E = re.compile(r"^E\s+(?P<e>\S.*)$", re.M)


def locate(text, cwd=None):
    """-> (exception line, [(file, line, fn)] project frames innermost last) for the LAST error in the text
    (pytest output: its FIRST failure)."""
    text = text or ""
    e = _PYTEST_E.search(text)
    if e and _PYTEST_FRAME.search(text[:e.start()]):
        start = text.rfind("\n___", 0, e.start())             # the failing test's section header
        frames = [(_resolve(m["f"], cwd), int(m["l"]), m["fn"]) for m in _PYTEST_FRAME.finditer(text, max(0, start), e.start())]
        if frames:
            return e["e"].strip(), [f for f in frames if not _is_lib(f[0])] or frames[-1:]
    i = text.rfind(_PY_TB)
    if i >= 0:
        block = text[i:]
        frames = [(_resolve(m["f"], cwd), int(m["l"]), (m["fn"] or "").strip()) for m in _PY_FRAME.finditer(block)]
        after = block[block.rfind("File \""):] if frames else block
        exc = next((ln.strip() for ln in after.splitlines()[1:] if _EXC_LINE.match(ln.strip())), "")
        return exc, [f for f in frames if not _is_lib(f[0])] or frames[-1:]
    m = None
    for m in _NODE_CHECK.finditer(text):         # node --check: "C:\x\a.js:12" then the code, a caret, the error
        pass
    if m:
        rest = text[m.end():]
        exc = next((ln.strip() for ln in rest.splitlines() if re.match(r"^\w*Error\b", ln.strip())), "")
        return exc, [(_resolve(m["f"], cwd), int(m["l"]), "")]
    ats = list(_JS_AT.finditer(text))
    if ats:
        first = ats[0]
        head = text[:first.start()].rstrip().splitlines()
        exc = next((ln.strip() for ln in reversed(head) if re.search(r"\w*Error\b|Exception", ln)), "")
        frames = [(_resolve(a["f"], cwd), int(a["l"]), (a["fn"] or "").strip()) for a in ats]
        proj = [f for f in frames if not _is_lib(f[0])]
        return exc, list(reversed(proj or frames[:1]))       # node lists innermost first
    return "", []


def _function_source(path, line, max_lines=45):
    """The whole Python function (or method) that contains `line`, numbered, the line marked '>' — '' if none."""
    if not str(path).lower().endswith(".py"):
        return ""
    try:
        import ast
        src = open(path, encoding="utf-8", errors="replace").read()
        best = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node                               # the innermost one
        if best is None:
            return ""
        lines = src.splitlines()
        lo, hi = best.lineno, best.end_lineno or best.lineno
        if hi - lo + 1 > max_lines:                            # a long function: the part around the line
            lo, hi = max(best.lineno, line - max_lines // 2), min(hi, line + max_lines // 2)
        return "\n".join(f"{'>' if i == line else ' '} {i} | {lines[i - 1]}" for i in range(lo, hi + 1))
    except Exception:
        return ""


def enrich(text, cwd=None):
    """Text plus an '[Error located]' note when it contains a traceback or stack with a frame in project code."""
    try:
        exc, frames = locate(text, cwd)
    except Exception:
        return text
    if not frames:
        return text
    f, line, fn = frames[-1]
    src = _source(f, line)
    if not src:
        return text
    chain = ""
    if len(frames) > 1:
        chain = "\nCalled from: " + " ← ".join(f"{os.path.basename(a)}:{b}{f' {c}()' if c else ''}"
                                               for a, b, c in reversed(frames[:-1][-4:]))
    whole = _function_source(f, line)               # the whole failing function beats ±3 lines
    caller = ""
    if len(frames) > 1:
        cf, cl, cfn = frames[-2]
        cs = _source(cf, cl)
        if cs:
            caller = f"\nCalled from {os.path.basename(cf)}:{cl}{f' in {cfn}()' if cfn else ''}:\n{cs}"
    note = (f"\n\n[Error located] {exc or 'error'}\nRaised at {f}:{line}{f' in {fn}()' if fn else ''}{chain}\n"
            + (whole if whole else src) + caller)
    return text + note


# ----------------------------------------------------------------------------- static checks on write
LINTABLE = (".py", ".js", ".mjs", ".cjs", ".html", ".htm", ".json")
_SCRIPT_RX = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)


def _run_quiet(argv, timeout=15, cwd=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=cwd, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)


def _node_check(js_path):
    if not shutil.which("node"):
        return None
    rc, out = _run_quiet(["node", "--check", js_path])
    if rc in (None, 0):
        return None
    m = re.search(r":(\d+)\s*\n", out)
    err = next((ln.strip() for ln in out.splitlines() if re.match(r"^\w*Error\b", ln.strip())), out.strip()[:200])
    return (int(m.group(1)) if m else 0), err


def _undefined_name_checker():
    """The first available checker for undefined names -> argv prefix, or None. ruff on PATH, then ruff / pyflakes
    for the system Python, then for this app's Python. None means only syntax is checked (and the report says so —
    it used to claim "no problems" while nothing had checked the names)."""
    if shutil.which("ruff"):
        return [shutil.which("ruff"), "check", "--isolated", "--no-cache", "--select", "E9,F63,F7,F82",
                "--output-format", "concise"]
    import paths
    for py in [paths.python_cmd()]:
        for mod, args in (("ruff", ["check", "--isolated", "--no-cache", "--select", "E9,F63,F7,F82",
                                    "--output-format", "concise"]), ("pyflakes", [])):
            rc, out = _run_quiet([*py, "-m", mod, "--version"], timeout=10)
            if rc == 0:
                return [*py, "-m", mod] + args
    return None


_CHECKER = []                     # cached result of _undefined_name_checker (a one-item list once looked up)


def problems(path):
    """-> (what was checked, ["file:line: message", ...]) or None when this file type isn't checked."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in LINTABLE or not os.path.isfile(path):
        return None
    probs = []
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        return None
    if ext == ".py":
        import ast
        try:
            ast.parse(src, filename=path)
        except SyntaxError as e:
            probs.append(f"{name}:{e.lineno}: SyntaxError: {e.msg}")
        if not _CHECKER:
            _CHECKER.append(_undefined_name_checker())
        checker = _CHECKER[0]
        if not probs and checker:
            # serious errors only: syntax, undefined names, misused statements (not style or unused imports)
            rc, out = _run_quiet(checker + [path])
            if "pyflakes" in " ".join(checker):
                out = "\n".join(ln for ln in out.splitlines() if "undefined name" in ln or "syntax" in ln.lower())
            for ln in out.splitlines():
                m = re.match(r"^(?:[A-Za-z]:)?[^:]*:(\d+):(?:\d+:)?\s*(.+)$", ln.strip())
                if m and not ln.startswith(("Found ", "All checks")):
                    probs.append(f"{name}:{m.group(1)}: {m.group(2).strip()}")
        what = "Python syntax + undefined names" if checker else "Python syntax only (no undefined-name checker found)"
    elif ext in (".js", ".mjs", ".cjs"):
        r = _node_check(path)
        if r:
            probs.append(f"{name}:{r[0]}: {r[1]}")
        what = "JavaScript syntax"
        if not shutil.which("node"):
            return None
    elif ext in (".html", ".htm"):
        if not shutil.which("node"):
            return None
        for m in _SCRIPT_RX.finditer(src):
            attrs = m.group("attrs").lower()
            if "src=" in attrs or re.search(r"type\s*=\s*[\"']?(?!text/javascript|module|application/javascript)\w", attrs):
                continue
            start = src[:m.start("body")].count("\n")           # lines before the script body
            suffix = ".mjs" if "module" in attrs else ".js"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="agent_check_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(m.group("body"))
            try:
                r = _node_check(tmp)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if r:
                probs.append(f"{name}:{start + r[0]}: {r[1]} (inline <script>)")
        what = "inline JavaScript syntax"
    else:
        try:
            json.loads(src)
        except ValueError as e:
            probs.append(f"{name}:{getattr(e, 'lineno', 0)}: invalid JSON: {getattr(e, 'msg', e)}")
        what = "JSON syntax"
    return what, probs


def _key(p):
    """A problem without its file and line, so the same problem after lines moved still counts as old."""
    return re.sub(r"^[^:]*:\d+:\s*", "", p).strip()


_NOFAIL_RX = re.compile(r"^\s*(assert\s+(True|1)\b|\w*check\w*\([^)\n]*,\s*True\s*\))", re.M)


def _cannot_fail(path, text):
    """Test lines that can never fail (assert True, check("x", True)) — a test that always passes hides bugs."""
    if not re.search(r"(^|[\\/])(test_[^\\/]*|[^\\/]*_test)\.py$|[\\/]tests?[\\/]", path, re.I):
        return []
    out = []
    for m in _NOFAIL_RX.finditer(text):
        ln = text[:m.start()].count("\n") + 1
        out.append(f"{os.path.basename(path)}:{ln}: this test line can never fail ({m.group(1).strip()[:60]}) — "
                   f"check a real result instead")
    return out[:5]


def lint_file(path, before=None):
    """The code check after a write/edit, plus a warning for test lines that can never fail."""
    out = _lint_core(path, before)
    try:
        extra = _cannot_fail(path, open(path, encoding="utf-8", errors="replace").read())
    except OSError:
        extra = []
    return out + ("\nTest check: " + "; ".join(extra) if extra else "")


def _lint_core(path, before=None):
    """-> a short report ('' when the file type isn't checked). With `before` (a copy of the file as it was before
    this change), only problems NEW in this change are listed; older ones are counted, so a small model fixes what it
    just broke instead of chasing problems that were already there."""
    r = problems(path)
    if r is None:
        return ""
    what, probs = r
    old = problems(before)[1] if before and os.path.isfile(before) and problems(before) else None
    if old is not None and not probs:
        return f"\nCode check: no problems ({what})" + (f"; this change fixed {len(old)}." if old else ".")
    if old is not None:
        left = {}
        for p in old:
            left[_key(p)] = left.get(_key(p), 0) + 1
        new = []
        for p in probs:
            k = _key(p)
            if left.get(k, 0) > 0:
                left[k] -= 1
            else:
                new.append(p)
        kept = len(probs) - len(new)
        if new:
            return ("\nCode check FOUND " + str(len(new)) + " NEW PROBLEM" + ("S" if len(new) > 1 else "") +
                    " from this change (" + what + "):\n" + "\n".join("  " + p for p in new[:12]) +
                    (f"\n({kept} older problem{'s were' if kept != 1 else ' was'} already there before this "
                     f"change; not listed.)" if kept else "") + "\nFix these before running it.")
        return (f"\nCode check: no new problems ({what})" +
                (f"; {kept} older problem{'s were' if kept != 1 else ' was'} already there." if kept else "."))
    if probs:
        return ("\nCode check FOUND PROBLEMS (" + what + "):\n" + "\n".join("  " + p for p in probs[:12])
                + "\nFix these before running it.")
    return f"\nCode check: no problems ({what})."


# ----------------------------------------------------------------------------- running processes
def _stream(argv, cwd, timeout, c, env=None):
    """Run a process, stream its output into the chat card, honour Stop -> (rc, text, seconds, why_stopped)."""
    try:
        import stackdump
        e = stackdump.env()                  # unbuffered + stack dumps on request (see the timeout below)
    except Exception:
        e = dict(os.environ)
    e.update(HEADLESS_ENV)
    if env:
        e.update(env)
    t0 = time.time()
    stuck_at = ""
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=e, creationflags=NO_WINDOW)
    q = queue.Queue()

    def pump():
        for raw in iter(proc.stdout.readline, b""):
            q.put(raw)
        q.put(None)
    threading.Thread(target=pump, daemon=True).start()
    out, pending, last, why, done = [], [], 0.0, None, False

    def owned():                             # the agent's running servers: a test waiting on a busy one isn't stuck
        try:
            import bg_tasks
            return [p for p in bg_tasks.MANAGER.procs.values()
                    if p.running and p.proc is not None and p.owner == getattr(c, "conv_id", None)]
        except Exception:
            return []
    try:
        import stackdump
        watch = stackdump.StallWatch(proc.pid, lambda: [p.proc.pid for p in owned()], quiet=10.0)
    except Exception:
        watch = None
    while not done:
        try:
            raw = q.get(timeout=0.1)
        except queue.Empty:
            raw = ""
        if raw is None:
            done = True
        elif raw:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            out.append(line)
            pending.append(line)
            if watch:
                watch.output()
        if pending and (done or time.time() - last > 0.15):
            try:
                c.emit("tool_output", id=c.call_id, text="\n".join(pending) + "\n")
            except Exception:
                pass
            pending, last = [], time.time()
        if not done and why is None:
            if getattr(c, "should_stop", lambda: False)():
                why = "stopped by the user"
            elif time.time() - t0 > timeout:
                why = f"timed out after {timeout}s"
                try:
                    import stackdump
                    stuck_at = stackdump.dump(proc.pid, project_hint=cwd or "")
                except Exception:
                    stuck_at = ""
                if stuck_at:
                    out.append("[" + stuck_at + "]")
            elif watch and proc.poll() is None and watch.blocked():
                # blocked, not working: stop now with where everything waits, instead of at the timeout
                why = (f"BLOCKED after {round(time.time() - t0)}s — no output and no CPU, disk or network activity "
                       f"for {watch.idle_for()}s: it was WAITING (a lock, a socket, input, a server), not working")
                try:
                    import stackdump
                    s = stackdump.dump(proc.pid, project_hint=cwd or "")
                    if s and not s.startswith("(no stack"):
                        out.append("[THE TEST RUN — " + s + "]")
                    for p in owned():
                        s = stackdump.dump(p.proc.pid, project_hint=p.cwd or cwd or "")
                        if s and not s.startswith("(no stack"):
                            out.append(f"[YOUR BACKGROUND TASK {p.id} ({p.name}) — " + s + "]")
                except Exception:
                    pass
            if why:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                                   creationflags=NO_WINDOW)
                except OSError:
                    proc.kill()
    rc = proc.wait()
    return rc, "\n".join(out).strip("\n"), round(time.time() - t0, 1), why


def run_tests(a, c, resolve, ToolError):
    """The run_tests tool: pytest for Python, node's test runner or npm test for JavaScript."""
    target = resolve(a.get("path"), True) if a.get("path") else (c.cwd or c.roots[0])
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    fw = str(a.get("framework") or "auto").lower()
    timeout = max(5, min(int(a.get("timeout") or 180), 900))
    import paths
    py = paths.python_cmd()
    pkg = os.path.join(folder, "package.json")
    if fw == "auto":
        if target.lower().endswith((".js", ".mjs", ".cjs", ".ts")):
            fw = "node"
        elif os.path.isfile(pkg) and '"test"' in open(pkg, encoding="utf-8", errors="replace").read():
            fw = "npm"
        else:
            fw = "pytest"
    if fw == "pytest":
        argv = [*py, "-m", "pytest", *(a.get("_ids") or [target]), "-q", "--tb=short", "--color=no", "-p", "no:cacheprovider",
                "--maxfail", str(int(a.get("max_failures") or 10))]
        if a.get("filter"):
            argv += ["-k", str(a["filter"])]
    elif fw == "node":
        if not shutil.which("node"):
            raise ToolError("node is not installed")
        argv = ["node", "--test", target]
    elif fw == "npm":
        argv = [shutil.which("npm") or "npm", "test", "--silent"]
    else:
        raise ToolError("framework must be auto, pytest, node or npm")
    c.display = {"type": "terminal", "shell": "tests", "cwd": folder, "command": " ".join(os.path.basename(x) if i == 0 else x
                                                                                          for i, x in enumerate(argv)),
                 "output": "", "exit": None}
    rc, text, secs, why = _stream(argv, folder, timeout, c)
    c.display.update(output=clip_ht(text, 30000), exit=rc, seconds=secs, stopped=why)
    summary = ""
    if fw == "pytest":
        m = re.findall(r"^=*\s*((?:\d+ \w+(?:, )?)+) in [\d.]+s", text, re.M)
        summary = m[-1] if m else ""
        if rc == 5:
            summary = ("no tests were collected: pytest looks for files named test_*.py or *_test.py with functions "
                       "named test_* (plain assert statements are fine)")
        elif "No module named pytest" in text:
            summary = "pytest is not installed for this Python (pip install pytest), or use framework='node'/'npm'"
    elif fw == "node":
        p, f = re.search(r"# pass (\d+)", text), re.search(r"# fail (\d+)", text)
        summary = f"{p.group(1) if p else '?'} passed, {f.group(1) if f else '?'} failed" if (p or f) else ""
    head = f"tests: {'PASSED' if rc == 0 else 'FAILED' if rc == 1 else f'exit code {rc}'}" + (f" — {summary}" if summary else "")
    body = enrich(text, folder)
    return clip_ht(f"{head}  ({secs}s, {' '.join(argv[1:4]) if fw != 'npm' else 'npm test'})\n{body}"
                   + (f"\n[{why}; the process was killed]" if why else ""))


_DEBUG_WRAPPER = r'''
import os, reprlib, runpy, sys, traceback
SCRIPT, ARGS, PROBE_FILE, PROBE_LINE, WATCH, MAXH, ROOT = __PARAMS__
R = reprlib.Repr(); R.maxstring = 160; R.maxother = 160; R.maxlist = 10; R.maxdict = 10; R.maxlevel = 3
def show(frame):
    rows = []
    for k, v in list(frame.f_locals.items())[:40]:
        if k.startswith("__") or type(v).__name__ in ("module", "function", "type", "builtin_function_or_method"):
            continue
        try:
            rows.append(f"    {k} = {R.repr(v)}")
        except Exception as e:
            rows.append(f"    {k} = <cannot show: {type(e).__name__}>")
    return "\n".join(rows) or "    (no local variables)"
def watch(frame):
    for ex in WATCH:
        try:
            print(f"    {ex} -> {R.repr(eval(ex, frame.f_globals, frame.f_locals))}", flush=True)
        except Exception as e:
            print(f"    {ex} -> {type(e).__name__}: {e}", flush=True)
HITS = [0]
def tracer(frame, event, arg):
    if HITS[0] >= MAXH:
        return None
    if os.path.normcase(os.path.abspath(frame.f_code.co_filename)) != PROBE_FILE:
        return None if event == "call" else tracer
    if event == "line" and frame.f_lineno == PROBE_LINE:
        HITS[0] += 1
        print(f"\n[probe] {os.path.basename(PROBE_FILE)}:{PROBE_LINE} reached (hit {HITS[0]}) in {frame.f_code.co_name}():", flush=True)
        print(show(frame), flush=True)
        watch(frame)
    return tracer
sys.argv = [SCRIPT] + ARGS
sys.path.insert(0, os.path.dirname(SCRIPT))
if PROBE_FILE:
    sys.settrace(tracer)
try:
    runpy.run_path(SCRIPT, run_name="__main__")
    sys.settrace(None)
    if PROBE_FILE and not HITS[0]:
        print(f"\n[probe] {os.path.basename(PROBE_FILE)}:{PROBE_LINE} was never reached", flush=True)
except SystemExit as e:
    sys.settrace(None)
    if PROBE_FILE and not HITS[0]:
        print(f"\n[probe] {os.path.basename(PROBE_FILE)}:{PROBE_LINE} was never reached", flush=True)
    raise
except BaseException as e:
    sys.settrace(None)
    traceback.print_exc()
    tbs, tb = [], e.__traceback__
    while tb:
        tbs.append(tb)
        tb = tb.tb_next
    own = [t for t in tbs if os.path.normcase(os.path.abspath(t.tb_frame.f_code.co_filename)).startswith(ROOT)]
    t = (own or tbs)[-1]
    print(f"\n[post-mortem] local variables in {t.tb_frame.f_code.co_name}() at {t.tb_frame.f_code.co_filename}:{t.tb_lineno} when it failed:", flush=True)
    print(show(t.tb_frame), flush=True)
    watch(t.tb_frame)
    sys.exit(1)
'''


def _drop_wrapper_frames(text):
    """Tracebacks from debug_run: leave out the wrapper's and runpy's own frames (noise, not the user's code)."""
    out, skip = [], False
    for ln in text.splitlines():
        if re.match(r'^\s*File "<(?:debug_run|frozen runpy)>"', ln):
            skip = True
            continue
        if skip and ln.startswith("    ") and not ln.lstrip().startswith("File "):
            continue
        skip = False
        out.append(ln)
    return "\n".join(out)


def debug_run(a, c, resolve, ToolError):
    """The debug_run tool: run a Python script with a post-mortem (local variables of the failing frame), or with a
    probe that prints the local variables every time a given line is reached."""
    script = resolve(a.get("script") or a.get("path"), True)
    if not script.lower().endswith(".py"):
        raise ToolError("debug_run runs a Python script (.py); for JavaScript in a page use browser_eval")
    folder = resolve(a.get("cwd"), True) if a.get("cwd") else os.path.dirname(script)
    probe_file, probe_line = "", 0
    if a.get("probe"):
        m = re.match(r"^(.*?):(\d+)$", str(a["probe"]).strip())
        if not m:
            raise ToolError("probe must be 'file.py:LINE', e.g. 'game/player.py:42'")
        rel = m.group(1)
        cands = [rel] if os.path.isabs(rel) else [os.path.join(b, rel) for b in
                                                  (getattr(c, "cwd", None), folder, os.path.dirname(script),
                                                   os.path.dirname(os.path.dirname(script))) if b]
        try:
            cands.append(resolve(rel))
        except Exception:
            pass
        pf = next((x for x in cands if os.path.isfile(x)), None)
        if not pf:
            raise ToolError(f"probe file not found: {rel} (give a path relative to the working folder, or absolute)")
        probe_file, probe_line = os.path.normcase(os.path.abspath(pf)), int(m.group(2))
    watch = a.get("watch") or []
    watch = [watch] if isinstance(watch, str) else [str(w) for w in watch][:8]
    args = a.get("args") or []
    args = str(args).split() if isinstance(args, str) else [str(x) for x in args]
    params = repr((script, args, probe_file, probe_line, watch, max(1, min(int(a.get("max_hits") or 3), 20)),
                   os.path.normcase(os.path.abspath(folder))))
    fd, wrapper = tempfile.mkstemp(suffix=".py", prefix="agent_debug_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_DEBUG_WRAPPER.replace("__PARAMS__", params))
    timeout = max(5, min(int(a.get("timeout") or 120), 600))
    import paths
    py = paths.python_cmd()
    c.display = {"type": "terminal", "shell": "debug", "cwd": folder,
                 "command": f"debug_run {os.path.basename(script)}" + (f"  probe {a['probe']}" if probe_file else ""),
                 "output": "", "exit": None}
    try:
        rc, text, secs, why = _stream([*py, "-X", "faulthandler", wrapper], folder, timeout, c)
    finally:
        try:
            os.remove(wrapper)
        except OSError:
            pass
    text = _drop_wrapper_frames(text.replace(wrapper, "<debug_run>"))
    c.display.update(output=clip_ht(text, 30000), exit=rc, seconds=secs, stopped=why)
    return clip_ht(f"exit code {rc}  ({secs}s)\n{enrich(text, folder)}" + (f"\n[{why}; the process was killed]" if why else ""))
