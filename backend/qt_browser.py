"""The agent's browser inside the app window: a native Qt WebEngine view (same Chromium as the UI), driven by the
server's Playwright over the Chrome DevTools protocol.

Before this, the agent drove a separate Microsoft Edge parked off-screen and the UI showed it as JPEG screenshots
polled 3x a second: a second browser (~500 MB, 8 processes) and constant redraws. Now there is one Chromium, the
user sees and uses the real page, and nothing is streamed.

Pieces (all in the window process, see app_web.py):
  AgentBrowser  - its own profile (cookies apart from the UI), the docked view, the agent's pages
  relay         - a localhost DevTools relay in front of Qt's remote-debugging port. Qt doesn't support a few
                  commands Playwright needs (new tabs, download settings), and the app's own UI page must never
                  be visible to the agent: the relay creates tabs in Qt, answers the rest, and passes through only
                  the agent profile's targets.
The server finds the relay through POST /api/browser/host (browser_agent.HOST); with no window it uses Edge.
"""
import asyncio
import concurrent.futures
import json
import os
import re
import socket
import threading

VIEW_W, VIEW_H = 1280, 800            # the page lays out at this width; the docked view is scaled to fit the panel

# DevTools in the app's look: dark theme (a DevTools preference; set once, then the frontend reloads) and the app's
# ink surfaces, lines, text and fonts as overrides of DevTools' own colour variables (they reach its shadow DOM).
DEVTOOLS_THEME_JS = r"""
(() => {
  try {
    if (window.InspectorFrontendHost && !sessionStorage.getItem('zen-theme')) {
      sessionStorage.setItem('zen-theme', '1');
      InspectorFrontendHost.setPreference('uiTheme', JSON.stringify('dark'));
      location.reload();
      return;
    }
  } catch (e) {}
  if (document.getElementById('zen-ink')) return;
  const st = document.createElement('style');
  st.id = 'zen-ink';
  st.textContent = `
    :root, :root.theme-with-dark-background, .theme-with-dark-background, html {
      --sys-color-cdt-base-container: #1e2026 !important;
      --sys-color-cdt-base: #1e2026 !important;
      --sys-color-base: #1e2026 !important;
      --sys-color-base-container: #23262d !important;
      --sys-color-base-container-elevated: #282b33 !important;
      --sys-color-surface: #1e2026 !important;
      --sys-color-surface1: #23262d !important;
      --sys-color-surface2: #262930 !important;
      --sys-color-surface3: #282b33 !important;
      --sys-color-surface4: #2c2f37 !important;
      --sys-color-surface5: #2f323a !important;
      --sys-color-header-container: #23262d !important;
      --sys-color-neutral-container: #282b33 !important;
      --sys-color-divider: rgba(255,255,255,0.08) !important;
      --sys-color-neutral-outline: rgba(255,255,255,0.13) !important;
      --sys-color-on-surface: #eceef2 !important;
      --sys-color-on-surface-subtle: #a3a7b3 !important;
      --sys-color-token-subtle: #8b909c !important;
      --sys-color-state-hover-on-subtle: rgba(255,255,255,0.05) !important;
      --sys-color-tonal-container: #2c3a52 !important;
      --sys-color-primary: #8fb4ec !important;
      color-scheme: dark !important;
      --sys-color-token-tag: #8fb4ec !important;
      --sys-color-token-attribute: #c3a6e6 !important;
      --sys-color-token-attribute-value: #e2bd85 !important;
      --sys-color-token-string: #e2bd85 !important;
      --sys-color-token-keyword: #c3a6e6 !important;
      --sys-color-token-number: #9fd3b0 !important;
      --sys-color-token-property: #9cc9e8 !important;
      --sys-color-token-comment: #7d828e !important;
      --sys-color-token-meta: #a3a7b3 !important;
      --sys-color-token-variable: #dfe3ea !important;
      --sys-color-token-subtle: #8b909c !important;
      --default-font-family: 'Segoe UI', sans-serif !important;
      --monospace-font-family: 'Cascadia Mono', Consolas, monospace !important;
      --source-code-font-family: 'Cascadia Mono', Consolas, monospace !important;
    }
    body { background: #1e2026 !important; }
  `;
  document.documentElement.appendChild(st);
})();
"""
PRIV = 1_900_000_000                  # ids of the relay's own commands (never used by Playwright)

# the popped-out browser window's toolbar, in the app's ink palette
POPOUT_QSS = """
QWidget { background: #1e2026; color: #e6e6e6; font-family: 'Segoe UI'; font-size: 9pt; }
#bar { border-bottom: 1px solid #2e3138; }
QToolButton { background: transparent; border: none; border-radius: 6px; padding: 4px 8px; color: #b4b6bc; }
QToolButton:hover { background: #2a2d34; color: #f2f2f2; }
QLineEdit { background: #262930; border: 1px solid #2e3138; border-radius: 6px; padding: 4px 8px;
            font-family: 'Cascadia Mono', Consolas, monospace; selection-background-color: #4a4f5a; }
QLineEdit:focus { border-color: #5a606c; }
QToolTip { background: #2a2d34; color: #e6e6e6; border: 1px solid #3a3e46; }
"""


def _address(text):
    """What the user typed in the address bar -> a URL (an address, or a web search for anything else)."""
    from urllib.parse import quote_plus
    t = text.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I) or t.startswith("about:"):
        return t
    if " " not in t and re.match(r"^(localhost|[\w-]+(\.[\w-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d+)?(/.*)?$", t):
        return ("http://" if t.startswith(("localhost", "127.", "192.168.", "10.")) else "https://") + t
    return "https://www.bing.com/search?q=" + quote_plus(t)


def _dark_title_bar(win):
    """Windows 10/11: a dark native title bar to match the app."""
    try:
        import ctypes
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(win.winId()), 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgentBrowser:
    """Lives on the Qt main thread. Other threads call it through .run(fn) (queued to the main thread)."""

    def __init__(self, host_view, data_dir):
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.host = host_view
        self.ids = set()                  # devtools target ids of the agent's pages (read by the relay thread)
        self.pages = []                   # the agent's pages (looked up by their current devtools id)
        self.rect = None                  # last panel rect from the UI (None = panel not showing)
        self.dev = None                   # the DevTools window (Chrome's own DevTools for the shown agent page)
        self.popout = None                # the browser's own window while popped out of the app (None = docked)
        self._closing_popout = False
        prof = QWebEngineProfile("zen-agent-browser", host_view)
        path = os.path.join(data_dir, "agent_browser_qt")
        os.makedirs(path, exist_ok=True)
        self._popout_file = os.path.join(path, "popout.json")
        prof.setPersistentStoragePath(path)
        prof.setCachePath(os.path.join(path, "cache"))
        prof.setHttpCacheMaximumSize(200 * 1024 * 1024)
        # a plain Chrome user agent: sites treat the "QtWebEngine/x" token as an unknown, sometimes blocked, browser
        prof.setHttpUserAgent(re.sub(r"QtWebEngine/\S+\s*", "", prof.httpUserAgent()))
        try:
            prof.downloadRequested.connect(lambda item: item.cancel())   # no silent downloads (as with Edge)
        except Exception:
            pass
        s = prof.settings()
        for attr in ("JavascriptCanOpenWindows", "LocalStorageEnabled", "PluginsEnabled", "PdfViewerEnabled",
                     "FullScreenSupportEnabled", "ScrollAnimatorEnabled"):
            if hasattr(QWebEngineSettings.WebAttribute, attr):
                s.setAttribute(getattr(QWebEngineSettings.WebAttribute, attr), attr != "FullScreenSupportEnabled")
        self.profile = prof
        outer = self

        class Page(QWebEnginePage):
            def createWindow(self, kind):                  # popups / target=_blank: a new agent tab, shown
                p = outer._new_page()
                outer.show(p.devToolsId())
                return p

            def javaScriptAlert(self, origin, msg):        # never block the window with a modal dialog
                pass

            def javaScriptConfirm(self, origin, msg):
                return False

            def javaScriptPrompt(self, origin, msg, default):
                return False, ""

        self._Page = Page

        class Bridge(QObject):
            call = Signal(object)

        self._bridge = Bridge()
        self._bridge.call.connect(self._on_call)
        self.view = QWebEngineView(host_view)              # a child of the main window, placed over the panel
        self.view.setPage(self._new_page())
        self.view.page().setUrl("about:blank")
        self._park()
        self.view.show()
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtCore import Qt
        sc = QShortcut(QKeySequence("F12"), self.view)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self.open_devtools)
        self._f12 = sc
        from PySide6.QtCore import QEvent, QObject

        class HostWatch(QObject):                          # the app closing closes a popped-out browser too
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Close:
                    outer._host_closing()
                return False
        self._host_watch = HostWatch(host_view)
        host_view.installEventFilter(self._host_watch)

    # ------------------------------------------------------------------ pages (main thread)
    def _new_page(self, url=None):
        from PySide6.QtWebEngineCore import QWebEnginePage
        p = self._Page(self.profile, self.profile)
        self.ids.add(p.devToolsId())
        self.pages.append(p)
        p.windowCloseRequested.connect(lambda: self.close(p.devToolsId()))
        try:                                               # Qt 6.8+: mouse lock (games, 3D views) is allowed —
            from PySide6.QtWebEngineCore import QWebEnginePermission   # Esc always releases it; camera, location,
            lock = QWebEnginePermission.PermissionType.MouseLock          # notifications… are denied
            p.permissionRequested.connect(lambda perm: perm.grant() if perm.permissionType() == lock else perm.deny())
        except Exception:
            pass
        p.fullScreenRequested.connect(lambda req: req.reject())
        if url:
            from PySide6.QtCore import QUrl
            p.setUrl(QUrl(url))
        _ = QWebEnginePage
        return p

    def create(self, url=None):
        """A new background tab (not shown) -> its target id."""
        return self._new_page(url or "about:blank").devToolsId()

    def _find(self, pid):
        for p in self.pages:
            if p.devToolsId() == pid:
                self.ids.add(pid)                          # a popup's id can change when Qt adopts its contents
                return p
        return None

    def close(self, pid):
        p = self._find(pid)
        if p is None:
            return False
        self.pages.remove(p)
        self.ids.discard(pid)
        if self.view.page() is p:                          # the shown tab closed: show another (or a blank one)
            self.view.setPage(self.pages[-1] if self.pages else self._new_page("about:blank"))
            self._follow_devtools()
        p.deleteLater()
        return True

    def show(self, pid):
        p = self._find(pid)
        if p is not None and self.view.page() is not p:
            self.view.setPage(p)
            self._follow_devtools()
            self._apply()
        return p is not None

    # ------------------------------------------------------------------ DevTools window (main thread)
    def open_devtools(self):
        """Chrome's DevTools for the shown agent page, docked in the app as the "DevTools" panel (button in the
        browser panel, or F12). Its page lives in a throwaway profile, so the relay never shows it to the agent."""
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
        if self.dev is None:
            self.dev = QWebEngineView(self.host)                       # a child of the window, like the browser
            self._dev_profile = QWebEngineProfile(self.dev)            # off the record
            page = QWebEnginePage(self._dev_profile, self.dev)
            page.setBackgroundColor(self._ink())
            page.loadFinished.connect(lambda ok: self._theme_devtools())
            page.windowCloseRequested.connect(self._devtools_closed)          # DevTools' own close button
            self.dev.setPage(page)
            self.dev.hide()
        self.view.page().setDevToolsPage(self.dev.page())
        self.dev_rect = getattr(self, "dev_rect", None)
        # ask the UI to show (or create) its DevTools panel; the panel reports where it is via set_dev_rect
        self.host.page().runJavaScript("window.__zenShowPanel && window.__zenShowPanel('devtools')")

    def _devtools_closed(self):
        self.dev.hide()
        self.host.page().runJavaScript("window.__zenHidePanel && window.__zenHidePanel('devtools')")
        for p in self.pages:                                             # detach; the next open re-attaches
            if p.devToolsPage() is self.dev.page():
                p.setDevToolsPage(None)

    def _follow_devtools(self):
        if self.dev is not None:
            self.view.page().setDevToolsPage(self.dev.page())

    def set_dev_rect(self, x, y, w, h, visible):
        if self.dev is None:
            if not visible:
                return
            self.open_devtools()
        z = self.host.zoomFactor() or 1.0
        if visible and w > 40 and h > 40:
            self.dev.setGeometry(int(x * z), int(y * z), int(w * z), int(h * z))
            self.dev.show()
            self.dev.raise_()
        else:
            self.dev.hide()

    @staticmethod
    def _ink():
        from PySide6.QtGui import QColor
        return QColor(30, 32, 38)                                        # the app's surface-0 (ink theme)

    def _theme_devtools(self):
        """Dark DevTools in the app's ink palette and fonts, instead of the default light chrome."""
        self.dev.page().runJavaScript(DEVTOOLS_THEME_JS)

    # ------------------------------------------------------------------ placement (main thread)
    def set_rect(self, x, y, w, h, visible):
        """From the UI: where the browser panel is (CSS px of the app page), or that it isn't showing."""
        z = self.host.zoomFactor() or 1.0
        self.rect = (int(x * z), int(y * z), int(w * z), int(h * z)) if visible and w > 40 and h > 40 else None
        self._apply()

    def _apply(self):
        if self.popout is not None:                         # in its own window: the panel no longer places it
            return
        if self.rect is None:
            self._park()
            return
        x, y, w, h = self.rect
        self.view.setGeometry(x, y, w, h)
        self.view.setZoomFactor(max(0.25, min(1.0, w / VIEW_W)))   # keep a desktop-width layout, scaled to fit
        self.view.raise_()

    def _park(self):
        """Panel closed: keep the page rendering at desktop size (the agent still browses and takes screenshots),
        under the app's own page so it can't be seen."""
        self.view.setZoomFactor(1.0)
        self.view.setGeometry(0, 0, VIEW_W, VIEW_H)
        self.view.lower()

    # ------------------------------------------------------------------ pop out / back in (main thread)
    def pop_out(self):
        """Move the page into its own window (a toolbar + the same view: tabs, session and the agent's control all
        carry on). Closing that window, or "Back in app", docks it again."""
        if self.popout is not None:
            self.popout.showNormal()
            self.popout.raise_()
            self.popout.activateWindow()
            return True
        from PySide6.QtCore import QEvent, QObject, QUrl
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget
        outer = self
        win = QWidget(None)
        win.setWindowTitle("Zen Harness — Browser")
        win.setWindowIcon(self.host.windowIcon())
        win.setStyleSheet(POPOUT_QSS)
        bar = QWidget(win)
        bar.setObjectName("bar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(4)

        def button(text, tip, fn):
            b = QToolButton(bar)
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
            return b

        button("←", "Back", lambda: self.view.back())
        button("→", "Forward", lambda: self.view.forward())
        button("⟳", "Reload", lambda: self.view.reload())
        addr = QLineEdit(bar)
        addr.setPlaceholderText("Search or enter an address")

        def go():
            t = addr.text().strip()
            if t:
                self.view.setUrl(QUrl(_address(t)))
        addr.returnPressed.connect(go)
        row.addWidget(addr, 1)
        button("DevTools", "DevTools for this page (F12) — opens in the app", self.open_devtools)
        button("Back in app", "Dock the browser in the app window again", self.pop_in)
        on_url = lambda u: None if addr.hasFocus() else addr.setText(u.toString() if u.toString() != "about:blank" else "")
        self.view.urlChanged.connect(on_url)
        on_url(self.view.url())
        col = QVBoxLayout(win)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(bar)
        self.view.setParent(win)
        col.addWidget(self.view, 1)

        class Watch(QObject):                              # resize: keep the desktop layout; close: dock again
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Resize:
                    outer.view.setZoomFactor(max(0.25, min(1.0, outer.view.width() / VIEW_W)))
                elif ev.type() == QEvent.Type.Close and obj is win and not outer._closing_popout:
                    ev.ignore()
                    outer.pop_in()
                    return True
                return False
        self._popout_watch = Watch(win)
        win.installEventFilter(self._popout_watch)
        self.view.installEventFilter(self._popout_watch)
        self._popout_url = on_url
        self.popout = win
        g = self._popout_geometry()
        win.setGeometry(*g) if g else win.resize(VIEW_W, VIEW_H + 44)
        win.show()
        _dark_title_bar(win)
        self.view.show()
        win.activateWindow()
        self._tell_ui(True)
        return True

    def pop_in(self):
        """Dock the page in the app's browser panel again."""
        win = self.popout
        if win is None:
            return True
        self._save_popout_geometry(win)
        try:
            self.view.urlChanged.disconnect(self._popout_url)
        except Exception:
            pass
        self.view.removeEventFilter(self._popout_watch)
        self.popout = None
        self.view.setParent(self.host)
        self.view.show()
        self._apply()
        self._closing_popout = True
        win.close()
        win.deleteLater()
        self._closing_popout = False
        self._tell_ui(False)
        self.host.page().runJavaScript("window.__zenShowPanel && window.__zenShowPanel('browser')")
        return True

    def _host_closing(self):
        """The app window is closing: take the browser window with it."""
        if self.popout is not None:
            self._save_popout_geometry(self.popout)
            self._closing_popout = True
            self.popout.close()

    def _tell_ui(self, popped):
        self.host.page().runJavaScript(
            f"window.dispatchEvent(new CustomEvent('zen-browser-popout', {{detail: {str(bool(popped)).lower()}}}))")

    def _popout_geometry(self):
        try:
            with open(self._popout_file, encoding="utf-8") as f:
                g = [int(v) for v in json.load(f)["geometry"]]
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtCore import QRect
            if any(s.availableGeometry().intersects(QRect(*g)) for s in QGuiApplication.screens()):
                return g                                   # only where a screen still is
        except Exception:
            pass
        return None

    def _save_popout_geometry(self, win):
        try:
            r = win.normalGeometry() if win.isMaximized() else win.geometry()
            with open(self._popout_file, "w", encoding="utf-8") as f:
                json.dump({"geometry": [r.x(), r.y(), r.width(), r.height()]}, f)
        except Exception:
            pass

    # ------------------------------------------------------------------ cross-thread
    def _on_call(self, job):
        fn, fut = job
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)

    def run(self, fn):
        """Run fn() on the Qt main thread -> concurrent Future."""
        fut = concurrent.futures.Future()
        self._bridge.call.emit((fn, fut))
        return fut


# ---------------------------------------------------------------------- DevTools relay (own thread)
def start_relay(agent, upstream_port):
    """-> the relay's http endpoint for Playwright's connect_over_cdp."""
    import aiohttp
    from aiohttp import web

    box, ready = {}, threading.Event()

    async def upstream_ws_url():
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{upstream_port}/json/version") as r:
                return await r.json(content_type=None)

    async def version(req):
        d = await upstream_ws_url()
        d["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{box['port']}/devtools/browser/zen"
        return web.json_response(d)

    async def relay(req):
        client = web.WebSocketResponse(max_msg_size=0)
        await client.prepare(req)
        d = await upstream_ws_url()
        async with aiohttp.ClientSession() as s, s.ws_connect(d["webSocketDebuggerUrl"], max_msg_size=0) as up:
            st = {"ctx": None, "priv": PRIV}
            hidden_sessions, hidden_targets, sess_target, filter_ids, waits = set(), set(), {}, set(), {}

            async def priv(method, params=None, session=None):
                st["priv"] += 1
                m = {"id": st["priv"], "method": method, "params": params or {}}
                if session:
                    m["sessionId"] = session
                await up.send_str(json.dumps(m))
                return st["priv"]

            def allowed(info):
                tid = info.get("targetId")
                if info.get("type") == "browser":          # the browser itself (Playwright asks for its info)
                    return True
                if tid in agent.ids:
                    if st["ctx"] is None and info.get("browserContextId"):
                        st["ctx"] = info["browserContextId"]
                    return True
                return bool(st["ctx"]) and info.get("browserContextId") == st["ctx"]

            # learn the agent profile's browser context before Playwright sees anything
            pid = await priv("Target.getTargets")
            async for m in up:
                if m.type != aiohttp.WSMsgType.TEXT:
                    break
                r = json.loads(m.data)
                if r.get("id") == pid:
                    for info in r.get("result", {}).get("targetInfos", []):
                        allowed(info)
                    break

            async def reply(msg, result=None, error=None):
                r = {"id": msg["id"]}
                if error:
                    r["error"] = {"code": -32000, "message": error}
                else:
                    r["result"] = result or {}
                if "sessionId" in msg:
                    r["sessionId"] = msg["sessionId"]
                await client.send_str(json.dumps(r))

            async def from_client():
                async for m in client:
                    if m.type != aiohttp.WSMsgType.TEXT:
                        break
                    msg = json.loads(m.data)
                    meth, p, sid = msg.get("method", ""), msg.get("params") or {}, msg.get("sessionId")
                    if meth in ("Browser.setDownloadBehavior", "Browser.close", "Browser.crash"):
                        await reply(msg)                   # never let the agent close the app's Chromium
                    elif meth == "Target.createTarget" and not sid:
                        url = p.get("url") or "about:blank"
                        tid = await asyncio.wrap_future(agent.run(lambda: agent.create(url)))
                        await reply(msg, {"targetId": tid})
                    elif meth == "Target.closeTarget" and not sid:
                        ok = p.get("targetId") in agent.ids and await asyncio.wrap_future(
                            agent.run(lambda: agent.close(p.get("targetId"))))
                        await reply(msg, {"success": bool(ok)})
                    elif meth == "Page.bringToFront" and sid in sess_target:
                        tid = sess_target[sid]
                        await asyncio.wrap_future(agent.run(lambda: agent.show(tid)))
                        await reply(msg)
                    elif meth in ("Target.attachToTarget", "Target.activateTarget", "Target.closeTarget",
                                  "Target.exposeDevToolsProtocol") and p.get("targetId") in hidden_targets:
                        await reply(msg, error="Not allowed")
                    elif meth in ("Target.createBrowserContext", "Target.disposeBrowserContext"):
                        await reply(msg, error="Not supported")
                    else:
                        if meth in ("Target.getTargets", "Target.getTargetInfo"):
                            filter_ids.add(msg["id"])
                        await up.send_str(m.data)
                await up.close()

            async def from_upstream():
                head = re.compile(r'^\{"id":(\d+)')
                async for m in up:
                    if m.type != aiohttp.WSMsgType.TEXT:
                        break
                    data = m.data
                    h = head.match(data)
                    if h:                                  # a command response: pass through unparsed (fast)
                        i = int(h.group(1))
                        if i > PRIV:
                            continue                       # the relay's own commands
                        if i in filter_ids:
                            filter_ids.discard(i)
                            r = json.loads(data)
                            res = r.get("result") or {}
                            if "targetInfos" in res:
                                res["targetInfos"] = [t for t in res["targetInfos"] if allowed(t)]
                            elif "targetInfo" in res and not allowed(res["targetInfo"]):
                                r = {"id": i, "error": {"code": -32000, "message": "No target with given id"}}
                            data = json.dumps(r)
                        await client.send_str(data)
                        continue
                    msg = json.loads(data)
                    sid, meth, p = msg.get("sessionId"), msg.get("method", ""), msg.get("params") or {}
                    if sid in hidden_sessions:
                        continue
                    if meth == "Target.attachedToTarget":
                        info, child = p.get("targetInfo", {}), p.get("sessionId")
                        if sid or allowed(info):           # nested (frames/workers of an agent page) or an agent page
                            sess_target[child] = info.get("targetId")
                        else:                              # the app's UI (or anything else): let it run, detach
                            hidden_sessions.add(child)
                            hidden_targets.add(info.get("targetId"))
                            await priv("Runtime.runIfWaitingForDebugger", session=child)
                            await priv("Target.detachFromTarget", {"sessionId": child})
                            continue
                    elif meth == "Target.detachedFromTarget":
                        if p.get("sessionId") in hidden_sessions:
                            hidden_sessions.discard(p.get("sessionId"))
                            continue
                        sess_target.pop(p.get("sessionId"), None)
                    elif meth in ("Target.targetCreated", "Target.targetInfoChanged"):
                        if not allowed(p.get("targetInfo", {})):
                            hidden_targets.add(p.get("targetInfo", {}).get("targetId"))
                            continue
                    elif meth in ("Target.targetDestroyed", "Target.targetCrashed"):
                        if p.get("targetId") in hidden_targets:
                            continue
                    await client.send_str(data)
                await client.close()

            _ = waits
            await asyncio.gather(from_client(), from_upstream(), return_exceptions=True)
        return client

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = web.Application()
        app.router.add_get("/json/version", version)
        app.router.add_get("/json/version/", version)
        app.router.add_get("/devtools/browser/zen", relay)
        runner = web.AppRunner(app, access_log=None)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        box["port"] = site._server.sockets[0].getsockname()[1]
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True, name="cdp-relay").start()
    ready.wait(10)
    return f"http://127.0.0.1:{box['port']}"


def register_with_server(server_url, endpoint):
    """Tell the server where the relay is; repeated, so a restarted server finds it again."""
    import time
    import urllib.request

    def loop():
        while True:
            try:
                req = urllib.request.Request(server_url + "/api/browser/host",
                                             data=json.dumps({"endpoint": endpoint}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
                time.sleep(5)
            except Exception:
                time.sleep(2)
    threading.Thread(target=loop, daemon=True, name="browser-host-register").start()


def unregister(server_url):
    """The window is closing: the server stops using its browser (and falls back to Edge only if still needed)."""
    import urllib.request
    try:
        req = urllib.request.Request(server_url + "/api/browser/host", data=json.dumps({"endpoint": ""}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass
