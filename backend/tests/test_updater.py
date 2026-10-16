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


def test_apply_refuses_a_version_that_is_not_staged(env):
    origin, app, first = env
    (origin / "backend" / "app.py").write_text("V = 3\n")
    git(origin, "commit", "-q", "-am", "third")
    st = updater.check()
    with pytest.raises(updater.UpdateError):
        updater.apply(st["latest"])                                  # never built or tested: not installed
