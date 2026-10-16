"""In-app browser the agent drives like a person: navigate, read, click, type, scroll, tabs.

With the app window open, the browser is a native view inside the window (qt_browser.py: Qt's Chromium, its own
profile), reached through the window's DevTools relay; the user sees and uses the real page. Without a window
(server only, --browser mode) it falls back to Microsoft Edge (installed) driven by Playwright, with its own profile
(agent_browser_profile/), shown in the UI as polled screenshots. Either way it never touches the user's normal
browser data. Everything runs on one dedicated thread (Playwright's
sync API is single-threaded); callers submit jobs and wait. The UI shows a live view (latest frame)
and can send its own clicks/keys, so the user can take over at any time.

Rules (the only ones): never type into payment fields, never buy anything.
"""
import base64
import os
import queue
import re
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
PROFILE = paths.data("agent_browser_profile")
SHOTS = paths.data("agent_browser_shots")
VIEW_W, VIEW_H = 1280, 800
HOST = {}                         # {"endpoint": DevTools relay of the app window, "t": last registration}


def set_host(endpoint):
    """The app window registers its browser every few seconds; '' = the window closed (Edge is then the fallback)."""
    if endpoint:
        HOST.update(endpoint=endpoint, t=time.time())
    else:
        HOST.clear()


def _window_expected():
    """A window registered recently: wait for it (it may be restarting) instead of starting Edge."""
    return bool(HOST.get("t")) and time.time() - HOST["t"] < 60

# --- the two rules ------------------------------------------------------------------------------
PAYMENT_FIELD = re.compile(r"\b(cc-|card[\s_-]?(number|num|no)|cardnumber|credit[\s_-]?card|debit[\s_-]?card|cvv|cvc|"
                           r"csc|security[\s_-]?code|exp(iry|iration)?[\s_-]?(date|month|year)|mm\s*/\s*yy|"
                           r"iban|routing[\s_-]?number|account[\s_-]?number|sort[\s_-]?code|billing[\s_-]?(address|zip))",
                           re.I)
PAYMENT_FRAMES = re.compile(r"(stripe\.com|braintreegateway|adyen|checkout\.com|paypal\.com|squareup|"
                            r"pay\.google|apple\.com/apple-pay|klarna|affirm|afterpay|authorize\.net|worldpay)", re.I)
BUY_BUTTON = re.compile(r"\b(buy|purchase|place\s+(your\s+)?order|pay(\s+now|\s+\$|\s+with)?|check\s*out|checkout|"
                        r"complete\s+(order|purchase|payment)|confirm\s+(order|purchase|payment)|submit\s+(order|payment)|"
                        r"subscribe|start\s+(free\s+)?trial|upgrade\s+now|add\s+to\s+(cart|bag|basket)|proceed\s+to\s+(payment|checkout)|"
                        r"book\s+now|reserve\s+now|donate)\b", re.I)


SEARCH_JS_BING = r"""() => [...document.querySelectorAll('li.b_algo')].map(li => {
  const a = li.querySelector('h2 a'); if (!a) return null;
  let url = a.href;
  try {                                   // bing wraps results in /ck/a?...&u=a1<base64url>
    const u = new URL(url); const p = u.searchParams.get('u');
    if (u.hostname.endsWith('bing.com') && p && p.startsWith('a1'))
      url = atob(p.slice(2).replace(/-/g, '+').replace(/_/g, '/'));
  } catch (e) {}
  const s = li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, p');
  return {title: a.innerText.trim(), url, snippet: s ? s.innerText.trim() : ''};
}).filter(Boolean)"""

SEARCH_JS = r"""() => {
  const out = [], seen = new Set();
  const engines = /(^|\.)(bing|google|yahoo|brave|startpage|mojeek|ecosia|duckduckgo|yimg)\.(com|org|net)$/;
  for (const h of document.querySelectorAll('h2, h3, .title, .snippet-title')) {
    const a = h.closest('a') || h.querySelector('a'); if (!a || !a.href) continue;
    let url = a.href;
    try {                                 // unwrap redirect links (?url= / ?uddg= / Yahoo's /RU=.../)
      const u = new URL(url);
      for (const k of ['uddg', 'url', 'q']) { const v = u.searchParams.get(k); if (v && v.startsWith('http')) url = v; }
      const ru = url.match(/\/RU=([^/]+)\//); if (ru) url = decodeURIComponent(ru[1]);
    } catch (e) {}
    let host = ''; try { host = new URL(url).hostname; } catch (e) { continue; }
    if (!url.startsWith('http') || seen.has(url) || engines.test(host)) continue;
    seen.add(url);
    let box = a; for (let i = 0; i < 4 && box.parentElement && box.innerText.length < 120; i++) box = box.parentElement;
    const title = h.innerText.trim().slice(0, 160);
    const snippet = box.innerText.replace(title, '').replace(/\s+/g, ' ').trim().slice(0, 300);
    out.push({title, url, snippet});
  }
  return out;
}"""

SEARCH_JS_DDG = r"""() => [...document.querySelectorAll('.result')].map(r => {
  const a = r.querySelector('a.result__a'); if (!a) return null;
  let url = a.href;
  try { const u = new URL(url); const p = u.searchParams.get('uddg'); if (p) url = decodeURIComponent(p); } catch (e) {}
  const s = r.querySelector('.result__snippet');
  return {title: a.innerText.trim(), url, snippet: s ? s.innerText.trim() : ''};
}).filter(Boolean)"""


class BrowserError(Exception):
    pass


def _kill_stale_profile():
    """End browser processes still holding agent_browser_profile (left by a crash)."""
    import subprocess
    script = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{PROFILE}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=20,
                       creationflags=0x08000000)
        time.sleep(1.0)
    except Exception:
        pass


# Uncaught errors reach the page view with their file and line. A syntax error stops a script before it runs, so it
# has no stack and Playwright's page error says only "Unexpected token '{'" — the window error event knows the line.
ERR_JS = r"""
window.addEventListener('error', function (e) {
  try {
    if (!e || !e.message || (e.message === 'Script error.' && !e.lineno)) return;   // opaque: the page error has it
    var f = String(e.filename || '').split('/').pop() || 'page';
    console.error('[uncaught] ' + e.message + ' (' + f + ':' + e.lineno + ':' + e.colno + ')');
  } catch (_) {}
}, true);
window.addEventListener('unhandledrejection', function (e) {
  try { console.error('[uncaught] unhandled promise rejection: ' + (e.reason && (e.reason.stack || e.reason.message) || e.reason)); } catch (_) {}
});
"""

# browser_eval: indirect eval runs in the global scope; the result comes back as JSON (cycles, functions and DOM
# nodes made printable)
EVAL_JS = r"""
(src) => {
  let v;
  try { v = (0, eval)(src); } catch (e) { return 'Error: ' + (e && e.name ? e.name + ': ' + e.message : e); }
  const seen = new WeakSet();
  const fix = (k, x) => {
    if (typeof x === 'function') return '[function ' + (x.name || 'anonymous') + ']';
    if (typeof x === 'undefined') return '[undefined]';
    if (typeof Node !== 'undefined' && x instanceof Node) return '[' + (x.nodeName || 'node').toLowerCase() + (x.id ? '#' + x.id : '') + ']';
    if (x && typeof x === 'object') { if (seen.has(x)) return '[circular]'; seen.add(x); }
    return x;
  };
  try { const s = JSON.stringify(v, fix, 1); return s === undefined ? String(v) : s; } catch (e) { return String(v); }
}
"""

SNAPSHOT_JS = r"""
(maxEls) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    return r.bottom > -200 && r.top < innerHeight + 1200;
  };
  const label = (el) => {
    let t = el.getAttribute('aria-label') || el.getAttribute('title') || '';
    if (!t && el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t) t = el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('alt') || '';
    if (!t && el.querySelector) { const img = el.querySelector('img[alt]'); if (img) t = img.alt; }
    return (t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  window.__agentRefMax = window.__agentRefMax || 0;   // numbers stay stable while the page lives
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], ' +
              '[role=checkbox], [role=radio], [role=option], [role=menuitem], [role=combobox], [role=searchbox], ' +
              '[contenteditable=""], [contenteditable=true], summary, [onclick]';
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    n += 1;
    if (n > maxEls) break;
    if (!el.hasAttribute('data-agent-ref')) el.setAttribute('data-agent-ref', String(++window.__agentRefMax));
    const ref = Number(el.getAttribute('data-agent-ref'));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'select' :
                 tag === 'textarea' ? 'textbox' : tag === 'input' ? (el.type || 'text') : tag);
    const r = el.getBoundingClientRect();
    out.push({ref, role, name: label(el), href: tag === 'a' ? el.href : undefined,
              value: (tag === 'input' || tag === 'textarea') && el.type !== 'password' ? (el.value || '').slice(0, 80) : undefined,
              placeholder: el.getAttribute('placeholder') || undefined, checked: el.checked || undefined,
              disabled: el.disabled || undefined, inView: r.top >= 0 && r.top < innerHeight});
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return {elements: out, text, title: document.title, url: location.href,
          scroll: {y: Math.round(scrollY), h: document.documentElement.scrollHeight, vh: innerHeight}};
}
"""

INFO_JS = r"""
(el) => ({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
          autocomplete: el.getAttribute('autocomplete') || '', placeholder: el.getAttribute('placeholder') || '',
          aria: el.getAttribute('aria-label') || '', text: (el.innerText || el.value || '').trim().slice(0, 200),
          label: el.labels && el.labels.length ? el.labels[0].innerText : '',
          form: el.form ? (el.form.getAttribute('action') || '') + ' ' + (el.form.id || '') : ''})
"""


_LOCAL_URL = re.compile(r"^(file:|https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$))", re.I)


class BrowserManager:
    def __init__(self):
        self.jobs = queue.Queue()
        self.thread = None
        self.ctx = None
        self.page = None
        self.pw = None
        self.browser = None            # set when connected to the app window's browser (mode "qt")
        self.mode = ""                 # "qt" (native view in the app window) | "edge" (separate Edge) | "" (not started)
        self.frame = None              # latest JPEG of the current page (live view)
        self.frame_t = 0.0
        self.frame_seq = 0
        self.last_note = ""                # explanation from the last click (e.g. clicked through a pop-up)
        self.last_agent = 0.0          # last time the agent did something (live view shows "agent")
        self.last_viewer = 0.0         # last time the UI asked for a frame
        # headless browsers get captchas, "Access Denied" or junk results from most sites; a normal window
        # parked off-screen does not (LWS_BROWSER_HEADLESS=1 restores headless)
        self.headless = os.environ.get("LWS_BROWSER_HEADLESS") == "1"
        self.listeners = []            # fn(event, info)
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- thread plumbing
    def _ensure_thread(self):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True, name="agent-browser")
                self.thread.start()

    def call(self, fn, *args, timeout=90, agent=True, **kw):
        """Run fn(*args) on the browser thread and return its result."""
        self._ensure_thread()
        if agent:
            self.last_agent = time.time()
        box, evt = {}, threading.Event()
        self.jobs.put((fn, args, kw, box, evt))
        if not evt.wait(timeout):
            raise BrowserError(f"the browser did not respond within {timeout}s")
        if "error" in box:
            e = box["error"]
            raise e if isinstance(e, BrowserError) else BrowserError(f"{type(e).__name__}: {str(e).splitlines()[0][:300]}")
        return box.get("result")

    def _loop(self):
        while True:
            try:
                job = self.jobs.get(timeout=0.25)
            except queue.Empty:
                job = None
            if job:
                fn, args, kw, box, evt = job
                try:
                    box["result"] = fn(*args, **kw)
                except Exception as e:
                    box["error"] = e
                evt.set()
            now = time.time()
            active = now - self.last_agent < 90 or now - self.last_viewer < 4
            if self.page is not None and active and now - self.frame_t > 0.45:
                self._capture()

    def _notify(self, event, **info):
        for fn in list(self.listeners):
            try:
                fn(event, info)
            except Exception:
                pass

    # ---------------------------------------------------------------- browser lifecycle (browser thread)
    def _start(self):
        if self.ctx is not None and self.mode == "edge" and HOST.get("endpoint"):
            # Edge was the fallback (no window then); a window is registered now: move into it, close Edge
            try:
                old = self.ctx
                if self._connect_window(quiet=True):
                    try:
                        old.close()
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        if self.ctx is not None:
            try:
                if self.browser is not None and not self.browser.is_connected():
                    raise BrowserError("the app window's browser went away")
                self.ctx.pages  # still alive?
                if self.page is None or self.page.is_closed():
                    self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
                return
            except Exception:
                self.ctx, self.browser, self.page, self.mode = None, None, None, ""
        from playwright.sync_api import sync_playwright
        if self.pw is None:
            self.pw = sync_playwright().start()
        if HOST.get("endpoint") and self._connect_window():
            return
        t_end = time.time() + 20                 # the window is restarting: its new address arrives within seconds
        while _window_expected() and time.time() < t_end:
            time.sleep(1.0)
            if HOST.get("endpoint") and self._connect_window():
                return
        os.makedirs(PROFILE, exist_ok=True)
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        if not self.headless:
            args += ["--window-position=-32000,-32000", f"--window-size={VIEW_W},{VIEW_H + 120}",
                     "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"]
        opts = dict(user_data_dir=PROFILE, headless=self.headless, viewport={"width": VIEW_W, "height": VIEW_H},
                    args=args, locale="en-US")
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="msedge", **opts)
        except Exception:
            _kill_stale_profile()          # a crashed earlier run can leave the profile locked
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(channel="msedge", **opts)
            except Exception:
                self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **opts)
        self.ctx.on("page", self._on_new_page)
        try:
            self.ctx.add_init_script(ERR_JS)          # every page: uncaught errors with file:line (syntax errors too)
        except Exception:
            pass
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        for p in self.ctx.pages:
            self._watch_console(p)
        self.mode = "edge"
        self._notify("started")

    def _connect_window(self, quiet=False):
        """Use the native browser view in the app window (one Chromium for UI and agent). -> True if connected."""
        ep = HOST.get("endpoint")
        try:
            browser = self.pw.chromium.connect_over_cdp(ep, timeout=10000)
            ctx = browser.contexts[0]
        except Exception as e:
            if not quiet:
                print("browser: app window not reachable:", str(e).splitlines()[0][:200])
            if HOST.get("endpoint") == ep:
                HOST.pop("endpoint", None)            # a stale address; the window re-registers within ~5 s
            return False
        self.browser, self.ctx = browser, ctx
        self.ctx.on("page", self._on_new_page)
        try:
            self.ctx.add_init_script(ERR_JS)
        except Exception:
            pass
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        for p in self.ctx.pages:
            self._watch_console(p)
        self.mode = "qt"
        self._notify("started")
        return True

    def _bg_page(self):
        """A background tab for searches and page reads (not shown; desktop-sized)."""
        page = self.ctx.new_page()
        if self.mode == "qt":
            try:
                page.set_viewport_size({"width": VIEW_W, "height": VIEW_H})
            except Exception:
                pass
        return page

    def _watch_console(self, page):
        """Keep the page's JavaScript errors and console warnings/errors: the agent sees them with the page view
        (without this, a script that crashes on load looks like a page that renders but does nothing)."""
        if getattr(page, "_agent_console", False):
            return
        page._agent_console = True
        try:                                          # DevTools: network log + full console from the start
            import browser_devtools
            browser_devtools.attach(page)
        except Exception as e:
            print("devtools attach failed:", repr(e))
        if not hasattr(self, "console"):
            self.console = []

        def keep(kind, text, prefer=False):
            if page is not self.page:
                return
            line = f"{kind}: {' '.join(str(text).split())[:300]}"
            if kind == "uncaught error":              # reported twice (page error + window error): keep the one
                def norm(x):                          # "Uncaught SyntaxError: X (f:1)" and "X" are the same error
                    x = x.split(": ", 1)[-1].split(" (")[0]
                    return re.sub(r"^(Uncaught\s+)?(\w*Error:\s*)?", "", x).strip()
                base = norm(line)
                for i, old in enumerate(self.console):
                    if old.startswith("uncaught error") and norm(old) == base:
                        # the page error's location is the project's own frame (engine.js:83); the window error's
                        # is where it was thrown, often a library (three.module.js:3991)
                        if (" (" in line and " (" not in old) or (prefer and " (" in line):
                            self.console[i] = line
                        return
            self.console.append(line)
            del self.console[:-30]

        def on_msg(m):
            if m.text.startswith("[uncaught] "):      # from ERR_JS: the error with its file and line
                keep("uncaught error", m.text[len("[uncaught] "):])
                return
            if m.text.startswith("Failed to load resource"):   # the network line already says which file
                return
            if m.type in ("error", "warning"):
                loc = m.location or {}
                where = (f" ({os.path.basename(loc.get('url', ''))}:{int(loc.get('lineNumber') or 0) + 1})"
                         if loc.get("url") else "")
                keep(f"console.{m.type}", m.text + where)

        def on_err(e):
            # the first stack frame in a local file (the project's code), else the first frame; stack lines are
            # 1-based already (the old +1 put engine.js:83 at line 84)
            frames = [m for m in (re.search(r"((?:file|https?)://[^\s()]*?([^/\\\s()]+\.\w+)):(\d+):\d+\)?\s*$", f)
                                  for f in (getattr(e, "stack", "") or "").split("\n")[1:12]) if m]
            mine = [m for m in frames if _LOCAL_URL.match(m.group(1))]
            at = (mine or frames or [None])[0]
            keep("uncaught error", f"{getattr(e, 'message', e)}" + (f" ({at.group(2)}:{at.group(3)})" if at else ""),
                 prefer=True)

        def short(u):
            u = str(u or "")
            return os.path.basename(u.split("?")[0].rstrip("/")) or u[:80]

        def on_failed(req):                       # a script, image or data file that could not load at all
            keep("network", f"{req.method} {short(req.url)} failed: {req.failure or 'error'}")

        def on_response(resp):                    # ... or loaded with an error status (404 for a mistyped path)
            try:
                if resp.status >= 400 and not resp.url.split("?")[0].endswith("favicon.ico"):
                    keep("network", f"{resp.status} {resp.request.method} {short(resp.url)}")
            except Exception:
                pass

        page.on("console", on_msg)
        page.on("pageerror", on_err)
        page.on("requestfailed", on_failed)
        page.on("response", on_response)

    def _on_new_page(self, page):
        self._watch_console(page)
        if getattr(self, "_bg_tab", False):
            return                                # a background search tab: leave the agent's tab alone
        self.page = page                          # popups / target=_blank become the current tab
        self._notify("tab")

    def _capture(self):
        if self.mode == "qt":                     # the user sees the real page: no frames to stream
            return
        try:
            self.frame = self.page.screenshot(type="jpeg", quality=60, timeout=3000)
            self.frame_t = time.time()
            self.frame_seq += 1
        except Exception:
            self.frame_t = time.time()

    def _settle(self, ms=900):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(ms)
        self._capture()

    def _state(self):
        return {"url": self.page.url, "title": self.page.title(),
                "tabs": [{"i": i, "url": p.url, "title": p.title(), "current": p is self.page}
                         for i, p in enumerate(self.ctx.pages)] if self.ctx else []}

    # ---------------------------------------------------------------- operations (browser thread)
    def _local_cache(self, url):
        """Your own pages (file:, localhost) load without the HTTP cache: an edited module is what runs. Run 71a2ec62
        spent ~50 steps on a ui.js the browser kept from before the fix (the server sent the new one; ?v=2 on the page
        URL doesn't reach the files it imports). Websites keep their cache."""
        local = bool(_LOCAL_URL.match(url))
        if getattr(self.page, "_zen_nocache", None) == local:
            return local
        try:
            cdp = getattr(self.page, "_zen_net_cdp", None)
            if cdp is None:
                cdp = self.page.context.new_cdp_session(self.page)
                cdp.send("Network.enable")
                self.page._zen_net_cdp = cdp
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": local})
            self.page._zen_nocache = local
        except Exception as e:
            print("browser: cache switch failed:", str(e).splitlines()[0][:200] if str(e) else repr(e))
        return local

    def _goto(self, url):
        self._start()
        if not re.match(r"^[a-z]+://", url, re.I):
            url = ("https://" + url) if re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", url) else \
                "https://duckduckgo.com/?q=" + re.sub(r"\s+", "+", url.strip())
        self._watch_console(self.page)
        self.console = []                         # messages belong to the page being opened
        local = self._local_cache(url)
        # your own page: wait for every script (module imports too) before its console is read
        self.page.goto(url, wait_until="load" if local else "domcontentloaded", timeout=45000)
        self._settle(1200)
        return self._state()

    def _snapshot(self, max_chars=6000, max_els=150):
        self._start()
        snap = self.page.evaluate(SNAPSHOT_JS, max_els)
        snap["text"] = snap["text"][:max_chars]
        snap["console"] = list(getattr(self, "console", []))[-12:]
        return snap

    def _element(self, ref):
        loc = self.page.locator(f'[data-agent-ref="{int(ref)}"]')
        if loc.count() == 0:
            raise BrowserError(f"element [{ref}] is gone (the page changed). Call browser_read again for fresh numbers.")
        return loc.first

    def _check_buy(self, info):
        words = " ".join(str(info.get(k, "")) for k in ("text", "aria", "label", "name", "id", "form"))
        if BUY_BUTTON.search(words):
            raise BrowserError(f"refused: '{(info.get('text') or info.get('aria') or words)[:60]}' looks like a purchase "
                               f"step. The agent cannot buy anything.")

    def _check_payment_field(self, info, frame_url=""):
        words = " ".join(str(info.get(k, "")) for k in ("name", "id", "autocomplete", "placeholder", "aria", "label"))
        if PAYMENT_FIELD.search(words) or PAYMENT_FRAMES.search(frame_url or ""):
            raise BrowserError("refused: that is a payment field. The agent never types card or bank details.")

    def _cover_of(self, el):
        try:
            return el.evaluate(r"""e => { const r = e.getBoundingClientRect(); const t = document.elementFromPoint(
                r.left + r.width / 2, r.top + r.height / 2); if (!t || t === e || e.contains(t) || t.contains(e)) return '';
                const d = t.closest('[role=dialog],dialog,[aria-modal=true],.modal,[class*=modal],[class*=overlay],[class*=popup]') || t;
                return (d.getAttribute('aria-label') || d.innerText || d.className || d.tagName).toString().replace(/\s+/g,' ').slice(0, 160) }""")
        except Exception:
            return ""

    def _robust_click(self, el):
        """Normal click. If a pop-up/overlay intercepts it, click the element itself through the DOM and say so.
        -> note for the agent ('' when the click was ordinary)."""
        try:
            el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        cover = self._cover_of(el)
        if not cover:
            try:
                el.click(timeout=5000)
                return ""
            except Exception as e:
                err = str(e)
                cover = self._cover_of(el)
        else:
            err = "covered"
        try:
            el.evaluate("e => e.click()")
            return (f"(A pop-up or overlay ('{cover[:100]}') was covering it, so it was clicked directly. If nothing "
                    f"changed, close the pop-up first: browser_press 'Escape' or click its close/X button.)") if cover else                 "(Clicked directly after a normal click failed.)"
        except Exception:
            pass
        raise BrowserError(f"could not click it ({err.splitlines()[0][:160]}). The page may have changed: call "
                           f"browser_read for fresh element numbers, or try clicking by text.")

    def _click(self, ref=None, text=None, x=None, y=None):
        self._start()
        if ref is not None:
            el = self._element(ref)
            self._check_buy(el.evaluate(INFO_JS))
            self.last_note = self._robust_click(el)
        elif text:
            el = self.page.get_by_text(str(text), exact=False).first
            if el.count() == 0:
                el = self.page.get_by_role("button", name=re.compile(re.escape(str(text)), re.I)).first
            if el.count() == 0:
                raise BrowserError(f"no visible element with the text '{text}'")
            self._check_buy(el.evaluate(INFO_JS))
            self.last_note = self._robust_click(el)
        elif x is not None and y is not None:
            info = self.page.evaluate("([x,y]) => { const el = document.elementFromPoint(x,y); return el ? "
                                      "{text: (el.innerText||el.value||'').trim().slice(0,200), aria: el.getAttribute('aria-label')||'', "
                                      "name: el.name||'', id: el.id||''} : {} }", [float(x), float(y)])
            self._check_buy(info)
            self.page.mouse.click(float(x), float(y))
        else:
            raise BrowserError("give an element number (ref), visible text, or x/y coordinates")
        self._settle(900)
        return self._state()

    def _type(self, ref=None, text="", submit=False, clear=True):
        self._start()
        if ref is not None:
            el = self._element(ref)
            self._check_payment_field(el.evaluate(INFO_JS))
            self._robust_click(el)
            if clear:
                el.fill("")
            el.type(str(text), delay=25)
        else:                                       # type into whatever has focus
            info = self.page.evaluate("() => { const el = document.activeElement; if (!el) return {}; return {name: el.name||'', "
                                      "id: el.id||'', autocomplete: el.getAttribute('autocomplete')||'', "
                                      "placeholder: el.getAttribute('placeholder')||'', aria: el.getAttribute('aria-label')||''} }")
            focused_frame = ""
            try:
                focused_frame = self.page.evaluate("() => document.activeElement && document.activeElement.tagName === 'IFRAME' ? "
                                                   "document.activeElement.src : ''")
            except Exception:
                pass
            self._check_payment_field(info, focused_frame)
            self.page.keyboard.type(str(text), delay=25)
        if submit:
            self.page.keyboard.press("Enter")
        self._settle(1100 if submit else 300)
        return self._state()

    def _press(self, keys):
        self._start()
        self.page.keyboard.press(str(keys))
        self._settle(700)
        return self._state()

    def _select(self, ref, option):
        self._start()
        el = self._element(ref)
        try:
            el.select_option(label=str(option), timeout=5000)
        except Exception:
            el.select_option(value=str(option), timeout=5000)
        self._settle(600)
        return self._state()

    def _scroll(self, direction="down", amount=None):
        self._start()
        px = int(amount or VIEW_H * 0.85)
        dy = {"down": px, "up": -px}.get(str(direction).lower(), 0)
        dx = {"right": px, "left": -px}.get(str(direction).lower(), 0)
        if str(direction).lower() == "top":
            self.page.evaluate("window.scrollTo(0, 0)")
        elif str(direction).lower() == "bottom":
            self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        else:
            self.page.mouse.wheel(dx, dy)
        self._settle(500)
        return self.page.evaluate("({y: Math.round(scrollY), h: document.documentElement.scrollHeight, vh: innerHeight})")

    def _history(self, which):
        self._start()
        local = False
        if which == "reload":
            local = self._local_cache(self.page.url)
            self.console = []                     # this load's messages
        {"back": self.page.go_back, "forward": self.page.go_forward, "reload": self.page.reload}[which](
            wait_until="load" if local else "domcontentloaded", timeout=30000)
        self._settle(800)
        return self._state()

    def _tab(self, action, index=None, url=None):
        self._start()
        pages = self.ctx.pages
        if action == "new":
            self.page = self.ctx.new_page()
            self.page.bring_to_front()
            if url:
                return self._goto(url)
        elif action == "switch":
            self.page = pages[int(index)]
            self.page.bring_to_front()
        elif action == "close":
            p = pages[int(index)] if index is not None else self.page
            p.close()
            pages = self.ctx.pages
            self.page = pages[-1] if pages else self.ctx.new_page()
        self._settle(300)
        return self._state()

    def _screenshot(self, full=False):
        self._start()
        png = self.page.screenshot(type="png", full_page=bool(full), timeout=15000,
                                   scale="css" if self.mode == "qt" else "device")
        if self.mode == "qt":            # the docked view is zoomed to fit the panel: return CSS-pixel size
            try:
                import io
                from PIL import Image
                w = int(self.page.evaluate("document.documentElement.clientWidth || innerWidth"))
                im = Image.open(io.BytesIO(png))
                if w and im.width > w * 1.05:
                    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "PNG")
                    png = buf.getvalue()
            except Exception:
                pass
        return png

    def _wait(self, seconds=None, text=None):
        self._start()
        if text:
            self.page.get_by_text(str(text), exact=False).first.wait_for(timeout=int(float(seconds or 15) * 1000))
        else:
            self.page.wait_for_timeout(int(min(float(seconds or 2), 30) * 1000))
        self._capture()
        return self._state()

    def _links(self, contains=None, max_links=80):
        self._start()
        links = self.page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => ({text: (a.innerText||a.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim().slice(0,140), href: a.href})).filter(l => l.text && l.href.startsWith('http'))")
        if contains:
            c = str(contains).lower()
            links = [l for l in links if c in l["text"].lower() or c in l["href"].lower()]
        seen, out = set(), []
        for l in links:
            if l["href"] not in seen:
                seen.add(l["href"])
                out.append(l)
        return out[:int(max_links)]

    def _user_input(self, kind, **a):
        """Clicks/keys from the live view (the user taking over). No agent rules apply to the user."""
        self._start()
        if kind == "click":
            self.page.mouse.click(float(a["x"]), float(a["y"]))
        elif kind == "type":
            self.page.keyboard.type(str(a.get("text", "")))
        elif kind == "key":
            self.page.keyboard.press(str(a.get("key", "")))
        elif kind == "scroll":
            self.page.mouse.wheel(0, float(a.get("dy", 0)))
        elif kind == "navigate":
            return self._goto(str(a.get("url", "")))
        elif kind in ("back", "forward", "reload"):
            return self._history(kind)
        self._settle(350)
        return self._state()

    def _close(self):
        try:
            if self.browser is not None:
                self.browser.close()              # connected to the app window: only disconnects
            elif self.ctx is not None:
                self.ctx.close()
        finally:
            self.ctx, self.page, self.frame, self.browser, self.mode = None, None, None, None, ""

    def _search(self, query, n):
        """Search Bing, then DuckDuckGo, in a background tab of the real browser profile."""
        import urllib.parse
        self._start()
        q = urllib.parse.quote_plus(query)
        engines = [("brave", f"https://search.brave.com/search?q={q}&source=web", SEARCH_JS),
                   ("startpage", f"https://www.startpage.com/do/search?q={q}", SEARCH_JS),
                   ("yahoo", f"https://search.yahoo.com/search?p={q}", SEARCH_JS),
                   ("bing", f"https://www.bing.com/search?q={q}&setlang=en", SEARCH_JS_BING)]
        self._bg_tab = True
        page = self._bg_page()
        try:
            for name, url, js in engines:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1200)
                    rows = page.evaluate(js) or []
                except Exception:
                    rows = []
                rows = [r for r in rows if r.get("url", "").startswith("http")][:n]
                if rows:
                    return name, rows
            return None, []
        finally:
            self._bg_tab = False
            try:
                page.close()
            except Exception:
                pass

    def _read_bg(self, url, max_chars=20000):
        """Load a page in a background tab of the real browser and return its readable text (for pages that block
        plain HTTP fetches or build their content with JavaScript). The agent's own tab is not touched."""
        self._start()
        self._bg_tab = True
        page = self._bg_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            try:
                page.mouse.wheel(0, 2500)              # lazy-loaded content (comments, feeds)
                page.wait_for_timeout(700)
            except Exception:
                pass
            text = ""
            try:
                import trafilatura
                import tools as _wt
                text = _wt.extract_html(page.content(), include_comments=True, include_links=False)
            except Exception:
                pass
            if len(text) < 400:                        # extraction found little: take the visible text
                text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            return {"url": page.url, "title": page.title(), "text": text[:max_chars]}
        finally:
            self._bg_tab = False
            try:
                page.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- public API (any thread)
    def search(self, query, n=8):
        return self.call(self._search, query, n, timeout=60, agent=False)

    def read_page(self, url, max_chars=20000):
        return self.call(self._read_bg, url, max_chars, timeout=18, agent=False)

    def goto(self, url):
        return self.call(self._goto, url)

    def snapshot(self, max_chars=6000):
        return self.call(self._snapshot, max_chars)

    def evaluate(self, expr):
        return self.call(self._evaluate, expr)

    def devtools(self, action, **kw):
        """browser_devtools.<action>(current page, **kw) on the browser thread."""
        import browser_devtools as dt
        fn = {"network": dt.network, "request": dt.request, "console": dt.console, "element": dt.element,
              "listeners": dt.listeners, "storage": dt.storage, "performance": dt.performance,
              "breakpoint": dt.breakpoint, "exceptions": dt.exceptions}[action]

        def run():
            self._start()
            return fn(self.page, **kw)
        wait = float(kw.get("timeout") or 15) + 30 if action in ("breakpoint", "exceptions") else 60
        return self.call(run, timeout=wait)

    def _evaluate(self, expr):
        """Evaluate JavaScript in the page (global scope, so top-level let/const are visible) -> JSON text."""
        self._start()
        out = self.page.evaluate(EVAL_JS, expr)
        out = str(out)
        con = list(getattr(self, "console", []))[-5:]
        return (out if len(out) <= 4000 else out[:4000] + f"\n…[{len(out) - 4000:,} more characters]") + \
            (("\n\nJavaScript console (latest):\n" + "\n".join(con)) if con else "")

    def click(self, **kw):
        return self.call(self._click, **kw)

    def type(self, **kw):
        return self.call(self._type, **kw)

    def press(self, keys):
        return self.call(self._press, keys)

    def select(self, ref, option):
        return self.call(self._select, ref, option)

    def scroll(self, direction="down", amount=None):
        return self.call(self._scroll, direction, amount)

    def history(self, which):
        return self.call(self._history, which)

    def tab(self, action, index=None, url=None):
        return self.call(self._tab, action, index, url)

    def screenshot(self, full=False):
        return self.call(self._screenshot, full)

    def wait(self, seconds=None, text=None):
        return self.call(self._wait, seconds, text, timeout=60)

    def links(self, contains=None):
        return self.call(self._links, contains)

    def state(self):
        if self.page is None:
            return {"url": "", "title": "", "tabs": [], "running": False, "mode": "",
                    "host": bool(HOST.get("endpoint"))}
        try:
            st = self.call(self._state, agent=False, timeout=10)
        except BrowserError:
            st = {"url": "", "title": "", "tabs": []}
        st.update(running=True, mode=self.mode, agent=time.time() - self.last_agent < 3,
                  host=bool(HOST.get("endpoint")))
        return st

    def user_input(self, kind, **a):
        return self.call(self._user_input, kind, agent=False, **a)

    def latest_frame(self):
        self.last_viewer = time.time()
        return self.frame, self.frame_seq, time.time() - self.last_agent < 3

    def close(self):
        if self.thread and self.thread.is_alive():
            try:
                self.call(self._close, agent=False, timeout=15)
            except Exception:
                pass

    def save_shot(self, jpeg, tag):
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.jpg")
        with open(p, "wb") as f:
            f.write(jpeg)
        return p


MANAGER = BrowserManager()
