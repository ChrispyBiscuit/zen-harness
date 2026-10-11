"""Sandboxed Python for the agent: Pyodide (CPython compiled to WebAssembly) inside Node.js.

  - isolated: the code sees only its work folder (/work); it cannot read or change anything else on the PC, has no
    network access and cannot start programs (Node permission model + no JavaScript bridge inside Python)
  - small: ~14 MB runtime; NumPy / pandas / SciPy / matplotlib etc. are downloaded once and cached
  - persistent per conversation: variables and loaded data survive between calls
Work folders: <data>/agent_sandbox/<conv>/ . Input files are copied in; files the code writes there stay there.
A run that exceeds its time limit kills the process (the next call starts a fresh one).
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
DATA_DIR = paths.data_dir()
SANDBOX_DIR = paths.data("sandbox")
RUNNER = os.path.join(SANDBOX_DIR, "runner.mjs")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LocalAgent", "pyodide_cache")
WORK_ROOT = os.path.join(DATA_DIR, "agent_sandbox")
MAX_OUT = 6000                  # characters of output the model sees; the full output goes to a file
MAX_INPUT_BYTES = 200 * 1024 * 1024
IDLE_SHUTDOWN = 30 * 60


class SandboxError(Exception):
    pass


def available():
    return bool(shutil.which("node")) and os.path.isfile(RUNNER) and \
        os.path.isdir(os.path.join(SANDBOX_DIR, "node_modules", "pyodide"))


def work_dir(conv):
    d = os.path.join(WORK_ROOT, re.sub(r"[^\w-]", "_", conv or "misc"))
    os.makedirs(d, exist_ok=True)
    return d


class Session:
    def __init__(self, conv):
        self.conv = conv
        self.work = work_dir(conv)
        os.makedirs(CACHE_DIR, exist_ok=True)
        node = shutil.which("node")
        args = [node, "--permission",
                f"--allow-fs-read={SANDBOX_DIR}", f"--allow-fs-read={CACHE_DIR}", f"--allow-fs-read={self.work}",
                f"--allow-fs-write={CACHE_DIR}", f"--allow-fs-write={self.work}",
                "--max-old-space-size=2048", RUNNER, self.work, CACHE_DIR]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=SANDBOX_DIR, text=True, encoding="utf-8", bufsize=1,
                                     creationflags=0x08000000)
        self.lines = queue.Queue()
        self.err = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.n = 0
        first = self._read(120)
        if not first or not first.get("ready"):
            self.kill()
            raise SandboxError("the sandbox did not start: " + ("".join(self.err)[-400:] or "no output"))
        self.version = first.get("version")

    def _pump(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.lines.put(json.loads(line))
                except ValueError:
                    pass
        self.lines.put(None)

    def _pump_err(self):
        for line in self.proc.stderr:
            self.err.append(line)
            del self.err[:-50]

    def _read(self, timeout):
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            return "timeout"

    def alive(self):
        return self.proc.poll() is None

    def run(self, code, timeout=120, preload="", on_output=None):
        with self.lock:
            self.n += 1
            rid = f"{int(time.time())}_{self.n}"
            self.last_used = time.time()
            self.proc.stdin.write(json.dumps({"id": rid, "code": code, "preload": preload}) + "\n")
            self.proc.stdin.flush()
            end = time.time() + timeout
            while True:
                r = self._read(max(0.1, end - time.time()))
                if r == "timeout":
                    self.kill()
                    return {"timed_out": True}
                if r is None:
                    raise SandboxError("the sandbox stopped: " + ("".join(self.err)[-400:] or "unknown error"))
                if r.get("id") == rid and "stream" in r:
                    if on_output:
                        try:
                            on_output(r["stream"])
                        except Exception:
                            pass
                    continue
                if r.get("id") == rid:
                    self.last_used = time.time()
                    return r

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass


_sessions = {}
_lock = threading.Lock()


def _session(conv, reset=False):
    with _lock:
        s = _sessions.get(conv)
        if s and (reset or not s.alive()):
            s.kill()
            s = None
        if s is None:
            s = Session(conv)
            _sessions[conv] = s
        return s


def _snapshot(d):
    out = {}
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x != ".figures"]
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
                out[p] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return out


def copy_inputs(conv, paths, inside):
    """Copy files (checked with inside(path)) into the work folder. -> names in /work."""
    d, names, total = work_dir(conv), [], 0
    for p in paths or []:
        p = os.path.abspath(str(p))
        if not inside(p):
            raise SandboxError(f"{p} is outside the allowed folders")
        if not os.path.isfile(p):
            raise SandboxError(f"{p} does not exist")
        total += os.path.getsize(p)
        if total > MAX_INPUT_BYTES:
            raise SandboxError("input files are too large (200 MB limit)")
        shutil.copy2(p, os.path.join(d, os.path.basename(p)))
        names.append(os.path.basename(p))
    return names


def run(conv, code, timeout=120, reset=False, preload="", on_output=None):
    if not available():
        raise SandboxError("the sandbox is not installed (needs Node.js and web-agent/sandbox/node_modules/pyodide)")
    fresh = reset or conv not in _sessions or not _sessions[conv].alive()
    s = _session(conv, reset)
    before = _snapshot(s.work)
    r = s.run(code, timeout, preload, on_output)
    parts = []
    if fresh:
        parts.append(f"(new sandbox session, Python {s.version}; files are in /work)")
    if r.get("timed_out"):
        with _lock:
            _sessions.pop(conv, None)
        parts.append(f"⚠ Stopped after {timeout}s: the sandbox was restarted and its variables are gone (files in "
                     f"/work are kept). Use a longer timeout or less work per call.")
        return {"text": "\n".join(parts), "images": [], "files": [], "error": True}
    if r.get("loaded"):
        parts.append("(loaded packages: " + ", ".join(r["loaded"]) + ")")
    saved = None
    if r.get("stdout"):
        out = r["stdout"].rstrip()
        if len(out) > MAX_OUT:              # too big to read: keep it whole in a file, show head and tail
            os.makedirs(os.path.join(s.work, "outputs"), exist_ok=True)
            saved = os.path.join(s.work, "outputs", f"output_{int(time.time())}.txt")
            with open(saved, "w", encoding="utf-8") as f:
                f.write(out)
            out = (out[:2500] + f"\n\n… [output was {len(out):,} characters; the full text is saved as "
                   f"/work/outputs/{os.path.basename(saved)}. Don't print large data: inspect it with code "
                   f"(len(), the first few items) or write it to a file.] …\n\n" + out[-1500:])
        parts.append(out)
    if r.get("result") not in (None, "None"):
        parts.append(str(r["result"]))
    images = [os.path.join(s.work, ".figures", os.path.basename(p)) for p in r.get("figures") or []]
    if images:
        parts.append(f"[{len(images)} figure(s) shown to the user]")
    after = _snapshot(s.work)
    changed = sorted(p for p, v in after.items() if before.get(p) != v and p != saved)
    if saved:
        changed.insert(0, saved)
    if changed:
        parts.append("Files written: " + ", ".join("/work/" + os.path.relpath(p, s.work).replace("\\", "/")
                                                  for p in changed[:20]) + f"  (on the PC: {s.work})")
    if r.get("error"):
        err = r["error"]
        tb = err.split("Traceback (most recent call last):")[-1] if "Traceback" in err else err
        if 'File "<exec>"' in tb:                     # drop Pyodide's own frames: start at the user's code
            tb = tb[tb.index('File "<exec>"'):]
        lines = [ln for ln in tb.splitlines() if "/lib/python3" not in ln and "_pyodide" not in ln
                 and "pyodide/" not in ln]
        parts.append("Error:\n" + "\n".join(lines).strip())
        if re.search(r"No module named '?(pptx|docx|openpyxl|xlsxwriter|reportlab|fpdf|win32com)", err):
            parts.append("Hint: Office/PDF files are made with create_document (read_guide('powerpoint'|'word'|'excel'|"
                         "'pdf')); to edit an existing Office file use python_host, which has python-pptx, python-docx "
                         "and openpyxl installed. Don't try to install packages.")
        elif "No module named" in err:
            parts.append("Hint: that package isn't available in the sandbox and can't be installed here. Use another "
                         "approach, or python_host if the package is installed on the PC.")
        elif "does not support processes" in err or "subprocess" in err:
            parts.append("Hint: the sandbox can't run programs or pip. Use powershell for commands.")
    text = "\n".join(p for p in parts if p) or "(ran; no output)"
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT // 2] + f"\n… [{len(text) - MAX_OUT:,} characters cut] …\n" + text[-MAX_OUT // 2:]
    return {"text": text, "images": images, "files": changed, "error": bool(r.get("error")), "work": s.work}


def reaper():
    while True:
        time.sleep(120)
        now = time.time()
        with _lock:
            for conv, s in list(_sessions.items()):
                if now - s.last_used > IDLE_SHUTDOWN and not s.lock.locked():
                    s.kill()
                    _sessions.pop(conv, None)


def shutdown_all():
    with _lock:
        for s in _sessions.values():
            s.kill()
        _sessions.clear()


threading.Thread(target=reaper, daemon=True, name="sandbox-reaper").start()
