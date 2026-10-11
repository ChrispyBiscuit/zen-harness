"""Tests for checkpoints.py — temp folders only, LWS_DATA_DIR pointed at a temp dir. Run with pytest or directly:
    venv/Scripts/python.exe tests/test_checkpoints.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import checkpoints  # noqa: E402
from checkpoints import Checkpoints  # noqa: E402

_NO_WINDOW = 0x08000000


class _Env:
    """A project folder (with a space in its name) and a separate data dir, both removed afterwards."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="zen ckpt ")
        self.root = os.path.join(self.tmp, "my project")
        self.data = os.path.join(self.tmp, "data dir")
        os.makedirs(self.root)
        os.makedirs(self.data)
        self._old = os.environ.get("LWS_DATA_DIR")
        os.environ["LWS_DATA_DIR"] = self.data
        return self

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8", newline="") as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def __exit__(self, *a):
        if self._old is None:
            os.environ.pop("LWS_DATA_DIR", None)
        else:
            os.environ["LWS_DATA_DIR"] = self._old

        def _onerr(fn, path, *_):
            os.chmod(path, 0o666)
            fn(path)
        shutil.rmtree(self.tmp, onexc=_onerr)


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env, timeout=60,
                       creationflags=_NO_WINDOW)
    assert p.returncode == 0, p.stderr
    return p.stdout.decode("utf-8", "replace")


def test_shadow_lives_in_data_dir():
    with _Env() as e:
        e.write("a.txt", "x")
        cp = Checkpoints(e.root)
        assert cp.git_dir.startswith(os.path.join(e.data, "checkpoints"))
        assert os.path.basename(cp.git_dir) == checkpoints.shadow_id(e.root) + ".git"
        assert not e.exists(".git")
        assert cp.snapshot("first")
        assert not e.exists(".git") and os.listdir(e.root) == ["a.txt"]


def test_modify_restore_roundtrip():
    with _Env() as e:
        e.write("src/app.py", "print('v1')\n")
        e.write("keep.txt", "keep\n")
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("step 1", {"conv": "c1", "turn": 1, "step": 1, "tool": "write_file"})
        assert s1 and set(s1["files"]) == {"src/app.py", "keep.txt"}
        assert s1["meta"]["tool"] == "write_file" and s1["label"] == "step 1"
        # "shell command" edits via plain Python
        e.write("src/app.py", "print('v2')\n")
        s2 = cp.snapshot("step 2", {"tool": "powershell"})
        assert s2 and s2["files"] == ["src/app.py"]
        lst = cp.list()
        assert [x["sha"] for x in lst] == [s2["sha"], s1["sha"]] and lst[0]["meta"]["tool"] == "powershell"
        assert "+print('v2')" in cp.diff(s1["sha"], s2["sha"])
        r = cp.restore(s1["sha"])
        assert e.read("src/app.py") == "print('v1')\n"
        assert r["restored"] == ["src/app.py"] and r["deleted"] == []
        assert r["safety"] == s2["sha"]                   # nothing changed since s2 -> safety is s2 itself


def test_created_file_removed_and_deleted_file_restored():
    with _Env() as e:
        e.write("a.txt", "A\n")
        e.write("gone.txt", "will be deleted\n")
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("base")
        os.remove(os.path.join(e.root, "gone.txt"))
        e.write("new/dir/created.txt", "new\n")
        e.write("a.txt", "A2\n")
        r = cp.restore(s1["sha"])                         # not snapshotted yet: safety snapshot captures it
        assert e.read("gone.txt") == "will be deleted\n"
        assert e.read("a.txt") == "A\n"
        assert not e.exists("new/dir/created.txt") and not e.exists("new")
        assert r["deleted"] == ["new/dir/created.txt"]
        assert set(r["restored"]) == {"a.txt", "gone.txt"}
        safety = cp.list()[0]
        assert safety["sha"] == r["safety"] and safety["label"] == "before restore"
        # restore forward again via the safety snapshot
        cp.restore(r["safety"])
        assert e.read("new/dir/created.txt") == "new\n" and not e.exists("gone.txt")


def test_exclusions_node_modules_gitignore_and_restore_never_touches_them():
    with _Env() as e:
        e.write("index.js", "1")
        e.write("node_modules/lib/x.js", "dep")
        e.write("venv/Lib/y.py", "venv")
        e.write("__pycache__/z.pyc", "pyc")
        e.write(".gitignore", "secret.env\nlogs/\n")
        e.write("secret.env", "TOKEN=1")
        e.write("logs/run.log", "log")
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("base")
        assert set(s1["files"]) == {"index.js", ".gitignore"}
        e.write("node_modules/lib/later.js", "added after")
        e.write("secret.env", "TOKEN=2")
        cp.restore(s1["sha"])
        assert e.exists("node_modules/lib/later.js") and e.read("secret.env") == "TOKEN=2"


def test_large_file_skipped():
    with _Env() as e:
        e.write("small.txt", "s")
        with open(os.path.join(e.root, "big file [1].bin"), "wb") as f:
            f.write(b"\0" * (checkpoints.MAX_FILE_BYTES + 1))
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("base")
        assert s1["files"] == ["small.txt"]
        # a tracked file that grows past the cap is dropped from the snapshot, not committed
        with open(os.path.join(e.root, "small.txt"), "wb") as f:
            f.write(b"x" * (checkpoints.MAX_FILE_BYTES + 10))
        s2 = cp.snapshot("grew")
        assert s2 and s2["files"] == ["small.txt"]
        assert {"path": "small.txt", "status": "D"} in cp.files_changed(s1["sha"], s2["sha"])


def test_no_change_returns_none_and_empty_project():
    with _Env() as e:
        cp = Checkpoints(e.root)
        assert cp.snapshot("empty") is None and cp.list() == []
        e.write("a.txt", "a")
        assert cp.snapshot("one")
        assert cp.snapshot("again") is None
        os.utime(os.path.join(e.root, "a.txt"), None)    # touched, same content
        assert cp.snapshot("touched") is None
        assert len(cp.list()) == 1


def test_diff_to_working_tree():
    with _Env() as e:
        e.write("a.txt", "line1\n")
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("base")
        e.write("a.txt", "line1\nline2\n")
        e.write("fresh.txt", "untracked so far\n")
        d = cp.diff(s1["sha"])
        assert "+line2" in d and "+untracked so far" in d
        fc = {x["path"]: x["status"] for x in cp.files_changed(s1["sha"])}
        assert fc == {"a.txt": "M", "fresh.txt": "A"}
        # diffing the working tree must not create a snapshot or disturb the real index
        assert len(cp.list()) == 1
        s2 = cp.snapshot("next")
        assert set(s2["files"]) == {"a.txt", "fresh.txt"}


def test_unicode_and_spaces():
    with _Env() as e:
        e.write("dossier été/naïve 文件.txt", "v1")
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("uni")
        assert s1["files"] == ["dossier été/naïve 文件.txt"]
        e.write("dossier été/naïve 文件.txt", "v2")
        e.write("dossier été/ñ new.txt", "n")
        r = cp.restore(s1["sha"])
        assert e.read("dossier été/naïve 文件.txt") == "v1" and not e.exists("dossier été/ñ new.txt")
        assert r["deleted"] == ["dossier été/ñ new.txt"]


def test_real_git_repo_untouched():
    with _Env() as e:
        _git(e.root, "init", "-q")
        _git(e.root, "config", "user.name", "t")
        _git(e.root, "config", "user.email", "t@t")
        e.write("tracked.txt", "t1\n")
        e.write(".gitignore", "ignored.txt\n")
        _git(e.root, "add", "-A")
        _git(e.root, "commit", "-q", "-m", "init")
        e.write("tracked.txt", "t2 dirty\n")              # leave a dirty working tree + an untracked file
        e.write("untracked.txt", "u\n")
        e.write("ignored.txt", "i\n")

        def state():
            g = os.path.join(e.root, ".git")
            with open(os.path.join(g, "index"), "rb") as f:
                idx = f.read()
            return (_git(e.root, "rev-parse", "HEAD"), _git(e.root, "status", "--porcelain", "--ignored"),
                    _git(e.root, "log", "--oneline", "--all"), idx,
                    sorted(os.path.relpath(os.path.join(dp, n), g) for dp, _, fs in os.walk(g) for n in fs))

        before = state()
        cp = Checkpoints(e.root)
        s1 = cp.snapshot("s1")
        assert set(s1["files"]) == {".gitignore", "tracked.txt", "untracked.txt"}
        assert not any(f.startswith(".git/") for f in s1["files"])
        assert state() == before
        e.write("tracked.txt", "t3\n")
        cp.snapshot("s2")
        cp.diff(s1["sha"])
        cp.restore(s1["sha"])
        assert e.read("tracked.txt") == "t2 dirty\n"
        assert state() == before


def test_prune_keeps_newest():
    with _Env() as e:
        cp = Checkpoints(e.root)
        shas = []
        for i in range(8):
            e.write("f.txt", f"v{i}\n")
            shas.append(cp.snapshot(f"s{i}")["sha"])
        res = cp.prune(keep=3)
        assert res["pruned"] == 5
        lst = cp.list()
        assert [x["sha"] for x in lst] == shas[:4:-1]     # newest 3, shas unchanged
        cp.restore(shas[5])
        assert e.read("f.txt") == "v5\n"
        e.write("f.txt", "after prune\n")
        assert cp.snapshot("after")                       # history still extends after pruning
        assert cp.prune(keep=100)["pruned"] == 0


def test_timing_2000_files():
    with _Env() as e:
        for i in range(2000):
            e.write(f"pkg{i // 100}/mod_{i}.py", f"x = {i}\n")
        cp = Checkpoints(e.root)
        t0 = time.perf_counter()
        s1 = cp.snapshot("initial")
        t_first = time.perf_counter() - t0
        assert len(s1["files"]) == 2000
        t0 = time.perf_counter()
        assert cp.snapshot("noop") is None
        t_noop = time.perf_counter() - t0
        e.write("pkg3/mod_300.py", "x = 'changed'\n")
        t0 = time.perf_counter()
        s2 = cp.snapshot("one change")
        t_one = time.perf_counter() - t0
        assert s2["files"] == ["pkg3/mod_300.py"]
        t0 = time.perf_counter()
        cp.restore(s1["sha"])
        t_restore = time.perf_counter() - t0
        print(f"    timing (2000 files): first snapshot {t_first:.2f}s, no-change {t_noop:.2f}s, "
              f"1-file change {t_one:.2f}s, restore {t_restore:.2f}s")
        assert t_one < 10


if __name__ == "__main__":
    fails = 0
    for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"PASS {name} ({time.perf_counter() - t0:.2f}s)")
        except Exception as ex:  # noqa: BLE001
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {name}: {ex!r}")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
