"""Zen Headless: Zen as a resident background service: the backend (UI, /api, /v1 REST, /mcp) with a tray icon and no
window. The desktop window attaches to it; closing the window leaves it running. Quit from the tray.

  pythonw headless.py          (the Startup shortcut runs this; see autostart.py)
  python headless.py           the same, with a console

Env: LWS_PORT / LWS_DATA_DIR as for the backend. See plans/headless-harness-prd.md and docs/headless-harness.md.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_NAME = "headless_service.log"
LOG_MAX = 5 * 1024 * 1024


def log_path():
    import paths
    return os.path.join(paths.logs_dir(), LOG_NAME)


def open_log():
    """stdout/stderr + native crash stacks go to headless_service.log (rotated at 5 MB, one backup)."""
    import faulthandler
    p = log_path()
    try:
        if os.path.exists(p) and os.path.getsize(p) > LOG_MAX:
            os.replace(p, p + ".1")
        f = open(p, "a", buffering=1, encoding="utf-8", errors="replace")
        f.write(f"\n=== headless service started {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} ===\n")
        faulthandler.enable(f, all_threads=True)
        if sys.stdout is None or not sys.stdout.isatty():
            sys.stdout = sys.stderr = f
        return f
    except Exception:
        return None


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _get(port, path, timeout=4):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def ask_window_server_to_exit(port):
    """A server started by the desktop window holds the port: ask it to exit (it refuses while a run is in
    progress). -> True once the port is free."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        return not port_in_use(port)
    for _ in range(40):
        if not port_in_use(port):
            return True
        time.sleep(0.25)
    return False


class Service:
    """The tray icon and its menu, driving the in-process backend."""

    def __init__(self, app, port):
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon
        import web_backend as wb
        self.app, self.port, self.wb = app, port, wb
        self.state = "starting"                       # starting | waiting | serving | stopping
        ico = next((p for p in (os.path.join(HERE, "assets", "zen_harness.ico"),
                                os.path.join(HERE, "assets", "power_harness.ico")) if os.path.exists(p)), "")
        self.tray = QSystemTrayIcon(QIcon(ico) if ico else app.windowIcon(), app)
        menu = QMenu()
        self.a_status = QAction("Starting…", menu)
        self.a_status.setEnabled(False)
        self.a_open = QAction("Open Zen", menu)
        self.a_pause = QAction("Pause API runs", menu)
        self.a_token = QAction("Copy API token", menu)
        self.a_logs = QAction("Open logs", menu)
        self.a_quit = QAction("Quit Zen Headless", menu)
        for a in (self.a_status, None, self.a_open, self.a_pause, self.a_token, self.a_logs, None, self.a_quit):
            menu.addSeparator() if a is None else menu.addAction(a)
        self.a_open.triggered.connect(self.open_ui)
        self.a_pause.triggered.connect(self.toggle_pause)
        self.a_token.triggered.connect(self.copy_token)
        self.a_logs.triggered.connect(self.open_logs)
        self.a_quit.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open_ui()
                                    if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.menu = menu

        class Bridge(QObject):
            note = Signal(str, str)
        self.bridge = Bridge()
        self.bridge.note.connect(lambda t, b: self.tray.showMessage(t, b, QSystemTrayIcon.MessageIcon.Information, 8000))
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.tray.show()
        self.refresh()

    # ---- status
    def status_text(self):
        if self.state == "waiting":
            return "Waiting for the app's server to finish its run…"
        if self.state != "serving":
            return {"starting": "Starting…", "stopping": "Stopping…"}.get(self.state, self.state)
        import harness_api as api
        st = api.status()
        parts = [("Running: " + (st["busy_title"] or "a task")[:40]) if st["busy"] else "Idle"]
        if st["queued"]:
            parts.append(f"{st['queued']} queued")
        if st["paused"]:
            parts.append("API paused")
        parts.append(f"{'LAN' if st['lan'] else 'localhost'}:{self.port}")
        return " · ".join(parts)

    def refresh(self):
        text = self.status_text()
        self.a_status.setText(text)
        self.tray.setToolTip(f"Zen Headless\n{text}"[:120])
        if self.state == "serving":
            import harness_api as api
            self.a_pause.setText("Resume API runs" if api.STATE["paused"] else "Pause API runs")
        for a in (self.a_pause, self.a_token):
            a.setEnabled(self.state == "serving")

    def notify(self, title, body, conv_id=None):
        """Finished runs / approvals: the attached window shows them if one is open, else the tray does."""
        self.wb.queue_note(title, body, conv_id)
        if not self.wb.BUS.clients:
            self.bridge.note.emit(title, body)

    # ---- menu actions
    def open_ui(self):
        import paths
        subprocess.Popen(paths.role_command("window"), cwd=paths.work_dir(),
                         env={**os.environ, "LWS_PORT": str(self.port)}, creationflags=0x00000008 | 0x00000200)

    def toggle_pause(self):
        import harness_api as api
        api.set_paused(not api.STATE["paused"])
        self.refresh()

    def copy_token(self):
        import harness_api as api
        self.app.clipboard().setText(api.ensure_token())
        self.tray.showMessage("Zen Headless", "API token copied to the clipboard.")

    def open_logs(self):
        os.startfile(log_path())

    def quit(self):
        self.state = "stopping"
        self.refresh()

        def bye():
            try:
                self.wb.HOST_stop_all()
                self.wb.STORE.save_convs()
            finally:
                os._exit(0)
        threading.Thread(target=bye, daemon=True).start()


def run_server(svc, port):
    """Server thread: take the port over from a window-started server if needed, then serve forever."""
    wb = svc.wb
    while port_in_use(port):
        h = _get(port, "/v1/health")
        if h and h.get("resident"):
            print(f"another resident service is already running on port {port}: exiting", flush=True)
            os._exit(0)
        svc.state = "waiting"
        if ask_window_server_to_exit(port):
            break
        time.sleep(10)
    wb.NOTIFIER = svc.notify

    def ready():
        svc.state = "serving"
        print(f"headless service ready on port {port}", flush=True)
    wb.serve(port, ready)


def main():
    open_log()
    if "--server-only" in sys.argv:                    # no tray (CI/tests): the backend as a resident service
        import web_backend as wb
        wb.RESIDENT = True
        wb.NOTIFIER = wb.queue_note
        wb.serve()
        return
    from PySide6.QtWidgets import QApplication
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZenHarness.Service")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Zen Headless")
    app.setQuitOnLastWindowClosed(False)
    import web_backend as wb
    wb.RESIDENT = True
    port = wb.PORT
    h = _get(port, "/v1/health") if port_in_use(port) else None
    if h and h.get("resident"):
        print(f"already running on port {port}", flush=True)
        return
    svc = Service(app, port)
    threading.Thread(target=run_server, args=(svc, port), daemon=True, name="server").start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
