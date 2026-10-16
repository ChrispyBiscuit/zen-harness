"""First-run setup for this PC (plans/hardware-scaling.md): nothing heavy ships in the installer.

  GET  /api/hardware          GPUs, RAM, the runtime order, the plan for the model, what setup would download
  POST /api/hardware/setup    one background job: the GPU runtime (checked against the GPU, next one on failure),
                              the model file the plan picked, its vision file, and offline Wikipedia (optional)
  GET  /api/hardware/setup    the job's progress
  POST /api/hardware/cancel   stop it (downloads resume where they stopped)

Every file is checked against a published SHA-256 before it is used.
"""
import asyncio
import hashlib
import os
import sys
import threading
import time
import urllib.request
import zipfile

from aiohttp import web

import paths

routes = web.RouteTableDef()
MODEL = "ternary-bonsai-2-27b"
WIKI = {"file": "wikipedia_en_all_mini_2026-09.zim", "bytes": 14386799064,
        "url": "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_mini_2026-09.zim",
        "sha256": "350d8956ee3d7abe2839c464c3a983f93d6934a16c7f04b10715b49439dfd349"}
JOB = {"status": "idle"}
_lock = threading.Lock()


class Cancelled(Exception):
    pass


def fetch(url, dest, size, sha, progress=lambda done, total: None, cancel=lambda: False):
    """Download to dest (resuming a .part file), check size and SHA-256, then move into place."""
    if os.path.isfile(dest) and os.path.getsize(dest) == size:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    if have < size:
        req = urllib.request.Request(url, headers={"User-Agent": "Zazen", **({"Range": f"bytes={have}-"} if have else {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0                              # the server ignored the range: start over
            with open(part, "ab" if have else "wb") as f:
                done = have
                while True:
                    if cancel():
                        raise Cancelled()
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    progress(done, size)
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            if cancel():
                raise Cancelled()
            h.update(chunk)
    if os.path.getsize(part) != size or h.hexdigest() != sha:
        os.remove(part)
        raise RuntimeError(f"{os.path.basename(dest)} did not match its published checksum: the download was damaged. "
                           f"Run setup again to fetch it fresh.")
    os.replace(part, dest)
    return dest


def _profile():
    from agent_config import model_profile
    try:
        import web_backend as wb
        ag = dict(wb.STORE.settings.get("agent") or {})
    except Exception:
        ag = {}
    return model_profile(ag, MODEL), dict(ag.get("hardware") or {})


def existing(prof):
    """A CUDA build the profile already points at (an existing install of PrismML's fork): no download needed."""
    own = paths.resolve_llama_dir(prof.get("llama_dir"))
    return {"cuda": own} if own and os.path.isfile(os.path.join(own, "llama-server.exe")) else {}


def report():
    """What this PC has and what setup would do."""
    import hardware
    import runtimes
    prof, hw = _profile()
    gpus = hardware.gpus(refresh=True)
    gpu = hardware.primary(hw)
    order = hardware.backend_order(gpu, hw)
    p = hardware.plan(prof, gpu=gpu, backend=order[0] if gpu else "cpu")
    want = p.get("model_file") if p.get("model_file") and not p.get("better_file") else p.get("better_file") \
        or p.get("model_file")
    var = next((v for v in prof.get("variants") or [] if v["file"] == want), {})
    need = []
    if order[0] != "cpu" and not runtimes.installed(order[0]) and order[0] not in existing(prof):
        need.append({"kind": "runtime", "name": f"llama.cpp for {order[0].upper()}", "mib": runtimes.download_mib(order[0])})
    if var and not os.path.isfile(paths.resolve_model(var["file"])):
        need.append({"kind": "model", "name": var["file"], "mib": var["bytes"] // 1048576})
    mm = prof.get("mmproj_download") or {}
    if mm and not os.path.isfile(paths.resolve_model(prof.get("mmproj"))):
        need.append({"kind": "vision", "name": prof.get("mmproj"), "mib": mm["bytes"] // 1048576})
    wiki = os.path.isfile(os.path.join(paths.knowledge_dir(), WIKI["file"]))
    return {"gpus": gpus, "gpu": gpu, "ram_mib": hardware.system_ram_mib(), "order": order,
            "supported": bool(gpu and gpu["vram_mib"] >= hardware.MIN_VRAM_MIB),
            "runtimes": {b: runtimes.installed(b) for b in runtimes.ASSETS}, "plan": p, "downloads": need,
            "wiki": {"installed": wiki, "mib": WIKI["bytes"] // 1048576, "file": WIKI["file"]},
            "settings": hw, "job": dict(JOB)}


def _step(name, done=0, total=0):
    JOB.update(step=name, done=done, total=total, t=time.time())


def _libzim(cancel):
    """The offline Wikipedia reader (libzim, GPL-3.0: not in the installer). Installed builds get the wheel for their
    Python into <data>/pylibs; a source checkout uses its own venv's copy when it has one."""
    try:
        import libzim  # noqa: F401
        return
    except ImportError:
        pass
    import json
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    meta = json.load(urllib.request.urlopen("https://pypi.org/pypi/libzim/json", timeout=30))
    wheel = next((u for u in meta["urls"] if f"-{tag}-{tag}-win_amd64.whl" in u["filename"]), None)
    if not wheel:
        raise RuntimeError(f"no offline-Wikipedia reader (libzim) for Python {tag}")
    dest = paths.data("pylibs")
    w = fetch(wheel["url"], os.path.join(paths.data("downloads"), wheel["filename"]), wheel["size"],
              wheel["digests"]["sha256"], cancel=cancel)
    with zipfile.ZipFile(w) as zf:
        zf.extractall(dest)
    if dest not in sys.path:
        sys.path.insert(0, dest)


def run_setup(wiki=True, backend=None):
    import hardware
    import runtimes
    cancel = lambda: JOB.get("cancel", False)
    try:
        prof, hw = _profile()
        gpu = hardware.primary(hw)
        order = hardware.backend_order(gpu, dict(hw, backend=backend) if backend else hw)
        _step("runtime")
        chosen, exe, dev, notes = runtimes.choose(gpu, order, progress=lambda b, d, t: _step(f"runtime:{b}", d, t),
                                                  existing=existing(prof))
        if not chosen:
            raise RuntimeError("no llama.cpp runtime could use this GPU: " + "; ".join(notes))
        JOB.update(backend=chosen, device=(dev or {}).get("name"), notes=notes)
        p = hardware.plan(prof, gpu=gpu, backend=chosen)
        want = p.get("better_file") or p.get("model_file")
        var = next((v for v in prof.get("variants") or [] if v["file"] == want), None)
        if var:
            _step("model", 0, var["bytes"])
            fetch(f"https://huggingface.co/{var['repo']}/resolve/main/{var['file']}",
                  paths.resolve_model(var["file"]), var["bytes"], var["sha256"],
                  progress=lambda d, t: _step("model", d, t), cancel=cancel)
        mm = prof.get("mmproj_download") or {}
        if mm:
            _step("vision", 0, mm["bytes"])
            fetch(f"https://huggingface.co/{mm['repo']}/resolve/main/{prof['mmproj']}", paths.resolve_model(prof["mmproj"]),
                  mm["bytes"], mm["sha256"], progress=lambda d, t: _step("vision", d, t), cancel=cancel)
        _save(chosen)
        if wiki:
            _step("wiki", 0, WIKI["bytes"])
            _libzim(cancel)
            fetch(WIKI["url"], os.path.join(paths.knowledge_dir(), WIKI["file"]), WIKI["bytes"], WIKI["sha256"],
                  progress=lambda d, t: _step("wiki", d, t), cancel=cancel)
        JOB.update(status="done", step="", finished=time.time())
    except Cancelled:
        JOB.update(status="cancelled")
    except Exception as e:
        JOB.update(status="failed", error=str(e)[:400])


def _save(backend):
    """Make the 27B the model, under automatic hardware settings, with the runtime that worked."""
    try:
        import web_backend as wb
    except Exception:
        return
    with wb.STORE.lock:
        ag = wb.STORE.settings.setdefault("agent", {})
        hw = dict(ag.get("hardware") or {})
        hw.setdefault("auto", True)
        if str(hw.get("backend") or "auto") == "auto":
            hw["detected_backend"] = backend          # for the Hardware panel; "auto" still decides at each start
        ag["hardware"] = hw
        if not ag.get("model"):
            ag["model"], ag["backend"] = MODEL, "llama"
    wb.STORE.save_settings()
    wb.BUS.send({"t": "settings", "settings": wb.STORE.settings})


def start(wiki=True, backend=None):
    with _lock:
        if JOB.get("status") == "running":
            return False
        JOB.clear()
        JOB.update(status="running", started=time.time(), wiki=bool(wiki))
    threading.Thread(target=run_setup, args=(wiki, backend), daemon=True, name="hardware-setup").start()
    return True


def add_pylibs():
    """Make a wheel unpacked by setup (libzim) importable: called at startup."""
    d = paths.data("pylibs")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)


@routes.get("/api/hardware")
async def api_report(req):
    return web.json_response(await asyncio.to_thread(report))


@routes.post("/api/hardware/setup")
async def api_setup(req):
    try:
        b = await req.json()
    except Exception:
        b = {}
    backend = str(b.get("backend") or "").lower() or None
    ok = start(wiki=b.get("wiki", True) is not False, backend=backend if backend not in (None, "auto") else None)
    return web.json_response({"ok": ok, "job": dict(JOB)}, status=200 if ok else 409)


@routes.get("/api/hardware/setup")
async def api_job(req):
    return web.json_response({"job": dict(JOB)})


@routes.post("/api/hardware/cancel")
async def api_cancel(req):
    JOB["cancel"] = True
    return web.json_response({"ok": True})


def install(app):
    add_pylibs()
    app.add_routes(routes)
