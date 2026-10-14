import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_policy import RunPolicy


def test_identical_refusals_trigger_one_general_recovery_rule():
    p = RunPolicy()
    notes = [p.observe('read_guide', {'topic': 'project'}, 'refused', refused=True) for _ in range(6)]
    assert not any(notes[:5]) and notes[5]


def test_productive_long_task_and_changing_observations_are_not_capped():
    p = RunPolicy()
    for i in range(100):
        assert not p.observe('read_file', {'path': 'state'}, str(i))
        assert not p.observe('write_file', {'path': 'app.py'}, 'saved', changed=True)


def test_polling_is_not_a_stall():
    p = RunPolicy()
    for _ in range(100):
        assert not p.observe('bg_output', {'id': 'server'}, 'listening', polling=True)


def test_changed_state_allows_same_action_again():
    p = RunPolicy()
    for _ in range(5):
        assert not p.observe('read_file', {'path': 'x'}, 'same', version=0)
    assert not p.observe('read_file', {'path': 'x'}, 'same', version=1)


def test_distinct_failures_are_bounded_and_recovery_starts_a_new_window():
    p = RunPolicy()
    for i in range(5):
        assert not p.observe('powershell', {'command': str(i)}, 'error', failed=True)
    assert p.observe('powershell', {'command': 'six'}, 'error', failed=True)
    p.reset()
    assert not p.observe('powershell', {'command': 'six'}, 'error', failed=True)


def test_workspace_is_literal_and_pins_survive_restart(tmp_path):
    import offline_suite as suite
    root = str(tmp_path)
    e = suite.engine(root)
    target = str(tmp_path / 'project')
    e._turn_request = 'Please put the project here: ' + target
    assert not e._bind_workspace(str(tmp_path / 'invented'))
    assert not e._bind_workspace('relative')
    assert e._bind_workspace(target)
    assert e.ctx.resolve('app.py') == str(tmp_path / 'project' / 'app.py')
    e._pins = {'requests': ['Keep exact contract'], 'tests': '7 passed'}
    restored = suite.engine(root)
    restored.import_state(e.export_state())
    assert restored._pins == e._pins
    assert restored.ctx.workspace == target


def test_tool_execution_can_write_code_without_paperwork_and_never_rewrites_paths(tmp_path):
    import offline_suite as suite
    e = suite.engine(str(tmp_path))
    e._basic = False
    e._project = True
    e._need_plan = False
    e._since_tracker = 0
    e._fan_due = False
    e._gather_job = None
    e._offered = None
    e._turn_request = 'Build an app'
    target = tmp_path / 'app.py'
    e._run_tool(suite.tc('write_file', {'path': str(target), 'content': 'print("ready")\n'}))
    assert target.read_text() == 'print("ready")\n'
    assert not (tmp_path / 'docs').exists()
    invented = str(tmp_path / 'missing' / 'input.csv')
    call = suite.tc('read_file', {'path': invented})
    e._run_tool(call)
    import json
    assert json.loads(call['function']['arguments'])['path'] == invented


def test_recovery_stop_is_not_overridden_by_an_unfinished_checklist(tmp_path):
    import offline_suite as suite
    e = suite.engine(str(tmp_path), require_checklist=False, max_steps=12)
    e.reg.call('update_tracker', {'new': True, 'goal': 'Read input', 'milestones': [
        {'title': 'Read input', 'subtasks': [{'step': 'Read the missing input'}]}]})
    replies = iter([{'role': 'assistant', 'content': '', 'tool_calls': [
        suite.tc('read_file', {'path': str(tmp_path / 'missing.txt')}, 'r'+str(i))]}
        for i in range(6)] + [{'role': 'assistant', 'content': 'The input is missing; the task is blocked.'}])
    e.prepare = lambda: None
    e._complete = lambda **kw: next(replies)
    e._stuck_reset = lambda reason: None
    e._plan_phase = lambda: None
    e.run('Continue the task')
    assert e._force_answer
    assert e.history[-1]['content'] == 'The input is missing; the task is blocked.'
    assert len([m for m in e.history if m.get('role') == 'tool' and m.get('name') == 'read_file']) == 6

def test_explicit_zero_has_no_step_pause_and_normal_budget_is_preserved():
    from run_policy import step_budget,step_allowed
    import pytest
    assert step_budget(0)==0 and step_budget('0')==0
    assert all(step_allowed(n,0) for n in (0,12,100,1000000))
    assert step_budget(None)==40
    assert step_allowed(11,step_budget(12)) and not step_allowed(12,step_budget(12))
    with pytest.raises(ValueError):step_budget(-1)
