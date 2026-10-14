import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import context_checkpoint as cp
import optical_context as occ


@pytest.fixture
def records():
    return [{"id":"E1","kind":"tool result","text":"Error: browser timed out"}]


def response(records):
    return {"_finish_reason":"stop","content":json.dumps({
        "facts":[],"problems":[{"text":"Browser unavailable","refs":["E1"],"quote":"browser timed out"}],
        "failed_approaches":[],"next_actions":[]})}


@pytest.mark.parametrize("finish",["length",None,"tool_calls","error"])
def test_partial_generation_never_becomes_checkpoint(records,finish):
    m=response(records);m["_finish_reason"]=finish
    with pytest.raises(ValueError):cp.validate(m,records)


def test_all_fields_and_real_evidence_required(records):
    m=response(records);d=json.loads(m["content"]);d.pop("next_actions");m["content"]=json.dumps(d)
    with pytest.raises(ValueError):cp.validate(m,records)


def test_factual_wording_is_retrieved_not_model_paraphrase(records):
    m=response(records);d=json.loads(m["content"])
    d["facts"]=[{"text":"Browser is working perfectly", "refs":["E1"], "quote":"browser timed out"}]
    m["content"]=json.dumps(d)
    state=cp.validate(m,records)
    fact=state["facts"][0]
    assert fact["text"]=="browser timed out"
    assert fact["interpretation"]=="Browser is working perfectly"
    span=fact["snippets"][0]
    assert records[0]["text"][span["start"]:span["end"]]==fact["text"]
    assert len(span["source_hash"])==64
    m=response(records);d=json.loads(m["content"]);d["problems"][0]["refs"]=["E999"];m["content"]=json.dumps(d)
    with pytest.raises(ValueError):cp.validate(m,records)
    m=response(records);d=json.loads(m["content"]);d["problems"][0]["quote"]="Browser works";m["content"]=json.dumps(d)
    with pytest.raises(ValueError):cp.validate(m,records)


@pytest.mark.parametrize("prefix",["</think>","<tool_call>","I have the full picture.\n"])
def test_thinking_and_narrative_rejected(records,prefix):
    m=response(records);m["content"]=prefix+m["content"]
    with pytest.raises((ValueError,json.JSONDecodeError)):cp.validate(m,records)


def test_call_result_pairing_preserves_unknown_and_failures(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    history=[{"role":"user","content":"Keep exact identifier abc012 and do not reset the service."},
             {"role":"assistant","content":"I now know everything", "tool_calls":[
                 {"id":"a","function":{"name":"browser_read","arguments":"{}"}},
                 {"id":"b","function":{"name":"powershell","arguments":"{}"}}]},
             {"role":"tool","tool_call_id":"a","content":"Error: browser timed out"}]
    state,es=cp.project("test",history)
    assert [e["outcome"] for e in state["evidence"]]==["failed","unknown"]
    assert es[0]["text"]==history[0]["content"]
    assert not any("I now know" in r["text"] for r in es)
    state["path"]=cp.path("test")
    rendered=cp.render(state,es)
    assert "abc012" in rendered and "unknown" in rendered and "Current status" in rendered


def test_large_workbook_uses_bounded_retrievable_checkpoint(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    state,es=cp.project("large",[{"role":"user","content":"Keep the FPS task and test shooting."}])
    state["path"]=cp.path("large")
    state["status"]=[{"id":str(i),"status":"pending","text":"Build a checked component "+("x"*180)} for i in range(1,100)]
    state["verification_workbook"]={"root":"project","active":"game","unowned_changes":[],
        "modules":[{"id":"game","state":"needs_verification","receipt":None,"purpose":"x"*20000,
                    "sources":["game.js"],"tests":["game.test.js"],"tracker_ids":["2"]}],
        "latest_receipts":[]}
    output=cp.render(state,es)
    assert len(output)<=14000
    assert "Keep the FPS task" in output
    assert "game" in output and "needs_verification" in output
    assert "Full checkpoint" in output


def test_two_invalid_calls_fall_back_without_losing_contract(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    calls=[]
    def run(*a,**kw):
        calls.append(kw);return {"content":"</think>garbage","_finish_reason":"length"},0,0
    eng=SimpleNamespace(ctx=SimpleNamespace(conv_id="fallback",tracker={}),_call_openai=run,
                        should_stop=lambda:False)
    out=cp.compact(eng,[{"role":"user","content":"Never delete existing work."}])
    assert len(calls)==1 and "Never delete existing work." in out
    assert len(cp.load("fallback")["generation_errors"])==1


def test_cancel_is_not_a_summary_retry(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    class Stopped(Exception):pass
    def run(*a,**kw):raise Stopped()
    eng=SimpleNamespace(ctx=SimpleNamespace(conv_id="cancel",tracker={}),_call_openai=run,
                        should_stop=lambda:True)
    with pytest.raises(Stopped):cp.compact(eng,[{"role":"user","content":"Task"}])
    saved=cp.load("cancel")
    assert saved is not None and saved["generation_errors"]==["synthesis pending"]
    assert saved["contract"]


def test_atomic_save_failure_keeps_previous(tmp_path,monkeypatch):
    p=tmp_path/"state.json";cp.atomic_save(str(p),{"revision":1})
    monkeypatch.setattr(cp.os,"replace",lambda *a:(_ for _ in ()).throw(OSError("write failure")))
    with pytest.raises(OSError):cp.atomic_save(str(p),{"revision":2})
    assert json.loads(p.read_text())["revision"]==1


def test_exact_later_decision_survives_repetitive_archive_and_second_compaction(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    initial="Accepted contract: endpoint POST /v2/roommate-export; port 7341. Billing's 37 tests passed."
    update="Settled decision: delimiter is |. Column order is account|amount_minor|period|trace_id; trace_id is trc_8q2m."
    noise="\n".join(f"Closed archive item {i}: component {i%7}; previous measurement {i*13}." for i in range(90))
    messages=[{"role":"user","content":initial}]+[{"role":"user","content":noise} for _ in range(8)]+[
        {"role":"user","content":update},{"role":"user","content":noise}]
    state,records=cp.project("decisions",messages)
    state["path"]=cp.path("decisions")
    compact=cp.render(state,records,budget=3000)
    assert initial in compact and update in compact
    assert "Closed archive item 0:" not in compact
    assert all(r["source_hash"]==__import__("hashlib").sha256(
        next(e["text"] for e in records if e["id"]==r["ref"]).encode()).hexdigest()
        for r in state["active_facts"])
    correction="Latest accepted correction: port is 7441; all other accepted fields remain."
    next_state,next_records=cp.project("decisions",[{"role":"user","content":correction}],previous=state)
    next_state["path"]=cp.path("decisions")
    compact2=cp.render(next_state,next_records,budget=3500)
    assert update in compact2 and correction in compact2


def test_unique_long_request_is_whole_or_explicitly_incomplete(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    unique="\n".join(f"Requirement {i}: preserve setting name_{i} and value_{i}." for i in range(65))
    state,records=cp.project("long-unique",[{"role":"user","content":unique}])
    state["path"]=cp.path("long-unique")
    output=cp.render(state,records)
    assert "Requirement 0" in output and "name_32" in output and "Requirement 64" in output
    assert not state["active_coverage_gaps"]
    too_long="\n".join(f"Requirement {i}: preserve distinctive setting name_{i} and value_{i}." for i in range(180))
    state,records=cp.project("too-long",[{"role":"user","content":too_long}])
    state["path"]=cp.path("too-long")
    output=cp.render(state,records)
    assert state["active_coverage_gaps"] and "INCOMPLETE SOURCE COVERAGE" in output


def test_optional_state_cannot_push_exact_active_facts_out_of_budget(tmp_path,monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR",str(tmp_path))
    requirement="\n".join(f"Requirement {i}: retain unique token key_{i} and value_{i}." for i in range(110))
    state,records=cp.project("budget",[{"role":"user","content":requirement}])
    state["path"]=cp.path("budget")
    state["status"]=[{"id":str(i),"status":"pending","text":"work "+"z"*300} for i in range(100)]
    state["verification_workbook"]={"root":"project","active":"x","modules":[{"id":"x","purpose":"p"*20000}]}
    output=cp.render(state,records,budget=14000)
    assert len(output)<=14000
    assert "key_0" in output and "key_55" in output and "key_109" in output
    assert "Full checkpoint:" in output


def test_optical_is_opt_in_and_corrupt_or_unsupported_falls_back(tmp_path):
    r={"id":"E1","text":"Data","optical_eligible":True,"priority":"historical"}
    assert occ.select([r], ["E1"],active=["E1"])==[]
    assert occ.select([{"id":"E2","text":"secret"}], ["E2"])==[]
    page={"path":str(tmp_path/"missing.png"),"image_hash":"bad","record_ids":["E1"]}
    assert occ.representation([r],[page],enabled=True,capability_verified=True)["mode"]=="text"


def test_optical_determinism_and_limit_no_partial_replacement(tmp_path):
    font=Path(r'C:\Windows\Fonts\consola.ttf')
    if not font.exists():pytest.skip("fixed test font unavailable")
    r={"id":"E1","text":"Evidence line\n"*120,"priority":"historical","optical_eligible":True}
    a=occ.render_record(r,tmp_path/"a",font)
    b=occ.render_record(r,tmp_path/"b",font)
    assert [p["image_hash"] for p in a]==[p["image_hash"] for p in b]
    assert len(a)>2 and occ.representation([r],a,True,True)["mode"]=="text"
    assert occ.representation([r],a[:2],True,True)["mode"]=="text"
    assert r["text"]=="Evidence line\n"*120
