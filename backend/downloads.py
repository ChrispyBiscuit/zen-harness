"""Every download the app makes on its own asks first. Backend code that needs something fetched (the speech model on
first dictation, the memory's embedding model) calls ask(); the UI shows each request in an Accept / Decline modal
(components/ConsentDialog.tsx, polling /api/downloads), and the caller goes on only when the user accepts. While an
accepted download runs, track() reports its progress from the bytes arriving on disk, so the modal and the status line
can show a real bar even when the library doing the download has no progress hook.

Downloads the user starts from the UI (models, updates) are confirmed in the UI before the request is sent; they are not
routed through here. A declined request is remembered for this session, so the app does not nag.

  ask(kind, title, detail, size, source, timeout) -> bool    blocks until answered (or timeout -> declined)
  track(rid, folder, expected_bytes)                          progress from a folder's growth, until finish()
  finish(rid, ok, error=None)
"""
import os
import threading
import time
import uuid

REQUESTS = {}            # id -> {id, kind, title, detail, size, source, status, done, total, error, asked}
_events = {}
_lock = threading.Lock()
_declined = set()        # kinds declined this session


def ask(kind, title, detail="", size=0, source="", timeout=900):
    """Ask the user (via the UI) to allow a download. -> True when accepted; also returns the request id."""
    with _lock:
        if kind in _declined:
            return False, None
        for r in REQUESTS.values():            # the same thing asked twice (two threads): share the one question
            if r["kind"] == kind and r["status"] == "asking":
                rid = r["id"]
                break
        else:
            rid = uuid.uuid4().hex[:10]
            REQUESTS[rid] = {"id": rid, "kind": kind, "title": title, "detail": detail, "size": int(size or 0),
                             "source": source, "status": "asking", "done": 0, "total": int(size or 0), "error": None,
                             "asked": time.time()}
            _events[rid] = threading.Event()
        ev = _events[rid]
    ev.wait(timeout)
    with _lock:
        r = REQUESTS[rid]
        if r["status"] == "asking":            # nobody answered: treat as declined, ask again next session
            r["status"] = "declined"
        if r["status"] == "declined":
            _declined.add(kind)
            return False, rid
        return True, rid


def answer(rid, accept):
    with _lock:
        r = REQUESTS.get(rid)
        if not r or r["status"] != "asking":
            return False
        r["status"] = "downloading" if accept else "declined"
        ev = _events.get(rid)
    if ev:
        ev.set()
    return True


def _folder_bytes(folder):
    n = 0
    for dp, _, files in os.walk(folder):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def track(rid, folder, expected=0):
    """Report progress from `folder` growing towards `expected` bytes, until finish(rid)."""
    if not rid:
        return
    base = _folder_bytes(folder) if os.path.isdir(folder) else 0

    def loop():
        while True:
            r = REQUESTS.get(rid)
            if not r or r["status"] != "downloading":
                return
            now = (_folder_bytes(folder) if os.path.isdir(folder) else 0) - base
            r["done"] = max(r["done"], now)
            if expected:
                r["total"] = max(int(expected), r["done"])
            time.sleep(0.5)
    threading.Thread(target=loop, daemon=True, name=f"dl-track-{rid}").start()


def finish(rid, ok, error=None):
    r = REQUESTS.get(rid)
    if not r:
        return
    r["status"] = "done" if ok else "failed"
    r["error"] = (str(error)[:300] if error else None)
    if ok and r.get("total"):
        r["done"] = r["total"]


def snapshot():
    """For the UI: open questions, and downloads running or finished in the last minute."""
    now = time.time()
    out = []
    for r in list(REQUESTS.values()):
        if r["status"] in ("asking", "downloading") or now - r.get("asked", now) < 60:
            out.append(dict(r))
    return out


def install(app):
    from aiohttp import web

    async def list_(req):
        return web.json_response({"requests": snapshot()})

    async def answer_(req):
        b = await req.json()
        ok = answer(str(b.get("id") or ""), bool(b.get("accept")))
        return web.json_response({"ok": ok})

    app.router.add_get("/api/downloads", list_)
    app.router.add_post("/api/downloads/answer", answer_)
