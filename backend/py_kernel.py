"""Persistent Python for the agent: one real IPython kernel per conversation (variables, imports and loaded data
survive between calls), with charts captured as PNG files, a time limit with interrupt, and capped output.

The kernel runs with the user's permissions (like PowerShell); it is not a sandbox.
"""
import os
import queue
import re
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("LWS_DATA_DIR") or APP_DIR
MAX_OUT = 6000
IDLE_SHUTDOWN = 30 * 60          # stop kernels unused for this long
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

SETUP = r"""
import os as _os, warnings as _w
_w.filterwarnings("ignore", category=DeprecationWarning)
import matplotlib as _mpl
_mpl.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams.update({"figure.dpi": 110, "figure.figsize": (7, 4.2), "axes.grid": True, "grid.alpha": 0.3})
try:
    import numpy as np
    import pandas as pd
    pd.set_option("display.width", 160); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 60)
except Exception:
    pass
def _lws_save_figs(folder, stamp):
    import matplotlib.pyplot as _plt
    out = []
    for n in _plt.get_fignums():
        p = _os.path.join(folder, f"fig_{stamp}_{n}.png")
        _plt.figure(n).savefig(p, bbox_inches="tight")
        out.append(p)
    _plt.close("all")
    return out
"""


class KernelError(Exception):
    pass


class _Hung(Exception):
    """The kernel ignored the interrupt (on Windows a blocking call such as sleep or a socket read can't be
    interrupted)."""


class Session:
    def __init__(self, conv, cwd):
        from jupyter_client import KernelManager
        self.conv = conv
        self.fig_dir = os.path.join(DATA_DIR, "agent_artifacts", re.sub(r"[^\w-]", "_", conv or "misc"), "figures")
        os.makedirs(self.fig_dir, exist_ok=True)
        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel(cwd=cwd if cwd and os.path.isdir(cwd) else None)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.count = 0
        self._run(SETUP, timeout=60)

    def alive(self):
        try:
            return self.km.is_alive()
        except Exception:
            return False

    def _run(self, code, timeout, on_output=None):
        msg_id = self.kc.execute(code, store_history=True, allow_stdin=False)
        out, images, error, result = [], [], None, None
        deadline = time.time() + timeout
        timed_out = False
        while True:
            left = deadline - time.time()
            if left <= 0:
                if timed_out:
                    raise _Hung()
                timed_out = True
                self.km.interrupt_kernel()
                deadline = time.time() + 5            # give the interrupt 5 s to land
                left = 5
            try:
                msg = self.kc.get_iopub_msg(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            t, c = msg["msg_type"], msg["content"]
            if t == "stream":
                out.append(c.get("text", ""))
                if on_output:
                    try:
                        on_output(c.get("text", ""))
                    except Exception:
                        pass
            elif t in ("execute_result", "display_data"):
                data = c.get("data", {})
                if "image/png" in data:
                    import base64
                    p = os.path.join(self.fig_dir, f"img_{int(time.time() * 1000)}_{len(images)}.png")
                    with open(p, "wb") as f:
                        f.write(base64.b64decode(data["image/png"]))
                    images.append(p)
                elif "text/plain" in data:
                    if t == "execute_result":
                        result = data["text/plain"]
                    else:
                        out.append(data["text/plain"] + "\n")
            elif t == "error":
                tb = "\n".join(c.get("traceback") or [f"{c.get('ename')}: {c.get('evalue')}"])
                error = _ANSI.sub("", tb)
            elif t == "status" and c.get("execution_state") == "idle":
                break
        return "".join(out), result, error, images, timed_out

    def execute(self, code, timeout=120, on_output=None):
        with self.lock:
            self.last_used = time.time()
            self.count += 1
            try:
                stdout, result, error, images, timed_out = self._run(code, timeout, on_output)
            except _Hung:
                self.km.restart_kernel(now=True)
                self.kc.wait_for_ready(timeout=60)
                self._run(SETUP, timeout=60)
                return {"stdout": "", "result": None, "images": [], "timed_out": True,
                        "error": f"The code was still running after {timeout}s and could not be interrupted, so the "
                                 f"Python session was restarted: all variables and imports are gone. Re-run the setup "
                                 f"you need (or use a longer timeout)."}
            if not error and not timed_out:
                stamp = f"{int(time.time())}_{self.count}"
                _o, figs, _e, _i, _t = self._run(f"_lws_save_figs({self.fig_dir!r}, {stamp!r})", 30)
                try:
                    import ast
                    images += [p for p in ast.literal_eval(figs or "[]") if os.path.exists(p)]   # paths from the kernel
                except Exception:
                    pass
            self.last_used = time.time()
            return {"stdout": stdout, "result": result, "error": error, "images": images, "timed_out": timed_out}

    def shutdown(self):
        try:
            self.kc.stop_channels()
        except Exception:
            pass
        try:
            self.km.shutdown_kernel(now=True)
        except Exception:
            pass


_sessions = {}
_lock = threading.Lock()


def session(conv, cwd=None, reset=False):
    with _lock:
        s = _sessions.get(conv)
        if s and (reset or not s.alive()):
            s.shutdown()
            s = None
        if s is None:
            s = Session(conv, cwd)
            _sessions[conv] = s
        return s


def run(conv, code, cwd=None, timeout=120, reset=False, on_output=None):
    """-> dict(text for the model, images, error flag)."""
    fresh = conv not in _sessions or reset
    s = session(conv, cwd, reset)
    r = s.execute(code, timeout, on_output)
    parts = []
    if fresh:
        parts.append("(new Python session)")
    if r["stdout"]:
        out = r["stdout"].rstrip()
        if len(out) > MAX_OUT:              # keep the full output in a file, show head and tail
            p = os.path.join(s.fig_dir, f"output_{int(time.time())}.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(out)
            r["images"] = r["images"]       # (figures unaffected)
            r.setdefault("files", []).append(p)
            out = (out[:2500] + f"\n\n… [output was {len(out):,} characters; the full text is saved at {p}. Don't "
                   f"print large data: inspect it with code or write it to a file.] …\n\n" + out[-1500:])
        parts.append(out)
    if r["result"] is not None:
        parts.append(r["result"])
    if r["images"]:
        parts.append(f"[{len(r['images'])} figure(s) shown to the user: " + ", ".join(os.path.basename(p) for p in r["images"]) + "]")
    if r["timed_out"] and "restarted" not in (r["error"] or ""):
        parts.append(f"⚠ Stopped after {timeout}s (interrupted). Variables defined before the interrupt still exist.")
    if r["error"]:
        parts.append(r["error"].rstrip())
    text = "\n".join(p for p in parts if p) or "(ran; no output)"
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT // 2] + f"\n… [{len(text) - MAX_OUT:,} characters cut] …\n" + text[-MAX_OUT // 2:]
    return {"text": text, "images": r["images"], "error": bool(r["error"] or r["timed_out"]), "stdout": r["stdout"],
            "files": r.get("files") or []}


def reaper():
    while True:
        time.sleep(120)
        now = time.time()
        with _lock:
            for conv, s in list(_sessions.items()):
                if now - s.last_used > IDLE_SHUTDOWN and not s.lock.locked():
                    s.shutdown()
                    _sessions.pop(conv, None)


def shutdown_all():
    with _lock:
        for s in _sessions.values():
            s.shutdown()
        _sessions.clear()


threading.Thread(target=reaper, daemon=True, name="py-kernel-reaper").start()
