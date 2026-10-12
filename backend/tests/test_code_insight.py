"""Tests for code_insight.py (warn-only coherence checks) and its wiring into the tools. Runs under pytest, or
standalone: python tests/test_code_insight.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("LWS_DATA_DIR", tempfile.mkdtemp(prefix="insight_data_"))
os.environ.setdefault("LWS_NO_TELEGRAM", "1")
os.environ.setdefault("LWS_NO_SCHEDULER", "1")

import agent_tools as at  # noqa: E402
import code_insight as ci  # noqa: E402

DESIGN = """# Design

## CLI contract
`python client.py add --person NAME --amount N`

## API
| # | Method | Path |
|---|---|---|
| 1 | GET | `/api/ping` |
| 2 | POST | `/api/add` |
"""


def _project(files):
    d = tempfile.mkdtemp(prefix="insight_")
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
    return d


def test_contract_drift_flags_and_routes():
    d = _project({
        "DESIGN.md": DESIGN,
        "client.py": "import argparse\np = argparse.ArgumentParser()\np.add_argument('--user')\n"
                     "p.add_argument('--amount')\n",
        "server.py": "ROUTES = {'/api/ping': 1}\n",
    })
    note = ci.contract_drift(os.path.join(d, "client.py"))
    assert "--person" in note and "--user" in note, note
    assert "/api/add" in note and "/api/ping" not in note, note
    assert ci.contract_drift(os.path.join(d, "client.py")) == "", "the same note must not repeat"


def test_contract_drift_silent_when_matching():
    d = _project({
        "DESIGN.md": DESIGN,
        "client.py": "import argparse\np = argparse.ArgumentParser()\np.add_argument('--person')\n"
                     "p.add_argument('--amount')\n",
        "server.py": "ROUTES = {'/api/ping': 1, '/api/add': 2}\n",
    })
    assert ci.contract_drift(os.path.join(d, "server.py")) == ""
    assert ci.contract_drift(os.path.join(d, "README.md")) == "", "only Python files are checked"


def test_regression_note():
    prev = {"passed": 10, "failed": 0, "failing": []}
    cur = {"passed": 8, "failed": 2, "failing": ["tests/test_a.py::test_x", "tests/test_a.py::test_y"]}
    note = ci.regression(prev, cur)
    assert "Regression" in note and "test_x" in note and "10" in note and "8" in note, note
    assert ci.regression(cur, prev) == "", "more passing is not a regression"
    assert ci.regression(None, cur) == ""
    same = {"passed": 7, "failed": 3, "failing": cur["failing"]}
    assert ci.regression(cur, same) == "", "no newly failing test -> no note"


def test_failing_names():
    out = "FAILED tests/test_a.py::test_x - assert 1\nFAILED tests/test_a.py::test_y\nok\n"
    assert ci.failing_names(out) == ["tests/test_a.py::test_x", "tests/test_a.py::test_y"]


def test_signature_impact_names_callers():
    d = _project({
        ".git/HEAD": "",
        "util.py": "def total(rows):\n    return sum(rows)\n",
        "report.py": "from util import total\n\nprint(total([1, 2]))\n",
    })
    old = open(os.path.join(d, "util.py")).read()
    new = "def total(rows, currency):\n    return sum(rows)\n"
    note = ci.signature_impact(os.path.join(d, "util.py"), old, new)
    assert "total" in note and "report.py" in note, note
    assert ci.signature_impact(os.path.join(d, "util.py"), old, old) == ""


def test_tests_map():
    d = _project({"db.py": "X = 1\n", "server.py": "import db\n",
                  "tests/test_db.py": "import db\n", "tests/test_srv.py": "from server import x\nimport db\n"})
    m = ci.tests_map(d)
    assert "test_db.py → db" in m and "server" in m, m


def test_edit_reports_callers_via_registry():
    d = _project({".git/HEAD": "", "util.py": "def total(rows):\n    return sum(rows)\n",
                  "report.py": "from util import total\n\nprint(total([1, 2]))\n"})
    reg = at.Registry(at.Ctx([d]), None, None)
    reg.call("read_file", {"path": os.path.join(d, "util.py")})
    text, _ = reg.call("edit_file", {"path": os.path.join(d, "util.py"), "old": "def total(rows):",
                                     "new": "def total(rows, currency):"})
    assert "Check the callers" in text and "report.py" in text, text


def test_done_with_failing_tests_gets_a_note():
    d = tempfile.mkdtemp(prefix="insight_trk_")
    c = at.Ctx([d])
    c.conv_id = "insight-trk"
    at.t_update_tracker({"new": True, "goal": "g", "milestones": [{"title": "A", "subtasks": ["a", "b"]}]}, c)
    at._hist(c)["last_tests"] = {"passed": 3, "failed": 2, "t": "12:00:00", "failing": ["t1", "t2"]}
    out = at.t_update_tracker({"updates": [{"id": "1.1", "status": "done"}]}, c)
    assert "2 failing test(s)" in out and "1.1" in out, out
    at._hist(c)["last_tests"] = {"passed": 5, "failed": 0, "t": "12:01:00", "failing": []}
    out = at.t_update_tracker({"updates": [{"id": "1.2", "status": "done"}]}, c)
    assert "failing test" not in out, out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
