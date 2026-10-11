"""Tests for projects.py. Runs under pytest, or standalone: `python tests/test_projects.py` (pytest may be absent)."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import projects  # noqa: E402


class _Tmp:
    """Temp folder that also points LWS_DATA_DIR at its own data/ subfolder, restored afterwards."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="zen_proj_")
        self._old = os.environ.get("LWS_DATA_DIR")
        os.environ["LWS_DATA_DIR"] = os.path.join(self.dir, "data")
        return self.dir

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("LWS_DATA_DIR", None)
        else:
            os.environ["LWS_DATA_DIR"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)


def _w(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ---------------------------------------------------------------- find_root

def test_find_root_nesting():
    with _Tmp() as t:
        outer = os.path.join(t, "outer")
        inner = os.path.join(outer, "packages", "inner")
        _w(os.path.join(outer, "pyproject.toml"), "[project]\nname='o'\n")
        _w(os.path.join(inner, "package.json"), "{}")
        deep = _w(os.path.join(inner, "src", "a", "b.js"), "")
        assert _same(projects.find_root(deep), inner)                       # from a file
        assert _same(projects.find_root(os.path.dirname(deep)), inner)      # from a folder
        assert _same(projects.find_root(os.path.join(outer, "packages")), outer)
        assert _same(projects.find_root(outer), outer)
        _w(os.path.join(t, "sln", "App.sln"), "")
        assert _same(projects.find_root(os.path.join(t, "sln")), os.path.join(t, "sln"))


def test_find_root_none():
    with _Tmp() as t:
        lonely = os.path.join(t, "nothing", "here")
        os.makedirs(lonely)
        r = projects.find_root(lonely)
        # The temp dir normally has no marker above it; if the machine has one, it must be outside our tree.
        assert r is None or not os.path.normcase(r).startswith(os.path.normcase(t))
        assert projects.find_root("") is None


# ---------------------------------------------------------------- detect

def test_detect_node_pnpm():
    with _Tmp() as t:
        root = os.path.join(t, "webapp")
        _w(os.path.join(root, "package.json"), json.dumps({"name": "webapp", "scripts": {
            "dev": "vite", "build": "vite build", "lint": "eslint .",
            "test": "echo \"Error: no test specified\" && exit 1"}}))
        _w(os.path.join(root, "pnpm-lock.yaml"), "")
        d = projects.detect(root)
        assert d["kind"] == "node" and d["name"] == "webapp"
        c = d["commands"]
        assert c["setup"] == "pnpm install"
        assert c["run"] == "pnpm run dev"
        assert c["build"] == "pnpm run build"
        assert c["lint"] == "pnpm run lint"
        assert c["test"] == ""                                               # npm-init placeholder ignored
        assert "node_modules" in d["ignore"] and ".zen" in d["ignore"]


def test_detect_node_npm_default():
    with _Tmp() as t:
        _w(os.path.join(t, "package.json"), json.dumps({"scripts": {"start": "node s.js", "test": "jest"}}))
        c = projects.detect(t)["commands"]
        assert c["run"] == "npm run start" and c["test"] == "npm run test" and c["setup"] == "npm install"


def test_detect_python_pytest():
    with _Tmp() as t:
        root = os.path.join(t, "py")
        _w(os.path.join(root, "pyproject.toml"), "[project]\nname='py'\n\n[tool.ruff]\nline-length=120\n")
        _w(os.path.join(root, "requirements.txt"), "requests\n")
        _w(os.path.join(root, "main.py"), "print(1)\n")
        _w(os.path.join(root, "tests", "test_x.py"), "def test_a():\n    pass\n")
        d = projects.detect(root)
        assert d["kind"] == "python"
        c = d["commands"]
        assert c["test"] == "pytest"
        assert c["lint"] == "ruff check ."
        assert c["run"] == "python main.py"
        assert c["setup"] == "pip install -r requirements.txt"


def test_detect_python_module_and_makefile_gaps():
    with _Tmp() as t:
        _w(os.path.join(t, "setup.py"), "")
        _w(os.path.join(t, "tool", "__init__.py"), "")
        _w(os.path.join(t, "tool", "__main__.py"), "")
        _w(os.path.join(t, "Makefile"), "VAR := 1\nlint:\n\tflake8\ntest:\n\tpytest\n")
        c = projects.detect(t)["commands"]
        assert c["run"] == "python -m tool"
        assert c["lint"] == "make lint"                                      # gap filled from Makefile
        assert c["test"] == "make test"                                      # no tests/ dir, so make fills it


def test_detect_web_index_only():
    with _Tmp() as t:
        _w(os.path.join(t, "site", "index.html"), "<html></html>")
        d = projects.detect(os.path.join(t, "site"))
        assert d["kind"] == "web"
        assert d["commands"]["run"] == "open index.html"
        assert d["commands"]["test"] == ""


def test_detect_unknown():
    with _Tmp() as t:
        _w(os.path.join(t, "empty", "notes.txt"), "hi")
        d = projects.detect(os.path.join(t, "empty"))
        assert d["kind"] == "unknown"
        assert all(v == "" for v in d["commands"].values())
        assert set(d["commands"]) == {"setup", "run", "test", "lint", "build"}


def test_detect_rust_go():
    with _Tmp() as t:
        _w(os.path.join(t, "r", "Cargo.toml"), "[package]\nname='r'\n")
        _w(os.path.join(t, "g", "go.mod"), "module g\n")
        assert projects.detect(os.path.join(t, "r"))["commands"]["test"] == "cargo test"
        assert projects.detect(os.path.join(t, "g"))["kind"] == "go"


# ---------------------------------------------------------------- load / save

def test_load_save_round_trip():
    with _Tmp() as t:
        _w(os.path.join(t, "package.json"), json.dumps({"scripts": {"dev": "vite", "build": "vite build"}}))
        first = projects.load(t)
        assert first["saved"] is False and first["commands"]["run"] == "npm run dev"
        cfg = dict(first)
        cfg["commands"] = {"test": "npm run e2e", "lint": ""}               # partial: run/build must be refilled
        cfg["allowed_commands"] = ["npx playwright test"]
        path = projects.save(t, cfg)
        assert _same(path, os.path.join(t, ".zen", "project.json"))
        raw = json.load(open(path, encoding="utf-8"))
        assert "root" not in raw and "saved" not in raw
        back = projects.load(t)
        assert back["saved"] is True
        assert back["commands"]["test"] == "npm run e2e"
        assert back["commands"]["lint"] == ""                                # explicit blank is kept
        assert back["commands"]["run"] == "npm run dev"                      # filled from detect()
        assert back["commands"]["build"] == "npm run build"
        assert back["allowed_commands"] == ["npx playwright test"]
        assert _same(back["root"], t) and back["ignore"]


def test_load_bad_json_falls_back():
    with _Tmp() as t:
        _w(os.path.join(t, ".zen", "project.json"), "{not json")
        cfg = projects.load(t)
        assert cfg["saved"] is False and cfg["kind"] == "unknown"


# ---------------------------------------------------------------- recent registry

def test_recent_order_dedupe_prune():
    with _Tmp() as t:
        a, b, c = (os.path.join(t, n) for n in ("alpha", "beta", "gamma"))
        for p in (a, b, c):
            os.makedirs(p)
        projects.add_recent(a)
        projects.add_recent(b, name="Beta!")
        projects.add_recent(c)
        assert [e["root"] for e in projects.recent()] == [c, b, a]
        projects.add_recent(a.upper() if os.name == "nt" else a)            # same folder, different spelling
        r = projects.recent()
        assert len(r) == 3 and _same(r[0]["root"], a)
        assert _same(r[1]["root"], c) and r[2]["name"] == "Beta!" and r[0]["last_opened"]
        assert len(projects.recent(limit=2)) == 2
        shutil.rmtree(b)
        assert [e["root"] for e in projects.recent()] == [r[0]["root"], c]
        stored = json.load(open(os.path.join(t, "data", "projects.json"), encoding="utf-8"))["recent"]
        assert len(stored) == 2                                              # pruned on disk too
        assert projects.remove_recent(c) is True
        assert projects.remove_recent(c) is False
        assert len(projects.recent()) == 1


def test_recent_empty():
    with _Tmp():
        assert projects.recent() == []


# ---------------------------------------------------------------- instructions

def test_instructions_nearest_wins():
    with _Tmp() as t:
        _w(os.path.join(t, "AGENTS.md"), "root rules: use pnpm")
        _w(os.path.join(t, "pkg", "api", "AGENTS.md"), "api rules: use fastapi")
        f = _w(os.path.join(t, "pkg", "api", "src", "x.py"), "")
        text, src = projects.instructions(t, near=f)
        assert src == ["pkg/api/AGENTS.md", "AGENTS.md"]
        assert text.index("fastapi") < text.index("pnpm")
        text, src = projects.instructions(t, near=os.path.join(t, "pkg"))   # no nearer file: root only
        assert src == ["AGENTS.md"]
        text, src = projects.instructions(t)
        assert src == ["AGENTS.md"] and "pnpm" in text
        _w(os.path.join(t, "CLAUDE.md"), "ignored when AGENTS.md exists")
        assert "ignored" not in projects.instructions(t)[0]


def test_instructions_nested_only():
    with _Tmp() as t:
        _w(os.path.join(t, "sub", "AGENTS.md"), "sub only")
        assert projects.instructions(t, near=os.path.join(t, "sub"))[1] == ["sub/AGENTS.md"]
        assert projects.instructions(t) == ("", [])


def test_instructions_fallbacks():
    with _Tmp() as t:
        assert projects.instructions(t) == ("", [])
        _w(os.path.join(t, ".github", "copilot-instructions.md"), "copilot says hi")
        assert projects.instructions(t)[1] == [".github/copilot-instructions.md"]
        _w(os.path.join(t, ".cursorrules"), "cursorrules legacy")
        assert projects.instructions(t)[1] == [".cursorrules"]
        _w(os.path.join(t, ".cursor", "rules", "b.mdc"), "---\ndescription: b\nglobs: '*.py'\n---\nrule B body")
        _w(os.path.join(t, ".cursor", "rules", "a.mdc"), "rule A body")
        text, src = projects.instructions(t)
        assert src == [".cursor/rules/a.mdc", ".cursor/rules/b.mdc"]
        assert "rule A body" in text and "rule B body" in text and "globs" not in text
        _w(os.path.join(t, "CLAUDE.md"), "claude md wins")
        assert projects.instructions(t)[1] == ["CLAUDE.md"]


def test_instructions_cap():
    with _Tmp() as t:
        _w(os.path.join(t, "AGENTS.md"), "x" * 10000)
        text, src = projects.instructions(t, cap_chars=500)
        assert len(text) <= 500 and "truncated" in text and src == ["AGENTS.md"]
        text, _ = projects.instructions(t, cap_chars=20000)
        assert "truncated" not in text and text.count("x") == 10000


def test_summary():
    with _Tmp() as t:
        root = os.path.join(t, "proj")
        _w(os.path.join(root, "package.json"), json.dumps({"scripts": {"dev": "vite", "build": "vite build"}}))
        _w(os.path.join(root, "AGENTS.md"), "rules")
        s = projects.summary(root)
        assert "proj" in s and "node" in s and "npm run dev" in s and "AGENTS.md" in s
        assert "lint:" not in s and len(s) < 600


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:                                                    # noqa: BLE001 — report and continue
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
