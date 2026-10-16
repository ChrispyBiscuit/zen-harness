"""Flight recorder: what happened in every Python process and thread a command or test run started.

The class of bug that costs small models the most is the one whose symptom and cause sit in different places: a test
sees "connection refused" or a 500 while the cause is a server thread that died quietly, a port still held by an
earlier run, a file locked by another writer. Measured over 17 build runs, ~80% of debug time was that class; bugs
that came with a traceback in the project's own code were fixed in minutes. The recorder turns the first kind into
the second: every process the agent starts loads a tiny hook (via the stack-dump sitecustomize, see stackdump.py)
that, when ZEN_FLIGHT_DIR is set, writes one JSON line per notable event:

  start / exit           the process and its command line
  thread_exc             an exception that killed a thread (normally only printed, easily missed)
  handler_exc            an exception inside a socketserver/http.server request handler (normally only printed)
  uncaught               an uncaught exception in the main thread
  raised                 an exception raised in the project's own code, even if something caught it (a handler that
                         turns every error into "500 {'error': ...}")
  bind / bind_fail       a socket opened for listening, or failing to (address in use)
  connect_fail           a connection refused / timed out
  file_fail              a file the process could not open, replace or remove (PermissionError: locked)

summarize() turns a run's events into a few lines for the tool result; cleanup() stops processes the run started that
are still alive (a test that left its server running breaks the next run).
"""
import json
import os
import shutil
import tempfile
import time

BASE = os.path.join(tempfile.gettempdir(), "zen_flight")
KEEP_RUNS = 40

RECORDER = r'''# zen_flight: records notable events of this process for Zen (ZEN_FLIGHT_DIR set). Never raises.
import os, sys, json, time, threading, traceback
_D = os.environ.get("ZEN_FLIGHT_DIR")
_ROOT = os.path.normcase(os.path.abspath(os.environ.get("ZEN_FLIGHT_ROOT") or os.getcwd()))
_PID = os.getpid()
_lock = threading.Lock()
_count = [0]
_seen = set()
_SKIP = (StopIteration, StopAsyncIteration, GeneratorExit, KeyboardInterrupt, SystemExit)


def _w(kind, **kw):
    if _count[0] >= 300:
        return
    _count[0] += 1
    kw.update(kind=kind, pid=_PID, t=round(time.time(), 3))
    try:
        with _lock:
            with _real_open(os.path.join(_D, "%d.jsonl" % _PID), "a", encoding="utf-8") as f:
                f.write(json.dumps(kw, default=str) + "\n")
    except Exception:
        pass


def _mine(fn):
    if not fn or fn.startswith("<"):                  # <frozen importlib>, <string>: never the project's code
        return False
    fn = os.path.normcase(os.path.abspath(fn))
    return fn.startswith(_ROOT) and "site-packages" not in fn and "zen_flight" not in fn and "sitecustomize" not in fn


def _frames(tb):
    try:
        return [[fs.filename, fs.lineno, fs.name, (fs.line or "")[:140], _mine(fs.filename)]
                for fs in traceback.extract_tb(tb)[-10:]]
    except Exception:
        return []


import builtins as _bi
_real_open = _bi.open
_w("start", argv=list(getattr(sys, "orig_argv", sys.argv))[:8], cwd=os.getcwd())

_dead = set()                                          # exceptions already recorded (run wrapper + excepthook)
_orig_run = threading.Thread.run
def _thread_run(self):
    # Thread.run itself, not only threading.excepthook: pytest replaces the hook while tests run
    try:
        _orig_run(self)
    except BaseException as e:
        if not isinstance(e, _SKIP) and id(e) not in _dead:
            _dead.add(id(e))
            _w("thread_exc", thread=self.name, exc=type(e).__name__, msg=str(e)[:300], frames=_frames(e.__traceback__))
        raise
threading.Thread.run = _thread_run

_orig_thread_hook = threading.excepthook
def _thread_hook(a):
    if a.exc_type not in _SKIP and id(a.exc_value) not in _dead:
        _dead.add(id(a.exc_value))
        _w("thread_exc", thread=getattr(a.thread, "name", "?"), exc=a.exc_type.__name__, msg=str(a.exc_value)[:300],
           frames=_frames(a.exc_traceback))
    _orig_thread_hook(a)
threading.excepthook = _thread_hook

_orig_hook = sys.excepthook
def _hook(t, v, tb):
    if t not in _SKIP:
        _w("uncaught", exc=t.__name__, msg=str(v)[:300], frames=_frames(tb))
    _orig_hook(t, v, tb)
sys.excepthook = _hook

try:
    import socketserver as _ss
    _orig_he = _ss.BaseServer.handle_error
    def _handle_error(self, request, client_address):
        t, v, tb = sys.exc_info()
        if t is not None and t not in _SKIP:
            _w("handler_exc", exc=t.__name__, msg=str(v)[:300], frames=_frames(tb))
        return _orig_he(self, request, client_address)
    _ss.BaseServer.handle_error = _handle_error
except Exception:
    pass

_bound = {}                                            # id(socket) -> the address it is bound to
try:
    import socket as _so
    _base = _so.socket.__mro__[1]
    def _wrap(name, kind_ok, kind_fail):
        orig = getattr(_base, name)
        def f(self, *a):
            try:
                r = orig(self, *a)
            except OSError as e:
                _w(kind_fail, addr=str(a[0] if a else "")[:80], err=str(e)[:200])
                raise
            if kind_ok:
                try:
                    addr = str(self.getsockname())[:80]     # the real port (a request for port 0 gets one)
                except Exception:
                    addr = str(a[0] if a else "")[:80]
                _bound[id(self)] = addr
                _w(kind_ok, addr=addr, sock=id(self))
            return r
        setattr(_so.socket, name, f)
    _wrap("bind", "bind", "bind_fail")
    _wrap("connect", None, "connect_fail")
    _orig_close = _so.socket.close
    def _close(self):
        addr = _bound.pop(id(self), None)
        if addr is not None:
            # a listening socket is closed: a later bind of the same address while this one is still OPEN means
            # two sockets on one port (Windows allows it with SO_REUSEADDR) and connections can reach the old one
            _w("close", addr=addr, sock=id(self))
        return _orig_close(self)
    _so.socket.close = _close
except Exception:
    pass

def _file_guard(mod, name):
    orig = getattr(mod, name)
    def f(*a, **k):
        try:
            return orig(*a, **k)
        except PermissionError as e:
            _w("file_fail", op=name, path=str(a[0] if a else "")[:200], err=str(e)[:200])
            raise
    setattr(mod, name, f)
try:
    _file_guard(_bi, "open")
    for _n in ("replace", "rename", "remove", "unlink"):
        _file_guard(os, _n)
except Exception:
    pass

_mon = getattr(sys, "monitoring", None)
if _mon is not None:
    try:
        _mon.use_tool_id(4, "zen_flight")
        def _raised(code, offset, exc):
            if isinstance(exc, _SKIP):
                return
            fn = code.co_filename
            if not _mine(fn) or os.path.basename(fn).startswith(("test_", "conftest")):
                return
            try:
                line = sys._getframe(1).f_lineno
            except Exception:
                line = code.co_firstlineno
            key = (type(exc).__name__, fn, line)
            if key in _seen or len(_seen) >= 25:
                return
            _seen.add(key)
            _w("raised", exc=type(exc).__name__, msg=str(exc)[:240], file=fn, line=line, func=code.co_name)
        _mon.register_callback(4, _mon.events.RAISE, _raised)
        _mon.set_events(4, _mon.events.RAISE)
    except Exception:
        pass

import atexit as _ax
_ax.register(lambda: _w("exit"))
'''


def ensure(hook_dir):
    """Write the recorder module next to the stack-dump sitecustomize (which imports it)."""
    try:
        os.makedirs(hook_dir, exist_ok=True)
        p = os.path.join(hook_dir, "zen_flight.py")
        if not os.path.isfile(p) or open(p, encoding="utf-8").read() != RECORDER:
            with open(p, "w", encoding="utf-8") as f:
                f.write(RECORDER)
    except OSError:
        pass


def new_run(root):
    """-> (env additions, run folder) for one command or test run; old run folders are pruned."""
    os.makedirs(BASE, exist_ok=True)
    try:
        runs = sorted((os.path.join(BASE, d) for d in os.listdir(BASE)), key=os.path.getmtime)
        for d in runs[:-KEEP_RUNS]:
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass
    d = tempfile.mkdtemp(prefix="run_", dir=BASE)
    return {"ZEN_FLIGHT_DIR": d, "ZEN_FLIGHT_ROOT": os.path.abspath(root or os.getcwd())}, d


def events(d):
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        pass
        except OSError:
            pass
    return sorted(out, key=lambda e: e.get("t", 0))


def _proc_label(argv):
    parts = [os.path.basename(str(a)) for a in (argv or [])][1:4]
    return " ".join(p for p in parts if p not in ("-u", "-m", "-X")) or "python"


def _where(frames):
    """The newest frame in the project's own code, else the newest frame -> 'server.py:88 in run()  `code`'."""
    if not frames:
        return ""
    f = next((x for x in reversed(frames) if x[4]), frames[-1])
    return f"{os.path.basename(f[0])}:{f[1]} in {f[2]}()" + (f"  `{f[3]}`" if f[3] else "")


def summarize(d, skip_pid=None, limit=10):
    """The run's notable events as short lines ('' when nothing notable happened outside the main output)."""
    ev = events(d)
    if not ev:
        return ""
    procs = {e["pid"]: _proc_label(e.get("argv")) for e in ev if e.get("kind") == "start"}
    exited = {e["pid"] for e in ev if e.get("kind") == "exit"}
    lines, connect = [], {}
    for e in ev:
        k, who = e.get("kind"), f"[{procs.get(e.get('pid'), 'python')} pid {e.get('pid')}]"
        if k == "thread_exc":
            lines.append(f"{who} thread '{e.get('thread')}' died: {e.get('exc')}: {e.get('msg')} at {_where(e.get('frames'))}")
        elif k == "handler_exc":
            lines.append(f"{who} request handler error: {e.get('exc')}: {e.get('msg')} at {_where(e.get('frames'))}")
        elif k == "uncaught" and e.get("pid") != skip_pid:
            lines.append(f"{who} crashed: {e.get('exc')}: {e.get('msg')} at {_where(e.get('frames'))}")
        elif k == "raised":
            lines.append(f"{who} {os.path.basename(str(e.get('file')))}:{e.get('line')} in {e.get('func')}() raised "
                         f"{e.get('exc')}: {e.get('msg')} (it may have been caught — e.g. turned into an error reply)")
        elif k == "bind_fail":
            lines.append(f"{who} could not listen on {e.get('addr')}: {e.get('err')}")
        elif k == "file_fail":
            lines.append(f"{who} {e.get('op')} {e.get('path')} failed: {e.get('err')}")
        elif k == "connect_fail":
            key = (e.get("addr"), (e.get("err") or "")[:60])
            connect[key] = connect.get(key, 0) + 1
    for (addr, err), n in connect.items():
        lines.append(f"connecting to {addr} failed{f' ×{n}' if n > 1 else ''}: {err}")
    open_now = {}                                       # (pid, address) -> a socket bound there and not closed yet
    for e in ev:
        if e.get("kind") == "bind":
            key = (e.get("pid"), e.get("addr"))
            if open_now.get(key) not in (None, e.get("sock")):
                lines.append(f"[{procs.get(e.get('pid'), 'python')} pid {e.get('pid')}] {e.get('addr')} was bound "
                             f"again while an earlier socket on it was still open (never closed) — both can listen "
                             f"on Windows, and connections may reach the old socket that nothing serves")
            open_now[key] = e.get("sock")
        elif e.get("kind") == "close":
            open_now.pop((e.get("pid"), e.get("addr")), None)
    listening = [f"{procs.get(e['pid'], 'python')} on {e.get('addr')}" for e in ev if e.get("kind") == "bind"]
    gone = [f"{procs.get(p)} (pid {p})" for p in procs if p not in exited and p != skip_pid]
    if not lines:
        return ""
    seen, uniq = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            uniq.append(ln)
    out = uniq[:limit] + ([f"(+{len(uniq) - limit} more)"] if len(uniq) > limit else [])
    ctx = []
    if listening:
        ctx.append("listened: " + ", ".join(dict.fromkeys(listening))[:300])
    if gone:
        ctx.append("no clean exit recorded (killed, crashed hard, or still running): " + ", ".join(gone)[:300])
    return ("\nElsewhere during this run (recorded in every Python process and thread it started — the cause of a "
            "failure is often here, not where it showed up):\n- " + "\n- ".join(out)
            + ("\n(" + "; ".join(ctx) + ")" if ctx else ""))


def cleanup(d, protect=()):
    """Stop processes this run started that are still running (not the agent's background tasks) -> note or ''."""
    try:
        import psutil
    except ImportError:
        return ""
    starts = {e["pid"]: e for e in events(d) if e.get("kind") == "start"}
    exited = {e["pid"] for e in events(d) if e.get("kind") == "exit"}
    stopped = []
    for pid, e in starts.items():
        if pid in exited or pid in protect or pid == os.getpid():
            continue
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - float(e.get("t", 0))) > 5:      # the pid was reused by something else
                continue
            ports = [f":{c.laddr.port}" for c in p.net_connections(kind="inet") if c.status == "LISTEN"]
            p.kill()
            stopped.append(f"{_proc_label(e.get('argv'))} (pid {pid}{', listening ' + ' '.join(ports) if ports else ''})")
        except Exception:
            continue
    if not stopped:
        return ""
    return ("\n(Stopped what this run left running — a leftover process holds its port and files for the next run: "
            + ", ".join(stopped) + ". Tests must stop what they start.)")
