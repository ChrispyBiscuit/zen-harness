"""The compact prompt must create headroom without discarding durable work."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_tools as at
import context_checkpoint as checkpoint
import history_archive as archive
from offline_suite import engine


def _engine(**options):
    e = engine(tool_gate=True, tool_profiles=True, num_ctx=61440,
               compact_prompt=True, context_compaction_v2=True, **options)
    e._build_profile = True
    return e


def test_compact_tool_surface_preserves_contracts_and_discovery():
    e = _engine()
    compact = {s["function"]["name"]: s["function"]["parameters"]
               for s in e._schemas(True)}
    original = {s["function"]["name"]: s["function"]["parameters"]
                for s in e.reg.schemas()}
    assert "write_file" in compact and "available_tools" in compact
    assert "trace_symbol" not in compact
    for name, params in compact.items():
        assert params.get("required") == original[name].get("required")
        assert set(params.get("properties", {})) == set(original[name].get("properties", {}))
    e._enable(["trace_symbol"])
    assert "trace_symbol" in e._active_names()


def test_archive_deduplicates_and_pages_exact_output(monkeypatch, tmp_path):
    import paths
    monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
    original = "line-" * 1800
    first = archive.add("paging", "tool result", "read_file", original)
    assert archive.add("paging", "tool result", "read_file", original) == first
    assert len(archive.entries("paging")) == 1
    restored = ""
    offset = 0
    while offset < len(original):
        chunk, start, offset, total = archive.page(archive.get("paging", first)["text"], offset, 500)
        assert start == len(restored) and total == len(original)
        restored += chunk
    assert restored == original


def test_compact_core_keeps_build_tools_and_discovers_optional_tools():
    e = _engine()
    names = set(e._active_names())
    assert {"read_file", "write_file", "edit_file", "powershell", "run_tests", "available_tools"} <= names
    assert {"bg_start", "http_request", "port_info"}.isdisjoint(names)
    e._enable(["bg_start", "http_request"])
    assert {"bg_start", "http_request"} <= set(e._active_names())


def test_brief_tracker_keeps_decisions_and_open_blockers():
    tracker = {"goal":"Build a game", "next":"Fix cover blocking",
               "decisions":["Keep the existing project", "Use real Three.js raycasts"],
                   "milestones":[{"title":"Build", "status":"in_progress", "subtasks":[
                       {"step":"Docs", "status":"done"},
                       {"step":"Cover blocks shots", "status":"blocked"}]}]}
    brief = at.tracker_prompt_brief(tracker)
    assert "Keep the existing project" in brief
    assert "Use real Three.js raycasts" in brief
    assert "Cover blocks shots" in brief
    assert "Fix cover blocking" in brief
    assert "Docs" not in brief


def test_long_history_compacts_to_target_with_exact_request_and_pairs():
    e = _engine()
    e.history = [{"role":"user", "content":"Build the FPS game. Keep the character model and never remove shooting."}]
    for i in range(75):
        call_id = f"c{i}"
        e.history.append({"role":"assistant", "content":f"Investigating module {i}. " +
                          ("Reasoning before the next test. " * 70),
                          "tool_calls":[{"id":call_id, "type":"function", "function":{
                              "name":"read_file", "arguments":json.dumps({"path":f"module{i}.js"})}}]})
        e.history.append({"role":"tool", "tool_call_id":call_id,
                          "name":"read_file", "content":f"module{i}.js returned: " + ("source line\n" * 50)})
    e._call_openai = lambda *args, **kwargs: ({"content":"invalid", "_finish_reason":"stop"}, 0, 0)
    e._compact_req = True
    e._maybe_compact()
    assert e.turn["compactions"] == 1
    assert e._history_tokens() <= 22000
    assert "never remove shooting" in e.history[0]["content"]
    assert "Full checkpoint:" in e.history[0]["content"]
    saved = checkpoint.load(e.ctx.conv_id)
    assert saved["contract"]
    calls = {tc["id"] for m in e.history for tc in m.get("tool_calls") or []}
    assert all(m.get("tool_call_id") in calls for m in e.history if m.get("role") == "tool")


def test_checkpoint_synthesis_error_uses_recorded_state(monkeypatch):
    e = _engine()
    e.history = [{"role": "user", "content": "Build the FPS game; preserve shooting and the character model."}]
    for i in range(50):
        e.history.append({"role": "assistant", "content": "Investigating the game. " * 100})
        e.history.append({"role": "tool", "name": "read_file", "content": "source line\n" * 120})
    original = checkpoint.compact
    calls = []
    def fail_then_record(engine, messages, synthesize=True):
        calls.append(synthesize)
        if synthesize:
            raise ValueError("malformed model checkpoint")
        return original(engine, messages, synthesize=False)
    monkeypatch.setattr(checkpoint, "compact", fail_then_record)
    e._compact_req = True
    e._maybe_compact()
    assert calls == [True, False]
    assert e.turn["compactions"] == 1
    assert "preserve shooting" in e.history[0]["content"]
