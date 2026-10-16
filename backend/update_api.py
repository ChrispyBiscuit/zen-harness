"""/api/update/*: the app's side of updates from git (updater.py does the work).

  GET  /api/update/status     what is installed, what is available, what the updater is doing
  POST /api/update/check      fetch now
  POST /api/update/apply      stage + install the latest version, then restart the server (refused while a run is going)
  POST /api/update/rollback   put the previous version back, then restart
  POST /api/update/decline    'Not now' for the latest version: no prompt again until a newer one

The window is not restarted: the page sees the new server (a new `started` in /api/build) and reloads itself.
"""
import asyncio
import os
import threading
import time

from aiohttp import web

import paths
import updater

_ctx = {}                     # busy(), port, before_exit() from web_backend


def J(d, status=200):
    return web.json_response(d, status=status)


async def status(req):
    return J({**updater.status(), "run_busy": bool(_ctx["busy"]())})


async def check(req):
    try:
        return J(await asyncio.to_thread(updater.check))
    except Exception as e:
        return J({**updater.status(), "error": str(e)[:400]}, 502)


def _restart():
    """Save, start the helper that relaunches the server once the port is free, and exit."""
    def bye():
        time.sleep(0.5)                       # let the HTTP reply go out
        try:
            _ctx["before_exit"]()
        finally:
            updater.restart_server(_ctx["port"], paths.role_command("server"), paths.APP_DIR)
            os._exit(0)
    threading.Thread(target=bye, daemon=True).start()


async def apply(req):
    if _ctx["busy"]():
        return J({"ok": False, "error": "an agent run is in progress: install when it finishes"}, 409)
    st = updater.status()
    sha = st.get("latest")
    if not sha:
        return J({"ok": False, "error": "no update has been found yet: check first"}, 409)
    updater.progress_begin()
    try:
        await asyncio.to_thread(updater.fetch_for_install, sha)
        notes = await asyncio.to_thread(updater.stage, sha)
        if _ctx["busy"]():                    # a run started while the new version was being built
            updater.progress_end(False)
            return J({"ok": False, "error": "an agent run started: install when it finishes"}, 409)
        res = await asyncio.to_thread(updater.apply, sha)
        updater.progress_end(True)
    except Exception as e:
        updater.progress_end(False)
        s = updater.load_state()
        s["apply_error"] = str(e)[:400]
        updater.save_state(s)
        return J({"ok": False, "error": str(e)[:400]}, 500)
    _restart()
    return J({"ok": True, "notes": notes, **res, "restarting": True})


async def decline(req):
    b = await req.json()
    sha = str(b.get("sha") or updater.status().get("latest") or "")
    if sha:
        updater.decline(sha)
    return J({"ok": True})


async def rollback(req):
    if _ctx["busy"]():
        return J({"ok": False, "error": "an agent run is in progress"}, 409)
    try:
        sha = await asyncio.to_thread(updater.rollback, "asked")
    except Exception as e:
        return J({"ok": False, "error": str(e)[:400]}, 409)
    _restart()
    return J({"ok": True, "installed": sha, "restarting": True})


def install(app, busy, port, before_exit):
    _ctx.update(busy=busy, port=port, before_exit=before_exit)
    app.router.add_get("/api/update/status", status)
    app.router.add_post("/api/update/check", check)
    app.router.add_post("/api/update/apply", apply)
    app.router.add_post("/api/update/rollback", rollback)
    app.router.add_post("/api/update/decline", decline)

    async def started(_app):
        updater.start_background_checks()

        def verify():                         # up for a minute on a new version: it no longer needs the rollback
            time.sleep(60)
            updater.mark_started()
        threading.Thread(target=verify, daemon=True).start()
    app.on_startup.append(started)
