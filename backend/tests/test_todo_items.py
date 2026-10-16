"""TODO items closed 2026-10-15: green-suite checklist ticks, project-mode paperwork, inline web_fetch size."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_tools as at
from offline_suite import tmpdir


def _ctx(milestones):
    c = at.Ctx([tmpdir("tick")])
    c.conv_id = "todo-tick"
    at.t_update_tracker({"new": True, "goal": "g", "milestones": milestones}, c)
    return c


def test_green_suite_ticks_test_subtasks_of_a_tests_milestone():
    c = _ctx([{"title": "Storage", "done_when": "all tests pass",
               "subtasks": ["Write db.py", "Write tests for db.py", "Verify bad input is rejected"]},
              {"title": "README", "subtasks": ["Write README"]}])
    note = at._tick_on_green(c, 12, 0)
    status = {i: st for i, _t, st, _m in at.tracker_items(c.tracker)}
    assert status["1.2"] == "done" and status["1.3"] == "done", (note, status)
    assert status["1.1"] != "done" and status["2.1"] != "done"          # build work is never ticked by a test run
    assert "1.2" in note and "1.3" in note


def test_no_tick_when_the_milestone_is_not_about_tests_or_tests_fail():
    c = _ctx([{"title": "UI", "done_when": "the page shows the totals",
               "subtasks": ["Build the page", "Check the layout"]}])
    assert at._tick_on_green(c, 5, 0) == ""
    c2 = _ctx([{"title": "Storage", "done_when": "tests pass", "subtasks": ["Write tests"]}])
    assert at._tick_on_green(c2, 5, 1) == ""


def test_project_mode_playbook_scales_paperwork():
    text = open(os.path.join(os.path.dirname(at.__file__), "playbooks", "project-mode.md"), encoding="utf-8").read()
    assert "only for work a later chat will pick up" in text
    assert "INDEX.md lists every document" not in text and "research questions in INDEX.md" not in text
    assert "never one that needs a feature nobody asked for" in text


def test_single_page_fetch_shows_more_inline(monkeypatch):
    seen = {}
    monkeypatch.setattr(at.webtools, "web_fetch", lambda url, n: seen.setdefault("n", n) and "page text")
    c = at.Ctx([tmpdir("fetch")])
    at.t_web_fetch({"url": "https://example.com/a"}, c)
    assert seen["n"] == at.WEB_FETCH_CHARS > 6000 and at.WEB_FETCH_CHARS < at.MAX_RESULT


def test_shell_test_run_points_at_run_tests_once():
    root = tmpdir("rt")
    c = at.Ctx([root])
    c.conv_id = "todo-rt"
    reg = at.Registry(c, None, None)
    out = "exit code 0\n===== 3 passed in 0.2s ====="
    reg.by_name["powershell"] = dict(reg.by_name["powershell"], fn=lambda a, ctx: out)
    first = str(reg.call("powershell", {"command": "python -m pytest -q"})[0])
    second = str(reg.call("powershell", {"command": "python -m pytest -q"})[0])
    assert "Use run_tests" in first and "Use run_tests" not in second
    other = at.Ctx([root]); other.conv_id = "todo-rt2"
    reg2 = at.Registry(other, None, None)
    reg2.by_name["powershell"] = dict(reg2.by_name["powershell"], fn=lambda a, ctx: "exit code 0\nhello")
    assert "Use run_tests" not in str(reg2.call("powershell", {"command": "echo hello"})[0])
