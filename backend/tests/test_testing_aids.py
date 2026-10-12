"""Tests for the testing aids: the server test helper (serve_helper.SOURCE), re-running failed tests first, the tip
when a test failure looks like a server that never came up, and the note on re-writing a big file for a small change.
Runs under pytest, or standalone: python tests/test_testing_aids.py"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("LWS_DATA_DIR", tempfile.mkdtemp(prefix="aids_data_"))
os.environ.setdefault("LWS_NO_TELEGRAM", "1")
os.environ.setdefault("LWS_NO_SCHEDULER", "1")

import agent_tools as at  # noqa: E402

SERVER = """import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ["PORT"])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"ok": True}).encode()
        self.send_response(200 if self.path == "/api/ping" else 404)
        self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
print("listening", port, flush=True)
HTTPServer(("127.0.0.1", port), H).serve_forever()
"""

CLIENT = """import sys, urllib.request
print(urllib.request.urlopen(sys.argv[sys.argv.index("--url") + 1] + "/api/ping").read().decode())
"""

TEST = """from serve_helper import serve
def test_ping():
    with serve(["python", "server.py", "--port", "{port}"]) as srv:
        status, body = srv.get("/api/ping")
        assert status == 200 and body == {"ok": True}, (status, body, srv.output())
        assert srv.get("/nope")[0] == 404
        r = srv.run(["python", "client.py", "--url", "{url}"])
        assert '"ok": true' in r.stdout, r.stdout + r.stderr
def test_crash_reports_output():
    import pytest
    with pytest.raises(RuntimeError, match="exited"):
        with serve(["python", "-c", "print('boom'); raise SystemExit(3)"]):
            pass
"""


def _proj():
    d = tempfile.mkdtemp(prefix="aids_")
    open(os.path.join(d, "server.py"), "w").write(SERVER)
    open(os.path.join(d, "client.py"), "w").write(CLIENT)
    return d


def test_helper_written_and_works():
    d = _proj()
    c = at.Ctx([d])
    c.cwd = d
    out = at.t_add_server_test_helper({"path": os.path.join(d, "tests")}, c)
    assert "from serve_helper import serve" in out, out
    open(os.path.join(d, "tests", "test_srv.py"), "w").write(TEST)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=d,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already there" in at.t_add_server_test_helper({"path": os.path.join(d, "tests")}, c)


def test_rerun_failed_first_then_full_suite():
    d = tempfile.mkdtemp(prefix="aids_rerun_")
    os.makedirs(os.path.join(d, "tests"))
    tf = os.path.join(d, "tests", "test_x.py")
    open(tf, "w").write("def test_a():\n    assert True\n\ndef test_b():\n    assert False\n")
    c = at.Ctx([d])
    c.cwd = d
    c.conv_id = "aids-rerun"                          # its own file history (contexts without an id share one)
    reg = at.Registry(c, None, None)
    first, _ = reg.call("run_tests", {"path": os.path.join(d, "tests")})
    assert first.startswith("tests: FAILED") and "1 failed, 1 passed" in first, first
    again, _ = reg.call("run_tests", {"path": os.path.join(d, "tests")})
    assert again.startswith(at.SUBSET_HEAD) and "1 failed" in again and "passed" not in again.split("\n")[0], again
    assert at._hist(c)["last_tests"]["passed"] == 1, "a subset run must not replace the last full result"
    open(tf, "w").write("def test_a():\n    assert True\n\ndef test_b():\n    assert True\n")
    fixed, _ = reg.call("run_tests", {"path": os.path.join(d, "tests")})
    assert fixed.startswith("tests: PASSED") and "2 passed" in fixed and "pass now" in fixed, fixed
    allrun, _ = reg.call("run_tests", {"path": os.path.join(d, "tests"), "all": True})
    assert allrun.startswith("tests: PASSED") and "2 passed" in allrun


def test_server_tip_only_when_it_fits():
    d = tempfile.mkdtemp(prefix="aids_tip_")
    fail = "tests: FAILED — 1 failed\nE   ConnectionRefusedError: [WinError 10061]"
    assert "add_server_test_helper" in at._server_tip(fail, d)
    assert at._server_tip("tests: FAILED — 1 failed\nE   assert 1 == 2", d) == ""
    open(os.path.join(d, "serve_helper.py"), "w").write("x")
    assert at._server_tip(fail, d) == "", "no tip once the helper is there"


def test_rewrite_of_big_file_for_small_change_gets_a_note():
    d = tempfile.mkdtemp(prefix="aids_rw_")
    p = os.path.join(d, "big.py")
    body = "".join(f"x{i} = {i}\n" for i in range(200))
    c = at.Ctx([d])
    reg = at.Registry(c, None, None)
    reg.call("write_file", {"path": p, "content": body})
    reg.call("read_file", {"path": p})
    out, _ = reg.call("write_file", {"path": p, "content": body.replace("x5 = 5", "x5 = 50")})
    assert "use edit_file" in out, out
    reg.call("read_file", {"path": p})
    out, _ = reg.call("write_file", {"path": p, "content": "".join(f"y{i} = {i}\n" for i in range(200))})
    assert "use edit_file" not in out, "a real rewrite gets no note"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
