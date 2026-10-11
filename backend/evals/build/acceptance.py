"""Hidden acceptance tests for the "Ledger" build (SPEC.md). Black-box: starts the built server, drives the API and
the CLI over HTTP, restarts the server to check persistence, runs the project's own tests.

  python acceptance.py <project folder>  -> prints one line per check and a JSON summary as the last line
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROJ = os.path.abspath(sys.argv[1])
PY = sys.executable
results = []


def check(name, fn):
    try:
        ok = bool(fn())
    except Exception as e:
        ok = False
        name += f"  [{type(e).__name__}: {str(e)[:80]}]"
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name, flush=True)
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TMP = tempfile.mkdtemp(prefix="ledger_acc_")
ENV = dict(os.environ, LEDGER_DB=os.path.join(TMP, "acc.db"), LEDGER_PORT=str(free_port()), PYTHONIOENCODING="utf-8")
URL = f"http://127.0.0.1:{ENV['LEDGER_PORT']}"


def start():
    p = subprocess.Popen([PY, "server.py"], cwd=PROJ, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", int(ENV["LEDGER_PORT"])), timeout=0.3).close()
            return p
        except OSError:
            time.sleep(0.2)
    return p


def call(method, path, body=None, token=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw.strip() else None
        except ValueError:
            return e.code, None


def cli(*args):
    r = subprocess.run([PY, "cli.py", "--url", URL, *args], cwd=PROJ, env=ENV, capture_output=True, text=True,
                       timeout=30, creationflags=0x08000000)
    return r.returncode, r.stdout.strip()


st = {}
srv = None
check("files: storage.py, server.py, cli.py, README.md, tests/", lambda: all(
    os.path.exists(os.path.join(PROJ, f)) for f in ("storage.py", "server.py", "cli.py", "README.md", "tests")))
try:
    srv = start()
    check("server starts and listens on LEDGER_PORT", lambda: srv.poll() is None and call("GET", "/expenses")[0] == 401)
    check("POST /users -> 201 with id", lambda: (lambda s, b: s == 201 and "id" in b)(*call("POST", "/users", {"name": "ann", "password": "pw1"})))
    check("duplicate user -> 409", lambda: call("POST", "/users", {"name": "ann", "password": "x"})[0] == 409)
    call("POST", "/users", {"name": "bob", "password": "pw2"})
    check("bad login -> 401", lambda: call("POST", "/login", {"name": "ann", "password": "nope"})[0] == 401)

    def login():
        s, b = call("POST", "/login", {"name": "ann", "password": "pw1"})
        st["a"] = b["token"]
        st["b"] = call("POST", "/login", {"name": "bob", "password": "pw2"})[1]["token"]
        return s == 200 and st["a"]
    check("login -> 200 token", login)
    check("no/invalid token -> 401", lambda: call("GET", "/expenses", token="garbage")[0] == 401 and call("GET", "/summary?month=2026-10")[0] == 401)

    def add_all():
        rows = [(12.5, "food", "2026-10-03", "lunch"), (40, "travel", "2026-10-01", None), (7.25, "food", "2026-09-28", "snack"),
                (100, "rent", "2026-10-15", None)]
        ids = []
        for a, c, d, n in rows:
            s, b = call("POST", "/expenses", {"amount": a, "category": c, "date": d, **({"note": n} if n else {})}, st["a"])
            if s != 201 or "id" not in b:
                return False
            ids.append(b["id"])
        st["ids"] = ids
        call("POST", "/expenses", {"amount": 999, "category": "food", "date": "2026-10-05"}, st["b"])
        return True
    check("POST /expenses -> 201 with id (x4)", add_all)
    check("invalid expense -> 400 (amount<=0, bad date, empty category)", lambda: all(
        call("POST", "/expenses", b, st["a"])[0] == 400 for b in (
            {"amount": -5, "category": "food", "date": "2026-10-01"}, {"amount": 5, "category": "food", "date": "10/01/2026"},
            {"amount": 5, "category": "", "date": "2026-10-01"})))
    check("GET /expenses: own only, sorted by date", lambda: (lambda s, b: s == 200 and [x["date"] for x in b] ==
                                                               ["2026-09-28", "2026-10-01", "2026-10-03", "2026-10-15"])(*call("GET", "/expenses", token=st["a"])))
    check("filter ?category=food", lambda: [x["amount"] for x in call("GET", "/expenses?category=food", token=st["a"])[1]] in ([7.25, 12.5],))
    check("filter ?from=&to= inclusive", lambda: [x["date"] for x in call("GET", "/expenses?from=2026-10-01&to=2026-10-03", token=st["a"])[1]] == ["2026-10-01", "2026-10-03"])
    check("GET /expenses/<id> -> 200; other user's -> 404", lambda: call("GET", f"/expenses/{st['ids'][0]}", token=st["a"])[0] == 200
          and call("GET", f"/expenses/{st['ids'][0]}", token=st["b"])[0] == 404)
    check("summary month total + by_category", lambda: (lambda s, b: s == 200 and abs(b["total"] - 152.5) < 0.006
                                                       and abs(b["by_category"].get("food", 0) - 12.5) < 0.006
                                                       and abs(b["by_category"].get("rent", 0) - 100) < 0.006)(*call("GET", "/summary?month=2026-10", token=st["a"])))
    check("DELETE own -> 204, other's -> 404", lambda: call("DELETE", f"/expenses/{st['ids'][3]}", token=st["b"])[0] == 404
          and call("DELETE", f"/expenses/{st['ids'][3]}", token=st["a"])[0] == 204
          and call("GET", f"/expenses/{st['ids'][3]}", token=st["a"])[0] == 404)

    # CLI
    check("cli register prints an id", lambda: (lambda rc, out: rc == 0 and out.split()[-1].strip().isalnum())(*cli("register", "cara", "pw3")))

    def cli_login():
        rc, out = cli("login", "cara", "pw3")
        st["c"] = out.split()[-1] if out else ""
        return rc == 0 and len(st["c"]) >= 8
    check("cli login prints a token", cli_login)
    check("cli add prints an id", lambda: all(cli("--token", st["c"], "add", a, c, d)[0] == 0 for a, c, d in
                                              (("9.99", "books", "2026-10-02"), ("20", "food", "2026-10-04"))))
    check("cli list: 'ID DATE CATEGORY AMOUNT' lines", lambda: (lambda rc, out: rc == 0 and len(out.splitlines()) == 2
                                                              and "2026-10-02" in out.splitlines()[0] and "9.99" in out.splitlines()[0]
                                                              and "20.00" in out)(*cli("--token", st["c"], "list")))
    check("cli list --category", lambda: (lambda rc, out: rc == 0 and len(out.splitlines()) == 1 and "books" in out)(
        *cli("--token", st["c"], "list", "--category", "books")))
    check("cli summary: 'total: 29.99' then categories", lambda: (lambda rc, out: rc == 0 and out.splitlines()[0].strip() == "total: 29.99"
                                                                 and any(l.strip() == "food: 20.00" for l in out.splitlines()[1:]))(*cli("--token", st["c"], "summary", "2026-10")))
finally:
    if srv:
        srv.terminate()
        srv.wait(10)

# persistence across a restart
try:
    srv = start()
    check("data survives a restart", lambda: len(call("GET", "/expenses", token=call("POST", "/login", {"name": "ann", "password": "pw1"})[1]["token"])[1]) == 3)
finally:
    if srv:
        srv.terminate()
        srv.wait(10)

check("project's own tests pass", lambda: subprocess.run([PY, "-m", "unittest", "discover", "-s", "tests"], cwd=PROJ, capture_output=True,
                                                          timeout=180, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                                          creationflags=0x08000000).returncode == 0)
shutil.rmtree(TMP, ignore_errors=True)
print(json.dumps({"passed": sum(ok for _, ok in results), "total": len(results)}))
