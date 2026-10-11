"""Tests for git_tools.py. Runs under pytest, or standalone: `python tests/test_git_tools.py` (pytest may be absent).
Everything happens in temp folders with throwaway repos; no network, no gh calls that create anything."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import git_tools as gt  # noqa: E402

# Make the repos independent of the user's global git config (signing, line endings, identity).
_CFG = {"commit.gpgsign": "false", "tag.gpgsign": "false", "core.autocrlf": "false",
        "init.defaultBranch": "main", "user.name": "Test User", "user.email": "test@example.com"}
_ENV = {"GIT_CONFIG_COUNT": str(len(_CFG))}
for _i, (_k, _v) in enumerate(_CFG.items()):
    _ENV[f"GIT_CONFIG_KEY_{_i}"] = _k
    _ENV[f"GIT_CONFIG_VALUE_{_i}"] = _v


def _rm_ro(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)                     # git object files are read-only on Windows
    func(path)


class _Tmp:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="zen_git_")
        env = dict(_ENV, GIT_CEILING_DIRECTORIES=os.path.dirname(self.dir))   # never discover a repo above us
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        return self.dir

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, onexc=_rm_ro)


def _w(path, text=""):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def _r(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _git(root, *args):
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root, capture_output=True,
                          check=True, creationflags=0x08000000 if os.name == "nt" else 0).stdout.decode("utf-8")


def _repo(base, name="repo"):
    root = os.path.join(base, name)
    os.makedirs(root)
    gt.init(root, first_commit=False)
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    return root


TEN = "".join(f"line {i}\n" for i in range(1, 21))


# ---------------------------------------------------------------- init / is_repo

def test_init_and_is_repo():
    with _Tmp() as d:
        p = os.path.join(d, "proj")
        _w(os.path.join(p, "a.txt"), "hello\n")
        os.makedirs(os.path.join(p, "node_modules", "x"))
        _w(os.path.join(p, "node_modules", "x", "i.js"), "big\n")
        assert not gt.is_repo(p)
        assert gt.status(p)["is_repo"] is False
        sha = gt.init(p)
        assert gt.is_repo(p) and len(sha) == 40
        files = _git(p, "ls-files").split()
        assert "a.txt" in files and ".gitignore" in files and not any("node_modules" in f for f in files)
        assert gt.init(p) == sha                       # idempotent
        assert gt.branches(p)["current"] == "main"
        e = os.path.join(d, "empty")
        assert gt.init(e, first_commit=False) is None and gt.is_repo(e) and gt.log(e) == []


# ---------------------------------------------------------------- status

def test_status_staged_unstaged_untracked_renamed():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "keep.txt"), "k\n")
        _w(os.path.join(r, "old name.txt"), "rename me\n" * 5)
        _w(os.path.join(r, "mod.txt"), "a\n")
        _w(os.path.join(r, "gone.txt"), "x\n")
        gt.stage(r, ["."])
        gt.commit(r, "base")
        os.rename(os.path.join(r, "old name.txt"), os.path.join(r, "new name.txt"))
        gt.stage(r, ["old name.txt", "new name.txt"])
        _w(os.path.join(r, "mod.txt"), "a\nb\n")                   # unstaged modification
        _w(os.path.join(r, "keep.txt"), "k2\n")
        gt.stage(r, "keep.txt")
        _w(os.path.join(r, "keep.txt"), "k3\n")                    # staged AND unstaged
        os.remove(os.path.join(r, "gone.txt"))
        _w(os.path.join(r, "sub dir", "new ü.txt"), "n\n")          # untracked, nested
        st = gt.status(r)
        assert st["is_repo"] and st["branch"] == "main" and st["upstream"] is None
        staged = {e["path"]: e for e in st["staged"]}
        assert staged["new name.txt"]["status"] == "renamed" and staged["new name.txt"]["old_path"] == "old name.txt"
        assert staged["keep.txt"]["status"] == "modified"
        unst = {e["path"]: e["status"] for e in st["unstaged"]}
        assert unst == {"keep.txt": "modified", "mod.txt": "modified", "gone.txt": "deleted"}
        assert st["untracked"] == ["sub dir/new ü.txt"]
        assert st["conflicted"] == []
        # cross-check against git's own short status
        short = _git(r, "status", "--porcelain", "-z", "--untracked-files=all")
        assert "R  new name.txt\0old name.txt\0" in short and "MM keep.txt\0" in short
        assert " M mod.txt\0" in short and " D gone.txt\0" in short and "?? sub dir/new ü.txt\0" in short


def test_status_conflict():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "f.txt"), "base\n")
        gt.stage(r, "f.txt")
        gt.commit(r, "base")
        gt.create_branch(r, "other")
        _w(os.path.join(r, "f.txt"), "other\n")
        gt.stage(r, "f.txt")
        gt.commit(r, "other")
        gt.switch_branch(r, "main")
        _w(os.path.join(r, "f.txt"), "main\n")
        gt.stage(r, "f.txt")
        gt.commit(r, "main")
        subprocess.run(["git", "merge", "other"], cwd=r, capture_output=True)
        st = gt.status(r)
        assert st["conflicted"] == [{"path": "f.txt", "status": "both modified", "code": "UU"}]
        assert "f.txt" in gt.dirty_files(r)


# ---------------------------------------------------------------- diff / parse_diff

def _two_hunk_repo(d):
    r = _repo(d)
    p = os.path.join(r, "file with space é.txt")
    _w(p, TEN)
    gt.stage(r, p)
    gt.commit(r, "base")
    lines = TEN.splitlines(keepends=True)
    lines[1] = "line 2 CHANGED\n"                     # hunk 1
    lines[17] = "line 18 CHANGED\n"                   # hunk 2
    lines.insert(18, "inserted\n")
    _w(p, "".join(lines))
    return r, p


def test_diff_and_parse_multi_hunk():
    with _Tmp() as d:
        r, p = _two_hunk_repo(d)
        text = gt.diff(r, p)
        files = gt.parse_diff(text)
        assert len(files) == 1
        f = files[0]
        assert f["path"] == "file with space é.txt" and f["status"] == "modified" and f["old_path"] == f["path"]
        assert len(f["hunks"]) == 2
        h1, h2 = f["hunks"]
        assert (h1["old_start"], h1["old_lines"], h1["new_start"], h1["new_lines"]) == (1, 5, 1, 5)
        dl = [ln for ln in h1["lines"] if ln["kind"] == "del"][0]
        al = [ln for ln in h1["lines"] if ln["kind"] == "add"][0]
        assert dl == {"kind": "del", "text": "line 2", "old": 2, "new": None}
        assert al == {"kind": "add", "text": "line 2 CHANGED", "old": None, "new": 2}
        adds = [ln["text"] for ln in h2["lines"] if ln["kind"] == "add"]
        assert adds == ["line 18 CHANGED", "inserted"] and h2["new_lines"] == h2["old_lines"] + 1
        assert gt.diff(r, p, staged=True) == ""
        gt.stage(r, p)
        assert gt.parse_diff(gt.diff(r, staged=True))[0]["path"] == f["path"]


def test_parse_diff_added_deleted_renamed_noeol_and_tricky_lines():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "del.txt"), "bye\n")
        _w(os.path.join(r, "ren.txt"), "same content here\n" * 6)
        _w(os.path.join(r, "eol.txt"), "a\nb")
        gt.stage(r, ".")
        gt.commit(r, "base")
        os.remove(os.path.join(r, "del.txt"))
        os.rename(os.path.join(r, "ren.txt"), os.path.join(r, "renamed.txt"))
        _w(os.path.join(r, "add.txt"), "-- looks like a header\n--- also\n+++ and this\n")
        _w(os.path.join(r, "eol.txt"), "a\nc")
        gt.stage(r, ".")
        files = {f["path"]: f for f in gt.parse_diff(gt.diff(r, staged=True))}
        assert files["del.txt"]["status"] == "deleted" and files["del.txt"]["old_path"] == "del.txt"
        assert files["renamed.txt"]["status"] == "renamed" and files["renamed.txt"]["old_path"] == "ren.txt"
        assert files["add.txt"]["status"] == "added" and files["add.txt"]["old_path"] is None
        assert [ln["text"] for ln in files["add.txt"]["hunks"][0]["lines"]] == \
            ["-- looks like a header", "--- also", "+++ and this"]
        el = files["eol.txt"]["hunks"][0]["lines"]
        assert el[-2]["kind"] == "del" and el[-2].get("no_eol") and el[-1]["kind"] == "add" and el[-1].get("no_eol")
        assert len(files) == 4


def test_parse_difflib_output():
    import difflib
    old = ["a\n", "b\n", "c\n"]
    new = ["a\n", "B\n", "c\n"]
    text = "".join(difflib.unified_diff(old, new, "x.txt", "x.txt"))
    f = gt.parse_diff(text)
    assert len(f) == 1 and f[0]["path"] == "x.txt" and len(f[0]["hunks"]) == 1
    assert [ln["kind"] for ln in f[0]["hunks"][0]["lines"]] == ["ctx", "del", "add", "ctx"]


def test_unquote_roundtrip():
    for name in ['tab\there', 'quote"d', 'back\\slash', 'nl\nx']:
        assert gt._unquote(gt._quote(name)) == name
    assert gt._unquote('"caf\\303\\251"') == "café"


# ---------------------------------------------------------------- stage / unstage

def test_stage_unstage():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "a b.txt"), "1\n")
        gt.stage(r, ["a b.txt"])
        assert [e["path"] for e in gt.status(r)["staged"]] == ["a b.txt"]
        gt.unstage(r, ["a b.txt"])                     # no HEAD yet
        st = gt.status(r)
        assert st["staged"] == [] and st["untracked"] == ["a b.txt"]
        gt.stage(r, "a b.txt")
        gt.commit(r, "c1")
        _w(os.path.join(r, "a b.txt"), "2\n")
        gt.stage(r, os.path.join(r, "a b.txt"))         # absolute path accepted
        assert gt.status(r)["staged"][0]["status"] == "modified"
        gt.unstage(r, "a b.txt")
        st = gt.status(r)
        assert st["staged"] == [] and st["unstaged"][0]["path"] == "a b.txt"
        os.remove(os.path.join(r, "a b.txt"))
        gt.stage(r, "a b.txt")                          # staging a deletion
        assert gt.status(r)["staged"][0]["status"] == "deleted"
        try:
            gt.stage(r, "../outside.txt")
            raise AssertionError("expected GitError")
        except gt.GitError:
            pass


# ---------------------------------------------------------------- per-hunk revert

def test_apply_hunk_reverts_one_of_two():
    with _Tmp() as d:
        r, p = _two_hunk_repo(d)
        f = gt.parse_diff(gt.diff(r, p))[0]
        gt.apply_hunk(r, f["path"], f["hunks"][1], reverse=True)       # reject the second hunk
        cur = _r(p)
        assert "line 2 CHANGED" in cur and "line 18 CHANGED" not in cur and "inserted" not in cur
        left = gt.parse_diff(gt.diff(r, p))[0]["hunks"]
        assert len(left) == 1 and left[0]["new_start"] == 1
        # stage that remaining hunk alone, then unstage it again via the index
        h = left[0]
        gt.apply_hunk(r, f["path"], h, reverse=False, staged=True)
        assert len(gt.parse_diff(gt.diff(r, p, staged=True))[0]["hunks"]) == 1 and gt.diff(r, p) == ""
        sh = gt.parse_diff(gt.diff(r, p, staged=True))[0]["hunks"][0]
        gt.apply_hunk(r, f["path"], sh, reverse=True, staged=True)
        assert gt.diff(r, p, staged=True) == "" and "line 2 CHANGED" in _r(p)


def test_apply_hunk_crlf_and_zero_context():
    with _Tmp() as d:
        r = _repo(d)
        p = os.path.join(r, "crlf.txt")
        _w(p, "a\r\nb\r\nc\r\n")
        gt.stage(r, p)
        gt.commit(r, "base")
        _w(p, "a\r\nB\r\nc\r\n")
        h = gt.parse_diff(gt.diff(r, p, context=0))[0]["hunks"][0]
        assert all(ln["kind"] != "ctx" for ln in h["lines"]) and h["lines"][0]["text"] == "b\r"
        gt.apply_hunk(r, "crlf.txt", h)
        assert _r(p) == "a\r\nb\r\nc\r\n"


def test_apply_hunk_mismatch_raises():
    with _Tmp() as d:
        r, p = _two_hunk_repo(d)
        h = gt.parse_diff(gt.diff(r, p))[0]["hunks"][0]
        _w(p, TEN)                                       # file no longer has that change
        try:
            gt.apply_hunk(r, p, h)
            raise AssertionError("expected GitError")
        except gt.GitError as e:
            assert "patch" in str(e).lower() or "apply" in str(e).lower()


def test_revert_hunk_in_file_non_repo():
    import difflib
    with _Tmp() as d:
        p = os.path.join(d, "plain ü.txt")
        old = TEN.splitlines(keepends=True)
        new = list(old)
        new[2] = "line 3 X\n"
        new[15] = "line 16 Y\n"
        _w(p, "".join(new))
        f = gt.parse_diff("".join(difflib.unified_diff(old, new, "a/plain", "b/plain")))[0]
        assert len(f["hunks"]) == 2
        gt.revert_hunk_in_file(p, f["hunks"][0])
        cur = _r(p)
        assert "line 3 X" not in cur and "line 3\n" in cur and "line 16 Y" in cur
        gt.revert_hunk_in_file(p, f["hunks"][1])
        assert _r(p) == TEN
        # forward re-apply, CRLF file, and a hunk at EOF without final newline
        q = _w(os.path.join(d, "w.txt"), "a\r\nb\r\nc")
        h = {"old_start": 2, "old_lines": 2, "new_start": 2, "new_lines": 2, "lines": [
            {"kind": "del", "text": "b", "old": 2, "new": None},
            {"kind": "del", "text": "c", "old": 3, "new": None, "no_eol": True},
            {"kind": "add", "text": "b", "old": None, "new": 2},
            {"kind": "add", "text": "Z", "old": None, "new": 3, "no_eol": True}]}
        gt.revert_hunk_in_file(q, h, reverse=False)
        assert _r(q) == "a\r\nb\r\nZ"
        gt.revert_hunk_in_file(q, h)
        assert _r(q) == "a\r\nb\r\nc"
        try:
            gt.revert_hunk_in_file(q, f["hunks"][0])
            raise AssertionError("expected GitError")
        except gt.GitError:
            pass


# ---------------------------------------------------------------- discard

def test_discard_with_backup():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "t.txt"), "orig\n")
        _w(os.path.join(r, "s.txt"), "orig s\n")
        gt.stage(r, ".")
        gt.commit(r, "base")
        _w(os.path.join(r, "t.txt"), "edited\n")
        _w(os.path.join(r, "s.txt"), "staged edit\n")
        gt.stage(r, "s.txt")
        _w(os.path.join(r, "junk dir", "n é.txt"), "new\n")
        _w(os.path.join(r, "added.txt"), "added\n")
        gt.stage(r, "added.txt")
        bk = os.path.join(d, "backup")
        res = gt.discard(r, ["t.txt", "s.txt", "junk dir", "added.txt"], bk)
        acts = {x["path"]: x["action"] for x in res}
        assert acts == {"t.txt": "restored", "s.txt": "restored", "junk dir/n é.txt": "moved", "added.txt": "moved"}
        assert _r(os.path.join(r, "t.txt")) == "orig\n" and _r(os.path.join(r, "s.txt")) == "orig s\n"
        assert not os.path.exists(os.path.join(r, "junk dir", "n é.txt"))
        assert not os.path.exists(os.path.join(r, "added.txt"))
        assert _r(os.path.join(bk, "t.txt")) == "edited\n" and _r(os.path.join(bk, "s.txt")) == "staged edit\n"
        assert _r(os.path.join(bk, "junk dir", "n é.txt")) == "new\n" and _r(os.path.join(bk, "added.txt")) == "added\n"
        assert gt.dirty_files(r) == []
        _w(os.path.join(r, "t.txt"), "again\n")              # second backup does not overwrite the first
        res = gt.discard(r, "t.txt", bk)
        assert res[0]["backup"].endswith("t.txt.1") and _r(os.path.join(bk, "t.txt")) == "edited\n"


# ---------------------------------------------------------------- commit / log

def test_commit_and_log():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "x.txt"), "1\n")
        gt.stage(r, "x.txt")
        s1 = gt.commit(r, "feat: first\n\nbody text")
        s2 = gt.commit(r, "chore: empty", trailer="Co-Authored-By: Zen <zen@example.com>", allow_empty=True)
        lg = gt.log(r)
        assert [c["sha"] for c in lg] == [s2, s1]
        assert lg[0]["subject"] == "chore: empty" and lg[1]["subject"] == "feat: first"
        assert lg[0]["author"] == "Test User" and lg[0]["short"] == s2[:len(lg[0]["short"])] and "T" in lg[0]["date"]
        assert "Co-Authored-By: Zen <zen@example.com>" in _git(r, "log", "-1", "--format=%B")
        assert len(gt.log(r, n=1)) == 1
        for bad in (lambda: gt.commit(r, "nothing staged"), lambda: gt.commit(r, "  ")):
            try:
                bad()
                raise AssertionError("expected GitError")
            except gt.GitError:
                pass


# ---------------------------------------------------------------- branches

def test_branches_create_switch():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "x.txt"), "1\n")
        gt.stage(r, "x.txt")
        gt.commit(r, "c1")
        gt.create_branch(r, "feature/zen")
        b = gt.branches(r)
        assert b["current"] == "feature/zen" and sorted(b["local"]) == ["feature/zen", "main"]
        gt.create_branch(r, "later", switch=False)
        assert gt.branches(r)["current"] == "feature/zen"
        gt.switch_branch(r, "main")
        b = gt.branches(r)
        assert b["current"] == "main" and set(b["local"]) == {"main", "feature/zen", "later"}
        assert [x["current"] for x in b["detail"] if x["name"] == "main"] == [True]
        for bad in ("-x", "bad name", "a..b", ""):
            try:
                gt.create_branch(r, bad)
                raise AssertionError(f"expected GitError for {bad!r}")
            except gt.GitError:
                pass


# ---------------------------------------------------------------- dirty_files / staged_summary

def test_dirty_files():
    with _Tmp() as d:
        r = _repo(d)
        _w(os.path.join(r, "a.txt"), "1\n")
        _w(os.path.join(r, "b.txt"), "1\n")
        gt.stage(r, ".")
        gt.commit(r, "c")
        assert gt.dirty_files(r) == []
        _w(os.path.join(r, "a.txt"), "2\n")
        _w(os.path.join(r, "c.txt"), "new\n")
        _w(os.path.join(r, "b.txt"), "2\n")
        gt.stage(r, "b.txt")
        assert sorted(gt.dirty_files(r)) == ["a.txt", "b.txt", "c.txt"]


def test_staged_summary_cap():
    with _Tmp() as d:
        r = _repo(d)
        assert gt.staged_summary(r) == ""
        _w(os.path.join(r, "big.txt"), "".join(f"row {i} " + "x" * 60 + "\n" for i in range(500)))
        _w(os.path.join(r, "small.txt"), "s\n")
        gt.stage(r, ".")
        s = gt.staged_summary(r, cap_chars=1500)
        assert len(s) <= 1500 and "big.txt" in s.splitlines()[0] + s and "2 files changed" in s
        assert "diff truncated" in s
        full = gt.staged_summary(r, cap_chars=10 ** 7)
        assert "diff truncated" not in full and full.count("\n+row ") == 500


# ---------------------------------------------------------------- clone / push / remote

def test_clone_from_local_bare_and_push():
    with _Tmp() as d:
        src = _repo(d, "src")
        _w(os.path.join(src, "readme.md"), "hi\n")
        gt.stage(src, ".")
        gt.commit(src, "init")
        bare = os.path.join(d, "remote repo.git")
        subprocess.run(["git", "clone", "-q", "--bare", src, bare], check=True, capture_output=True)
        dest = gt.clone(bare, os.path.join(d, "projects"))
        assert os.path.basename(dest) == "remote repo" and _r(os.path.join(dest, "readme.md")) == "hi\n"
        assert gt.remote_url(dest) and os.path.normcase(os.path.normpath(gt.remote_url(dest))) == \
            os.path.normcase(os.path.normpath(bare))
        st = gt.status(dest)
        assert st["upstream"] == "origin/main" and st["ahead"] == 0
        _w(os.path.join(dest, "readme.md"), "hi again\n")
        gt.stage(dest, "readme.md")
        gt.commit(dest, "edit")
        assert gt.status(dest)["ahead"] == 1
        assert gt.push(dest)["ahead"] == 0
        assert _git(bare, "log", "-1", "--format=%s").strip() == "edit"
    # error cases need no network
    with _Tmp() as d:
        for url in ("-uploadpack=x", "", "https://user:secret@example.com/r.git"):
            try:
                gt.clone(url, d)
                raise AssertionError(f"expected GitError for {url!r}")
            except gt.GitError:
                pass
        assert gt.remote_url(d) is None
        r = _repo(d)
        assert gt.remote_url(r) is None
        _git(r, "remote", "add", "origin", "https://user:tok@example.com/x.git")
        assert gt.remote_url(r) == "https://user@example.com/x.git"


# ---------------------------------------------------------------- pull requests (dry run only)

def test_pr_create_dry_run():
    with _Tmp() as d:
        argv = gt.pr_create(d, "feat: add git panel", "Body with \"quotes\" and\nnewlines", base="main",
                            dry_run=True)
        assert argv == ["gh", "pr", "create", "--title", "feat: add git panel", "--body-file", "-", "--base", "main"]
        assert gt.pr_create(d, "t", "", dry_run=True) == ["gh", "pr", "create", "--title", "t", "--body-file", "-"]
        for kw in ({"title": ""}, {"title": "two\nlines"}, {"title": "ok", "base": "--force"}):
            try:
                gt.pr_create(d, kw.get("title"), "b", base=kw.get("base"), dry_run=True)
                raise AssertionError(f"expected GitError for {kw}")
            except gt.GitError:
                pass


def test_pr_status_without_gh_and_summary():
    with _Tmp() as d:
        orig = gt._gh_path
        gt._gh_path = lambda: None
        try:
            assert gt.pr_status(_repo(d)) is None
        finally:
            gt._gh_path = orig
    s = gt._summarize_pr({"url": "https://github.com/o/r/pull/1", "state": "OPEN", "title": "T",
                          "statusCheckRollup": [
                              {"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
                              {"__typename": "StatusContext", "context": "ci/legacy", "state": "PENDING"}]})
    assert s["checks_summary"] == "pending" and [c["name"] for c in s["checks"]] == ["build", "ci/legacy"]
    s = gt._summarize_pr({"statusCheckRollup": [{"name": "t", "status": "COMPLETED", "conclusion": "FAILURE"}]})
    assert s["checks_summary"] == "failing"
    assert gt._summarize_pr({"statusCheckRollup": None})["checks_summary"] == "none"


# ---------------------------------------------------------------- misc

def test_unicode_and_spaces_end_to_end():
    with _Tmp() as d:
        r = _repo(d, "プロジェクト dir")
        p = _w(os.path.join(r, "ñandú folder", "файл 1 [x].txt"), "uno\n")
        gt.stage(r, p)
        assert gt.status(r)["staged"] == [{"path": "ñandú folder/файл 1 [x].txt", "status": "added", "code": "A"}]
        gt.commit(r, "unicode ✓")
        assert gt.log(r)[0]["subject"] == "unicode ✓"
        _w(p, "uno\ndos\n")
        f = gt.parse_diff(gt.diff(r, "ñandú folder/файл 1 [x].txt"))[0]
        assert f["path"] == "ñandú folder/файл 1 [x].txt"
        gt.apply_hunk(r, f["path"], f["hunks"][0])
        assert _r(p) == "uno\n" and gt.dirty_files(r) == []


def test_is_git_internal_path():
    assert gt.is_git_internal_path(".git/config") and gt.is_git_internal_path("C:\\p\\.GIT\\hooks\\x")
    assert not gt.is_git_internal_path("src/.gitignore") and not gt.is_git_internal_path("a.git/x")


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
