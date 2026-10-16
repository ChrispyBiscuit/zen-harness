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
