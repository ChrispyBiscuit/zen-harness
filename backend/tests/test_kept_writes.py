"""A refused overwrite keeps its content (no regenerating a whole file), and a new module contract makes the next
verify_module a new call, not a repeat."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_tools as at
from offline_suite import engine


def test_unread_overwrite_keeps_content_and_shows_the_file(tmp_path):
    f = tmp_path / "test_server.py"
    f.write_text("def test_old():\n    assert True\n")
    c = at.Ctx([str(tmp_path)]); c.cwd = str(tmp_path)
    r = at.Registry(c, enabled={"write_file"})
    with pytest.raises(at.ToolError) as e:
        r.call("write_file", {"path": str(f), "content": "def test_new():\n    assert 1\n"})
    msg = str(e.value)
    assert "use_kept=true" in msg and "def test_old" in msg            # the refusal is the read
    assert f.read_text().startswith("def test_old")
    text, _ = r.call("write_file", {"path": str(f), "use_kept": True})  # no content resent
    assert f.read_text() == "def test_new():\n    assert 1\n" and "overwrote" in text
    with pytest.raises(at.ToolError):
        r.call("write_file", {"path": str(f), "use_kept": True})        # used once
    with pytest.raises(at.ToolError):
        r.call("write_file", {"path": str(f)})                           # content still required otherwise


def test_a_new_contract_is_a_change_for_the_repeat_gate():
    e = engine()
    t = {"kind": at.EXEC}
    e._record_call("verify_module", t, {"id": "server"}, "uncovered_checks")
    assert e._repeat_of("verify_module", t, {"id": "server"})
    e._record_call("module_contract", {"kind": at.NOTE}, {"id": "server"}, "Contract recorded")
    assert not e._repeat_of("verify_module", t, {"id": "server"})


def _big(tag):
    return "".join(f"def f{i}():\n    return '{tag}'\n" for i in range(30))   # 60 lines


def test_third_whole_file_rewrite_is_refused_until_tests_improve(tmp_path):
    f = tmp_path / "test_app.py"
    f.write_text(_big("v0"))
    c = at.Ctx([str(tmp_path)]); c.cwd = str(tmp_path)
    at.note_seen(c, str(f))
    r = at.Registry(c, enabled={"write_file"})
    r.call("write_file", {"path": str(f), "content": _big("v1")})
    at.note_seen(c, str(f))
    text, _ = r.call("write_file", {"path": str(f), "content": _big("v2")})
    assert "another one will be refused" in text
    at.note_seen(c, str(f))
    with pytest.raises(at.ToolError) as e:
        r.call("write_file", {"path": str(f), "content": _big("v3")})
    assert "full rewrite number 3" in str(e.value) and "edit_file" in str(e.value) and "undo_file" in str(e.value)
    assert "'v2'" in f.read_text()
    text, _ = r.call("write_file", {"path": str(f), "use_kept": True, "confirm_rewrite": True})   # the escape hatch
    assert "'v3'" in f.read_text()
    at._hist(c)["rewrites"] = {}                                     # (what a test improvement does)
    at.note_seen(c, str(f))
    r.call("write_file", {"path": str(f), "content": _big("v4")})


def test_small_files_are_not_limited(tmp_path):
    f = tmp_path / "cfg.py"
    f.write_text("A = 1\n")
    c = at.Ctx([str(tmp_path)]); c.cwd = str(tmp_path)
    r = at.Registry(c, enabled={"write_file"})
    for i in range(4):
        at.note_seen(c, str(f))
        r.call("write_file", {"path": str(f), "content": f"A = {i + 2}\n"})


def test_regression_note_names_the_writes_in_between():
    import code_insight as ci
    note = ci.regression({"passed": 12, "failed": 5, "failing": []}, {"passed": 0, "failed": 0, "failing": []},
                         ["test_client.py"])
    assert "12 tests passed before, 0 now" in note and "test_client.py" in note and "undo_file" in note
