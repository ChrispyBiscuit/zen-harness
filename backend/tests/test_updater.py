"""Updates from git, end to end against a local repo standing in for GitHub: check finds new commits, stage +
apply copy only code into the app folder (with a backup), a later update removes deleted files, rollback restores."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "backend").mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t"); git(origin, "config", "user.name", "t")
    (origin / "backend" / "app.py").write_text("V = 1\n")
    (origin / "backend" / "old.py").write_text("gone soon\n")
    git(origin, "add", "-A"); git(origin, "commit", "-q", "-m", "first")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True).stdout.strip()
    app = tmp_path / "app"; app.mkdir()
    (app / "app.py").write_text("V = 1\n"); (app / "old.py").write_text("gone soon\n")
    (app / "conversations.json").write_text("{\"mine\": 1}")          # user data: never touched
    data = tmp_path / "data"; data.mkdir()
    (data / "settings.json").write_text(json.dumps({"update": {"remote": str(origin), "channel": "main"}}))
    monkeypatch.setenv("LWS_DATA_DIR", str(data))
    monkeypatch.setattr(updater, "app_dir", lambda: str(app))
    monkeypatch.setattr(updater, "smoke", lambda python: None)
    # module state the updater keeps between calls: fresh for every test, whatever ran before
    monkeypatch.setattr(updater, "_progress", {"steps": {}, "current": None, "started": None})
    monkeypatch.setattr(updater, "_busy", {"stage": None})
    updater.record_install(first)
    return origin, app, first


def test_check_stage_apply_then_rollback(env):
    origin, app, first = env
    assert not updater.check()["available"]
    (origin / "backend" / "app.py").write_text("V = 2\n")
    (origin / "backend" / "new.py").write_text("N = 1\n")
    git(origin, "rm", "-q", "backend/old.py")
    git(origin, "commit", "-q", "-am", "second: bump V")
    git(origin, "add", "-A"); git(origin, "commit", "-q", "-m", "add new", "--allow-empty")
    st = updater.check()
    assert st["available"] and [c["subject"] for c in st["commits"]] == ["add new", "second: bump V"]
    updater.stage(st["latest"])
    res = updater.apply(st["latest"])
    assert (app / "app.py").read_text() == "V = 2\n" and (app / "new.py").exists() and not (app / "old.py").exists()
    assert (app / "conversations.json").read_text() == "{\"mine\": 1}"
    assert res["changed"] == 3 and updater.pending_verify() and not updater.check()["available"]
    assert updater.rollback() == first
    assert (app / "app.py").read_text() == "V = 1\n" and (app / "old.py").exists() and not (app / "new.py").exists()
    assert updater.status()["available"] and updater.status()["rolled_back"]["reason"] == "asked"


def test_install_reports_its_steps_and_not_now_is_remembered(env):
    """The steps the progress modal shows: what ran is done, what this update didn't need (no UI, no new packages)
    is skipped. 'Not now' is stored for that version; automatic updates are on unless switched off."""
    origin, app, first = env
    (origin / "backend" / "app.py").write_text("V = 4\n")
    git(origin, "commit", "-q", "-am", "fourth")
    st = updater.check()
    assert st["auto"] is True
    updater.decline(st["latest"])
    assert updater.status()["declined"] == st["latest"]
    updater.progress_begin()
    updater.fetch_for_install(st["latest"])
    updater.stage(st["latest"])
    updater.apply(st["latest"])
    updater.progress_end(True)
    steps = {s["key"]: s["state"] for s in updater.status()["progress"]["steps"]}
    assert steps == {"download": "done", "deps": "skipped", "build": "skipped", "python": "skipped",
                     "test": "done", "install": "done"}
    assert (app / "app.py").read_text() == "V = 4\n" and (app / "conversations.json").read_text() == "{\"mine\": 1}"


def test_automatic_updates_can_be_switched_off(env, monkeypatch):
    data = Path(os.environ["LWS_DATA_DIR"])
    s = json.loads((data / "settings.json").read_text())
    s["update"]["auto_check"] = False
    (data / "settings.json").write_text(json.dumps(s))
    assert updater.settings()["auto_check"] is False and updater.status()["auto"] is False


def test_apply_refuses_a_version_that_is_not_staged(env):
    origin, app, first = env
    (origin / "backend" / "app.py").write_text("V = 3\n")
    git(origin, "commit", "-q", "-am", "third")
    st = updater.check()
    with pytest.raises(updater.UpdateError):
        updater.apply(st["latest"])                                  # never built or tested: not installed


def test_installing_by_itself_is_opt_in(env):
    # checking for updates is on by default; installing without asking is off until the user turns it on
    data = Path(os.environ["LWS_DATA_DIR"])
    assert updater.settings()["auto_check"] is True and updater.settings()["auto_install"] is False
    assert updater.status()["auto_install"] is False
    s = json.loads((data / "settings.json").read_text())
    for value, expect in (("yes", False), (1, False), (True, True)):    # only a real true turns it on
        s["update"]["auto_install"] = value
        (data / "settings.json").write_text(json.dumps(s))
        assert updater.settings()["auto_install"] is expect


def test_an_installed_build_offers_the_newer_installer(env, monkeypatch):
    # a PyInstaller build can't run copied source files: it compares release versions and offers the download
    import io
    import urllib.request
    monkeypatch.setattr(updater.paths, "FROZEN", True)
    monkeypatch.setattr(updater, "own_version", lambda: "0.1.0-alpha.4")
    releases = [
        {"tag_name": "v0.1.0-alpha.10", "name": "0.1.0-alpha.10", "draft": False, "published_at": "2026-11-01T00:00:00Z",
         "html_url": "https://example/rel/10", "body": "notes",
         "assets": [{"name": "ZenHarness-Setup-0.1.0-alpha.10.exe", "size": 300 << 20, "browser_download_url": "https://example/10.exe"}]},
        {"tag_name": "v0.1.0-alpha.3", "name": "old", "draft": False, "published_at": "2026-10-12T00:00:00Z",
         "assets": [{"name": "ZenHarness-Setup-0.1.0-alpha.3.exe", "size": 1, "browser_download_url": "x"}]},
        {"tag_name": "v0.2.0", "name": "draft", "draft": True, "assets": []}]
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(json.dumps(releases).encode()))
    st = updater.check()
    assert st["available"] and st["installer"] and st["latest_short"] == "0.1.0-alpha.10"      # alpha.10 > alpha.4
    assert st["release"]["url"] == "https://example/10.exe" and not st["can_rollback"] and not st["auto_install"]
    monkeypatch.setattr(updater, "own_version", lambda: "0.1.0-alpha.10")
    assert not updater.check()["available"]
