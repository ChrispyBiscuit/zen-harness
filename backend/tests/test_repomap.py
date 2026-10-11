"""Tests for repomap.py (M6 code context). Runs under pytest, or standalone: python tests/test_repomap.py
Standalone also prints a benchmark on ../ui/src and the backend folder (not an assertion)."""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
_DATA = tempfile.mkdtemp(prefix="repomap_data_")
os.environ["LWS_DATA_DIR"] = _DATA                    # never write a cache into the project

import repomap  # noqa: E402

FILES = {
    "pkg/__init__.py": "",
    "pkg/util.py": (
        "MAX_SIZE = 10\n"
        "_private = 1\n"
        "\n"
        "def fmt(x):\n"
        "    return str(x)[:MAX_SIZE]\n"
        "\n"
        "def slugify(s):\n"
        "    return fmt(s).lower()\n"
    ),
    "pkg/core.py": (
        "from pkg.util import fmt\n"
        "\n"
        "class Engine:\n"
        "    def __init__(self, name):\n"
        "        self.name = fmt(name)\n"
        "\n"
        "    async def run(self, steps: int = 1) -> str:\n"
        "        return fmt(self.name) * steps\n"
        "\n"
        "def make_engine():\n"
        "    return Engine('x')\n"
    ),
    "app.py": (
        "from pkg.util import fmt\n"
        "from pkg.core import Engine\n"
        "\n"
        "def main():\n"
        "    e = Engine(fmt(1))\n"
        "    print(fmt(2), fmt(3))\n"
        "    return e.run()\n"
    ),
    "other.py": (
        "from pkg.util import fmt, slugify\n"
        "\n"
        "def report(rows):\n"
        "    return [fmt(r) + slugify(r) for r in rows]\n"
    ),
    "web/api.js": (
        "export function fetchData(url) {\n"
        "  return fetch(makeUrl(url))\n"
        "}\n"
        "\n"
        "export const makeUrl = (path) => `/api/${path}`\n"
        "\n"
        "export class Client {\n"
        "  constructor(base) {\n"
        "    this.base = base\n"
        "  }\n"
        "\n"
        "  get(url) {\n"
        "    return fetchData(this.base + url)\n"
        "  }\n"
        "}\n"
    ),
    "web/types.ts": (
        "export interface User {\n"
        "  id: Id\n"
        "  name: string\n"
        "  greet: (other: User) => string\n"
        "}\n"
        "\n"
        "export type Id = string\n"
        "\n"
        "export enum Color { Red, Green }\n"
    ),
    "web/App.tsx": (
        "import { fetchData, Client, makeUrl } from './api'\n"
        "import type { User } from './types'\n"
        "\n"
        "function Header({ user }: { user: User }) {\n"
        "  return <h1>{user.name}</h1>\n"
        "}\n"
        "\n"
        "export const App = () => {\n"
        "  const c = new Client(makeUrl('x'))\n"
        "  fetchData('a')\n"
        "  return <Header user={null as any} />\n"
        "}\n"
    ),
    "styles/site.css": ".card { color: red; }\n#main { margin: 0; }\n",
    "page.html": '<div id="root"></div>\n<script>\nfunction boot() { return 1 }\n</script>\n',
    # everything below must be ignored
    "node_modules/lib/index.js": "export function hiddenNodeFn() { return 'zzneedle' }\n",
    "build/gen.py": "def hidden_build_fn():\n    return 'zzneedle'\n",
    "dist/out.js": "function hiddenDistFn() { return 'zzneedle' }\n",
    "venv/lib/x.py": "def hidden_venv_fn():\n    return 'zzneedle'\n",
    "web/vendor.min.js": "function hiddenMinFn(){return 'zzneedle'}\n",
    "web/packed.js": ("var a=1;" * 200 + "\n") * 15 + "function hiddenPackedFn(){return 'zzneedle'}\n",
    "secret_dir/s.py": "def hidden_gitignored_fn():\n    return 'zzneedle'\n",
    "notes.log": "zzneedle in a log\n",
    ".gitignore": "secret_dir/\n*.log\n",
}


def _make_repo():
    d = tempfile.mkdtemp(prefix="repomap_repo_")
    for rel, text in FILES.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    big = os.path.join(d, "big.py")                     # > 1 MB: skipped
    with open(big, "w", encoding="utf-8") as f:
        f.write("def hidden_big_fn():\n    pass\n" + "# pad zzneedle\n" * 80000)
    return d


_REPO = None


def repo():
    global _REPO
    if _REPO is None:
        _REPO = _make_repo()
    return _REPO


def _names(defs):
    return {(d["name"], d["kind"]) for d in defs}


def _file_order(m):
    return [ln[:-1] for ln in m.splitlines() if ln and not ln.startswith(" ") and ln.endswith(":")]


# ── extraction ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_python_defs():
    defs = repomap.outline(os.path.join(repo(), "pkg/core.py"))
    n = _names(defs)
    assert ("Engine", "class") in n and ("run", "method") in n and ("__init__", "method") in n
    assert ("make_engine", "function") in n
    run = next(d for d in defs if d["name"] == "run")
    assert run["line"] == 7 and run["sig"].startswith("async def run(self, steps: int = 1)") and run["parent"] == "Engine"
    u = _names(repomap.outline(os.path.join(repo(), "pkg/util.py")))
    assert ("MAX_SIZE", "const") in u and ("fmt", "function") in u
    assert not any(nm == "_private" for nm, _k in u)    # only CAPS constants


def test_js_ts_defs():
    js = repomap.outline(os.path.join(repo(), "web/api.js"))
    n = _names(js)
    assert {("fetchData", "function"), ("makeUrl", "function"), ("Client", "class"), ("get", "method")} <= n
    assert next(d for d in js if d["name"] == "get")["parent"] == "Client"
    assert next(d for d in js if d["name"] == "Client")["line"] == 7
    ts = _names(repomap.outline(os.path.join(repo(), "web/types.ts")))
    assert {("User", "interface"), ("Id", "type"), ("Color", "enum")} <= ts
    assert not any(nm == "greet" for nm, _k in ts)       # interface members are types, not definitions
    tsx = _names(repomap.outline(os.path.join(repo(), "web/App.tsx")))
    assert {("App", "component"), ("Header", "component")} <= tsx


def test_other_languages():
    css = _names(repomap.outline(os.path.join(repo(), "styles/site.css")))
    assert (".card", "selector") in css and ("#main", "selector") in css
    html = repomap.outline(os.path.join(repo(), "page.html"))
    assert ("root", "id") in _names(html)
    boot = next(d for d in html if d["name"] == "boot")
    assert boot["line"] == 3


def test_references_counted():
    ix = repomap.index(repo())
    assert ix["app.py"]["refs"]["fmt"] == 4            # import + 3 calls
    assert ix["other.py"]["refs"]["fmt"] == 2
    assert ix["web/App.tsx"]["refs"].get("fetchData", 0) >= 2
    assert "Header" in ix["web/App.tsx"]["refs"]       # JSX use (definition itself not counted)
    assert ix["web/api.js"]["refs"].get("fetchData", 0) == 1   # one call; the definition is not a reference


# ── ignore rules ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_ignore_rules():
    ix = repomap.index(repo())
    for bad in ("node_modules/lib/index.js", "build/gen.py", "dist/out.js", "venv/lib/x.py", "web/vendor.min.js",
                "web/packed.js", "secret_dir/s.py", "big.py"):
        assert bad not in ix, bad
    assert "pkg/util.py" in ix and "web/App.tsx" in ix
    assert repomap.search(repo(), "zzneedle") == []
    assert not repomap.find_definition(repo(), "hidden_build_fn")


# ── repo map ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_map_ranks_most_referenced_first():
    m = repomap.build_map(repo(), budget_tokens=2000)
    order = _file_order(m)
    assert order[0] == "pkg/util.py", m
    assert "  4: def fmt(x):" in m


def test_focus_and_mentions_change_ranking():
    base = _file_order(repomap.build_map(repo(), budget_tokens=2000))
    foc = _file_order(repomap.build_map(repo(), focus_files=["web/types.ts"], budget_tokens=2000))
    assert foc.index("web/types.ts") < base.index("web/types.ts"), (base, foc)
    men = _file_order(repomap.build_map(repo(), mentions=["please fix the Client class"], budget_tokens=2000))
    assert men.index("web/api.js") < base.index("web/api.js"), (base, men)
    # an absolute focus path works too
    foc2 = _file_order(repomap.build_map(repo(), focus_files=[os.path.join(repo(), "web", "types.ts")],
                                         budget_tokens=2000))
    assert foc2 == foc


def test_budget_respected():
    for budget in (30, 80, 200):
        m = repomap.build_map(repo(), budget_tokens=budget)
        assert m and len(m) / 3.5 <= budget, (budget, len(m))
    small, big = repomap.build_map(repo(), budget_tokens=40), repomap.build_map(repo(), budget_tokens=400)
    assert len(big) > len(small)


def test_cache_reuse():
    d = _make_repo()
    t = time.perf_counter()
    repomap.index(d, refresh=True)
    cold = time.perf_counter() - t
    assert repomap.LAST_INDEX_STATS["parsed"] == repomap.LAST_INDEX_STATS["files"] > 5
    assert os.path.exists(repomap._cache_path(d)) and repomap._cache_path(d).startswith(_DATA)
    t = time.perf_counter()
    repomap.index(d)
    warm = time.perf_counter() - t
    assert repomap.LAST_INDEX_STATS["parsed"] == 0
    assert warm < cold, (warm, cold)
    p = os.path.join(d, "other.py")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\ndef added_later():\n    return fmt(9)\n")
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    ix = repomap.index(d)
    assert repomap.LAST_INDEX_STATS["parsed"] == 1
    assert any(x["name"] == "added_later" for x in ix["other.py"]["defs"])
    assert repomap.find_definition(d, "added_later")[0]["path"] == "other.py"
    os.remove(os.path.join(d, "app.py"))
    ix = repomap.index(d)
    assert "app.py" not in ix and repomap.LAST_INDEX_STATS["removed"] == 1


# ── symbol tools ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_find_definition():
    r = repomap.find_definition(repo(), "fmt")
    assert r == [{"path": "pkg/util.py", "line": 4, "kind": "function", "sig": "def fmt(x):"}]
    r = repomap.find_definition(repo(), "Engine.run")
    assert len(r) == 1 and r[0]["path"] == "pkg/core.py" and r[0]["line"] == 7
    assert repomap.find_definition(repo(), "Client.get")[0]["path"] == "web/api.js"
    assert repomap.find_definition(repo(), "User")[0]["kind"] == "interface"
    assert repomap.find_definition(repo(), "nope_not_here") == []


def test_find_references():
    refs = repomap.find_references(repo(), "fmt")
    paths = {(r["path"], r["line"]) for r in refs}
    assert ("pkg/util.py", 4) not in paths               # the definition line is excluded
    assert ("pkg/util.py", 8) in paths and ("app.py", 5) in paths and ("other.py", 4) in paths
    assert all("fmt" in r["text"] for r in refs)
    assert not any(r["path"] == "other.py" and "slugify(s)" in r["text"] for r in refs)
    assert len(repomap.find_references(repo(), "fmt", limit=2)) == 2
    assert not any(r["path"] == "pkg/util.py" and r["line"] == 7 for r in repomap.find_references(repo(), "slugify"))


def test_search():
    r = repomap.search(repo(), r"fmt\(\d\)", glob="*.py", context=1)
    assert [(x["path"], x["line"]) for x in r] == [("app.py", 5), ("app.py", 6)]
    assert r[0]["before"] == ["def main():"] and r[0]["after"] == ["    print(fmt(2), fmt(3))"]
    lit = repomap.search(repo(), "fmt(2)", regex=False, context=0)
    assert len(lit) == 1 and lit[0]["before"] == [] and lit[0]["after"] == []
    assert {x["path"] for x in repomap.search(repo(), "fetchData", glob="*.{ts,tsx}")} == {"web/App.tsx"}
    assert {x["path"] for x in repomap.search(repo(), "fetchData", glob="web/**/*.js")} == {"web/api.js"}
    assert len(repomap.search(repo(), "fmt", limit=3)) == 3
    try:
        repomap.search(repo(), "(unclosed")
        assert False, "bad regex should raise"
    except ValueError:
        pass


# ── benchmark (not an assertion) ──────────────────────────────────────────────────────────────────────────────────
def benchmark():
    targets = [("ui/src", os.path.join(os.path.dirname(ROOT), "ui", "src"), ["add a settings search box"]),
               ("web-agent", ROOT, ["fix the compaction archive history search"])]
    for label, root, mentions in targets:
        if not os.path.isdir(root):
            print(f"[bench] {label}: missing {root}")
            continue
        os.environ["LWS_DATA_DIR"] = tempfile.mkdtemp(prefix="repomap_bench_")
        t = time.perf_counter()
        ix = repomap.index(root)
        cold = time.perf_counter() - t
        t = time.perf_counter()
        repomap.index(root)
        warm = time.perf_counter() - t
        t = time.perf_counter()
        m = repomap.build_map(root, mentions=mentions, budget_tokens=1500)
        first_map = time.perf_counter() - t
        t = time.perf_counter()
        m = repomap.build_map(root, mentions=mentions, budget_tokens=1500)
        warm_map = time.perf_counter() - t
        print(f"\n[bench] {label}: {len(ix)} files, index cold {cold:.2f}s, warm {warm:.3f}s; build_map first "
              f"{first_map:.2f}s (incl. networkx/scipy import), warm {warm_map:.3f}s; map {len(m)} chars "
              f"~{len(m) / 3.5:.0f} tokens; mentions={mentions}")
        print("\n".join(m.splitlines()[:30]))
    os.environ["LWS_DATA_DIR"] = _DATA


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:                           # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if "--no-bench" not in sys.argv:
        benchmark()
    sys.exit(1 if failed else 0)
