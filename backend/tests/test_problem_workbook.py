import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import problem_workbook as pw


class Registry:
    def __init__(self, root):
        self.ctx = SimpleNamespace(resolve=lambda p: str(root / p), display=None)

    def canonical(self, name):
        return {"read": "read_file"}.get(name, name)

    def normalize_args(self, name, args):
        return args

    def get(self, name):
        return {"kind": "read"} if name in {"read_file", "calculate", "verify_module"} else None


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_text("observed A")
    return pw.Workbook("conversation", tmp_path / "store"), Registry(root), root


def plan(w, integration=True):
    return w.plan({"goal": "Recover correct behavior", "constraints": ["Use real observations"],
        "acceptance": [{"id": "works", "condition": "Actual integrated behavior matches requirements"}],
        "questions": [{"id": "cause", "question": "Is the expectation or implementation incorrect?",
            "why": "Avoid repairing a working implementation", "hypotheses": ["Wrong oracle", "Wrong implementation"],
            "dependencies": [], "acceptance": ["works"] if integration else []}]})


def experiment(w, r, **kw):
    a = {"question": "cause", "tool": "read_file", "arguments": {"path": "source.txt"},
         "inputs": ["source.txt"], "predictions": ["A implies oracle is wrong", "B implies implementation is wrong"],
         "rationale": "Inspect an independent observation"}
    a.update(kw)
    return w.experiment(a, r)


def execute(w, output="A", name="read_file", args=None, error=False, execution=None):
    args = args or {"path": "source.txt"}
    ticket = w.before(name, args)
    return w.observe(name, args, ticket, output, error=error, execution=execution)


def record(w, eid, action="resolve", target="cause", **kw):
    a = {"action": action, "target": target, "evidence": eid,
         "interpretation": "Observed A supports the wrong-oracle explanation", "next": "Validate integrated behavior"}
    a.update(kw)
    return w.record(a)


def test_executed_evidence_restart_and_integration_acceptance(env):
    w, r, root = env
    plan(w)
    experiment(w, r)
    eid = execute(w)
    assert Path(w.evidence(eid)["output"]).read_text() == "A"
    restored = pw.Workbook("conversation", w.folder.parent)
    record(restored, eid)
    record(restored, eid, "accept", "works")
    assert restored.state["acceptance"]["works"]["evidence"] == [eid]
    assert restored.state["questions"]["cause"]["status"] == "resolved"


def test_fabricated_or_unrelated_evidence_cannot_resolve(env):
    w, r, _ = env
    plan(w)
    unrelated = execute(w)
    experiment(w, r)
    eid = execute(w)
    for bad in ("f" * 32, unrelated):
        with pytest.raises(pw.PolicyError):
            record(w, bad)
    record(w, eid)


def test_source_change_reopens_and_invalidates_acceptance(env):
    w, r, root = env
    plan(w)
    experiment(w, r)
    eid = execute(w)
    record(w, eid)
    record(w, eid, "accept", "works")
    (root / "source.txt").write_text("new behavior")
    w.refresh()
    assert w.state["questions"]["cause"]["status"] == "open"
    assert not w.state["acceptance"]["works"]["evidence"]


def test_input_change_during_execution_not_a_resolution(env):
    w, r, root = env
    plan(w)
    experiment(w, r)
    ticket = w.before("read_file", {"path": "source.txt"})
    (root / "source.txt").write_text("changed")
    eid = w.observe("read_file", {"path": "source.txt"}, ticket, "new")
    with pytest.raises(pw.PolicyError):
        record(w, eid)
    record(w, eid, "inconclusive")
    experiment(w, r)
    record(w, execute(w, "new"))
    assert w.state["questions"]["cause"]["status"] == "resolved"


def test_three_identical_results_gate_even_with_unrelated_read(env):
    w, r, _ = env
    plan(w)
    execute(w)
    execute(w, "unrelated", args={"path": "other.txt"})
    execute(w)
    execute(w)
    assert w.state["gate"]
    with pytest.raises(pw.PolicyError):
        execute(w)                                          # the looping action itself
    execute(w, "other", args={"path": "unrelated.txt"})     # a different check still runs
    # Rewording the plan or changing only declared inputs cannot evade the gate.
    plan(w)
    with pytest.raises(pw.PolicyError):
        experiment(w, r, rationale="Entirely new wording", inputs=["other.txt"])
    experiment(w, r, tool="calculate", arguments={"expression": "1+1"})
    eid = execute(w, "2", name="calculate", args={"expression": "1+1"})
    record(w, eid, "supports")
    assert not w.state["gate"]
    assert w.state["questions"]["cause"]["status"] == "open"


def test_different_results_are_not_an_exact_repeat_loop(env):
    w, _, _ = env
    for i in range(40):
        execute(w, str(i))
    assert not w.state["gate"]
    assert len(w.state["evidence"]) == 40  # No experiment step cap.


def test_pending_experiment_is_single_use_and_cannot_switch(env):
    w, r, _ = env
    plan(w)
    experiment(w, r)
    with pytest.raises(pw.PolicyError):
        execute(w, name="calculate", args={"expression": "1+1"})
    eid = execute(w)
    with pytest.raises(pw.PolicyError):
        execute(w)
    with pytest.raises(pw.PolicyError):
        experiment(w, r)
    record(w, eid)
    with pytest.raises(pw.PolicyError):
        record(w, eid)


def test_unit_result_cannot_supply_integration_acceptance(env):
    w, r, _ = env
    plan(w, integration=False)
    experiment(w, r)
    eid = execute(w)
    record(w, eid)
    with pytest.raises(pw.PolicyError, match="in its acceptance"):    # names the question and how to link it
        record(w, eid, "accept", "works")


@pytest.mark.parametrize("execution", [{"exit": 1}, {"status": "no_tests"}, {"status": "uncovered_checks"}, {"status": "interrupted"}])
def test_failed_empty_or_interrupted_tests_cannot_accept(env, execution):
    w, r, _ = env
    plan(w)
    experiment(w, r)
    eid = execute(w, execution=execution)
    record(w, eid)  # A failed test can explain the cause, but not prove acceptance.
    with pytest.raises(pw.PolicyError):
        record(w, eid, "accept", "works")


def test_tool_exception_cannot_resolve_but_can_inform_next_attempt(env):
    w, r, _ = env
    plan(w)
    experiment(w, r)
    eid = execute(w, "missing dependency", error=True)
    with pytest.raises(pw.PolicyError):
        record(w, eid)
    record(w, eid, "inconclusive", interpretation="Environment blocked measurement", next="Inspect dependency installation")
    assert w.state["failed_approaches"][-1]["next"] == "Inspect dependency installation"


def test_contradictory_evidence_reopens_settled_question(env):
    w, r, _ = env
    plan(w)
    experiment(w, r)
    eid = execute(w)
    record(w, eid)
    with pytest.raises(pw.PolicyError):
        record(w, eid, "reopen")
    contradiction = execute(w, "B", args={"path": "independent.txt"})
    record(w, contradiction, "reopen", interpretation="Independent output contradicts the conclusion")
    assert w.state["questions"]["cause"]["status"] == "open"


def test_no_silent_goal_or_constraint_replacement(env):
    w, _, _ = env
    plan(w)
    with pytest.raises(pw.PolicyError):
        w.plan({"goal": "Easier goal", "constraints": [], "acceptance": [], "questions": []})


def test_dependency_cycle_rejected(env):
    w, _, _ = env
    plan(w)
    q = dict(w.state["questions"]["cause"], dependencies=["cause"])
    with pytest.raises(pw.PolicyError):
        w.plan({"goal": w.state["goal"], "constraints": w.state["constraints"], "acceptance": [], "questions": [q]})


def test_registry_real_read_full_capture_and_gate(tmp_path, monkeypatch):
    import agent_tools as at
    monkeypatch.setattr(at, "TRACKERS", str(tmp_path / "trackers"))
    c = at.Ctx([str(tmp_path)])
    c.cwd = str(tmp_path)
    c.conv_id = "real-registry"
    c.problem_workbook = True
    c.build_test_record = False
    r = at.Registry(c, enabled={"read_file", "problem_plan", "problem_experiment", "problem_record", "problem_status"})
    (tmp_path / "source.txt").write_text("same bytes")
    for _ in range(3):
        output, _ = r.call("read_file", {"path": "source.txt"})
        assert "Controller evidence:" in output
    w = pw.for_ctx(c)
    assert w.state["gate"]
    with pytest.raises(at.ToolError):
        r.call("read_file", {"path": "source.txt"})
    status, _ = r.call("problem_status", {})
    assert json.loads(status)["gate"]
    eid = w.state["evidence"][-1]
    evidence, _ = r.call("problem_status", {"evidence": eid})
    assert "same bytes" in json.loads(evidence)["exact_output"]


def test_full_capture_thread_local_not_clipped_and_errors_preserved(env, monkeypatch):
    w, r, _ = env
    monkeypatch.setattr(pw, "for_ctx", lambda c: w)
    def call():
        pw.capture(r.ctx, "x" * 10000, None)
        return "clipped", None
    pw.invoke(r, "read_file", {"path": "source.txt"}, call)
    assert len(Path(w.evidence(w.state["evidence"][-1])["output"]).read_text()) == 10000
    def fail():
        raise ValueError("actual failure")
    with pytest.raises(pw.PolicyError, match="Controller evidence"):
        pw.invoke(r, "read_file", {"path": "different.txt"}, fail)
    assert w.evidence(w.state["evidence"][-1])["error"]


def test_conversation_isolation(env):
    w, _, _ = env
    plan(w)
    other = pw.Workbook("other-conversation", w.folder.parent)
    assert other.state["goal"] is None


def test_checkpoint_preserves_problem_state_without_model_synthesis(tmp_path, monkeypatch):
    import context_checkpoint as cp
    import agent_tools as at
    monkeypatch.setenv("LWS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(at, "TRACKERS", str(tmp_path / "trackers"))
    ctx = SimpleNamespace(conv_id="checkpoint", tracker={}, problem_workbook=True)
    w = pw.for_ctx(ctx)
    plan(w)
    engine = SimpleNamespace(ctx=ctx, should_stop=lambda:False)
    result = cp.compact(engine, [{"role":"user", "content":"Keep real evidence and original constraints"}], synthesize=False)
    assert "Wrong oracle" in result
    assert cp.load("checkpoint")["problem_workbook"]["goal"] == "Recover correct behavior"


def test_cancel_ready_plan_does_not_clear_repeat_gate(env):
    w, r, _ = env
    plan(w)
    for _ in range(3):
        execute(w)
    experiment(w, r, tool="calculate", arguments={"expression": "1+1"})
    record(w, "", "cancel", interpretation="Need a different independent observation")
    assert w.state["gate"] and w.state["pending"] is None
    experiment(w, r, tool="calculate", arguments={"expression": "1+1"})
    eid = execute(w, "2", name="calculate", args={"expression": "1+1"})
    with pytest.raises(pw.PolicyError):
        record(w, "", "cancel")
    record(w, eid)


def test_pending_large_arguments_are_retrievable_not_repeated_in_prompt(env, monkeypatch):
    w, r, _ = env
    plan(w)
    experiment(w, r, arguments={"path": "a" * 20000})
    assert len(json.dumps(w.summary())) < 5000
    monkeypatch.setattr(pw, "for_ctx", lambda c: w)
    pending = json.loads(pw.tool({"experiment": True}, r.ctx, "status"))
    assert len(pending["arguments"]["path"]) == 20000


def test_changed_prerequisite_reopens_dependent_question(env):
    w, r, root = env
    plan(w, integration=False)
    dependent = {"id": "integration", "question": "Do components work together?", "why": "Overall acceptance",
                 "hypotheses": ["Compatible", "Incompatible"], "dependencies": ["cause"], "acceptance": ["works"]}
    w.plan({"goal":w.state["goal"], "constraints":w.state["constraints"], "acceptance":[], "questions":[dependent]})
    with pytest.raises(pw.PolicyError):
        experiment(w, r, question="integration")
    experiment(w, r)
    record(w, execute(w))
    experiment(w, r, question="integration", tool="calculate", arguments={"expression":"1+1"}, inputs=[])
    eid = execute(w, "2", name="calculate", args={"expression":"1+1"})
    record(w, eid, target="integration")
    record(w, eid, "accept", "works")
    (root / "source.txt").write_text("changed dependency")
    w.refresh()
    assert all(q["status"] == "open" for q in w.state["questions"].values())
    assert not w.state["acceptance"]["works"]["evidence"]


@pytest.mark.parametrize("change", ["test", "contract"])
def test_normal_registry_real_node_adapter_repeats_and_freshness(tmp_path, monkeypatch, change):
    import agent_tools as at
    monkeypatch.setattr(at, "TRACKERS", str(tmp_path / "app" / "trackers"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "math.cjs").write_text("exports.add=(a,b)=>a-b;")
    test = root / "math.test.cjs"
    test.write_text("const t=require('node:test'), a=require('node:assert/strict'), m=require('./math.cjs');t('adds',()=>a.equal(m.add(2,3),5));")
    c = at.Ctx([str(tmp_path)])
    c.cwd = c.verification_root = str(root)
    c.conv_id = "real-node"
    c.problem_workbook = c.build_test_record = True
    import verification_ledger as vl
    led = vl.Ledger(root, Path(at.TRACKERS).parent / "verification_ledgers")
    led.state["mode"] = "modules"                  # this test covers the module workflow (from-scratch builds)
    led.save()
    r = at.Registry(c)
    r.call("module_contract", {"id":"math","purpose":"Add", "inputs":"Two numbers","outputs":"Sum",
        "errors":"None","effects":"None","sources":["math.cjs"],"tests":["math.test.cjs"],
        "checks":["adds"],"dependencies":[],"tracker_ids":[],"framework":"node"})
    r.call("begin_module", {"id":"math"})
    for _ in range(3):
        r.call("verify_module", {"id":"math"})
    w = pw.for_ctx(c)
    assert w.state["gate"]  # Different UUIDs and durations do not hide repeated failures.
    last = w.evidence(w.state["evidence"][-1])
    assert last["execution"]["status"] == "failed"
    assert str(root / "math.cjs") in last["before"]
    # A real input change permits the same experiment; no narration can do this.
    (root / "math.cjs").write_text("exports.add=(a,b)=>a+b;")
    plan(w)
    experiment(w, r, tool="verify_module", arguments={"id":"math"}, inputs=[])
    output, _ = r.call("verify_module", {"id":"math"})
    w.load()
    eid = w.state["pending"]["evidence"]
    record(w, eid)
    record(w, eid, "accept", "works")
    assert '"status": "passed"' in output
    if change == "test":
        test.write_text(test.read_text()+"\n// test changed")
    else:
        import verification_ledger as vl
        ledger = vl.for_ctx(c)
        module = ledger.state["modules"]["math"]
        contract = {k:module[k] for k in ("id", "purpose", "inputs", "outputs", "errors", "effects", "sources", "tests", "checks", "dependencies", "tracker_ids", "framework")}
        contract["outputs"] = "A different contract"
        ledger.declare(contract)
    w.refresh()
    assert w.state["questions"]["cause"]["status"] == "open"


def test_stop_controls_remain_available_during_gate(env, monkeypatch):
    w, r, _ = env
    for _ in range(3):
        execute(w)
    monkeypatch.setattr(pw, "for_ctx", lambda c: w)
    monkeypatch.setattr(r, "get", lambda name: {"kind":"control"})
    assert pw.invoke(r, "stop_background_job", {}, lambda: ("stopped", None)) == ("stopped", None)


def test_user_request_is_preserved_separately_from_model_goal(env, monkeypatch):
    w, r, _ = env
    monkeypatch.setattr(pw, "for_ctx", lambda c: w)
    pw.user_turn(r.ctx, "Build a game. Keep my original files.")
    plan(w)
    request = w.state["requests"][0]
    assert Path(request["path"]).read_text() == "Build a game. Keep my original files."
    assert w.summary()["goal"] == "Recover correct behavior"  # Explicitly model interpretation, not a claimed exact user request.


def test_prompt_budget_does_not_delete_durable_details(env):
    w, _, _ = env
    plan(w)
    w.state["constraints"] = [str(i)+"x"*1500 for i in range(50)]
    w.save()
    view = pw.prompt_view(w.summary())
    assert len(json.dumps(view, ensure_ascii=False)) <= 6000
    assert "problem_status" in view["full_state_retrieval"]
    w.load()
    assert len(w.state["constraints"]) == 50


def test_new_tools_round_trip_through_normal_registry(tmp_path, monkeypatch):
    import agent_tools as at
    monkeypatch.setattr(at, "TRACKERS", str(tmp_path / "app" / "trackers"))
    c = at.Ctx([str(tmp_path)])
    c.cwd = str(tmp_path)
    c.conv_id = "full-tool-roundtrip"
    c.problem_workbook = True
    c.build_test_record = False
    r = at.Registry(c, enabled={"calculate"})
    output, _ = r.call("problem_plan", {"goal":"Check arithmetic", "constraints":["Use computation"],
        "acceptance":[{"id":"sum","condition":"2 + 3 = 5"}],
        "questions":[{"id":"addition","question":"Does calculation produce 5?","why":"Validate requested sum",
            "hypotheses":["It produces 5","It produces another result"],"dependencies":[],"acceptance":["sum"]}]})
    assert json.loads(output)["goal"] == "Check arithmetic"
    r.call("problem_experiment", {"question":"addition", "tool":"calculate", "arguments":{"expression":"2+3"},
        "inputs":[],"predictions":["5 supports the sum","Other result contradicts the sum"],"rationale":"Compute it independently"})
    output, _ = r.call("calculate", {"expression":"2+3"})
    assert "5" in output
    w = pw.for_ctx(c)
    eid = w.state["pending"]["evidence"]
    r.call("problem_record", {"action":"resolve","target":"addition","evidence":eid,
        "interpretation":"The computation returned 5","next":"Record acceptance"})
    assert pw.incomplete(c)
    r.call("problem_record", {"action":"accept","target":"sum","evidence":eid,
        "interpretation":"Actual result satisfies the requested sum","next":"Report result"})
    assert not pw.incomplete(c)


def test_a_write_between_identical_checks_is_not_a_loop_and_clears_one(env, monkeypatch):
    # run 51976b81: screenshot, edit, reload, screenshot (the scene still looked the same) tripped the gate, and then
    # every call was refused — screenshot, page JavaScript, devtools, a new tab, write_file
    w, r, root = env
    monkeypatch.setattr(pw, "for_ctx", lambda c: w)
    kinds = {"browser_screenshot": "read", "browser_eval": "read", "write_file": "write"}
    monkeypatch.setattr(r, "get", lambda name: {"kind": kinds.get(name, "read")})
    shot = lambda: pw.invoke(r, "browser_screenshot", {}, lambda: ("browser screenshot 1280x1274", None))
    write = lambda: pw.invoke(r, "write_file", {"path": "game.html"}, lambda: ("wrote game.html", None))
    for _ in range(3):
        shot()
        write()
    assert not w.state["gate"]                               # an edit between each: three new observations
    for _ in range(3):
        shot()
    assert w.state["gate"]                                   # nothing written between: a real loop
    with pytest.raises(pw.PolicyError) as e:
        shot()
    assert "browser_screenshot" in str(e.value)
    pw.invoke(r, "browser_eval", {"expression": "1"}, lambda: ("1", None))   # another check is allowed
    write()                                                  # and a write clears the loop
    shot()
    assert not w.state["gate"]
