"""UI errors reach a log file (the window used to go blank with no trace of why)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def call(wb, body):
    resp = asyncio.run(wb.api_ui_error(FakeRequest(body)))
    return resp.status, json.loads(resp.text)


def test_ui_errors_are_logged_once_a_minute_and_rotate(tmp_path, monkeypatch):
    monkeypatch.setenv("LWS_DATA_DIR", str(tmp_path))
    import web_backend as wb
    monkeypatch.setattr(wb.paths, "data", lambda *p: str(tmp_path.joinpath(*p)))
    wb._ui_errors_seen.clear()
    err = {"kind": "render", "message": "Cannot read properties of undefined (reading 'map')",
           "stack": "TypeError: …\n    at Transcript (index.js:1:2)", "component_stack": "\n    in Transcript",
           "url": "/#c1"}
    assert call(wb, err) == (200, {"ok": True})
    log = (tmp_path / "ui_errors.log").read_text(encoding="utf-8")
    assert "render" in log and "reading 'map'" in log and "in Transcript" in log
    assert call(wb, err)[1].get("repeat") is True               # a render loop: once a minute, not 1000 lines
    assert (tmp_path / "ui_errors.log").read_text(encoding="utf-8") == log
    assert call(wb, ValueError("not json"))[0] == 400
    monkeypatch.setattr(wb, "UI_ERROR_LOG_MAX", 10)
    call(wb, dict(err, message="another error"))
    assert (tmp_path / "ui_errors.log.1").exists()               # rotated past the size limit
