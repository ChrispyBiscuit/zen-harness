"""Updates from git: the app follows a branch of its GitHub repo and offers new commits as an update.

  check()      fetch the channel branch into the updater's own clone; is there a newer commit than the installed one?
  stage(sha)   check it out there, build the UI (npm), install changed Python requirements, and import-test the new
               code with this app's Python before anything is touched
  apply(sha)   copy the changed code files into the running app's folder (backing up what they replace) and record the
               new version; the caller restarts the server
  rollback()   put the backed-up files back (the Updates button, or automatically when a new version fails to start)

Only code moves. User data (chats, settings, memory, logs) is git-ignored and never touched, so it can stay in the app
folder. The clone and the backups live in <data>/update. The app never pulls into a developer's checkout: the clone is
its own, so work in progress in a dev folder never leaks into the running app.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import paths

DEFAULT_REMOTE = "https://github.com/ChrispyBiscuit/zen-harness.git"
DEFAULT_CHANNEL = "main"
CHECK_EVERY = 6 * 3600                 # while the app stays open
KEEP_BACKUPS = 5
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DETACHED = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0

_lock = threading.RLock()
_busy = {"stage": None}                # what the updater is doing now (for the UI)
# An install's steps, in order, for the progress bar: (key, label, typical seconds). Steps a given update doesn't need
# (no UI change, no new packages) are marked skipped; the bar moves by step and creeps within one by its typical time.
STEPS = [("download", "Downloading the update", 4), ("deps", "Installing UI packages", 60),
         ("build", "Building the interface", 25), ("python", "Installing Python packages", 40),
         ("test", "Testing the new version", 12), ("install", "Installing", 4)]
_progress = {"steps": {}, "current": None, "started": None}


def progress_begin():
    _progress.update(steps={k: "pending" for k, _, _ in STEPS}, current=None, started=time.time())


def _step(key):
    """Mark `key` as the running step (earlier pending steps were not needed: skipped)."""
    if not _progress["steps"]:
        return
    for k, _, _ in STEPS:
        if k == key:
            break
        if _progress["steps"].get(k) in ("pending", "running"):
            _progress["steps"][k] = "done" if _progress["steps"][k] == "running" else "skipped"
    _progress["steps"][key] = "running"
    _progress.update(current=key, step_started=time.time())


def progress_end(ok=True):
    for k, v in list(_progress["steps"].items()):
        if v == "running":
            _progress["steps"][k] = "done" if ok else "failed"
        elif v == "pending" and ok:
            _progress["steps"][k] = "skipped"
    _progress["current"] = None


def progress():
    if not _progress["steps"]:
        return None
    return {"steps": [{"key": k, "label": lab, "typical": t, "state": _progress["steps"].get(k, "pending")}
                      for k, lab, t in STEPS],
            "current": _progress["current"], "step_started": _progress.get("step_started"),
            "started": _progress["started"]}


def fetch_for_install(sha):
    """The download step of an install: make sure the clone has `sha` (normally already fetched by check())."""
    with _lock:
        _step("download")
        _busy["stage"] = "downloading"
        try:
            ensure_clone()
            if not _has(sha):
                _git("fetch", "--quiet", "origin", settings()["channel"], timeout=600)
            if not _has(sha):
                raise UpdateError("the update could not be downloaded (the commit is not on the server)")
        finally:
            _busy["stage"] = None


def decline(sha):
    """'Not now' for this version: the app won't ask again until a newer one appears."""
    st = load_state()
    st["declined_sha"] = sha
    save_state(st)


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------- places and state
def root():
    d = paths.data("update")
    os.makedirs(d, exist_ok=True)
    return d


def clone_dir():
    return os.path.join(root(), "repo")


def app_dir():
    """The running app's code folder (the backend folder of a checkout, or the live install)."""
    return paths.APP_DIR


def _state_path():
    return os.path.join(root(), "state.json")


def load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, _state_path())


def settings():
    """Settings > Updates: {"remote", "channel", "auto_check"} (defaults when unset). auto_check (on by default) is
    'Automatic updates': check in the background and ask (a modal) when a new version is found; nothing installs
    without the user's yes. Off: no background checks; Settings has Check now."""
    try:
        with open(paths.data("settings.json"), encoding="utf-8") as f:
            s = (json.load(f).get("update") or {})
    except (OSError, ValueError):
        s = {}
    return {"remote": s.get("remote") or DEFAULT_REMOTE, "channel": s.get("channel") or DEFAULT_CHANNEL,
            "auto_check": s.get("auto_check", True) is not False}


# ---------------------------------------------------------------- git
def _run(cmd, cwd=None, timeout=600, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=env, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise UpdateError(f"{' '.join(str(c) for c in cmd[:3])} failed: {(p.stderr or p.stdout).strip()[-600:]}")
    return p.stdout


def _git(*args, timeout=300):
    return _run(["git", "-C", clone_dir(), *args], timeout=timeout)


def ensure_clone():
    s = settings()
    if not os.path.isdir(os.path.join(clone_dir(), ".git")):
        shutil.rmtree(clone_dir(), ignore_errors=True)
        _run(["git", "clone", "--quiet", "--branch", s["channel"], s["remote"], clone_dir()], timeout=900)
    else:
        url = _git("remote", "get-url", "origin").strip()
        if url != s["remote"]:
            _git("remote", "set-url", "origin", s["remote"])


def _short(sha):
    return (sha or "")[:7]


def _has(sha):
    try:
        _git("cat-file", "-e", sha + "^{commit}")
        return True
    except UpdateError:
        return False


# ---------------------------------------------------------------- check
def check():
    """Fetch and compare. -> status(): available, installed, latest, commits [{sha, subject, when}]."""
    with _lock:
        s = settings()
        st = load_state()
        ensure_clone()
        _git("fetch", "--quiet", "origin", s["channel"], timeout=300)
        latest = _git("rev-parse", "FETCH_HEAD").strip()
        installed = st.get("installed_sha")
        args = [f"{installed}..{latest}"] if installed and _has(installed) else ["-15", latest]
        commits = []
        for line in _git("log", "--format=%H%x1f%s%x1f%ct", *args).splitlines():
            sha, subj, ct = (line.split("\x1f") + ["", "", "0"])[:3]
            if subj.startswith("Merge "):
                continue                 # merges of the two sessions' work: the commits below say what changed
            commits.append({"sha": sha, "subject": subj, "when": int(ct or 0)})
        st.update({"latest_sha": latest, "commits": commits[:30], "last_check": time.time(), "channel": s["channel"],
                   "check_error": None})
        save_state(st)
        return status()


def status():
    st = load_state()
    installed, latest = st.get("installed_sha"), st.get("latest_sha")
    bk = st.get("backup") or ""
    return {"installed": installed, "installed_short": _short(installed), "latest": latest,
            "latest_short": _short(latest), "available": bool(latest and latest != installed),
            "commits": st.get("commits") or [], "last_check": st.get("last_check"), "channel": st.get("channel"),
            "applied_at": st.get("applied_at"), "can_rollback": bool(bk and os.path.isdir(bk)),
            "busy": _busy["stage"], "error": st.get("apply_error") or st.get("check_error"),
            "rolled_back": st.get("rolled_back"), "declined": st.get("declined_sha"),
            "auto": settings()["auto_check"], "progress": progress()}


# ---------------------------------------------------------------- stage
def _changed(a, b, *prefixes):
    """Files under `prefixes` that differ between commits a and b; None when a is unknown (treat as all changed)."""
    if not (a and _has(a)):
        return None
    out = _git("diff", "--name-only", a, b, "--", *prefixes)
    return [x for x in out.splitlines() if x.strip()]


def stage(sha, python=None):
    """Check out `sha` in the clone, build what git doesn't hold, and import-test it. -> list of notes."""
    python = python or sys.executable.replace("pythonw.exe", "python.exe")
    notes = []
    with _lock:
        try:
            st = load_state()
            _busy["stage"] = "checking out"
            _git("checkout", "--quiet", "--force", "--detach", sha)
            base = st.get("installed_sha")
            ui_changed = _changed(base, sha, "ui")
            dist = os.path.join(clone_dir(), "backend", "web_dist", "index.html")
            has_ui = os.path.exists(os.path.join(clone_dir(), "ui", "package.json"))
            if has_ui and (ui_changed is None or ui_changed or not os.path.exists(dist) or st.get("built_sha") != sha):
                _busy["stage"] = "building the UI"
                ui = os.path.join(clone_dir(), "ui")
                npm = shutil.which("npm.cmd") or shutil.which("npm")
                if not npm:
                    raise UpdateError("the UI changed but npm (Node.js) is not installed, so it can't be built")
                deps = _changed(base, sha, "ui/package-lock.json", "ui/package.json")
                if deps is None or deps or not os.path.isdir(os.path.join(ui, "node_modules")):
                    _step("deps")
                    _run([npm, "ci", "--no-audit", "--no-fund"], cwd=ui, timeout=1200)
                    notes.append("UI packages installed")
                env = dict(os.environ, ZEN_UI_OUT=os.path.join(clone_dir(), "backend", "web_dist"))
                _step("build")
                _run([npm, "run", "build"], cwd=ui, timeout=900, env=env)
                notes.append("UI built")
                st["built_sha"] = sha
                save_state(st)
            req = _changed(base, sha, "backend/requirements.txt")
            if req:                      # (unknown base: assume the installed packages already fit)
                _busy["stage"] = "installing Python packages"
                _step("python")
                _run([python, "-m", "pip", "install", "--quiet", "-r",
                      os.path.join(clone_dir(), "backend", "requirements.txt")], timeout=1800)
                notes.append("Python packages updated")
            _busy["stage"] = "testing the new version"
            _step("test")
            smoke(python)
            notes.append("the new version imports cleanly")
            st = load_state()
            st["staged_sha"] = sha               # built and import-tested: apply() installs only this
            save_state(st)
            return notes
        finally:
            _busy["stage"] = None


def smoke(python):
    """Import the new code with this app's Python against a throwaway data folder: a syntax error or a missing
    package is caught before a single file of the running app changes."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, LWS_DATA_DIR=tmp, LWS_NO_CODE_INDEX="1", PYTHONIOENCODING="utf-8")
        code = "import agent_tools, agentic, web_backend, app_web, updater; print('import-ok')"
        out = _run([python, "-c", code], cwd=os.path.join(clone_dir(), "backend"), timeout=180, env=env)
        if "import-ok" not in out:
            raise UpdateError("the new version failed its import test: " + out[-400:])


# ---------------------------------------------------------------- apply / rollback
def _tracked(sha):
    out = _git("ls-tree", "-r", "--name-only", sha, "--", "backend")
    return [x[len("backend/"):] for x in out.splitlines() if x.startswith("backend/")]


def _same(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def apply(sha):
    """Copy the staged version's code into the app folder. -> {"changed": n, "backup": dir}. Restart afterwards."""
    with _lock:
        try:
            st = load_state()
            if st.get("staged_sha") != sha or _git("rev-parse", "HEAD").strip() != sha:
                raise UpdateError("that version has not been built and tested yet (stage it first)")
            _busy["stage"] = "installing"
            _step("install")
            src, dst = os.path.join(clone_dir(), "backend"), app_dir()
            stamp = time.strftime("%Y%m%d_%H%M%S")
            bk = os.path.join(root(), "backups", f"{stamp}_{_short(st.get('installed_sha')) or 'unknown'}")
            os.makedirs(bk, exist_ok=True)
            m = {"from": st.get("installed_sha"), "to": sha, "replaced": [], "created": [], "deleted": [],
                 "web_dist": False}
            for rel in _tracked(sha):
                s, d = os.path.join(src, rel), os.path.join(dst, rel)
                if not os.path.isfile(s) or (os.path.exists(d) and _same(s, d)):
                    continue
                if os.path.exists(d):
                    os.makedirs(os.path.dirname(os.path.join(bk, rel)), exist_ok=True)
                    shutil.copy2(d, os.path.join(bk, rel))
                    m["replaced"].append(rel)
                else:
                    m["created"].append(rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
            old = st.get("installed_sha")
            if old and _has(old):        # files the new version no longer has
                for g in _git("diff", "--name-only", "--diff-filter=D", old, sha, "--", "backend").splitlines():
                    rel = g[len("backend/"):]
                    d = os.path.join(dst, rel)
                    if os.path.isfile(d):
                        os.makedirs(os.path.dirname(os.path.join(bk, rel)), exist_ok=True)
                        shutil.move(d, os.path.join(bk, rel))
                        m["deleted"].append(rel)
            new_dist, cur_dist = os.path.join(src, "web_dist"), os.path.join(dst, "web_dist")
            if os.path.isdir(new_dist):
                if os.path.isdir(cur_dist):
                    shutil.copytree(cur_dist, os.path.join(bk, "web_dist"))
                    shutil.rmtree(cur_dist)
                shutil.copytree(new_dist, cur_dist)
                m["web_dist"] = True
            with open(os.path.join(bk, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(m, f, indent=1)
            st.update({"installed_sha": sha, "applied_at": time.time(), "backup": bk, "pending_verify": True,
                       "apply_error": None, "rolled_back": None})
            save_state(st)
            _prune_backups()
            return {"changed": len(m["replaced"]) + len(m["created"]) + len(m["deleted"]), "backup": bk}
        finally:
            _busy["stage"] = None


def rollback(reason="asked"):
    """Put the files of the last update back. -> the version now installed."""
    with _lock:
        st = load_state()
        bk = st.get("backup")
        if not (bk and os.path.isdir(bk)):
            raise UpdateError("there is no previous version to go back to")
        with open(os.path.join(bk, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        dst = app_dir()
        for rel in m.get("replaced", []) + m.get("deleted", []):
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copy2(os.path.join(bk, rel), os.path.join(dst, rel))
        for rel in m.get("created", []):
            try:
                os.remove(os.path.join(dst, rel))
            except OSError:
                pass
        if m.get("web_dist") and os.path.isdir(os.path.join(bk, "web_dist")):
            shutil.rmtree(os.path.join(dst, "web_dist"), ignore_errors=True)
            shutil.copytree(os.path.join(bk, "web_dist"), os.path.join(dst, "web_dist"))
        st.update({"installed_sha": m.get("from"), "backup": None, "pending_verify": False,
                   "rolled_back": {"from": m.get("to"), "reason": reason, "at": time.time()}})
        save_state(st)
        return m.get("from")


def _prune_backups():
    d = os.path.join(root(), "backups")
    try:
        old = sorted(os.listdir(d))[:-KEEP_BACKUPS]
    except OSError:
        return
    for name in old:
        shutil.rmtree(os.path.join(d, name), ignore_errors=True)


def mark_started():
    """The server came up on the new version: it no longer needs the automatic rollback."""
    st = load_state()
    if st.get("pending_verify"):
        st["pending_verify"] = False
        st["verified_at"] = time.time()
        save_state(st)


def pending_verify():
    return bool(load_state().get("pending_verify"))


def record_install(sha):
    """Mark the app folder as being at `sha` (a manual deploy, or before the updater's first check)."""
    st = load_state()
    st.update({"installed_sha": sha, "pending_verify": False})
    save_state(st)


# ---------------------------------------------------------------- restart
def restart_server(port, server_cmd, cwd):
    """Start a detached helper that waits for this server to release the port, then starts the server again.
    The caller exits right after; the page notices the new server and reloads."""
    helper = (
        "import socket,subprocess,time\n"
        f"port={int(port)}\n"
        "for _ in range(240):\n"
        "    s=socket.socket(); r=s.connect_ex(('127.0.0.1',port)); s.close()\n"
        "    if r!=0: break\n"
        "    time.sleep(0.25)\n"
        f"cmd={list(server_cmd)!r}\n"
        f"subprocess.Popen(cmd, cwd={cwd!r}, creationflags={DETACHED})\n"
        "def up():\n"
        "    for _ in range(240):\n"
        "        s=socket.socket(); r=s.connect_ex(('127.0.0.1',port)); s.close()\n"
        "        if r==0: return True\n"
        "        time.sleep(0.25)\n"
        "    return False\n"
        "if not up():\n"
        "    import updater\n"
        "    if updater.pending_verify():\n"
        "        updater.rollback('the new version did not start')\n"
        f"        subprocess.Popen(cmd, cwd={cwd!r}, creationflags={DETACHED})\n")
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    subprocess.Popen([exe, "-c", helper], cwd=cwd, creationflags=DETACHED)


# ---------------------------------------------------------------- background checks
def start_background_checks():
    """Check once shortly after start, then every CHECK_EVERY seconds while the app runs (when enabled)."""
    def loop():
        time.sleep(20)                   # let the server and the first model load settle
        while True:
            if settings()["auto_check"]:
                try:
                    check()
                except Exception as e:   # offline, no git: say so in the status, try again later
                    st = load_state()
                    st["check_error"] = str(e)[:300]
                    st["last_check"] = time.time()
                    save_state(st)
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, daemon=True, name="update-check").start()
