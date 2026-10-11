"""Programmatic access to the harness: the /v1 REST API, the run queue and the headless approval policy.

Runs inside the normal backend process (web_backend.make_app installs it), so the desktop window and API clients
share one engine host and one GPU. API runs are ordinary agent conversations tagged source="api"; a dispatcher
thread starts them one at a time, only while no other run is active. See plans/headless-harness-prd.md.
"""
import asyncio
import collections
import copy
import hmac
import json
import secrets
import threading
import time
import uuid
from urllib.parse import urlparse

from aiohttp import web

import web_backend as wb

VERSION = "1"
DEFAULT_HEADLESS = {
    "autostart": False, "api_enabled": True, "mcp_enabled": True, "port": 8765, "lan": False, "token": "",
    "approval": "deny",               # deny | ask (a card in the desktop app; denied after approval_timeout s)
    "approval_timeout": 120, "allow_rules": [],
    "max_queue": 20, "rate_limit": 120, "max_run_minutes": 30,
}
LOOPBACK = ("127.0.0.1", "::1", "localhost")
KEEP_RUNS = 200
MAX_EVENTS = 20000


def cfg():
    return {**DEFAULT_HEADLESS, **(wb.STORE.settings.get("headless") or {})}


def ensure_token():
    """The API token, generated (and saved) the first time it is needed."""
    with wb.STORE.lock:
        h = wb.STORE.settings.setdefault("headless", {})
        if h.get("token"):
            return h["token"]
        h["token"] = secrets.token_urlsafe(32)
    wb.STORE.save_settings()
    return h["token"]


def is_api(conv):
    """A conversation started through the API, or a sub-task delegated from one."""
    for _ in range(8):
        if conv is None:
            return False
        if conv.get("source") == "api":
            return True
        conv = wb.STORE.get(conv.get("parent")) if conv.get("parent") else None
    return False


# ============================================================================ approval policy (Host hooks)
def apply_cfg(conv, c):
    """Engine config for an API conversation: only the headless allow-list, and read-only when asked."""
    if not is_api(conv):
        return c
    h = cfg()
    c["allow_rules"] = copy.deepcopy(h.get("allow_rules") or [])
    c["permissions"] = "ask"                     # the desktop's permission tier never applies to API runs
    root = conv
    while root.get("parent") and wb.STORE.get(root["parent"]):
        root = wb.STORE.get(root["parent"])
    if root.get("api_read_only"):
        c["plan_mode"] = True
    return c


def approval_decision(run, req):
    """Host._ask asks this first. -> None (not an API run), 'deny', or ('wait', deadline) for a human in the
    desktop app. Calls matching headless.allow_rules never get here: apply_cfg gives the engine those rules."""
    if not is_api(run.conv):
        return None
    h = cfg()
    if h.get("approval") == "ask":
        return ("wait", time.time() + float(h.get("approval_timeout") or 120))
    log(f"run {RUN_BY_CONV.get(run.conv['id'], '?')}: denied {req.get('name')} (headless approval policy)")
    return "deny"


# ============================================================================ runs and queue
class ApiRun:
    def __init__(self, text, conv_id, title, read_only):
        self.id = "run_" + uuid.uuid4().hex[:16]
        self.input, self.conv_id, self.title, self.read_only = text, conv_id, title, read_only
        self.status = "queued"
        self.created, self.started, self.ended = time.time(), None, None
        self.result, self.error = "", ""
        self.events, self.seq = [], 0
        self.host_run = None
        self.cancel_requested = False

    def add_event(self, kind, data):
        if len(self.events) >= MAX_EVENTS and kind in ("think_delta", "ans_delta"):
            return
        self.seq += 1
        try:
            data = json.loads(json.dumps(data, default=str))
        except Exception:
            data = {"text": str(data)}
        self.events.append({"seq": self.seq, "kind": kind, "data": data, "t": time.time()})

    @property
    def finished(self):
        return self.status in ("done", "failed", "stopped", "cancelled")

    def public(self):
        pend = []
        if self.status == "running" and self.conv_id:
            with wb.HOST.lock:
                pend = [cid for cid, (r, _e, _b) in wb.HOST.approvals.items() if r.conv.get("id") == self.conv_id]
        return {"id": self.id, "status": self.status, "conversation_id": self.conv_id, "input": self.input,
                "created": self.created, "started": self.started, "ended": self.ended, "result": self.result,
                "error": self.error, "pending_approvals": pend, "events": self.seq,
                "queue_position": (QUEUE.index(self) + 1) if self in QUEUE else 0}


LOCK = threading.RLock()
RUNS = collections.OrderedDict()      # id -> ApiRun (newest last)
QUEUE = []                            # ApiRun waiting to start
CURRENT = [None]                      # the ApiRun running now
RUN_BY_CONV = {}                      # conv id -> run id of the running ApiRun
STATE = {"paused": False, "dispatcher": None}


def log(msg):
    print(time.strftime("%H:%M:%S"), "[api]", msg, flush=True)


def on_event(conv, kind, d):
    """web_backend.ConvWriter calls this for every engine event of every conversation."""
    rid = RUN_BY_CONV.get(conv.get("id"))
    if rid is None:
        return
    r = RUNS.get(rid)
    if r is not None:
        r.add_event(kind, d)


def submit(text, conv_id=None, title=None, read_only=False):
    """Queue a run. -> ApiRun. Raises ValueError (bad input) / OverflowError (queue full) / KeyError (no conv)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("input is empty")
    if conv_id and wb.STORE.get(conv_id) is None:
        raise KeyError(conv_id)
    with LOCK:
        if len(QUEUE) >= int(cfg().get("max_queue") or 20):
            raise OverflowError("queue is full")
        r = ApiRun(text, conv_id, (title or text)[:48], bool(read_only))
        RUNS[r.id] = r
        while len(RUNS) > KEEP_RUNS:
            old = next(iter(RUNS.values()))
            if not old.finished:
                break
            RUNS.popitem(last=False)
        QUEUE.append(r)
    log(f"run {r.id} queued ({len(QUEUE)} in queue)")
    start_dispatcher()
    return r


def cancel(r):
    with LOCK:
        if r in QUEUE:
            QUEUE.remove(r)
            r.status, r.ended = "cancelled", time.time()
            log(f"run {r.id} cancelled while queued")
            return r
        if r.status == "running":
            r.cancel_requested = True
            wb.HOST.stop(r.conv_id)
    return r


def _start(r):
    conv = wb.STORE.get(r.conv_id) if r.conv_id else None
    if conv is None:
        conv = wb.STORE.new_conv(r.title, mode="agent", source="api")
        r.conv_id = conv["id"]
    with wb.STORE.lock:
        conv["mode"] = "agent"
        if r.read_only:
            conv["api_read_only"] = True
        else:
            conv.pop("api_read_only", None)
        conv["messages"].append({"role": "user", "content": r.input, "t": time.time(), "via": "api"})
        conv["updated"] = time.time()
    wb.STORE.touch(conv, meta=True)
    r.status, r.started = "running", time.time()
    RUN_BY_CONV[conv["id"]] = r.id
    CURRENT[0] = r
    try:
        wb.HOST.start_agent(conv, r.input)
        r.host_run = wb.HOST.runs.get(conv["id"])
        log(f"run {r.id} started in conversation {conv['id']}")
    except Exception as e:
        _finish(r, "failed", error=f"{type(e).__name__}: {e}")


def _finish(r, status, error=""):
    hr = r.host_run
    final = (hr.writer.final if hr is not None else "") or ""
    if status is None:
        status = "stopped" if final == "(stopped)" else ("failed" if final.startswith("⚠") else "done")
        if r.cancel_requested:
            status = "cancelled"
    r.status, r.ended = status, time.time()
    r.result = "" if final == "(stopped)" else final
    if status == "failed" and not error:
        error = final.lstrip("⚠ ").strip()
    r.error = error
    RUN_BY_CONV.pop(r.conv_id, None)
    if CURRENT[0] is r:
        CURRENT[0] = None
    r.add_event("end", {"status": status})
    log(f"run {r.id} {status} after {r.ended - (r.started or r.ended):.1f}s")


def _tick():
    with LOCK:
        r = CURRENT[0]
        if r is not None:
            hr = r.host_run
            if hr is None or hr.done:
                _finish(r, None)
            elif r.started and time.time() - r.started > 60 * float(cfg().get("max_run_minutes") or 30):
                if not r.cancel_requested:
                    log(f"run {r.id} hit max_run_minutes: stopping it")
                    r.error = "stopped: exceeded max_run_minutes"
                    r.cancel_requested = True
                    wb.HOST.stop(r.conv_id)
        if CURRENT[0] is None and QUEUE and not STATE["paused"] and not wb.HOST.runs_active():
            nxt = QUEUE[0]
            if nxt.conv_id and wb.HOST.running(nxt.conv_id):
                return
            QUEUE.pop(0)
            _start(nxt)


def start_dispatcher():
    with LOCK:
        t = STATE["dispatcher"]
        if t is not None and t.is_alive():
            return

        def loop():
            while True:
                time.sleep(0.25)
                try:
                    _tick()
                except Exception as e:
                    log(f"dispatcher error: {type(e).__name__}: {e}")
        t = threading.Thread(target=loop, daemon=True, name="api-dispatcher")
        STATE["dispatcher"] = t
        t.start()


def set_paused(p):
    STATE["paused"] = bool(p)
    log("paused" if p else "resumed")


def status():
    with LOCK:
        cur = CURRENT[0]
        busy = wb.HOST.runs_active()
        title = ""
        if busy:
            for hr in list(wb.HOST.runs.values()):
                if hr.running():
                    title = hr.conv.get("title", "")
                    break
    h = cfg()
    return {"ok": True, "version": VERSION, "resident": bool(getattr(wb, "RESIDENT", False)),
            "paused": STATE["paused"], "busy": busy, "busy_title": title, "queued": len(QUEUE),
            "current_run": cur.id if cur else None, "port": wb.PORT, "lan": bool(h.get("lan")),
            "api_enabled": bool(h.get("api_enabled")), "mcp_enabled": bool(h.get("mcp_enabled"))}


async def wait_finished(r, timeout):
    """Wait (without holding a thread) until the run finishes or `timeout` seconds pass."""
    end = time.time() + max(0.0, min(float(timeout or 0), 600.0))
    while not r.finished and time.time() < end:
        await asyncio.sleep(0.25)
    return r.finished


# ============================================================================ middleware: auth, origin, LAN, rate limit
def _is_loopback(req):
    ip = (req.remote or "")
    return ip in LOOPBACK or ip.startswith("127.") or ip.startswith("::ffff:127.")


def _origin_ok(req):
    o = req.headers.get("Origin")
    if not o or o == "null":
        return not o
    return (urlparse(o).hostname or "") in LOOPBACK


UI_DEV_PORTS = (5173, 5174, 5180, 5181)        # vite dev server and replay/preview (ui/vite.config.ts)


def _ui_origin_ok(req):
    """The app's own pages only: no Origin (same-origin GETs, the Qt window's navigations, non-browser clients), or a
    loopback origin on the app's port or the UI dev-server ports. Pages served by the agent's own test servers
    (localhost:8790…) and every other site are refused."""
    o = req.headers.get("Origin")
    if not o:
        return True
    if o == "null":
        return False
    u = urlparse(o)
    if (u.hostname or "") not in LOOPBACK:
        return False
    port = u.port or (443 if u.scheme == "https" else 80)
    return port == int(getattr(wb, "PORT", 8765)) or port in UI_DEV_PORTS


_HITS = collections.defaultdict(collections.deque)


def _rate_ok(key, limit):
    now = time.time()
    q = _HITS[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return False, int(60 - (now - q[0])) + 1
    q.append(now)
    return True, 0


def _err(status, msg, headers=None):
    return web.json_response({"error": msg}, status=status, headers=headers)


def check_token(req):
    tok = cfg().get("token") or ""
    got = req.headers.get("Authorization", "")
    if got.lower().startswith("bearer "):
        got = got[7:].strip()
    else:
        got = req.headers.get("X-API-Key", "")
    return bool(tok) and hmac.compare_digest(got.encode(), tok.encode())


@web.middleware
async def middleware(req, handler):
    path = req.path
    api = path == "/v1" or path.startswith("/v1/")
    mcp = path == "/mcp" or path.startswith("/mcp/")
    if not (api or mcp):
        if not _is_loopback(req):                      # the UI and /api/* never answer other devices
            return _err(403, "the app UI is only available on this computer")
        if not _ui_origin_ok(req):
            # a web page in the user's normal browser could otherwise open ws://127.0.0.1:8765/ws (websockets
            # ignore CORS: chats, settings, token) or fire "simple" POSTs at /api/send (agent runs with a shell)
            return _err(403, "cross-origin requests to the app are not allowed")
        return await handler(req)
    t0 = time.time()
    resp = await _guarded(req, handler, api, mcp)
    if path != "/v1/health":
        log(f"{req.method} {path} {resp.status} {int((time.time() - t0) * 1000)}ms {req.remote}")
    return resp


async def _guarded(req, handler, api, mcp):
    h = cfg()
    if not _origin_ok(req):
        return _err(403, "cross-origin requests are not allowed")
    if req.path == "/v1/health":
        return await handler(req)
    if (api and not h.get("api_enabled")) or (mcp and not h.get("mcp_enabled")):
        return _err(403, "disabled in Settings → Headless")
    if not check_token(req):
        return _err(401, "missing or wrong API token", {"WWW-Authenticate": "Bearer"})
    ok, retry = _rate_ok(req.remote or "?", int(h.get("rate_limit") or 120))
    if not ok:
        return _err(429, "rate limit exceeded", {"Retry-After": str(retry)})
    try:
        return await handler(req)
    except web.HTTPException:
        raise
    except Exception as e:
        log(f"error in {req.path}: {type(e).__name__}: {e}")
        return _err(500, f"{type(e).__name__}: {e}")


# ============================================================================ REST routes
routes = web.RouteTableDef()


def J(obj, status=200):
    return web.json_response(obj, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


async def _body(req):
    try:
        b = await req.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"error": "body must be JSON"}), content_type="application/json")
    if not isinstance(b, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "body must be a JSON object"}), content_type="application/json")
    return b


def _run_or_404(req):
    r = RUNS.get(req.match_info["rid"])
    if r is None:
        raise web.HTTPNotFound(text=json.dumps({"error": "no such run"}), content_type="application/json")
    return r


@routes.get("/v1/health")
async def v1_health(req):
    return J(status())


@routes.post("/v1/runs")
async def v1_submit(req):
    b = await _body(req)
    text = b.get("input", b.get("task", ""))
    if not isinstance(text, str):
        return J({"error": "input must be a string"}, 400)
    try:
        r = submit(text, b.get("conversation_id") or None, b.get("title"), bool(b.get("read_only")))
    except ValueError as e:
        return J({"error": str(e)}, 400)
    except KeyError:
        return J({"error": "no such conversation"}, 404)
    except OverflowError as e:
        return J({"error": str(e)}, 409)
    wait = float(b.get("wait") or 0)
    if wait > 0:
        await wait_finished(r, wait)
        return J({"run": r.public()}, 200 if r.finished else 202)
    return J({"run": r.public()}, 202)


@routes.get("/v1/runs")
async def v1_runs(req):
    n = max(1, min(int(req.query.get("limit") or 50), KEEP_RUNS))
    return J({"runs": [r.public() for r in reversed(list(RUNS.values()))][:n]})


@routes.get("/v1/runs/{rid}")
async def v1_run(req):
    r = _run_or_404(req)
    wait = float(req.query.get("wait") or 0)
    if wait > 0:
        await wait_finished(r, wait)
    return J({"run": r.public()})


@routes.post("/v1/runs/{rid}/cancel")
async def v1_cancel(req):
    return J({"run": cancel(_run_or_404(req)).public()})


@routes.get("/v1/runs/{rid}/events")
async def v1_events(req):
    r = _run_or_404(req)
    since = int(req.query.get("since") or 0)
    end = time.time() + max(0.0, min(float(req.query.get("wait") or 0), 60.0))
    while r.seq <= since and not r.finished and time.time() < end:
        await asyncio.sleep(0.2)
    ev = [e for e in r.events if e["seq"] > since][:2000]
    return J({"events": ev, "next": ev[-1]["seq"] if ev else since, "status": r.status})


@routes.get("/v1/runs/{rid}/stream")
async def v1_stream(req):
    r = _run_or_404(req)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(req)
    sent = int(req.headers.get("Last-Event-ID") or req.query.get("since") or 0)
    beat = time.time()
    try:
        while True:
            ev = [e for e in r.events if e["seq"] > sent]
            for e in ev:
                await resp.write(f"id: {e['seq']}\nevent: {e['kind']}\ndata: {json.dumps(e['data'], ensure_ascii=False)}\n\n"
                                 .encode("utf-8"))
                sent = e["seq"]
            if r.finished and sent >= r.seq:
                break
            if time.time() - beat > 15:
                await resp.write(b": keep-alive\n\n")
                beat = time.time()
            await asyncio.sleep(0.2)
    except (ConnectionResetError, asyncio.CancelledError):
        return resp
    await resp.write_eof()
    return resp


def conv_summary(c):
    return {"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated"),
            "running": wb.HOST.running(c["id"]), "source": c.get("source") or "app", "mode": c.get("mode", "")}


def conv_messages(c, last=0):
    with wb.STORE.lock:
        msgs = [{"role": m.get("role"), "content": m.get("content", ""), **({"kind": m["kind"]} if m.get("kind") else {})}
                for m in c.get("messages") or []
                if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
                and m.get("kind") in (None, "", "draft")]
    return msgs[-last:] if last else msgs


@routes.get("/v1/conversations")
async def v1_convs(req):
    n = max(1, min(int(req.query.get("limit") or 50), 500))
    with wb.STORE.lock:
        convs = [c for c in wb.STORE.convs if not c.get("parent")][:n]
    return J({"conversations": [conv_summary(c) for c in convs]})


@routes.get("/v1/conversations/{cid}")
async def v1_conv(req):
    c = wb.STORE.get(req.match_info["cid"])
    if c is None:
        return J({"error": "no such conversation"}, 404)
    return J({**conv_summary(c), "messages": conv_messages(c, int(req.query.get("last") or 0))})


# ============================================================================ UI-side routes (loopback, no token)
@routes.get("/api/headless/status")
async def ui_status(req):
    import autostart
    st = status()
    st["autostart_registered"] = autostart.is_registered()
    st["base_url"] = f"http://127.0.0.1:{wb.PORT}"
    st["stdio_command"] = autostart.stdio_command()
    return J(st)


@routes.post("/api/headless/token")
async def ui_new_token(req):
    with wb.STORE.lock:
        wb.STORE.settings.setdefault("headless", {})["token"] = ""
    tok = ensure_token()
    wb.BUS.send({"t": "settings", "settings": wb.STORE.settings})
    return J({"ok": True, "token": tok, "settings": wb.STORE.settings})


@routes.post("/api/headless/pause")
async def ui_pause(req):
    b = await _body(req)
    set_paused(bool(b.get("paused")))
    return J(status())


def on_settings_saved():
    """After PUT /api/settings: make the Startup shortcut match the setting."""
    import autostart
    want = bool(cfg().get("autostart"))
    try:
        if want != autostart.is_registered():
            autostart.register() if want else autostart.unregister()
            log(f"start at sign-in {'on' if want else 'off'}")
    except Exception as e:
        log(f"autostart change failed: {e}")
        return str(e)
    return ""


def install(app):
    """Called by web_backend.make_app."""
    import harness_mcp
    app.middlewares.append(middleware)
    app.add_routes(routes)
    harness_mcp.install(app)
    ensure_token()
    start_dispatcher()
