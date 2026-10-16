"""Re-reads are judged by the lines actually on screen, and a compaction carries the files being worked on."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine, tmpdir


def _file(root, name, n):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(f"const line{i} = {i};" for i in range(1, n + 1)) + "\n")
    return p


def _read(e, path, cid, **extra):
    """One read_file step as the loop records it: the call, the result, the bookkeeping."""
    args = dict(path=path, **extra)
    pointer = e._unchanged_read(args)
    text = pointer or e.reg.call("read_file", dict(args))[0]
    e.history.append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": cid, "type": "function",
                                      "function": {"name": "read_file", "arguments": json.dumps(args)}}]})
    e.history.append({"role": "tool", "tool_call_id": cid, "name": "read_file", "content": text})
    if not pointer:
        e._remember_read(args, cid, text)
    return text


def test_overlapping_slices_of_an_unchanged_file_get_a_pointer():
    root = tmpdir("reread")
    e = engine(root=root)
    p = _file(root, "enemies.js", 200)               # small enough to be shown whole on the first read
    e.history = [{"role": "user", "content": "fix the enemies"}]
    first = _read(e, p, "r1")
    assert "showing the whole file" in first
    # the slices a 27B asked for in a084ff8b: different offset/limit pairs, all already on screen
    second = _read(e, p, "r2", offset=87, limit=16)
    assert second.startswith(e.READ_POINTER) and "lines 1-200" in second
    # asking for the same lines again right after the pointer gets the real text (no loop)
    third = _read(e, p, "r3", offset=87, limit=16)
    assert not third.startswith(e.READ_POINTER) and "line87" in third


def test_a_changed_file_is_read_again():
    root = tmpdir("reread")
    e = engine(root=root)
    p = _file(root, "player.js", 120)
    e.history = [{"role": "user", "content": "go"}]
    _read(e, p, "r1")
    with open(p, "a", encoding="utf-8") as f:
        f.write("const added = 1;\n")
    again = _read(e, p, "r2", offset=10, limit=20)
    assert not again.startswith(e.READ_POINTER)


def test_partly_visible_range_is_read():
    root = tmpdir("reread")
    e = engine(root=root)
    p = _file(root, "big.js", 2000)                   # too big to show whole: pages
    e.history = [{"role": "user", "content": "go"}]
    first = _read(e, p, "r1")
    assert "showing lines 1-" in first
    end = int(first.split("showing lines 1-")[1].split(";")[0])
    assert _read(e, p, "r2", offset=5, limit=20).startswith(e.READ_POINTER)
    beyond = _read(e, p, "r3", offset=end - 5, limit=40)     # runs past what was shown
    assert not beyond.startswith(e.READ_POINTER)


def test_pointer_does_not_mask_the_read_it_points_to():
    root = tmpdir("reread")
    e = engine(root=root)
    p = _file(root, "arena.js", 100)
    e.history = [{"role": "user", "content": "go"}]
    _read(e, p, "r1")
    assert _read(e, p, "r2").startswith(e.READ_POINTER)
    e.MASK_KEEP = 0
    stubs = e._stale_tool_stubs(e.history)
    assert "r1" not in stubs                          # the text the pointer refers to stays on screen


def test_compaction_carries_the_working_files_and_reads_point_to_them():
    root = tmpdir("wset")
    e = engine(root=root, tool_gate=True, tool_profiles=True, num_ctx=61440,
               compact_prompt=True, context_compaction_v2=True)
    e._build_profile = True
    game = _file(root, "game.js", 150)
    util = _file(root, "utils.js", 60)
    e.history = [{"role": "user", "content": "Build the FPS game. Never remove shooting."}]
    for i in range(70):                               # a long build: lots of narration and results
        cid = f"c{i}"
        name, args = (("edit_file", {"path": game, "old": "a", "new": "b"}) if i % 3 == 0 else
                      ("read_file", {"path": util if i % 2 else game, "offset": 1 + i}))
        e.history.append({"role": "assistant", "content": f"Step {i}. " + ("Thinking about the game loop. " * 60),
                          "tool_calls": [{"id": cid, "type": "function",
                                          "function": {"name": name, "arguments": json.dumps(args)}}]})
        e.history.append({"role": "tool", "tool_call_id": cid, "name": name, "content": "ok " + "result line\n" * 60})
    e._call_openai = lambda *a, **k: ({"content": "invalid", "_finish_reason": "stop"}, 0, 0)
    e._compact_req = True
    e._maybe_compact()
    head = e.history[0]["content"]
    assert "[Working set 1]" in head and "game.js" in head and "const line150 = 150;" in head
    assert "Never remove shooting" in head
    assert e._history_tokens() <= e.context_budget() * 0.85
    # the file is on screen in the working set: a re-read gets a pointer to it
    ptr = e._unchanged_read({"path": game, "offset": 40, "limit": 30})
    assert ptr.startswith(e.READ_POINTER) and "working set" in ptr
    # after an edit on disk the working-set copy is stale: read for real
    with open(game, "a", encoding="utf-8") as f:
        f.write("const more = 1;\n")
    assert e._unchanged_read({"path": game, "offset": 40, "limit": 30}) == ""


def test_working_set_can_be_turned_off():
    root = tmpdir("wset")
    e = engine(root=root, num_ctx=61440, compact_prompt=True, context_compaction_v2=True, working_set=False)
    game = _file(root, "game.js", 150)
    e.history = [{"role": "user", "content": "Build it."}]
    for i in range(70):
        cid = f"c{i}"
        e.history.append({"role": "assistant", "content": "Working. " * 200,
                          "tool_calls": [{"id": cid, "type": "function", "function": {
                              "name": "edit_file", "arguments": json.dumps({"path": game, "old": "a", "new": "b"})}}]})
        e.history.append({"role": "tool", "tool_call_id": cid, "name": "edit_file", "content": "ok " + "x\n" * 200})
    e._call_openai = lambda *a, **k: ({"content": "invalid", "_finish_reason": "stop"}, 0, 0)
    e._compact_req = True
    e._maybe_compact()
    assert "[Working set" not in e.history[0]["content"]


def test_run_tests_says_when_the_imported_code_was_never_written():
    import agent_tools as at
    root = tmpdir("missing")
    out = "tests: exit code 2 — 1 error\nE   ModuleNotFoundError: No module named 'data'"
    tip = at._missing_module_tip(out, root)
    assert "no data.py" in tip and "write data.py" in tip
    with open(os.path.join(root, "data.py"), "w") as f:
        f.write("x = 1\n")
    assert at._missing_module_tip(out, root) == ""          # the file exists: a different problem, no tip
    assert at._missing_module_tip("tests: PASSED — 3 passed", root) == ""
