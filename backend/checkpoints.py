"""Shadow-git checkpoints (Cline-style) — every file-changing agent step can be undone (PRD M2, section 4C).

After each tool call that can change files (write, edit, move, trash, PowerShell, python_host) the agent calls
Checkpoints(root).snapshot(label, meta). The snapshot is a commit in a SEPARATE git directory kept in the app's data
folder, never inside the project:
    <data>/checkpoints/<id>.git          id = sha1(normalised lower-case root path)[:12]
Every git call runs as `git --git-dir=<shadow> --work-tree=<root> ...`, so the project's own .git (if any), its index
and its history are never read or written, and plain folders work the same way. Because the snapshot stages the whole
tree, changes made by shell commands are captured exactly like edit_file changes.

Exclusions live in the shadow repo's info/exclude (refreshed every snapshot): the project's root .gitignore, a list
of heavy defaults (node_modules/, venv/, build output...), and any file over MAX_FILE_BYTES.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_EXCLUDES = [".git/", "node_modules/", ".venv/", "venv/", "__pycache__/", "dist/", "build/", ".next/",
                    "target/", "*.pyc", ".zen/checkpoints/"]
META_PREFIX = "zen-meta: "
_NO_WINDOW = 0x08000000                                  # CREATE_NO_WINDOW: no console flash from the GUI app
_locks = {}                                              # one lock per shadow repo: git's index.lock is not a queue
_locks_guard = threading.Lock()


class CheckpointError(RuntimeError):
    pass


def _data_dir(data_dir=None):
    import paths
    return data_dir or paths.data_dir()


def shadow_id(root):
    """Stable id per project folder; lower-cased because Windows paths are case-insensitive."""
    norm = os.path.normpath(os.path.abspath(root)).lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _clean_env():
    """Drop inherited GIT_* variables — a GIT_DIR or GIT_INDEX_FILE from a parent process would redirect our calls."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _escape_pattern(path):
    """Anchor a literal path for info/exclude, escaping glob characters so the line matches only that file."""
    out = path
    for ch in "\\*?[":
        out = out.replace(ch, "\\" + ch)
    if out.endswith(" "):
        out = out[:-1] + "\\ "
    return "/" + out


def _split_z(raw):
    return [p for p in raw.decode("utf-8", "replace").split("\0") if p]


class Checkpoints:
    def __init__(self, root, data_dir=None):
        self.root = os.path.normpath(os.path.abspath(root))
        if not os.path.isdir(self.root):
            raise CheckpointError(f"not a folder: {self.root}")
        self.id = shadow_id(self.root)
        self.git_dir = os.path.join(_data_dir(data_dir), "checkpoints", self.id + ".git")
        with _locks_guard:
            self._lock = _locks.setdefault(self.git_dir.lower(), threading.RLock())
        self._ensure_repo()

    # ---- plumbing ---------------------------------------------------------------------------------------------
    def _git(self, *args, input=None, env_extra=None, timeout=120, check=True):
        """Run git against the shadow repo; -> stdout bytes. --literal-pathspecs so '[1].txt' means that file."""
        cmd = ["git", "--git-dir=" + self.git_dir, "--work-tree=" + self.root, "--literal-pathspecs",
               "-c", "core.quotepath=false", "-c", "commit.gpgsign=false", *args]
        env = _clean_env()
        if env_extra:
            env.update(env_extra)
        try:
            p = subprocess.run(cmd, cwd=self.root, input=input, capture_output=True, env=env, timeout=timeout,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise CheckpointError(f"git {args[0]} timed out after {timeout}s") from e
        if check and p.returncode != 0:
            raise CheckpointError(f"git {' '.join(args[:3])} failed: {p.stderr.decode('utf-8', 'replace').strip()}")
        return p.stdout

    def _ensure_repo(self):
        if os.path.exists(os.path.join(self.git_dir, "HEAD")):
            return
        os.makedirs(self.git_dir, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-q", self.git_dir], capture_output=True, env=_clean_env(),
                       timeout=60, creationflags=_NO_WINDOW, check=True)
        for key, val in [("core.bare", "false"), ("core.autocrlf", "false"), ("core.safecrlf", "false"),
                         ("core.longpaths", "true"), ("core.symlinks", "false"), ("core.logAllRefUpdates", "false"),
                         ("core.untrackedCache", "true"), ("user.name", "zen-checkpoints"),
                         ("user.email", "zen-checkpoints@localhost"), ("gc.auto", "256"),
                         ("gc.autoPackLimit", "8"), ("zen.root", self.root)]:
            self._git("config", key, val)

    def _head(self):
        out = self._git("rev-parse", "--verify", "-q", "HEAD", check=False).strip()
        return out.decode() or None

    def _write_excludes(self, extra=()):
        lines = ["# written by checkpoints.py — refreshed on every snapshot"]
        gi = os.path.join(self.root, ".gitignore")
        if os.path.isfile(gi):
            try:
                with open(gi, encoding="utf-8", errors="replace") as f:
                    lines += ["# project .gitignore"] + [l.rstrip("\r\n") for l in f]
            except OSError:
                pass
        lines += ["# defaults (last, so a project '!' rule cannot re-include them)"] + DEFAULT_EXCLUDES
        if extra:
            lines += ["# files over the size cap"] + [_escape_pattern(p) for p in extra]
        path = os.path.join(self.git_dir, "info", "exclude")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def _stage(self, env_extra=None):
        """Stage the working tree (minus exclusions and big files) into the index given by env_extra; -> tree sha."""
        self._write_excludes()
        cand = _split_z(self._git("ls-files", "-z", "-o", "-m", "--exclude-standard", env_extra=env_extra))
        big = []
        for rel in cand:
            try:
                if os.path.getsize(os.path.join(self.root, rel)) > MAX_FILE_BYTES:
                    big.append(rel)
            except OSError:
                pass                                   # deleted since — `add -A` records the deletion
        if big:
            self._write_excludes(big)
            self._git("rm", "--cached", "-q", "--ignore-unmatch", "--pathspec-from-file=-", "--pathspec-file-nul",
                      input="\0".join(big).encode("utf-8"), env_extra=env_extra)
        self._git("add", "-A", "--ignore-errors", env_extra=env_extra, timeout=600, check=False)
        return self._git("write-tree", env_extra=env_extra).decode().strip()

    def _info(self, sha):
        return self._parse_log(self._git("log", "-1", "-z", "--name-only", "--format=" + _LOG_FMT, sha))[0]

    @staticmethod
    def _parse_log(raw):
        out = []
        for chunk in raw.decode("utf-8", "replace").split("\x1e")[1:]:
            sha, ct, body, rest = chunk.split("\x1f", 3)
            label, meta = body.strip(), None
            lines = label.splitlines()
            if lines and lines[-1].startswith(META_PREFIX):
                try:
                    meta = json.loads(lines[-1][len(META_PREFIX):])
                except ValueError:
                    pass
                label = "\n".join(lines[:-1]).strip()
            files = [f.strip("\n") for f in rest.split("\0") if f.strip("\n")]
            out.append({"sha": sha, "label": label, "time": int(ct), "meta": meta, "files": files})
        return out

    def _tree_for(self, to):
        """`to` snapshot sha, or None for the current working tree (staged into a throwaway index)."""
        if to is not None:
            return to
        tmp_index = os.path.join(self.git_dir, "index.zen-tmp")
        real = os.path.join(self.git_dir, "index")
        try:
            if os.path.exists(real):
                with open(real, "rb") as src, open(tmp_index, "wb") as dst:
                    dst.write(src.read())                # copy keeps stat info, so unchanged files are not re-hashed
            elif os.path.exists(tmp_index):
                os.remove(tmp_index)
            return self._stage({"GIT_INDEX_FILE": tmp_index})
        finally:
            try:
                os.remove(tmp_index)
            except OSError:
                pass

    # ---- public API -------------------------------------------------------------------------------------------
    def snapshot(self, label, meta=None):
        """Commit the tree if anything changed since the last snapshot; -> info dict, or None (no empty commits)."""
        with self._lock:
            head = self._head()
            tree = self._stage()
            if head:
                if self._git("rev-parse", head + "^{tree}").decode().strip() == tree:
                    return None
            elif not self._git("ls-files", "-z"):
                return None                               # empty project: nothing worth a commit
            msg = str(label or "checkpoint").strip() or "checkpoint"
            if meta:
                msg += "\n\n" + META_PREFIX + json.dumps(meta, ensure_ascii=False, default=str)
            args = ["commit-tree", tree, "-F", "-"] + (["-p", head] if head else [])
            sha = self._git(*args, input=msg.encode("utf-8")).decode().strip()
            self._git("update-ref", "HEAD", sha)
            return self._info(sha)

    def list(self, limit=100):
        with self._lock:
            if not self._head():
                return []
            raw = self._git("log", "-n", str(int(limit)), "-z", "--name-only", "--format=" + _LOG_FMT, "HEAD")
            return self._parse_log(raw)

    def diff(self, sha, to=None):
        """Unified diff from snapshot `sha` to snapshot `to`, or to the current working tree when to is None."""
        with self._lock:
            raw = self._git("diff", "--no-color", "--no-ext-diff", "--no-renames", sha, self._tree_for(to))
            return raw.decode("utf-8", "replace")

    def files_changed(self, sha, to=None):
        with self._lock:
            parts = _split_z(self._git("diff", "--name-status", "-z", "--no-renames", sha, self._tree_for(to)))
            return [{"path": parts[i + 1], "status": parts[i]} for i in range(0, len(parts) - 1, 2)]

    def restore(self, sha):
        """Make every shadow-tracked path match snapshot `sha`; excluded/ignored paths are never touched."""
        with self._lock:
            sha = self._git("rev-parse", "--verify", sha + "^{commit}").decode().strip()
            safe = self.snapshot("before restore", {"restore_to": sha})
            safety = safe["sha"] if safe else self._head()
            parts = _split_z(self._git("diff", "--name-status", "-z", "--no-renames", sha, "HEAD"))
            changes = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
            to_restore = [p for st, p in changes if st != "A"]     # modified, type-changed, or deleted since sha
            to_delete = [p for st, p in changes if st == "A"]      # created after sha
            if to_restore:
                self._git("checkout", sha, "--pathspec-from-file=-", "--pathspec-file-nul",
                          input="\0".join(to_restore).encode("utf-8"), timeout=600)
            deleted = []
            for rel in to_delete:
                full = os.path.join(self.root, rel)
                try:
                    os.chmod(full, 0o666)
                    os.remove(full)
                    deleted.append(rel)
                except FileNotFoundError:
                    deleted.append(rel)
                except OSError:
                    continue
                self._remove_empty_parents(os.path.dirname(full))
            if to_delete:
                self._git("rm", "--cached", "-q", "--ignore-unmatch", "--pathspec-from-file=-",
                          "--pathspec-file-nul", input="\0".join(to_delete).encode("utf-8"))
            return {"restored": to_restore, "deleted": deleted, "safety": safety}

    def _remove_empty_parents(self, d):
        while os.path.normcase(d).startswith(os.path.normcase(self.root) + os.sep):
            try:
                os.rmdir(d)                                # only succeeds when empty
            except OSError:
                return
            d = os.path.dirname(d)

    def prune(self, keep=300):
        """Keep the newest `keep` snapshots. Cheap: mark the oldest kept commit as a shallow boundary (as a shallow
        clone does) so older history becomes unreachable, then gc drops it. Kept shas do not change."""
        with self._lock:
            head = self._head()
            if not head:
                return {"pruned": 0}
            total = int(self._git("rev-list", "--count", "HEAD").decode().strip())
            if total <= keep or keep < 1:
                self._git("gc", "--auto", "--quiet", timeout=600, check=False)
                return {"pruned": 0}
            base = self._git("rev-list", "-n", "1", "--skip=" + str(keep - 1), "HEAD").decode().strip()
            with open(os.path.join(self.git_dir, "shallow"), "w", encoding="ascii", newline="\n") as f:
                f.write(base + "\n")
            self._git("reflog", "expire", "--expire=now", "--all", check=False)
            self._git("gc", "--prune=now", "--quiet", timeout=600, check=False)
            return {"pruned": total - keep}

    def size_bytes(self):
        total = 0
        for dp, _, fs in os.walk(self.git_dir):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return total


_LOG_FMT = "%x1e%H%x1f%ct%x1f%B%x1f"
