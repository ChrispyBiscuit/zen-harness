"""Background processes started by the agent (dev servers, watchers, long builds).

They are stopped when the agent finishes its turn (stop_owned), unless started with keep=True (something the user
keeps using). Every process in a command's tree is tracked with its creation time and saved to agent_bg/pids.json, so
leftovers from a session that ended without cleanup (crash, forced close) are stopped at the next app start
(reap_leftovers).
Output goes to a ring buffer (for the agent and the Tasks pane) and to agent_bg/<id>.log.
Thread-safe; Qt-free. listeners(event, proc_info) are called from reader threads.
"""
import atexit
import collections
import itertools
import json
import os
import subprocess
import tempfile
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
LOG_DIR = paths.data("agent_bg")
PIDS_FILE = os.path.join(LOG_DIR, "pids.json")
MAX_RUNNING = 6
_ids = itertools.count(1)

PS_HEAD = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8\n"
           "$ProgressPreference = 'SilentlyContinue'\n")


def _proc_env():
    try:
        import stackdump
        return stackdump.env()          # unbuffered output + "where is it stuck" stack dumps on request
    except Exception:
        return dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")


class BgProc:
    def __init__(self, pid_no, name, command, cwd, owner, keep=False):
        self.id = f"bg{pid_no}"
        self.name = name
        self.command = command
        self.cwd = cwd
        self.owner = owner            # conversation id that started it (for the Tasks pane)
        self.started = time.time()
        self.ended = None
        self.exit = None
        self.lines = collections.deque(maxlen=5000)
        self.total_lines = 0
        self.proc = None
        self.script = None
        self.log_path = os.path.join(LOG_DIR, f"{self.id}_{time.strftime('%Y%m%d_%H%M%S')}.log")
        self.stopped_by_user = False
        self.keep = bool(keep)       # survives the end of the agent's turn (still stopped when the app closes)
        self.tree = {}               # pid -> create_time of every process this command started (wrapper + children)

    @property
    def running(self):
        return self.ended is None

    def info(self):
        return {"id": self.id, "name": self.name, "command": self.command, "cwd": self.cwd,
                "owner": self.owner, "started": self.started, "ended": self.ended,
                "exit": self.exit, "running": self.running, "lines": self.total_lines,
                "last": self.lines[-1] if self.lines else "", "log": self.log_path,
                "pid": self.proc.pid if self.proc else None, "keep": self.keep}

    def tail(self, n=60):
        return list(self.lines)[-max(1, n):]


class BgManager:
    def __init__(self):
        self.procs = {}
        self.lock = threading.Lock()
        self.listeners = []

    def _notify(self, event, p):
        for fn in list(self.listeners):
            try:
                fn(event, p.info())
            except Exception:
                pass

    def start(self, command, cwd, name=None, owner=None, keep=False):
        with self.lock:
            running = [p for p in self.procs.values() if p.running]
            if len(running) >= MAX_RUNNING:
                raise RuntimeError(f"{len(running)} background processes are already running "
                                   f"(limit {MAX_RUNNING}); stop one first")
            p = BgProc(next(_ids), (name or command.splitlines()[0])[:60], command, cwd, owner, keep)
            self.procs[p.id] = p
        os.makedirs(LOG_DIR, exist_ok=True)
        fd, p.script = tempfile.mkstemp(suffix=".ps1", prefix="agentbg_")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(PS_HEAD + command + "\n")
        env, p.flight = _proc_env(), None
        try:
            import flight                 # what its threads and request handlers do (bg_output reports it)
            add, p.flight = flight.new_run(cwd)
            env.update(add)
        except Exception:
            p.flight = None
        p.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", p.script],
            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000, env=env)
        threading.Thread(target=self._pump, args=(p,), daemon=True).start()
        threading.Thread(target=self._track, args=(p,), daemon=True).start()
        self._notify("started", p)
        return p

    def adopt(self, proc, q, command, cwd, owner=None, prior=None, script=None):
        """Take over a foreground command that is still running (it was handed back to the agent early because it
        was blocked): its output keeps flowing from the reader queue `q` (raw lines, None at EOF) into a bg task."""
        with self.lock:
            running = [p for p in self.procs.values() if p.running]
            if len(running) >= MAX_RUNNING:
                raise RuntimeError(f"{len(running)} background processes are already running (limit {MAX_RUNNING})")
            p = BgProc(next(_ids), command.splitlines()[0][:60], command, cwd, owner)
            p.proc, p.script = proc, script
            self.procs[p.id] = p
        os.makedirs(LOG_DIR, exist_ok=True)
        for line in prior or []:
            p.lines.append(line)
            p.total_lines += 1
        threading.Thread(target=self._pump, args=(p, q, prior), daemon=True).start()
        threading.Thread(target=self._track, args=(p,), daemon=True).start()
        self._notify("started", p)
        return p

    # ---- process-tree tracking (the command runs inside PowerShell: the real program is a child)
    def _track(self, p):
        try:
            import psutil
        except Exception:
            return
        while p.running:
            try:
                root = psutil.Process(p.proc.pid)
                for q in [root] + root.children(recursive=True):
                    if q.pid not in p.tree:
                        p.tree[q.pid] = q.create_time()
                        self._save_pids()
            except Exception:
                pass
            try:                                   # a background server on the app's own port: stop it
                import agent_tools
                port = agent_tools.app_port_grab(p.proc.pid)
                if port:
                    p.lines.append("[stopped by the app: " + agent_tools.APP_PORT_MSG.format(port=port) + "]")
                    p.total_lines += 1
                    self.stop(p.id)
                    return
            except Exception:
                pass
            time.sleep(1.5)

    def _save_pids(self):
        with self.lock:
            rows = [{"pid": pid, "ct": ct, "owner": p.owner, "id": p.id, "cmd": p.command[:120]}
                    for p in self.procs.values() if p.running for pid, ct in p.tree.items()]
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(PIDS_FILE, "w", encoding="utf-8") as f:
                json.dump(rows, f)
        except OSError:
            pass

    @staticmethod
    def _kill_exact(pid, ct):
        """Kill pid only if it is still the same process (same creation time) — a reused pid is left alone."""
        try:
            import psutil
            q = psutil.Process(pid)
            if abs(q.create_time() - ct) < 1.0:
                q.kill()
                return True
        except Exception:
            pass
        return False

    def stop_owned(self, owner):
        """End of the agent's turn: stop the background processes it started (except keep=True ones)."""
        out = []
        for p in list(self.procs.values()):
            if p.running and p.owner == owner and not p.keep:
                try:
                    self.stop(p.id)
                    out.append(p.id)
                except Exception:
                    pass
        return out

    def reap_leftovers(self):
        """App start: stop processes a previous session's agent started and never stopped -> how many."""
        try:
            rows = json.load(open(PIDS_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        n = sum(1 for r in rows if self._kill_exact(int(r["pid"]), float(r["ct"])))
        try:
            os.remove(PIDS_FILE)
        except OSError:
            pass
        return n

    def _pump(self, p, q=None, prior=None):
        with open(p.log_path, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"# {p.name}\n# cwd: {p.cwd}\n# {p.command}\n\n")
            for line in prior or []:
                log.write(line + "\n")
            last_notify = 0.0
            source = iter(q.get, None) if q is not None else iter(p.proc.stdout.readline, b"")
            for raw in source:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                p.lines.append(line)
                p.total_lines += 1
                log.write(line + "\n")
                if time.time() - last_notify > 0.5:
                    log.flush()
                    self._notify("output", p)
                    last_notify = time.time()
            p.exit = p.proc.wait()
            p.ended = time.time()
            log.write(f"\n# exited with code {p.exit}\n")
        try:
            os.remove(p.script)
        except OSError:
            pass
        self._notify("ended", p)

    def get(self, pid):
        p = self.procs.get(str(pid).strip())
        if p is None:
            raise KeyError(f"no background process '{pid}' (known: {', '.join(self.procs) or 'none'})")
        return p

    def stop(self, pid):
        p = self.get(pid)
        if p.running:
            p.stopped_by_user = True
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.proc.pid)], capture_output=True,
                               timeout=10, creationflags=0x08000000)
            except Exception:
                p.proc.kill()
            for pid, ct in list(p.tree.items()):        # children that were re-parented or outlived the wrapper
                self._kill_exact(pid, ct)
            self._save_pids()
        return p

    def list(self):
        return [p.info() for p in sorted(self.procs.values(), key=lambda p: p.started)]

    def clear_finished(self):
        with self.lock:
            for k in [k for k, p in self.procs.items() if not p.running]:
                del self.procs[k]

    def stop_all(self):
        for p in list(self.procs.values()):
            if p.running:
                try:
                    self.stop(p.id)
                except Exception:
                    pass


MANAGER = BgManager()
atexit.register(MANAGER.stop_all)
