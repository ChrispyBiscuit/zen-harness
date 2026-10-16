"""Evidence mode (the default for code work): writes are free, a passing test run is the proof, any runner counts.
Module mode only for from-scratch builds, with a slim contract and a generic test command. Refusals name the fix."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_tools as at
import problem_workbook as pw
import verification_ledger as vl
from verification_ledger import Ledger, PolicyError


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "util.py").write_text("X = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return root


def ledger_for(root, tmp_path, project_build=False):
    led = Ledger(root, tmp_path / "store")
    led.decide_mode(SimpleNamespace(project_build=project_build))
    return led


def test_mode_is_modules_only_for_a_from_scratch_build(tmp_path, repo):
    empty = tmp_path / "new"
    empty.mkdir()
    assert ledger_for(empty, tmp_path, project_build=True).mode == "modules"
    assert ledger_for(repo, tmp_path, project_build=True).mode == "evidence"     # existing code: evidence
    other = tmp_path / "other"
    other.mkdir()
    assert ledger_for(other, tmp_path, project_build=False).mode == "evidence"  # not a build request


def test_evidence_mode_writes_freely_and_needs_a_passing_run_before_done(tmp_path, repo, monkeypatch):
    led = ledger_for(repo, tmp_path)
    c = at.Ctx([str(repo)]); c.cwd = str(repo); c.build_test_record = True; c.conv_id = "chat1"
    c.tracker_path = str(repo / "PROGRESS.md")
    monkeypatch.setattr(vl, "for_ctx", lambda ctx: led)
    r = at.Registry(c, enabled={"write_file", "update_tracker"})
    r.call("update_tracker", {"new": True, "goal": "Fix add", "milestones": [{"title": "Fix", "subtasks": [
        {"step": "Change add to handle floats"}, {"step": "Update the README"}]}]})
    at.note_seen(c, str(repo / "app.py"))
    r.call("write_file", {"path": "app.py", "content": "def add(a, b):\n    return float(a) + float(b)\n"})
    assert (repo / "app.py").read_text().startswith("def add")             # not blocked: no module needed
    assert led.pending("chat1") == ["app.py"]
    with pytest.raises(at.ToolError) as e:
        r.call("update_tracker", {"updates": [{"id": "1.1", "status": "done"}]})
    msg = str(e.value)
    assert "app.py" in msg and "run_tests" in msg and "cargo test" in msg and "'blocked'" in msg
    r.call("update_tracker", {"updates": [{"id": "1.2", "status": "done"}]})   # a docs item isn't held up
    led.note_test_run("chat1", "pytest", 1, 0, True, started=9e18)
    assert led.pending("chat1") == []
    r.call("update_tracker", {"updates": [{"id": "1.1", "status": "done"}]})


def test_a_partial_run_covers_its_test_files_and_what_they_import(tmp_path, repo):
    led = ledger_for(repo, tmp_path)
    before = led.inventory()
    (repo / "app.py").write_text("def add(a, b):\n    return b + a\n")
    (repo / "util.py").write_text("X = 2\n")
    led.record_changes(before, "write", "chat1")
    assert led.pending("chat1") == ["app.py", "util.py"]
    led.note_test_run("chat1", "pytest tests/test_app.py", 1, 0, False, started=9e18, targets=["tests/test_app.py"])
    assert led.pending("chat1") == ["util.py"]                               # test_app imports app, not util
    led.note_test_run("chat1", "pytest", 0, 2, True, started=9e18)          # a failing run covers nothing
    assert led.pending("chat1") == ["util.py"]


def test_changes_are_per_chat_and_runtime_data_is_not_code(tmp_path, repo):
    led = ledger_for(repo, tmp_path)
    before = led.inventory()
    (repo / "util.py").write_text("X = 3\n")
    (repo / "data.json").write_text("[]")
    led.record_changes(before, "exec", "chat2")
    assert led.pending("chat2") == ["util.py"] and led.pending("chat1") == []


def test_any_runner_through_the_shell_is_a_receipt(tmp_path, repo, monkeypatch):
    led = ledger_for(repo, tmp_path)
    c = SimpleNamespace(conv_id="chat1", resolve=lambda p, *a: str(repo / p))
    monkeypatch.setattr(vl, "for_ctx", lambda ctx: led)
    before = led.inventory()
    (repo / "util.py").write_text("X = 4\n")
    led.record_changes(before, "write", "chat1")
    at._record_test_run(c, "powershell", {"command": "cargo test"}, "exit code 0\ntest result: ok. 7 passed; 0 failed",
                        7, 0, 9e18)
    assert led.pending("chat1") == []
    assert led.state["test_runs"][-1]["command"] == "cargo test"
    for runner, out in [("go test ./...", "exit code 1\n--- FAIL: TestX"), ("dotnet test", "exit code 1\nFailed: 1, Passed: 4")]:
        p_, f_ = at.test_counts(out)
        assert f_ >= 1, runner


def test_module_tools_explain_evidence_mode(tmp_path, repo, monkeypatch):
    led = ledger_for(repo, tmp_path)
    c = at.Ctx([str(repo)]); c.cwd = str(repo); c.build_test_record = True
    monkeypatch.setattr(vl, "for_ctx", lambda ctx: led)
    r = at.Registry(c, enabled={"module_contract"})
    text, _ = r.call("module_contract", {"id": "m", "sources": ["app.py"], "tests": ["tests/test_app.py"]})
    assert "Not needed here" in text and "run_tests" in text


def test_slim_contract_and_a_generic_test_command(tmp_path):
    root = tmp_path / "rusty"
    root.mkdir()
    led = Ledger(root, tmp_path / "store")
    led.state["mode"] = "modules"
    led.save()
    assert "Contract recorded" in led.declare({"id": "core", "sources": ["src/lib.rs"], "tests": ["tests/it.rs"],
                                               "test_command": 'python -c "print(\'3 passed\')"'})
    m = led.state["modules"]["core"]
    assert m["framework"] == "command" and m["checks"] == [] and m["purpose"] == ""
    (root / "src").mkdir(); (root / "src" / "lib.rs").write_text("fn x() {}")
    (root / "tests").mkdir(); (root / "tests" / "it.rs").write_text("#[test] fn t() {}")
    led.begin("core")
    out = led.verify("core", SimpleNamespace(should_stop=lambda: False, display=None))
    assert '"status": "passed"' in out
    with pytest.raises(PolicyError) as e:
        led.declare({"id": "bad id", "sources": ["a.rs"], "tests": ["t.rs"]})
    assert "no spaces" in str(e.value) and "'bad_id'" in str(e.value)
    with pytest.raises(PolicyError) as e:
        led.declare({"id": "rs", "sources": ["a.rs"], "tests": ["t.rs"]})
    assert "test_command" in str(e.value)


def test_workbook_refusals_name_the_field_and_the_fix(tmp_path):
    wb = pw.Workbook("conv-x", tmp_path / "wb")
    with pytest.raises(pw.PolicyError) as e:
        wb.plan({"goal": "g", "constraints": [], "acceptance": [{"id": "a1", "condition": "tests pass"}],
                 "questions": [{"id": "q1", "question": "why?", "why": "blocks", "hypotheses": ["x", "y"],
                                "dependencies": [], "acceptance": ["all client tests pass"]}]})
    msg = str(e.value)
    assert "acceptance ids (a1)" in msg and "all client tests pass" in msg and "acceptance=['a1']" in msg
    with pytest.raises(pw.PolicyError) as e:
        pw.identifier("server returns ok", "target")
    assert "target" in str(e.value) and "'server_returns_ok'" in str(e.value)
