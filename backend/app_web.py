"""Desktop window for the web UI: a launch splash at once, the agent server (web_backend) in its own process, the UI in
a Qt WebEngine view shown as soon as it has loaded.

  pythonw app_web.py           (or double-click "Local Agent.bat")
  python app_web.py --browser  serve and open in your normal browser instead
"""
import json
import os
import socket
import sys
import threading
import time
import webbrowser

import paths

# The window must not import web_backend (the whole agent: ~3 s): the server runs in its own process, and the
# window only needs its port. Same rule as web_backend: LWS_PORT, else Settings → Headless, else 8765.
def _port():
    if os.environ.get("LWS_PORT"):
        return int(os.environ["LWS_PORT"])
    try:
        with open(os.path.join(paths.data_dir(), "settings.json"), encoding="utf-8") as f:
            return int((json.load(f).get("headless") or {}).get("port") or 8765)
    except Exception:
        return 8765


PORT = _port()
URL = f"http://127.0.0.1:{PORT}"
DIST = os.environ.get("LWS_DIST") or paths.resource("web_dist")


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _stale_server_replaced():
    """A server from an older launch still holds the port. If it runs older code and is idle, ask it to
    exit so this launch starts fresh. -> True if the port is free now."""
    import json
    import time
    import urllib.request
    try:
        b = json.load(urllib.request.urlopen(URL + "/api/build", timeout=5))
    except Exception:
        return False                          # something else (or an older build without /api/build)
    if b.get("started", 0) >= b.get("code_mtime", 0) or b.get("busy"):
        return False                          # up to date, or busy: just attach to it
    try:
        req = urllib.request.Request(URL + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        return False
    for _ in range(40):
        if not port_in_use(PORT):
            return True
        time.sleep(0.25)
    return False


HERE = os.path.dirname(os.path.abspath(__file__))


def _crash_log(name):
    """Native crashes (Qt, lxml…) write the Python stack here instead of vanishing (pythonw has no console)."""
    import faulthandler
    try:
        import paths
        f = open(os.path.join(paths.logs_dir(), name), "a", buffering=1, encoding="utf-8")
        import time
        f.write(f"\n=== started {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} ===\n")
        faulthandler.enable(f, all_threads=True)
        if sys.stdout is None:                # pythonw: keep prints too
            sys.stdout = sys.stderr = f
        return f
    except Exception:
        return None


def start_backend():
    """Start the server in its own process (a crash of this window must not take agent runs and model servers
    down with it). -> True if this window started it (False: attached to a running server)."""
    if port_in_use(PORT) and not _stale_server_replaced():
        return False                          # an up-to-date (or busy) server is running: attach to it
    import subprocess
    import time
    flags = 0x00000008 | 0x00000200 | 0x08000000          # detached, own process group, no window
    import paths
    cmd = paths.role_command("server")
    try:
        subprocess.Popen(cmd, cwd=HERE, creationflags=flags | 0x01000000)   # + break away from our job, if any
    except OSError:
        subprocess.Popen(cmd, cwd=HERE, creationflags=flags)
    for _ in range(240):                       # first start loads models' metadata, embeddings…
        if port_in_use(PORT):
            return True
        time.sleep(0.25)
    return True


def _start_note_poller(emit):
    """Tray notifications from the server process: poll /api/notes (cheap, localhost)."""
    import json
    import time
    import urllib.request

    def run():
        seq = None
        while True:
            try:
                d = json.load(urllib.request.urlopen(f"{URL}/api/notes?since={seq or 0}", timeout=5))
                if seq is not None:
                    for n in d.get("notes", []):
                        emit(n.get("title", ""), n.get("body", ""), n.get("conv", ""))
                seq = d.get("seq", seq or 0)
            except Exception:
                pass
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()


def _allow_microphone(view):
    """Voice input: grant the app's own page microphone access (Qt asks the embedding app, not the user)."""
    page = view.page()
    try:                                              # Qt 6.8+: QWebEnginePermission
        from PySide6.QtWebEngineCore import QWebEnginePermission

        def on_perm(perm):
            if perm.origin().host() in ("127.0.0.1", "localhost") and perm.permissionType() in (
                    QWebEnginePermission.PermissionType.MediaAudioCapture,
                    QWebEnginePermission.PermissionType.Notifications):
                perm.grant()
            else:
                perm.deny()
        page.permissionRequested.connect(on_perm)
        return
    except (ImportError, AttributeError):
        pass
    from PySide6.QtWebEngineCore import QWebEnginePage

    def on_feature(url, feature):                    # older Qt
        ok = url.host() in ("127.0.0.1", "localhost") and feature in (
            QWebEnginePage.Feature.MediaAudioCapture, QWebEnginePage.Feature.Notifications)
        page.setFeaturePermission(url, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser if ok
                                  else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
    page.featurePermissionRequested.connect(on_feature)


_WINDOW_BOOT_JS = """
new QWebChannel(qt.webChannelTransport, function (ch) {
  window.zenWindow = ch.objects.zenWindow;
  if (ch.objects.zenBrowser) window.zenBrowser = ch.objects.zenBrowser;
  window.dispatchEvent(new Event("zen-window-ready"));
});
"""


def _custom_frame(view, agent_browser=None):
    """Frameless window: the web UI draws its own title bar in the Zen theme (src/components/WindowFrame.tsx) and
    drives the window through QWebChannel as `window.zenWindow`. Moving and resizing go through the OS
    (startSystemMove / startSystemResize), so Windows snap and Win+arrow keys keep working."""
    from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt, Signal, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript

    view.setWindowFlags(view.windowFlags() | Qt.WindowType.FramelessWindowHint)
    view.setMinimumSize(720, 480)
    edges = {"left": Qt.Edge.LeftEdge, "right": Qt.Edge.RightEdge, "top": Qt.Edge.TopEdge,
             "bottom": Qt.Edge.BottomEdge}

    class WindowBridge(QObject):
        stateChanged = Signal(str)                    # "maximized" | "normal" | "minimized"

        def _state(self):
            return "minimized" if view.isMinimized() else "maximized" if view.isMaximized() else "normal"

        @Slot(result=str)
        def state(self):
            return self._state()

        @Slot()
        def startMove(self):
            h = view.windowHandle()
            if h:
                h.startSystemMove()

        @Slot(str)
        def startResize(self, which):                 # "left", "top,right", ...
            h = view.windowHandle()
            flags = None
            for w in which.split(","):
                e = edges.get(w.strip())
                if e is not None:
                    flags = e if flags is None else flags | e
            if h and flags is not None and not view.isMaximized():
                h.startSystemResize(flags)

        @Slot()
        def minimize(self):
            view.showMinimized()

        @Slot()
        def toggleMaximize(self):
            view.showNormal() if view.isMaximized() else view.showMaximized()

        @Slot()
        def close(self):
            view.close()

    class StateWatch(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.WindowStateChange:
                bridge.stateChanged.emit(bridge._state())
            elif ev.type() == QEvent.Type.WindowActivate:
                # back in front (e.g. after another app was full-screen over it): make the web view draw again in
                # case Chromium dropped its last frame while it was covered
                target = view.focusProxy() or view
                target.update()
            return False

    class BrowserBridge(QObject):
        """The UI reports where its browser panel is; the native agent browser view is placed there."""

        @Slot(float, float, float, float, bool)
        def setRect(self, x, y, w, h, visible):
            if agent_browser is not None:
                agent_browser.set_rect(x, y, w, h, visible)

        @Slot()
        def openDevTools(self):
            if agent_browser is not None:
                agent_browser.open_devtools()

        @Slot(float, float, float, float, bool)
        def setDevToolsRect(self, x, y, w, h, visible):
            if agent_browser is not None:
                agent_browser.set_dev_rect(x, y, w, h, visible)

        @Slot()
        def popOut(self):
            if agent_browser is not None:
                agent_browser.pop_out()

        @Slot()
        def popIn(self):
            if agent_browser is not None:
                agent_browser.pop_in()

        @Slot(result=bool)
        def isPoppedOut(self):
            return agent_browser is not None and agent_browser.popout is not None

    bridge = WindowBridge(view)
    browser_bridge = BrowserBridge(view) if agent_browser is not None else None
    watch = StateWatch(view)
    view.installEventFilter(watch)
    channel = QWebChannel(view.page())
    channel.registerObject("zenWindow", bridge)
    if browser_bridge is not None:
        channel.registerObject("zenBrowser", browser_bridge)
    view.page().setWebChannel(channel)
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        script = QWebEngineScript()
        script.setName("zen-window")
        script.setSourceCode(bytes(f.readAll()).decode("utf-8") + _WINDOW_BOOT_JS)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        view.page().scripts().insert(script)
        f.close()
    view._zen_frame = (bridge, watch, channel, browser_bridge)   # keep references alive


def _external_links(view):
    """Links in the chat (sources, citations, any web link) open in the default browser. The UI marks them
    target=_blank, which a Qt view ignores unless it creates a window — so it "creates" one that only hands the
    address to the system and closes. A plain link that would navigate the app itself away goes there too."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWebEngineCore import QWebEnginePage

    def external(url):
        return url.scheme() in ("http", "https", "mailto") and not (
            url.host() in ("127.0.0.1", "localhost") and url.port() == PORT)

    class Handoff(QWebEnginePage):                # the "new window": its first navigation goes to the system
        def acceptNavigationRequest(self, url, kind, main):
            if url.scheme() in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            self.deleteLater()
            return False

    class UiPage(QWebEnginePage):
        def createWindow(self, kind):
            p = Handoff(self.profile(), self)
            self._handoffs = getattr(self, "_handoffs", []) + [p]
            return p

        def acceptNavigationRequest(self, url, kind, main):
            if main and kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked and external(url):
                QDesktopServices.openUrl(url)
                return False
            return super().acceptNavigationRequest(url, kind, main)

    view.setPage(UiPage(view))
    _ = QUrl


def _round_corners(view):
    """Windows 11: rounded corners and the system shadow for the frameless window (no-op elsewhere)."""
    try:
        import ctypes
        hwnd = int(view.winId())
        pref = ctypes.c_int(2)                        # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def _install_notifier(app, view):
    """Tray notifications for finished runs / approvals, only while the window is not in front."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QSystemTrayIcon
    tray = QSystemTrayIcon(app.windowIcon() or view.windowIcon(), app)
    tray.setToolTip("Zen")
    tray.show()
    state = {"conv": None}

    def bring_up():
        view.showNormal()
        view.raise_()
        view.activateWindow()
        if state["conv"]:
            view.page().runJavaScript(f"window.__lwsSelect && window.__lwsSelect({state['conv']!r})")

    tray.messageClicked.connect(bring_up)
    tray.activated.connect(lambda reason: bring_up() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    class Bridge(QObject):
        show = Signal(str, str, str)

    bridge = Bridge()

    def on_show(title, body, conv):
        if view.isActiveWindow() and not view.isMinimized():
            return                                        # the user is looking at the app already
        state["conv"] = conv or None
        tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8000)

    bridge.show.connect(on_show)                          # queued: called from the poller thread
    _start_note_poller(lambda t, b, c="": bridge.show.emit(t, b, c or ""))
    app._lws_tray = (tray, bridge)                        # keep references alive


def main():
    if "--server" in sys.argv:                # the server process the window starts
        _crash_log("app_server.log")
        import web_backend
        web_backend.serve_headless()
        return
    if "--browser" in sys.argv:               # serve in this process and open the normal browser
        import web_backend
        if not port_in_use(PORT) or _stale_server_replaced():
            threading.Thread(target=web_backend.serve, daemon=True).start()
        webbrowser.open(URL)
        threading.Event().wait()              # keep serving until Ctrl+C
        return
    _crash_log("app_window.log")
    t0 = time.perf_counter()
    trace = (lambda what: print(f"[startup] {time.perf_counter() - t0:6.2f}s {what}", flush=True))         if os.environ.get("ZEN_STARTUP_TRACE") else (lambda what: None)
    # the agent's browser lives in this window (qt_browser.py); Qt's DevTools port must be set before Qt starts.
    # LWS_NATIVE_BROWSER=0 keeps the old separate Edge.
    native = os.environ.get("LWS_NATIVE_BROWSER", "1") != "0"
    if native:
        import qt_browser
        devtools_port = qt_browser.free_port()
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{devtools_port}"
    # Chromium on Windows stops drawing a window it thinks is hidden (another app full-screen in front of it) and did
    # not always start again on switching back: the whole window stayed black until a refresh. Must be set before Qt
    # starts its WebEngine.
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    for f in ("--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows"):
        if f not in flags:
            flags = (flags + " " + f).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    try:                                      # own taskbar entry/icon instead of python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZenHarness.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Zen")
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    ico = next((p for p in (os.path.join(assets, "zen_harness.ico"), os.path.join(assets, "power_harness.ico"))
                if os.path.exists(p)), "")
    if ico:
        app.setWindowIcon(QIcon(ico))

    # the splash first, before anything slow: it paints within half a second of the click (splash.py)
    import splash as zen_splash
    sp = zen_splash.Splash(os.path.join(assets, "zen_harness.png"), os.path.join(DIST, "favicon.svg"))
    sp.show()
    app.processEvents()
    trace("splash shown")

    # the agent server starts in parallel (its own process; a few seconds the first time)
    boot = {"up": False}

    def start_server():
        start_backend()
        boot["up"] = True
    threading.Thread(target=start_server, daemon=True).start()

    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    view = QWebEngineView()
    _external_links(view)                     # before anything else uses view.page()
    s = view.settings()
    for attr in ("PluginsEnabled", "PdfViewerEnabled", "LocalStorageEnabled", "JavascriptCanAccessClipboard"):
        if hasattr(QWebEngineSettings.WebAttribute, attr):
            s.setAttribute(getattr(QWebEngineSettings.WebAttribute, attr), True)
    if hasattr(QWebEngineSettings.WebAttribute, "PlaybackRequiresUserGesture"):   # read-aloud starts on its own
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    view.setWindowTitle("Zen")
    icon = os.path.join(DIST, "favicon.svg")
    if os.path.exists(icon):
        view.setWindowIcon(QIcon(icon))
    view.resize(1400, 900)
    _allow_microphone(view)
    agent_browser = None
    if native:
        try:
            agent_browser = qt_browser.AgentBrowser(view, paths.data_dir())
            qt_browser.register_with_server(URL, qt_browser.start_relay(agent_browser, devtools_port))   # retries
        except Exception as e:                # the agent falls back to Edge
            print("native agent browser failed:", repr(e))
            agent_browser = None
    _custom_frame(view, agent_browser)
    sp.milestone("window")
    trace("window built")

    # server listening -> load the UI; UI loaded -> wait until it has its settings and chats -> show it, splash out
    phase = {"loading": False, "shown": False}

    def show_app():
        if phase["shown"]:
            return
        phase["shown"] = True
        sp.milestone("ready")
        trace("app ready")
        view.show()
        _round_corners(view)
        view.raise_()
        view.activateWindow()
        sp.save_times()
        QTimer.singleShot(60, sp.finish)
        poll.stop()

    def on_ready(ok):
        if ok:
            show_app()

    def tick():
        if not phase["loading"] and boot["up"]:
            phase["loading"] = True
            sp.milestone("server")
            trace("server listening")
            view.load(QUrl(URL))
        elif phase["loading"] and not phase["shown"]:
            # the UI sets data-zen-ready once it has its settings and chats (an older UI: its main view has rendered)
            view.page().runJavaScript("document.documentElement.dataset.zenReady === '1' || !!document.querySelector('main')",
                                      0, on_ready)
        if time.perf_counter() - t0 > 90:     # never hide the app behind a splash: show whatever there is
            show_app()

    def on_loaded(ok):
        sp.milestone("ui")
        trace(f"interface loaded ({'ok' if ok else 'failed'})")
        if not ok:
            show_app()                        # let the page's own error/retry show

    view.loadFinished.connect(on_loaded)
    poll = QTimer()
    poll.timeout.connect(tick)
    poll.start(100)
    _install_notifier(app, view)
    code = app.exec()
    if agent_browser is not None:
        qt_browser.unregister(URL)
    try:                                      # window closed: stop the server (runs, browser, llama.cpp) like
        import urllib.request                 # before — unless another window is still connected to it
        req = urllib.request.Request(URL + "/api/shutdown", data=json.dumps({"force": True, "if_last": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
    finally:
        os._exit(code)


if __name__ == "__main__":
    main()
