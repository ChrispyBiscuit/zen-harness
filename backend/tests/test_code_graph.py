"""Code graph P1 (plans/code-graph-prd.md): the broken-reference check, on a regression set — the two bugs that sank
the 3D game runs, seeded bugs, and tricky clean code that must raise no alarm. Targets: catch >= 80% of seeded bugs,
at most 1 false alarm per 20 clean cases, under 200 ms per check once warm."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_graph as cg

pytestmark = pytest.mark.skipif(not cg.available(), reason="tree-sitter not installed")


def project(tmp_path, files):
    root = tmp_path / "proj"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "package.json").write_text("{}")
    return root


def found_in(root, changed):
    return cg.broken_refs(str(root), [str(root / c) for c in changed])


CONFIG_JS = "export const ARENA = { size: 40, wallHeight: 4, playerEyeHeight: 1.6 };\nexport const SPEED = 5;\n"

# (name, files, changed file, a word the finding must contain)
BUGS = [
    ("fps: ARENA.center (run 71a2ec62)", {
        "src/config.js": CONFIG_JS,
        "src/engine.js": "import { ARENA } from './config.js';\nexport class App {\n  constructor(p) {\n"
                         "    p.pos.copy(ARENA.center);\n  }\n}\n"}, "src/engine.js", "center"),
    ("fps: Screen vs UIScreen", {
        "src/ui.js": "export class UIScreen {}\nexport const Hud = 1;\n",
        "src/main.js": "import { Screen } from './ui.js';\nnew Screen();\n"}, "src/main.js", "UIScreen"),
    ("renamed function, old caller", {
        "utils.js": "export function computeScore(a) { return a * 2; }\n",
        "game.js": "import { calcScore } from './utils.js';\ncalcScore(3);\n"}, "game.js", "computeScore"),
    ("misspelt relative path", {
        "config.js": CONFIG_JS,
        "main.js": "import { SPEED } from './confg.js';\nconsole.log(SPEED);\n"}, "main.js", "does not exist"),
    ("namespace member typo", {
        "config.js": CONFIG_JS,
        "main.js": "import * as cfg from './config.js';\nconsole.log(cfg.SPEEED);\n"}, "main.js", "SPEED"),
    ("default import with no default export", {
        "board.js": "export function makeBoard() { return []; }\n",
        "main.js": "import makeBoard from './board.js';\nmakeBoard();\n"}, "main.js", "default"),
    ("CommonJS destructure of a missing export", {
        "math.js": "function add(a, b) { return a + b; }\nmodule.exports = { add };\n",
        "app.js": "const { sub } = require('./math');\nsub(1, 2);\n"}, "app.js", "add"),
    ("TypeScript: renamed export", {
        "types.ts": "export interface Options { size: number }\nexport function build(o: Options) { return o; }\n",
        "main.ts": "import { Options, make } from './types';\nmake({ size: 1 } as Options);\n"}, "main.ts", "make"),
    ("python: from .constants import a misspelt name", {
        "game/__init__.py": "",
        "game/constants.py": "SPEED = 5\nWIDTH = 640\n",
        "game/player.py": "from .constants import SPEEED\n"}, "game/player.py", "SPEED"),
    ("python: missing dict key", {
        "config.py": "ARENA = {'size': 40, 'wall_height': 4}\n",
        "engine.py": "import config\n\ndef start():\n    return config.ARENA['center']\n"}, "engine.py", "center"),
    ("python: module function renamed", {
        "utils.py": "def compute_score(a):\n    return a * 2\n",
        "main.py": "import utils\n\nprint(utils.compute_scor(2))\n"}, "main.py", "compute_score"),
    ("python: relative import of a missing module", {
        "pkg/__init__.py": "",
        "pkg/main.py": "from .helpres import load\n"}, "pkg/main.py", "helpres"),
    ("callers of a changed file: export removed", {
        "store.js": "export function save() {}\n",
        "a.js": "import { load } from './store.js';\nload();\n"}, "store.js", "load"),
]

CLEAN = [
    ("guarded with ??, ?. and typeof", {
        "config.js": CONFIG_JS,
        "main.js": "import { ARENA } from './config.js';\nconst a = ARENA.center ?? [0, 0];\nconst b = ARENA?.center;\n"
                   "if (typeof ARENA.extra === 'undefined') {}\n"}, "main.js"),
    ("a key added by another file", {
        "config.js": CONFIG_JS,
        "setup.js": "import { ARENA } from './config.js';\nARENA.center = [0, 0, 0];\n",
        "main.js": "import { ARENA } from './config.js';\nconsole.log(ARENA.center);\n"}, "main.js"),
    ("object with a spread is open", {
        "config.js": "const base = { a: 1 };\nexport const CFG = { ...base, b: 2 };\n",
        "main.js": "import { CFG } from './config.js';\nconsole.log(CFG.a);\n"}, "main.js"),
    ("shadowed by a parameter", {
        "config.js": CONFIG_JS,
        "main.js": "import { ARENA } from './config.js';\nfunction f(ARENA) { return ARENA.anything; }\n"}, "main.js"),
    ("export * re-exports", {
        "a.js": "export const X = 1;\n",
        "index.js": "export * from './a.js';\n",
        "main.js": "import { X } from './index.js';\nconsole.log(X);\n"}, "main.js"),
    ("packages and URLs are not checked", {
        "main.js": "import * as THREE from 'three';\nimport x from 'https://unpkg.com/x.js';\n"
                   "console.log(THREE.Vector3, x.y);\n"}, "main.js"),
    ("Object.assign makes the object open", {
        "config.js": "export const CFG = { a: 1 };\nObject.assign(CFG, { b: 2 });\n",
        "main.js": "import { CFG } from './config.js';\nconsole.log(CFG.b);\n"}, "main.js"),
    ("JSON import", {
        "data.json": "{\"a\": 1}",
        "main.js": "import data from './data.json';\nconsole.log(data.a);\n"}, "main.js"),
    ("TypeScript types and the .js-for-.ts import style", {
        "types.ts": "export type Id = string;\nexport interface User { id: Id }\nexport enum Role { A }\n",
        "main.ts": "import { Id, User, Role } from './types.js';\nconst u: User = { id: 'x' as Id };\n"
                   "console.log(Role.A);\n"}, "main.ts"),
    ("default export function", {
        "App.tsx": "export default function App() { return null; }\n",
        "main.tsx": "import App from './App';\nApp();\n"}, "main.tsx"),
    ("python: submodule of a package", {
        "pkg/__init__.py": "",
        "pkg/sub.py": "X = 1\n",
        "main.py": "from pkg import sub\nprint(sub.X)\n"}, "main.py"),
    ("python: module __getattr__ and star import", {
        "lazy.py": "def __getattr__(name):\n    return 1\n",
        "star.py": "from os.path import *\n",
        "main.py": "import lazy\nimport star\nprint(lazy.anything, star.join)\n"}, "main.py"),
    ("python: hasattr guard", {
        "mod.py": "A = 1\n",
        "main.py": "import mod\nv = mod.B if hasattr(mod, 'B') else mod.A\n"}, "main.py"),
    ("python: dict changed by its own module", {
        "config.py": "ARENA = {'size': 40}\nARENA['center'] = (0, 0)\n",
        "main.py": "import config\nprint(config.ARENA['center'])\n"}, "main.py"),
    ("python: names defined under if/try", {
        "compat.py": "try:\n    import tomllib as toml\nexcept ImportError:\n    toml = None\nif True:\n"
                     "    FAST = 1\nelse:\n    FAST = 0\n",
        "main.py": "from compat import toml, FAST\nimport compat\nprint(compat.FAST, compat.toml)\n"}, "main.py"),
]


def test_the_regression_set(tmp_path):
    caught, missed, alarms = [], [], []
    for i, (name, files, changed, word) in enumerate(BUGS):
        root = project(tmp_path / f"bug{i}", files)
        found = found_in(root, [changed])
        if any(word in msg for _p, _l, msg in found):
            caught.append(name)
        else:
            missed.append((name, found))
    for i, (name, files, changed) in enumerate(CLEAN):
        root = project(tmp_path / f"clean{i}", files)
        found = found_in(root, [changed])
        if found:
            alarms.append((name, found))
    print(f"\ncaught {len(caught)}/{len(BUGS)} seeded bugs; {len(alarms)} false alarm(s) in {len(CLEAN)} clean cases")
    assert len(caught) >= 0.8 * len(BUGS), missed
    assert not missed[:0]
    assert len(alarms) <= len(CLEAN) // 20, alarms
    assert "fps: ARENA.center (run 71a2ec62)" in caught and "fps: Screen vs UIScreen" in caught


def test_findings_name_the_real_options(tmp_path):
    root = project(tmp_path, dict(BUGS[0][1]))
    (msg,) = [m for _p, _l, m in found_in(root, ["src/engine.js"])]
    assert "size" in msg and "wallHeight" in msg and "config.js" in msg


def test_a_check_stays_fast_on_a_200_file_project(tmp_path):
    files = {f"src/mod{i}.js": f"import {{ f{i - 1} }} from './mod{i - 1}.js';\nexport function f{i}() {{ return f{i - 1}(); }}\n"
             for i in range(1, 200)}
    files["src/mod0.js"] = "export function f0() { return 0; }\n"
    root = project(tmp_path, files)
    found_in(root, ["src/mod100.js"])                          # first check parses the project
    t = time.perf_counter()
    found = found_in(root, ["src/mod100.js"])
    assert not found and time.perf_counter() - t < 0.2


def test_graph_questions(tmp_path):
    root = project(tmp_path, {
        "board.py": "def make_board():\n    return []\n\ndef reset(b):\n    return make_board()\n",
        "game.py": "from board import make_board\n\ndef start():\n    b = make_board()\n    return b\n",
        "tests/test_board.py": "from board import make_board\n\ndef test_it():\n    assert make_board() == []\n"})
    callers = cg.question(str(root), "callers", "make_board")
    assert "3 call(s)" in callers and "game.py:4" in callers and "reset" in callers
    impact = cg.question(str(root), "impact", "make_board")
    assert "test: tests/test_board.py" in impact and "imports it: game.py" in impact
    assert "make_board" in cg.question(str(root), "callees", "reset")
    path = cg.question(str(root), "path", "start", "make_board")
    assert "start" in path and "make_board" in path
    assert "board.py:1" in cg.question(str(root), "explain", "make_board")
    assert "Did you mean" in cg.question(str(root), "explain", "make_bord")


def test_a_write_reports_the_break_and_a_fix_clears_it(tmp_path):
    import agent_tools as at
    root = project(tmp_path, {"src/config.js": CONFIG_JS})
    c = at.Ctx([str(root)]); c.cwd = str(root)
    r = at.Registry(c, enabled={"write_file", "edit_file", "read_file", "code_graph"})
    text, _ = r.call("write_file", {"path": "src/engine.js", "content": "import { ARENA } from './config.js';\n"
                                                                        "export const start = () => ARENA.center;\n"})
    assert "Broken references" in text and "ARENA.center" in text and "src/engine.js:2" in text
    assert at.open_broken_refs(c)
    r.call("read_file", {"path": "src/engine.js"})
    text, _ = r.call("edit_file", {"path": "src/engine.js", "old": "ARENA.center", "new": "ARENA.size"})
    assert "Broken references" not in text and not at.open_broken_refs(c)
    out, _ = r.call("code_graph", {"action": "explain", "name": "start", "root": str(root)})
    assert "src/engine.js:2" in out


def test_the_answer_gate_lists_open_breaks(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_tools as at
    root = project(tmp_path, {"ui.js": "export class UIScreen {}\n"})
    c = at.Ctx([str(root)]); c.cwd = str(root)
    at.Registry(c, enabled={"write_file"}).call("write_file", {"path": "main.js",
                                                               "content": "import { Screen } from './ui.js';\n"})
    (item,) = at.open_broken_refs(c)
    assert item[0] == "main.js" and "UIScreen" in item[2]


P3_FIXTURES = {   # hand-checked: every definition a reader would look for, with its kind and owner
    "c": ("typedef struct { int x; } Pt;\nstruct Board { int w; };\nenum Dir { UP };\n#define SIZE 28\n"
          "int board_new(void) { return 0; }\nstatic void move(struct Board *b) {}\n",
          {("Pt", "type", None), ("Board", "class", None), ("Dir", "enum", None), ("SIZE", "const", None),
           ("board_new", "function", None), ("move", "function", None)}),
    "cpp": ("class Game { public: void tick(); };\nvoid Game::tick() {}\nnamespace ns { int f() { return 1; } }\n",
            {("Game", "class", None), ("tick", "method", "Game"), ("ns", "namespace", None), ("f", "function", None)}),
    "go": ("package main\ntype Board struct{}\nfunc (b *Board) Move() {}\nfunc main() {}\n"
           "type Shape interface{ Area() float64 }\n",
           {("Board", "class", None), ("Move", "method", "Board"), ("main", "function", None),
            ("Shape", "interface", None)}),
    "rust": ("struct Board { w: u32 }\nimpl Board { fn new() -> Self { Board{w:0} } }\nfn main() {}\n"
             "trait T { fn a(&self); }\nenum E { A }\nconst N: u32 = 1;\n",
             {("Board", "class", None), ("new", "method", "Board"), ("main", "function", None),
              ("T", "interface", None), ("a", "method", "T"), ("E", "enum", None), ("N", "const", None)}),
    "csharp": ("namespace N { class Game { public void Tick() {} } interface IX {} enum E { A } }\n",
               {("Game", "class", None), ("Tick", "method", "Game"), ("IX", "interface", None), ("E", "enum", None)}),
    "tsx": ("import { x } from './x';\nexport interface Props { a: number }\nexport type Id = string;\n"
            "export default function App() { const inner = 1; return null }\nexport const Button = () => null;\n"
            "export const api = { about: () => 1, save(x: number) { return x } };\nclass Store { load() {} }\n"
            "export enum Mode { A }\n",
            {("Props", "interface", None), ("Id", "type", None), ("App", "component", None),
             ("Button", "component", None), ("api", "const", None), ("about", "method", "api"),
             ("save", "method", "api"), ("Store", "class", None), ("load", "method", "Store"), ("Mode", "enum", None)}),
}


@pytest.mark.parametrize("lang", sorted(P3_FIXTURES))
def test_p3_definitions_match_the_hand_checked_list(lang):
    text, want = P3_FIXTURES[lang]
    got = {(d["name"], d["kind"], d.get("parent")) for d in cg.ts_defs(text, lang)}
    assert want <= got, want - got
    assert not {n for n, _k, _p in got} & {"inner", "x"}          # locals and imports are not definitions


def test_the_code_map_uses_tree_sitter_and_falls_back(tmp_path):
    import repomap
    defs, _refs = repomap._extract("export const api = { about: () => 1 };\n", "ts")
    assert ("about", "method") in {(d["name"], d["kind"]) for d in defs}
    defs, _refs = repomap._extract("struct Board { int w; };\nint board_new(void) { return 0; }\n", "c")
    assert {"Board", "board_new"} <= {d["name"] for d in defs}
