"""Tray menu of the headless service (headless.Service): status line, Pause/Resume, Copy token, Open logs,
Open UI and Quit, driven through the real QActions with a stub engine on a spare port and a throwaway data dir.
Shows a tray icon for a few seconds. Standalone: `venv\\Scripts\\python.exe tests/scripts/headless_tray.py`.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # backend/
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
TMP = tempfile.mkdtemp(prefix="zen_tray_")
PORT = 8777
os.environ.update({"LWS_DATA_DIR": TMP, "LWS_PORT": str(PORT), "LWS_NO_TELEGRAM": "1", "LWS_NO_SCHEDULER": "1",
                   "LWS_NO_INDEXER": "1", "LWS_STARTUP_DIR": os.path.join(TMP, "Startup")})
with open(os.path.join(TMP, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"agent": {"backend": "llama", "model": "stub"}, "ui": {"notifications": False}}, f)

import agentic  # noqa: E402


class Stub:
    def __init__(self, cfg, emit, **k):
        self.cfg, self.emit, self.reg = cfg, emit, None
        self.ctx = type("Ctx", (), {"emit": emit})()
        self.should_stop = lambda: False

    def prepare(self):
        self.reg = object()

    def import_state(self, st):
        pass

    def export_state(self):
        return {}

    def close(self):
        pass

    def run(self, text, images=None):
        for _ in range(30):
            if self.should_stop():
                break
            time.sleep(0.1)
        self.emit("answer_end", text="ok")


agentic.AgentEngine = Stub
import headless  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   -> {detail}"), flush=True)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    import web_backend as wb
    wb.RESIDENT = True
    svc = headless.Service(app, PORT)
    threading.Thread(target=headless.run_server, args=(svc, PORT), daemon=True).start()
    menu_texts = [a.text() for a in svc.menu.actions() if a.text()]
    check("menu has every item", menu_texts[1:] == ["Open Zen Harness", "Pause API runs", "Copy API token",
                                                    "Open logs", "Quit Zen Headless Harness"], menu_texts)

    def pump(secs, until=lambda: False):
        end = time.time() + secs
        while time.time() < end and not until():
            app.processEvents()
            time.sleep(0.05)

    pump(30, lambda: svc.state == "serving")
    pump(2.2)
    check("status line: idle with the port", svc.a_status.text() == f"Idle · localhost:{PORT}", svc.a_status.text())
    check("tooltip mirrors the status", "Idle" in svc.tray.toolTip(), svc.tray.toolTip())
    import harness_api as api
    check("health says resident", json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/health"))["resident"])

    svc.a_pause.trigger()
    check("Pause pauses the API queue", api.STATE["paused"] and svc.a_pause.text() == "Resume API runs")
    r = api.submit("hello")
    svc.refresh()
    check("status shows paused + queued", "1 queued" in svc.a_status.text() and "API paused" in svc.a_status.text(),
          svc.a_status.text())
    svc.a_pause.trigger()
    pump(3, lambda: r.status == "running")
    svc.refresh()
    check("Resume starts the queued run; status shows it running",
          not api.STATE["paused"] and svc.a_status.text().startswith("Running: hello"), svc.a_status.text())
    pump(8, lambda: r.finished)
    check("the run finishes", r.status == "done", r.status)

    svc.a_token.trigger()
    check("Copy API token puts the token on the clipboard", app.clipboard().text() == api.ensure_token())

    with mock.patch("os.startfile") as sf:
        svc.a_logs.trigger()
    check("Open logs opens headless_service.log", sf.call_args and sf.call_args[0][0] == headless.log_path(), sf.call_args)

    with mock.patch("subprocess.Popen") as po:
        svc.a_open.trigger()
    args = po.call_args
    check("Open Zen Harness launches the window on this port",
          args and args[0][0][-1].endswith("app_web.py") and args[1]["env"]["LWS_PORT"] == str(PORT), args)

    calls = []
    with mock.patch.object(wb, "HOST_stop_all", lambda: calls.append("stop")), \
            mock.patch.object(wb.STORE, "save_convs", lambda: calls.append("save")), \
            mock.patch("os._exit", lambda code: calls.append(("exit", code))):
        svc.a_quit.trigger()
        pump(3, lambda: len(calls) >= 3)
    check("Quit stops runs, saves and exits", calls == ["stop", "save", ("exit", 0)], calls)
    svc.tray.hide()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        os._exit(code)
