"""Downloads ask first (downloads.py) and an update install reports its steps (updater.progress)."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import downloads  # noqa: E402
import updater  # noqa: E402


def _answer_when_asked(kind, accept):
    def run():
        for _ in range(200):
            for r in downloads.snapshot():
                if r["kind"] == kind and r["status"] == "asking":
                    downloads.answer(r["id"], accept)
                    return
            time.sleep(0.01)
    threading.Thread(target=run, daemon=True).start()


def test_accept_then_progress_then_done(tmp_path):
    _answer_when_asked("t-accept", True)
    ok, rid = downloads.ask("t-accept", "Test model", size=1000, timeout=5)
    assert ok and rid
    downloads.track(rid, str(tmp_path), 1000)
    (tmp_path / "part.bin").write_bytes(b"x" * 400)
    time.sleep(0.8)
    r = downloads.REQUESTS[rid]
    assert r["status"] == "downloading" and 300 <= r["done"] <= 1000 and r["total"] == 1000
    downloads.finish(rid, True)
    assert downloads.REQUESTS[rid]["status"] == "done" and downloads.REQUESTS[rid]["done"] == 1000


def test_decline_is_remembered_for_the_session():
    _answer_when_asked("t-decline", False)
    ok, _ = downloads.ask("t-decline", "Test model", timeout=5)
    assert not ok
    ok2, rid2 = downloads.ask("t-decline", "Test model", timeout=5)     # not asked again: declined at once
    assert not ok2 and rid2 is None


def test_unanswered_question_times_out_as_declined():
    ok, rid = downloads.ask("t-timeout", "Test model", timeout=0.05)
    assert not ok and downloads.REQUESTS[rid]["status"] == "declined"


def test_install_steps_skip_what_is_not_needed():
    updater.progress_begin()
    updater._step("download")
    updater._step("build")                 # no new UI packages: "deps" skipped, "download" done
    p = {s["key"]: s["state"] for s in updater.progress()["steps"]}
    assert p["download"] == "done" and p["deps"] == "skipped" and p["build"] == "running"
    updater.progress_end(True)
    p = {s["key"]: s["state"] for s in updater.progress()["steps"]}
    assert p["build"] == "done" and p["install"] == "skipped" and updater.progress()["current"] is None
