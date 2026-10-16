"""Slow models keep a smaller working context: compaction at ~35K down to ~22K, the working set whole, and every
older write/edit call stripped of the file text it carried."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine


def test_slow_models_compact_earlier_and_deeper():
    e = engine(num_ctx=61440)
    assert e.context_budget() == 61440 - e.REPLY_RESERVE and e._compact_target() > 30000   # fast: as before
    e._last_tps = 15.0                                           # a 27B late in a build
    assert e.context_budget() == 35000 and e._compact_target() == 22000
    e._last_tps = 40.0                                           # sticky: one fast stretch doesn't flip it back
    assert e.context_budget() == 35000
    small = engine(num_ctx=16384)
    small._last_tps = 10.0
    assert small.context_budget() == max(int(16384 * 0.75), 16384 - small.REPLY_RESERVE)   # already below 35K


def test_the_working_set_stays_whole_for_slow_models():
    e = engine(num_ctx=61440)
    assert e._working_set_budget(25000) == int(25000 * e.WS_SHARE)
    e._last_tps = 15.0
    assert e._working_set_budget(25000) == e.WS_MAX_TOKENS          # 9K, not 4.5K


def test_every_older_write_drops_its_text():
    e = engine()
    big = "x = 1\n" * 200
    msgs = [{"role": "user", "content": "build"}]
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "w1", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "server.py", "content": big})}}]})
    msgs.append({"role": "tool", "tool_call_id": "w1", "content": "created server.py"})
    for i in range(8):                                            # later steps that never touch server.py again
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"r{i}", "type": "function",
                     "function": {"name": "run_tests", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"r{i}", "content": "ok"})
    args, _ = e._stale_extras(msgs)
    assert "w1" in args and "content_omitted" in args["w1"] and "x = 1" not in args["w1"]
