"""Where is a hung Python process stuck? Stack dumps for the processes the agent starts.

Every Python process the agent launches (powershell, bg_start, run_tests) gets an environment whose PYTHONPATH starts
with HOOK_DIR. Its sitecustomize.py starts a tiny daemon thread that, when a request file appears for its pid, writes
the stack of every thread (faulthandler — standard library, nothing installed) to a file. dump(root_pid) asks every
Python process in a tree for its stacks and formats them, marking the agent's own code. Used automatically when a
command or test run times out, and by the stack_dump tool for a background server that hangs.
"""
import os
import re
import tempfile
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
HOOK_DIR = paths.data("agent_bg", "pyhook")
DUMP_DIR = os.path.join(tempfile.gettempdir(), "zen_stackdumps")

HOOK = '''# Zen: lets the app ask this Python process where it is stuck (a stack dump on request). Harmless otherwise.
import os as _os
_d = _os.environ.get("ZEN_DUMP_DIR")
if _d:
    import faulthandler as _fh, threading as _th, time as _t

    def _zen_watch():
        pid = _os.getpid()
        req, out = _os.path.join(_d, "req_%d" % pid), _os.path.join(_d, "dump_%d.txt" % pid)
        while True:
            try:
                if _os.path.exists(req):
                    with open(out, "w") as f:
                        _fh.dump_traceback(file=f, all_threads=True)
                    _os.remove(req)
            except Exception:
                pass
            _t.sleep(0.25)
    _th.Thread(target=_zen_watch, daemon=True, name="zen-stackdump").start()
if _os.environ.get("ZEN_FLIGHT_DIR"):
    try:
        import zen_flight  # noqa: F401  the flight recorder (flight.py): what every process and thread did
    except Exception:
        pass
'''


def _ensure_hook():
    try:
        os.makedirs(HOOK_DIR, exist_ok=True)
        os.makedirs(DUMP_DIR, exist_ok=True)
        p = os.path.join(HOOK_DIR, "sitecustomize.py")
        if not os.path.isfile(p) or open(p, encoding="utf-8").read() != HOOK:
            with open(p, "w", encoding="utf-8") as f:
                f.write(HOOK)
    except OSError:
        pass
    try:
        import flight
        flight.ensure(HOOK_DIR)
    except Exception:
        pass


def env(base=None):
    """An environment for a process the agent starts: unbuffered output + the stack-dump hook."""
    _ensure_hook()
    e = dict(os.environ if base is None else base)
    e["PYTHONUNBUFFERED"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["ZEN_DUMP_DIR"] = DUMP_DIR
    e["PYTHONPATH"] = HOOK_DIR + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    shim = paths.shim_dir()             # installed app on a PC without Python: `python x.py` uses the bundled one
    if shim:
        e["PATH"] = shim + os.pathsep + e.get("PATH", "")
    return e


def _python_pids(root_pid):
    try:
        import psutil
        root = psutil.Process(int(root_pid))
        procs = [root] + root.children(recursive=True)
        return [(q.pid, " ".join(q.cmdline())[:120]) for q in procs if "python" in (q.name() or "").lower()]
    except Exception:
        return []


_FRAME_RX = re.compile(r'^\s*File "([^"]+)", line (\d+) in (\S+)')


def _format(raw, project_hint=""):
    """faulthandler text -> per thread, the frames newest first; the agent's own code marked '>'."""
    out, skip = [], False
    for ln in raw.splitlines():
        if ln.startswith(("Thread ", "Current thread ")):
            skip = False
            out.append(ln.strip())
            continue
        m = _FRAME_RX.match(ln)
        if not m:
            continue
        path, line, fn = m.groups()
        low = path.lower()
        if "sitecustomize" in low and "_zen_watch" in fn:
            skip = True                                    # our own watcher thread
            if out and out[-1].startswith(("Thread ", "Current thread ")):
                out.pop()
            continue
        if skip:
            continue
        lib = (("\\lib\\" in low or "/lib/" in low or "site-packages" in low) and not (project_hint and project_hint.lower() in low)) or low.startswith("<")
        code = ""
        try:
            code = open(path, encoding="utf-8", errors="replace").read().splitlines()[int(line) - 1].strip()
        except Exception:
            pass
        out.append(f"  {' ' if lib else '>'} {os.path.basename(path)}:{line} in {fn}" + (f"   {code[:100]}" if code else ""))
    return "\n".join(out[:60])


def dump(root_pid, wait=2.5, project_hint=""):
    """Ask every Python process in root_pid's tree for its stacks -> text ('' when there is none to ask)."""
    _ensure_hook()
    pids = _python_pids(root_pid)
    if not pids:
        return ""
    for pid, _cmd in pids:
        for f in (f"dump_{pid}.txt",):
            try:
                os.remove(os.path.join(DUMP_DIR, f))
            except OSError:
                pass
        try:
            open(os.path.join(DUMP_DIR, f"req_{pid}"), "w").close()
        except OSError:
            pass
    got, t0 = {}, time.time()
    while time.time() - t0 < wait and len(got) < len(pids):
        for pid, _cmd in pids:
            p = os.path.join(DUMP_DIR, f"dump_{pid}.txt")
            if pid not in got and os.path.isfile(p) and not os.path.exists(os.path.join(DUMP_DIR, f"req_{pid}")):
                try:
                    got[pid] = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    pass
        time.sleep(0.15)
    parts = []
    for pid, cmd in pids:
        if pid in got:
            body = _format(got[pid], project_hint)
            if body.strip():
                parts.append(f"[pid {pid}] {cmd}\n{body}")
        try:
            os.remove(os.path.join(DUMP_DIR, f"req_{pid}"))
        except OSError:
            pass
    if not parts:
        return ("(no stack available — the process was started before stack dumps were enabled, or is not a Python "
                "program)")
    return ("Where it was when asked (newest call first; '>' = your code — the top '>' line is where it waits):\n"
            + "\n\n".join(parts))


# ---- stall detection: a command that is BLOCKED (waiting on a lock, socket, input or a server) instead of working.
# A deadline can't tell "slow" from "stuck" and makes the agent wait the whole timeout for nothing; this can. Blocked =
# no output, ~no CPU and ~no disk/network bytes across the command's process tree AND the agent's own background
# servers (a client waiting on a busy server is not stuck) for `quiet` seconds.
class StallWatch:
    def __init__(self, pid, extra_pids=None, quiet=10.0):
        self.pid, self.extra, self.quiet = pid, extra_pids or (lambda: []), quiet
        self.t0 = self.last_out = time.time()
        self.samples = []                    # (time, cpu seconds, io bytes)
        self.next_at = 0.0

    def output(self):
        self.last_out = time.time()

    @staticmethod
    def _activity(pids):
        import psutil
        cpu = io = 0.0
        seen = set()
        for root_pid in pids:
            try:
                root = psutil.Process(int(root_pid))
                procs = [root] + root.children(recursive=True)
            except Exception:
                continue
            for q in procs:
                if q.pid in seen:
                    continue
                seen.add(q.pid)
                try:
                    t = q.cpu_times()
                    cpu += t.user + t.system
                    c = q.io_counters()
                    io += c.read_bytes + c.write_bytes
                except Exception:
                    pass
        return cpu, io

    def blocked(self):
        """Call often; True once the tree has been idle and silent for `quiet` seconds."""
        now = time.time()
        if now < self.next_at:
            return False
        self.next_at = now + 1.0
        try:
            cpu, io = self._activity([self.pid] + list(self.extra()))
        except Exception:
            return False
        self.samples.append((now, cpu, io))
        self.samples = [s for s in self.samples if now - s[0] <= self.quiet + 2]
        if now - self.last_out < self.quiet or now - self.t0 < self.quiet:
            return False
        old = next((s for s in self.samples if now - s[0] >= self.quiet * 0.8), None)
        if old is None:
            return False
        span = now - old[0]
        return (cpu - old[1]) < 0.03 * span and (io - old[2]) < 4096 * span

    def idle_for(self):
        return round(time.time() - self.last_out)
