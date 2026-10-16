"""Run the program before "done" (plans/program-run-gate.md): unit tests prove the parts, a run proves they are wired
together. Run 71a2ec62 passed 20/20 tests, ticked 18/18 items and said "ready to play"; the page crashed on load."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_tools as at
import debug_tools as dbg
import verification_ledger as vl
from verification_ledger import Ledger, PolicyError


def ledger_for(root, tmp_path, mode="evidence"):
    led = Ledger(root, tmp_path / "store")
    led.state["mode"] = mode
    led.save()
    return led


def change(led, root, rel, text, conv="c1"):
    before = led.inventory()
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    led.record_changes(before, "write", conv)


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "game"
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text('<script type="module" src="src/main.js"></script>')
    (root / "src" / "main.js").write_text("export const x = 1;\n")
    return root


def test_entry_point_names_the_exact_calls(tmp_path, game):
    led = ledger_for(game, tmp_path)
    ep = led.entry_point()                                   # module scripts: served, not file://
    assert ep["kind"] == "page" and "http.server 8000" in ep["how"] and "browser_open" in ep["how"]
    (game / "server.py").write_text("import http.server\nPORT = 8312\nhttp.server.HTTPServer(('127.0.0.1', PORT), None)\n")
    ep = led.entry_point()
    assert "python server.py" in ep["how"] and "wait_port=8312" in ep["how"] and "127.0.0.1:8312/" in ep["how"]

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "index.html").write_text("<script src='app.js'></script>")
    assert ledger_for(plain, tmp_path).entry_point()["how"].startswith("browser_open(url='file:")

    cli = tmp_path / "cli"
    cli.mkdir()
    (cli / "main.py").write_text("print('hi')\n")
    assert ledger_for(cli, tmp_path).entry_point() == {
        "kind": "cli", "how": f"powershell(command='python main.py', cwd='{cli.resolve()}', timeout=30)"}

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "mathlib.py").write_text("def add(a, b):\n    return a + b\n")
    assert ledger_for(lib, tmp_path).entry_point() is None   # a library: the tests are the run


def test_a_code_change_needs_a_clean_run_of_the_right_kind(tmp_path, game):
    led = ledger_for(game, tmp_path)
    assert led.run_needed("c1") is None                      # nothing changed in this chat
    change(led, game, "tests/test_x.js", "// a test\n")
    assert led.run_needed("c1") is None                      # a test change doesn't need a run
    change(led, game, "src/main.js", "export const x = 2;\n")
    msg, label = led.run_needed("c1")
    assert "Before you finish" in msg and "browser_open" in msg and "tests only" in label
    assert led.run_needed("other chat") is None              # another chat's changes are its own
    led.note_program_run("c1", "server", True)               # a server start isn't the page loading
    assert led.run_needed("c1")
    led.note_program_run("c1", "page", False, "uncaught error: Cannot read properties of undefined (engine.js:83)")
    msg, label = led.run_needed("c1")
    assert "crashed" in msg and "engine.js:83" in msg and "engine.js:83" in label
    led.note_program_run("c1", "page", True)
    assert led.run_needed("c1") is None
    change(led, game, "src/main.js", "export const x = 3;\n")
    assert led.run_needed("c1")                              # changed again: run again


def test_program_runs_are_recorded_from_the_tools_the_model_already_uses(tmp_path, game, monkeypatch):
    led = ledger_for(game, tmp_path)
    monkeypatch.setattr(vl, "for_ctx", lambda ctx: led)
    c = at.Ctx([str(game)]); c.cwd = str(game); c.conv_id = "c1"
    rec = lambda name, args, text: at._record_program_run(c, name, args, text)
    runs = lambda: [(r["kind"], r["ok"]) for r in led.state.get("program_runs", [])]

    rec("powershell", {"command": "python -m pytest"}, "exit code 0")                       # tests: not a run
    rec("powershell", {"command": "pip install requests"}, "exit code 0")
    rec("powershell", {"command": "python main.py"}, "exit code 0  (cwd: x)\nhello\nValueError: bad input skipped")
    rec("powershell", {"command": "python main.py"},
        "exit code 1  (cwd: x)\nTraceback (most recent call last):\n  File \"main.py\", line 3\nNameError: name 'y' is not defined")
    rec("bg_start", {"command": "python server.py"}, "started background process bg1 ('s'): already exited with code 1. ...")
    rec("bg_start", {"command": "python server.py", "wait_port": 8312}, "started background process bg2 ('s'): listening on 8312.")
    rec("http_request", {"url": "http://127.0.0.1:8312/"}, "HTTP 500 in 3 ms")
    assert runs() == [("cli", True), ("cli", False), ("server", False), ("server", True), ("server", False)]
    assert "NameError" in led.state["program_runs"][1]["detail"]

    page = "http://127.0.0.1:8312/"
    rec("browser_open", {"url": page}, "Page: x\nURL: " + page + "\nscrolled 0px of 0px\n" + at._CONSOLE_HEAD
        + "console.warning: deprecated\nuncaught error: Cannot read properties of undefined (reading 'x') (engine.js:83)"
        + "\n\nElements you can use")
    rec("browser_nav", {"action": "reload"}, "reload: Zen Arena (" + page + ")\nJavaScript console: no errors or "
        "warnings since the page was opened.")
    rec("browser_open", {"url": "https://example.com/"}, "JavaScript console: no errors or warnings")   # a website
    assert runs()[-2:] == [("page", False), ("page", True)]
    assert "engine.js:83" in led.state["program_runs"][-2]["detail"]

    other = tmp_path / "elsewhere"
    other.mkdir()
    c.roots.append(str(other))
    c.cwd = str(other)
    n = len(runs())
    rec("powershell", {"command": "python main.py"}, "exit code 0")                         # another folder's program
    assert len(runs()) == n


def test_a_failed_file_counts_as_a_crash():
    text = (at._CONSOLE_HEAD + "network: 404 GET ui.js\n\nElements")
    assert at._page_crash(text) == "network: 404 GET ui.js"
    assert at._page_crash(at._CONSOLE_HEAD + "console.warning: slow\n\nx") == ""


def test_module_mode_ticks_on_unlinked_items_need_the_changes_covered(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    led = ledger_for(root, tmp_path, mode="modules")
    change(led, root, "player.js", "export const p = 1;\n")
    tracker = {"milestones": [{"title": "Build", "status": "pending", "subtasks": [
        {"step": "Build player model with arms and weapon", "status": "pending"},
        {"step": "Write the README", "status": "pending"}]}]}
    with pytest.raises(PolicyError) as e:
        led.check_tracker([{"id": "1.1", "status": "done"}], tracker, "c1")
    assert "player.js" in str(e.value) and "verify_module" in str(e.value)
    led.check_tracker([{"id": "1.2", "status": "done"}], tracker, "c1")       # a docs item isn't held up
    led.note_test_run("c1", "node --test", 3, 0, True, started=9e18)
    led.check_tracker([{"id": "1.1", "status": "done"}], tracker, "c1")


def test_npm_is_picked_only_for_a_real_test_script(tmp_path):
    pkg = tmp_path / "package.json"
    pkg.write_text('{"name": "x", "test": "node --test tests/"}')          # run 71a2ec62: not under scripts
    assert not dbg._npm_test_script(str(pkg))
    pkg.write_text('{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
    assert not dbg._npm_test_script(str(pkg))
    pkg.write_text('{"scripts": {"test": "node --test"}}')
    assert dbg._npm_test_script(str(pkg))
    assert not dbg._npm_test_script(str(tmp_path / "missing.json"))


def test_the_last_tick_says_the_run_is_still_owed(tmp_path, game, monkeypatch):
    led = ledger_for(game, tmp_path)
    monkeypatch.setattr(vl, "for_ctx", lambda ctx: led)
    change(led, game, "src/main.js", "export const x = 5;\n")
    led.note_test_run("c1", "node --test", 2, 0, True, started=9e18)
    c = at.Ctx([str(game)]); c.cwd = str(game); c.conv_id = "c1"; c.build_test_record = True
    c.tracker_path = str(game / "PROGRESS.md")
    r = at.Registry(c, enabled={"update_tracker"})
    r.call("update_tracker", {"checklist": "GOAL: g\n## Build\n- [ ] engine\n- [ ] ui"})
    text, _ = r.call("update_tracker", {"done": "1.1, 1.2"})
    assert "everything is done" in text and "Before you finish" in text and "browser_open" in text
