"""A build resumed with 'continue' after an app restart keeps its build prompt and code tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine
import agentic


def test_continue_reads_the_original_request():
    e = engine()
    e.history = [{"role": "user", "content": "build an expense tracker app for my roommates\n\n" + agentic.REMINDER_TAG + "\nnote"},
                 {"role": "assistant", "content": "ok"}, {"role": "user", "content": "continue"}]
    t = e._task_text("continue")
    assert t.startswith("build an expense tracker app") and agentic.REMINDER_TAG not in t
    assert e._BUILD_REQ_RX.search(t)
    assert e._task_text("make it blue") == "make it blue"


def test_build_flags_survive_a_saved_state_roundtrip():
    e = engine()
    e._build_profile, e._code_work = True, True
    st = e.export_state()
    assert st["build_profile"] and st["code_work"]
    e2 = engine()
    e2.import_state(st)
    assert e2._build_profile and e2._code_work


def test_read_memory_survives_a_restart(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("".join(f"line {i}\n" for i in range(1, 51)))
    e = engine()
    e.ctx.roots = [str(tmp_path)]
    e.ctx.cwd = str(tmp_path)
    shown = "[app.py: 50 lines, 400 characters — showing the whole file]\n" + "x" * 5000
    e.history = [{"role": "user", "content": "build an app"},
                 {"role": "assistant", "content": "", "tool_calls": [{"id": "r1", "type": "function",
                  "function": {"name": "read_file", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": "r1", "content": shown}]
    e._remember_read({"path": str(f)}, "r1", shown)
    assert e._unchanged_read({"path": str(f)})
    e2 = engine()
    e2.ctx.roots = [str(tmp_path)]
    e2.ctx.cwd = str(tmp_path)
    e2.import_state(e.export_state())
    assert e2._unchanged_read({"path": str(f)}).startswith(agentic.READ_POINTER if hasattr(agentic, "READ_POINTER")
                                                           else e2.READ_POINTER)   # 5K result kept whole: pointer
