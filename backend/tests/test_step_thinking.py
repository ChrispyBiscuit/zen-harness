"""Thinking is set per step: a failed call earns one thoughtful step, a broken program earns thinking until it runs
clean. A build-gate refusal used to leave a 27B 'debugging' (2048 tokens a step) for the whole build."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine
import agentic


def test_a_failed_call_thinks_for_one_step_only():
    e = engine(effort="medium")
    e._think_effort = "medium"
    e.turn = {"planned": True}
    e._watch_progress("write_file", {}, False, "Error: Build gate: server.py was NOT written.")
    assert e._fail_streak == 1 and not e._debugging
    assert e._wants_thinking(True, 4)                       # the step right after the refusal thinks
    e._watch_progress("write_file", {}, True, "created PRD.md (57 lines)")
    assert e._fail_streak == 0 and not e._debugging
    assert not e._wants_thinking(True, 5)                   # routine steps after it act directly


def test_a_broken_program_keeps_debugging_until_a_clean_run():
    e = engine(effort="medium")
    e._think_effort = "medium"
    e.turn = {"planned": True}
    e._watch_progress("powershell", {}, True, "exit code 1\nTraceback (most recent call last):\nValueError: bad")
    e._watch_progress("read_file", {}, True, "1| import os")
    assert e._debugging and e._wants_thinking(True, 6)
    e._watch_progress("run_tests", {}, True, "== 10 passed in 1.0s ==")
    assert not e._debugging and not e._wants_thinking(True, 7)
