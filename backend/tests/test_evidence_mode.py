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


def test_the_workbook_takes_a_plain_text_plan_and_assigns_ids(tmp_path):
    wb = pw.Workbook("conv-text", tmp_path / "wb")
    out = wb.plan({"text": "GOAL: client tests pass against the server\nACCEPT: all client tests pass\n"
                           "ACCEPT: server tests still pass\n"
                           "QUESTION: does do_POST drop the saved record? | because: 5 KeyError 'amount' failures\n"
                           "  MAYBE: the server returns {'ok': true} instead of the record\n"
                           "  MAYBE: the client reads the wrong field\n  CHECKS: 1\n"
                           "QUESTION: is the host saved by the add command?\n  MAYBE: it is never written\n  NEEDS: 1\n"})
    st = wb.state
    assert set(st["acceptance"]) == {"a1", "a2"} and set(st["questions"]) == {"q1", "q2"}
    assert st["questions"]["q1"]["acceptance"] == ["a1"] and st["questions"]["q2"]["dependencies"] == ["q1"]
    assert len(st["questions"]["q2"]["hypotheses"]) == 2                  # 'something else' added
    wb.plan({"text": "GOAL: anything\nACCEPT: all client tests pass\nQUESTION: does do_POST drop the saved record?\n"
                     "  MAYBE: x\n  MAYBE: y\n"})                          # same text: same ids, goal kept
    assert st["goal"] == wb.state["goal"] == "client tests pass against the server"


def test_a_plain_text_checklist_and_done_ids(tmp_path):
    c = at.Ctx([str(tmp_path)]); c.cwd = str(tmp_path); c.tracker_path = str(tmp_path / "PROGRESS.md")
    r = at.Registry(c, enabled={"update_tracker"})
    r.call("update_tracker", {"checklist": "GOAL: roommate expense tracker\n## Store | done when: store tests pass\n"
                                           "- [x] write PRD\n- [ ] write store.py\n## Server\n- [ ] write server.py"})
    t = c.tracker
    assert t["goal"] == "roommate expense tracker" and [m["title"] for m in t["milestones"]] == ["Store", "Server"]
    assert t["milestones"][0]["done_when"] == "store tests pass"
    assert t["milestones"][0]["subtasks"][0]["status"] == "pending"     # a new plan starts with nothing done
    r.call("update_tracker", {"checklist": "GOAL: roommate expense tracker\n## Store\n- [x] write PRD\n"
                                           "- [ ] write store.py\n## Server\n- [ ] write server.py"})
    t = c.tracker
    assert t["milestones"][0]["subtasks"][0]["status"] == "done"        # the same plan again: its marks count
    r.call("update_tracker", {"done": "1.2", "started": "2.1"})
    t = c.tracker
    assert t["milestones"][0]["subtasks"][1]["status"] == "done" and t["milestones"][1]["subtasks"][0]["status"] == "in_progress"
    with pytest.raises(at.ToolError) as e:
        r.call("update_tracker", {"checklist": "just some words"})
    assert "- [ ] a concrete step" in str(e.value)


def test_compact_schemas_and_catalog_are_small():
    import json
    reg = at.Registry(at.Ctx(["."]))
    assert len(json.dumps(reg.schemas(["update_tracker"], compact=True))) // 4 < 350
    assert len(json.dumps(reg.schemas(["powershell"], compact=True))) // 4 < 230
    text = at.tool_catalog_compact(reg, {"read_file"})
    assert len(text) // 4 < 700 and "read_file*" in text and "[files]" in text


def test_module_and_workbook_tools_load_only_when_in_use(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from offline_suite import engine
    e = engine(compact_prompt=True)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    e.ctx.cwd = e.ctx.verification_root = str(fresh)
    e.ctx.build_test_record = e.ctx.problem_workbook = True
    e.ctx.project_build = True
    e._decide_turn_tools()
    assert e._module_tools and not e._workbook_tools                 # from scratch: modules; no workbook open yet
    core = e._core_names()
    assert "module_contract" in core and "problem_plan" not in core and "history_search" not in core
    e.ctx.project_build = False
    e._decide_turn_tools()
    assert not e._module_tools and "module_contract" not in e._core_names()


def test_a_passing_run_through_the_registry_clears_the_changes(tmp_path, monkeypatch):
    # run 2405352c: the gate wrapper's stale copy of the ledger overwrote the receipt the test run had just saved,
    # so 'Not marked done' kept coming after a passing 39-test run
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    store = tmp_path / "store"

    def fresh(ctx):                                  # a new Ledger object per call, as in the app
        led = Ledger(root, store)
        led.decide_mode(ctx)
        return led
    monkeypatch.setattr(vl, "for_ctx", fresh)
    c = at.Ctx([str(root)]); c.cwd = str(root); c.build_test_record = True; c.conv_id = "chat1"
    r = at.Registry(c, enabled={"write_file", "run_tests"})
    at.note_seen(c, str(root / "app.py"))
    r.call("write_file", {"path": "app.py", "content": "def add(a, b):\n    return b + a\n"})
    assert fresh(c).pending("chat1") == ["app.py"]
    text, _ = r.call("run_tests", {"path": str(root)})
    assert text.startswith("tests: PASSED")
    assert fresh(c).pending("chat1") == []


def test_runtime_data_named_in_a_build_answer_is_not_a_missing_file():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from offline_suite import engine
    e = engine()
    e._build_profile = True
    text = "Done. Expenses are stored in data/expenses.json and the report is saved to report.md."
    assert e._missing_claimed_files(text) == ["report.md"]          # the document claim is still checked


def test_a_run_covers_the_conftest_pytest_loads_and_the_refusal_says_run_the_folder(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "app.py").write_text("X = 1\n")
    (root / "tests" / "test_app.py").write_text("import app\n\ndef test_x():\n    assert app.X == 1\n")
    (root / "tests" / "conftest.py").write_text("import sys\n")
    led = ledger_for(root, tmp_path)
    before = led.inventory()
    for f in ("app.py", "tests/conftest.py"):
        (root / f).write_text((root / f).read_text() + "# changed\n")
    led.record_changes(before, "write", "c1")
    msg = led.evidence_message("2.1", led.pending("c1"))
    led.note_test_run("c1", "run_tests tests/test_app.py", 1, 0, False, started=9e18, targets=["tests/test_app.py"])
    assert led.pending("c1") == []                                    # conftest.py covered with the run
    (root / "util.py").write_text("Y = 2\n")
    before = led.inventory()
    (root / "util.py").write_text("Y = 3\n")
    led.record_changes(before, "write", "c1")
    msg = led.evidence_message("2.2", led.pending("c1"))
    assert "Run the whole suite once" in msg and "run_tests(path=" in msg


def test_simple_done_ticks_count_as_progress_and_a_batch_hint_appears(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from offline_suite import engine
    e = engine()
    e.ctx.tracker = {"goal": "g", "milestones": [{"title": "Build", "status": "pending", "subtasks": [
        {"step": "a", "status": "done"}, {"step": "b", "status": "pending"}, {"step": "c", "status": "pending"}]}]}
    assert e._closes_open_items({"done": "1.2"})              # the simple field counts as closing an item
    assert not e._closes_open_items({"done": "1.1"})          # already done: not progress
    c = at.Ctx([str(tmp_path)]); c.cwd = str(tmp_path); c.tracker_path = str(tmp_path / "PROGRESS.md")
    r = at.Registry(c, enabled={"update_tracker"})
    r.call("update_tracker", {"checklist": "GOAL: g\n## Build\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d"})
    text, _ = r.call("update_tracker", {"done": "1.1"})
    assert "done='1.2, 1.3, 1.4'" in text
    text2, _ = r.call("update_tracker", {"done": "1.2"})
    assert "tick them together" not in text2                   # once per chat


def test_run_tests_finds_and_runs_javascript_tests_in_a_folder(tmp_path):
    import shutil as _sh
    if not _sh.which("node"):
        pytest.skip("node not installed")
    root = tmp_path / "game"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "package.json").write_text('{"type": "module"}')
    (root / "src" / "waves.js").write_text("export const count = (n) => n * 2;\n")
    (root / "tests" / "test_waves.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { count } from '../src/waves.js';\ntest('doubles', () => assert.equal(count(2), 4));\n")
    c = at.Ctx([str(root)]); c.cwd = str(root)
    r = at.Registry(c, enabled={"run_tests"})
    text, _ = r.call("run_tests", {"path": str(root)})
    assert text.startswith("tests: PASSED") and "1 passed" in text, text[:300]


def test_a_task_the_agent_rates_as_needing_no_plan_is_never_a_module_build():
    # battery code-045: "write sort_2.py" matched the build words, the empty folder made it modules, and gpt-5-nano
    # was refused 8 times in 28 turns by the build gate on a 2-line script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from offline_suite import engine
    e = engine()
    e._build_profile = True
    e._effort = {"plan": "none", "think": "low", "size": "quick"}
    assert e._project_build() is False                       # the agent's own rating: no plan, so no modules
    assert e._project_build(fresh=False) is True             # a "continue" keeps the build it already was
    e._effort = {"plan": "full", "think": "high", "size": "deep"}
    assert e._project_build() is True                        # a planned build still gets modules
    e._effort = None
    assert e._project_build() is True                        # no rating (no lean pass): as before
    e._build_profile = False
    assert e._project_build() is False
