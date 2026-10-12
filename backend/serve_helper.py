"""The test helper the add_server_test_helper tool writes into a project: start a server (or any program that listens
on a port) for a test, wait until it accepts connections, stop it afterwards. Kept as a string so the frozen app
ships it too. Standard library only, so it works in any project.

Why: getting a project's own tests to start and stop its server was the costliest part of small-model builds (a run
spent ~40 minutes on tests that hung or errored because the server never came up inside them), and none of it
was the app's logic. A tested helper takes that whole class of bug off the table.
"""

SOURCE = r'''"""serve_helper — start a program for a test, wait until it listens, stop it afterwards (standard library only).

    from serve_helper import serve

    def test_ping():
        with serve(["python", "server.py", "--port", "{port}"]) as srv:   # "{port}" becomes a free port
            status, body = srv.get("/api/ping")
            assert status == 200 and body == {"ok": True}

    def test_client_add():
        with serve(["python", "server.py", "--port", "{port}"]) as srv:
            r = srv.run(["python", "client.py", "--url", "{url}", "add", "x"])  # a client against the same server
            assert r.returncode == 0, r.stdout + r.stderr

- serve() picks a free port, puts it in the command wherever "{port}" appears (and in the PORT environment
  variable), runs the command from the project folder, and waits until the port accepts connections.
- If the program exits or never opens the port, serve() raises AT ONCE with the program's own output — no hang.
- srv.url is "http://127.0.0.1:<port>"; srv.get / srv.post / srv.request return (status, body) where body is the
  parsed JSON, or the text when it isn't JSON. HTTP errors are returned too, not raised.
- srv.output() is everything the server printed so far (useful in an assert message).
- "python" as the first word of a command means the Python running the tests.
"""
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the folder above tests/


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _argv(cmd, port, url):
    parts = cmd.split() if isinstance(cmd, str) else list(cmd)
    parts = [str(p).replace("{port}", str(port)).replace("{url}", url) for p in parts]
    if parts and parts[0] in ("python", "python3", "py"):
        parts[0] = sys.executable
    return parts


class Server:
    def __init__(self, proc, port, log_path, cwd, env):
        self.proc, self.port, self._log, self.cwd, self.env = proc, port, log_path, cwd, env
        self.url = f"http://127.0.0.1:{port}"

    def output(self):
        try:
            with open(self._log, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def request(self, method, path, body=None, headers=None, timeout=10):
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.url + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def run(self, cmd, timeout=30, input=None):
        """Run another program (e.g. the project's client) against this server: "{port}"/"{url}" are filled in."""
        return subprocess.run(_argv(cmd, self.port, self.url), cwd=self.cwd, env=self.env, input=input,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


@contextlib.contextmanager
def serve(cmd, cwd=None, port=None, ready_timeout=15, env=None):
    port = port or free_port()
    url = f"http://127.0.0.1:{port}"
    cwd = cwd or PROJECT
    full_env = dict(os.environ, PORT=str(port), PYTHONUNBUFFERED="1", **(env or {}))
    fd, log_path = tempfile.mkstemp(prefix="serve_", suffix=".log")
    log = os.fdopen(fd, "w", encoding="utf-8")
    argv = _argv(cmd, port, url)
    proc = subprocess.Popen(argv, cwd=cwd, env=full_env, stdout=log, stderr=subprocess.STDOUT)
    srv = Server(proc, port, log_path, cwd, full_env)
    try:
        deadline = time.time() + ready_timeout
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"the server exited (code {proc.returncode}) before listening on port {port}. "
                                   f"Command: {argv}\nIts output:\n{srv.output()[-3000:]}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                pass
            if time.time() > deadline:
                raise RuntimeError(f"the server did not listen on port {port} within {ready_timeout}s — does it "
                                   f"take the port from the command line or PORT, and bind 127.0.0.1 or 0.0.0.0? "
                                   f"Command: {argv}\nIts output:\n{srv.output()[-3000:]}")
            time.sleep(0.1)
        yield srv
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log.close()
        try:
            os.remove(log_path)
        except OSError:
            pass
'''
