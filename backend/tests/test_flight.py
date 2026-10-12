"""The flight recorder on real multi-layer failures, through the real run_tests path: the cause of a failure that shows
up in one process (a test) but happens in another (a server thread, a request handler, a child process) must appear
in the result. Runs under pytest, or standalone: python tests/test_flight.py"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["LWS_DATA_DIR"] = tempfile.mkdtemp(prefix="flight_data_")    # never the running app's hook folder
os.environ.setdefault("LWS_NO_TELEGRAM", "1")
os.environ.setdefault("LWS_NO_SCHEDULER", "1")

import agent_tools as at  # noqa: E402
import debug_tools as dbg  # noqa: E402
import flight  # noqa: E402

SERVER = '''import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        try:
            body = self.render()
            self.send_response(200)
        except Exception as e:                      # every error becomes a 500 with no traceback
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
        self.end_headers()
        self.wfile.write(body)
    def render(self):
        return self.request.headers.get("x")       # bug: a socket has no headers

def start(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv

def start_broken(port):
    def run():
        raise OSError("could not open the ledger: data/ledger.db is locked")
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
'''

TEST_500 = '''import socket, urllib.request, urllib.error, server

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def test_get():
    port = free()
    server.start(port)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/")
    except urllib.error.HTTPError as e:
        assert e.code == 200, e.code
'''

TEST_THREAD = '''import socket, time, urllib.request, server

def test_server_answers():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server.start_broken(port)
    time.sleep(0.3)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
'''

TEST_LEFTOVER = '''import subprocess, sys, time

def test_leaves_a_server_running():
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    time.sleep(1.0)
    assert False, "the test failed and never stopped its server"
'''


def _project(test_src, name="test_it.py"):
    d = tempfile.mkdtemp(prefix="flight_proj_")
    os.makedirs(os.path.join(d, "docs"))
    os.makedirs(os.path.join(d, "tests"))
    open(os.path.join(d, "server.py"), "w").write(SERVER)
    open(os.path.join(d, "tests", "conftest.py"), "w").write(
        "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
    open(os.path.join(d, "tests", name), "w").write(test_src)
    return d


def _run_tests(d):
    c = at.Ctx([d])
    c.cwd = d
    return dbg.run_tests({"path": os.path.join(d, "tests")}, c, lambda p, must=False: c.resolve(p, must), at.ToolError)


def test_a_caught_handler_error_is_shown_with_its_line():
    out = _run_tests(_project(TEST_500))
    assert "FAILED" in out and "Elsewhere during this run" in out, out[-1500:]
    assert "server.py" in out and "AttributeError" in out and "render" in out, out[-1500:]


def test_a_dead_server_thread_is_shown():
    out = _run_tests(_project(TEST_THREAD))
    assert "Elsewhere during this run" in out, out[-1500:]
    assert "died" in out and "ledger.db is locked" in out and "server.py" in out, out[-1500:]


def test_leftover_processes_are_stopped_and_reported():
    out = _run_tests(_project(TEST_LEFTOVER))
    assert "Stopped what this run left running" in out, out[-1500:]


TEST_RESTART = '''import socket, threading, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

def up(port):
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_restart_keeps_data():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv1 = up(port)
    srv1.shutdown()                      # stops serving, but the listening socket stays open (no server_close)
    up(port)                             # binds fine on Windows (SO_REUSEADDR) ...
    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)   # ... and this can reach the dead socket
'''


def test_a_second_bind_while_the_first_socket_is_open_is_shown():
    """bonsai9's 80-minute bug: no exception anywhere, a connection just reached the old, unserved socket."""
    out = _run_tests(_project(TEST_RESTART))
    if out.startswith("tests: PASSED"):
        return                           # this OS routed the connection to the new socket: nothing to show
    assert "was bound again while an earlier socket on it was still open" in out, out[-1500:]


def test_quiet_when_nothing_happened_elsewhere():
    d = _project("def test_ok():\n    assert 1 + 1 == 2\n")
    out = _run_tests(d)
    assert out.startswith("tests: PASSED") and "Elsewhere" not in out and "Stopped" not in out, out


def test_summarize_bind_failure_and_connect_refusals():
    d = tempfile.mkdtemp(prefix="flight_ev_")
    import json
    with open(os.path.join(d, "11.jsonl"), "w") as f:
        for e in ({"kind": "start", "pid": 11, "t": time.time(), "argv": ["python", "server.py"]},
                  {"kind": "bind_fail", "pid": 11, "t": time.time(), "addr": "('0.0.0.0', 8080)",
                   "err": "[WinError 10048] Only one usage of each socket address"},
                  {"kind": "connect_fail", "pid": 11, "t": time.time(), "addr": "('127.0.0.1', 8080)", "err": "refused"},
                  {"kind": "connect_fail", "pid": 11, "t": time.time(), "addr": "('127.0.0.1', 8080)", "err": "refused"}):
            f.write(json.dumps(e) + "\n")
    s = flight.summarize(d)
    assert "could not listen on ('0.0.0.0', 8080)" in s and "×2" in s and "no clean exit" in s, s


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
