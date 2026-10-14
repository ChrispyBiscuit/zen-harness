"""Test Mode's durable observations remain separate from model claims."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_tools as at
import execution_memory as em
from offline_suite import engine


def test_action_receipt_and_missing_next(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "_root", lambda _conv: str(tmp_path))
    conv = "journal-test-a"
    aid = em.begin(conv, "call-1", "run_tests", {"path": "tests"}, step=3)
    em.finish(conv, aid, True, "2 passed, 1 failed\nactual traceback", step=3)
    tracker = {"goal": "Build game", "next": "", "milestones": [
        {"title": "Shooting", "status": "in_progress", "subtasks": [
            {"step": "Cover blocks shots", "status": "in_progress"}]}]}
    view = em.brief(conv, tracker)
    assert aid in view and "2 passed, 1 failed" in view
    assert "NEEDS SELECTION" in view
    assert "tool outcomes are observations, not task completion" in view
    exact = em.get(conv, aid)
    assert json.loads(exact["arguments"]) == {"path": "tests"}
    assert exact["result"].endswith("actual traceback")
    assert exact["status"] == "observed"
    assert em.search(conv, "traceback")[0]["id"] == aid


def test_pending_call_and_rebuild_after_torn_projection(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "_root", lambda _conv: str(tmp_path))
    conv = "journal-test-b"
    done = em.begin(conv, "call-1", "read_file", {"path": "a"})
    em.finish(conv, done, True, "first result")
    waiting = em.begin(conv, "call-2", "powershell", {"command": "long job"})
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    em._RECONCILED.discard(conv)
    recovered = em.state(conv)
    assert waiting in recovered["pending"]
    assert recovered["recent"][-1]["id"] == done
    assert "Interrupted/unresolved calls" in em.brief(conv)


def test_test_mode_reminder_and_archive_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "_root", lambda _conv: str(tmp_path))
    e = engine(execution_memory_test_mode=True, compact_prompt=True)
    e.ctx.conv_id = "journal-test-c"
    aid = em.begin(e.ctx.conv_id, "call-3", "read_file", {"path": "game.js"})
    em.finish(e.ctx.conv_id, aid, True, "exact game source")
    reminder = e._reminder()["content"]
    assert aid in reminder and "Recent actions" in reminder
    result = at.t_history_search({"entry": aid}, e.ctx)
    assert "exact game source" in result and "game.js" in result
