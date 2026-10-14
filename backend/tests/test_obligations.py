"""Obligations: evidence on done items, resolved tests, stale evidence, the narrow retry, the reopen guard and the
stall line — walked through bonsai9's own sequence with real tool calls. Runs under pytest, or standalone."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["LWS_DATA_DIR"] = tempfile.mkdtemp(prefix="obl_data_")
os.environ.setdefault("LWS_NO_TELEGRAM", "1")
os.environ.setdefault("LWS_NO_SCHEDULER", "1")

import agent_tools as at  # noqa: E402
import obligations  # noqa: E402

CLIENT = "def total(rows):\n    return sum(rows)\n"
TESTS_BAD = ("import client\n\ndef test_total():\n    assert client.total([1, 2]) == 3\n\n"
             "def test_a6_persistence():\n    assert client.total([2, 2]) == 5\n")


def _setup():
    d = tempfile.mkdtemp(prefix="obl_proj_")
    os.makedirs(os.path.join(d, "docs"))
    os.makedirs(os.path.join(d, "tests"))
    open(os.path.join(d, "client.py"), "w").write(CLIENT)
    open(os.path.join(d, "tests", "conftest.py"), "w").write(
        "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
    open(os.path.join(d, "tests", "test_client.py"), "w").write(TESTS_BAD)
    c = at.Ctx([d])
    c.cwd = d
    c.conv_id = f"obl-{os.path.basename(d)}"
    reg = at.Registry(c, None, None)
    reg.call("update_tracker", {"new": True, "goal": "tracker", "milestones": [
        {"title": "Build", "done_when": "the suite passes", "subtasks": [
            {"step": "client totals"}, {"step": "run_tests green"}, {"step": "Real run with the CLI"}]}]})
    return d, c, reg


def test_bonsai9_sequence():
    d, c, reg = _setup()
    tests = os.path.join(d, "tests")
    reg.call("read_file", {"path": os.path.join(d, "client.py")})
    reg.call("edit_file", {"path": os.path.join(d, "client.py"), "old": "return sum(rows)",
                           "new": "return sum(rows)  # totals"})
    reg.call("update_tracker", {"updates": [{"id": "1.1", "status": "done", "evidence": "total([1,2]) == 3 by hand"}]})
    out, _ = reg.call("run_tests", {"path": tests})
    assert out.startswith("tests: FAILED"), out
    reg.call("read_file", {"path": os.path.join(tests, "test_client.py")})
    reg.call("edit_file", {"path": os.path.join(tests, "test_client.py"), "old": "== 5", "new": "== 4"})
    out, _ = reg.call("run_tests", {"path": tests})
    assert out.startswith("tests: PASSED"), out
    b = obligations.brief(c)
    assert "NOW: 1.3" in b and "done when: the suite passes" in b, b
    assert "1.2 run_tests green — tests: PASSED" in b, "auto-ticked with the run as its evidence\n" + b
    assert "1.1 client totals — total([1,2]) == 3 by hand" in b, b
    assert "RESOLVED" in b and "test_a6_persistence" in b, b

    # the real run fails on one call: ONE narrow retry, not a restart
    out, _ = reg.call("powershell", {"command": "python -c \"import sys; print('error: --amount 1000 is not valid'); "
                                                "sys.exit(1)\""})
    b = obligations.brief(c)
    assert "LAST CHECK on 1.3" in b and "FAILED" in b and "don't restart the sequence" in b, b

    # editing the resolved test again while it passes: the reopen guard
    out, _ = reg.call("edit_file", {"path": os.path.join(tests, "test_client.py"), "old": "== 4", "new": "== 2 + 2"})
    assert "RESOLVED" in out and "reopens settled work" in out, out

    # client.py changes after 1.1 was verified with it: 1.1 is stale
    reg.call("read_file", {"path": os.path.join(d, "client.py")})
    reg.call("edit_file", {"path": os.path.join(d, "client.py"), "old": "# totals", "new": "# the totals"})
    b = obligations.brief(c)
    assert "STALE" in b and "1.1" in b and "client.py changed" in b, b


def test_the_same_outcome_again_is_a_stall():
    d, c, reg = _setup()
    reg.call("update_tracker", {"updates": [{"id": "1.1", "status": "done"}, {"id": "1.2", "status": "done"}]})
    cmd = "python -c \"import sys; print('error: --amount 1000 is not valid'); sys.exit(1)\""
    for _ in range(9):
        reg.call("powershell", {"command": cmd})
    b = obligations.brief(c)
    assert "NO NEW EVIDENCE" in b and "--amount 1000" in b, b
    for _ in range(6):
        reg.call("powershell", {"command": cmd})
    b = obligations.brief(c)
    assert "NO NEW EVIDENCE" in b and "--amount 1000" in b
    assert "ask_user" not in b and "STUCK:" not in b  # recovery belongs to the engine policy


def test_scratch_scripts_dont_stale_or_count_as_item_files():
    d, c, reg = _setup()
    reg.call("update_tracker", {"updates": [{"id": "1.1", "status": "done"}]})
    reg.call("write_file", {"path": os.path.join(d, "_probe.py"), "content": "print(1)\n"})
    st = obligations._state(c)
    assert not any("_probe.py" in p for fs in st["item_files"].values() for p in fs)
    assert "STALE" not in obligations.brief(c)


def test_replay_rebuilds_the_brief_at_the_cut():
    """replay_point --dry-run on a small recorded chat: the checklist from its history at the cut, the observations
    from the tool log, nothing written to the source data folder."""
    import json
    import subprocess
    data = tempfile.mkdtemp(prefix="replay_src_")
    proj = os.path.join(data, "proj")
    os.makedirs(os.path.join(proj, "docs"))
    open(os.path.join(proj, "app.py"), "w").write("x = 1\n")
    conv = "abc123replay"
    tracker = {"goal": "g", "milestones": [{"title": "Build", "status": "in_progress", "done_when": "suite passes",
                                            "subtasks": [{"step": "write app", "status": "done"},
                                                         {"step": "live check", "status": "pending"}]}],
               "notes": [], "next": ""}
    arch = os.path.join(data, "agent_attachments", conv, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, "trackers.jsonl"), "w") as f:
        f.write(json.dumps({"at": "2026-10-12 03:00:00", "tracker": tracker}) + "\n")
    rows = [{"conv": conv, "t": "2026-10-12 03:00:05", "tool": "write_file", "ok": True,
             "args": {"path": os.path.join(proj, "app.py"), "content": "x = 1\n"}, "result": "created"},
            {"conv": conv, "t": "2026-10-12 03:00:09", "tool": "powershell", "ok": True,
             "args": {"command": "python app.py --amount 1000"}, "result": "exit code 1\nerror: bad amount"}]
    with open(os.path.join(data, "agent_log.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    hist = [{"role": "user", "content": "build it"}, {"role": "assistant", "content": "checkpoint here"}]
    json.dump([{"id": conv, "messages": [{"role": "user", "content": "x"}], "agent_state": {"history": hist}}],
              open(os.path.join(data, "conversations.json"), "w"))
    json.dump({"agent": {}}, open(os.path.join(data, "settings.json"), "w"))
    before = sorted(os.listdir(data))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "evals", "context", "replay_point.py"), "--conv", conv,
                        "--after-text", "checkpoint here", "--log-until", "03:00:09", "--dry-run", "--data", data],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout + r.stderr
    assert "NOW: 1.2 \"live check\"" in out and "LAST CHECK on 1.2" in out and "bad amount" in out, out[-2000:]
    assert sorted(os.listdir(data)) == before, "the replay must not write into the source data folder"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
