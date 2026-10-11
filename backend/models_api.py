"""Settings → Models: list, inspect, propose a profile, test-load, save, download (alpha release plan, phase C3).

  GET  /api/models/list                 models dir files (with GGUF metadata) + saved profiles + hardware
  POST /api/models/inspect   {path}     metadata + a proposed profile for this PC (llama_local.fit_context)
  POST /api/models/save      {name, profile, make_default?}
  POST /api/models/delete    {name}     removes a profile (never the file)
  POST /api/models/test      {profile}  loads it on a spare slot, generates a few tokens, reports speed/memory, stops
  POST /api/models/pick                 native "open file" dialog for a .gguf -> {path}
  POST /api/models/download  {url}      starts a resumable download into the models dir
  GET  /api/models/downloads            progress of downloads
"""
import asyncio
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from aiohttp import web

import paths

routes = web.RouteTableDef()
HELPER_RX = re.compile(r"(^mtp-|mmproj)", re.I)
QUANT_RX = re.compile(r"(IQ\d_[A-Z]+|Q\d_K_[A-Z]+|Q\d_K|Q\d_\d|BF16|F16|F32|MXFP4|Q8_0)", re.I)


def _store():
    import web_backend as wb
    return wb


def _gguf_files(folder):
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            if fn.lower().endswith(".gguf"):
                out.append(os.path.join(dp, fn))
        if dp.count(os.sep) - folder.count(os.sep) >= 2:          # models\, models\<org>\, models\<org>\<x>\
            dns[:] = []
    return sorted(out)


def describe(path):
    import llama_local
    meta = {}
    try:
        meta = llama_local.gguf_meta(path)
    except Exception as e:
        meta = {"error": str(e)}
    name = os.path.basename(path)
    q = QUANT_RX.search(name)
    return {"path": path, "name": name, "size": os.path.getsize(path), "quant": q.group(1).upper() if q else "",
            "architecture": meta.get("architecture") or meta.get("general.architecture") or "",
            "layers": next((v for k, v in meta.items() if k.endswith("block_count")), None),
            "experts": next((v for k, v in meta.items() if k.endswith("expert_count") and "used" not in k), None),
            "helper": bool(HELPER_RX.search(name)),
            "kind": "drafter" if name.lower().startswith("mtp-") else "vision" if "mmproj" in name.lower() else "model"}


def helpers_for(path):
    """mmproj (vision) and MTP drafter files that belong to this model: same folder (or the models dir)."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    key = re.split(r"[-_.](?:i?q\d|bf16|f16|mxfp4)", stem)[0][:12]
    out = {"mmproj": None, "drafter": None}
    for d in {os.path.dirname(path), paths.models_dir(), os.path.join(paths.models_dir(), "mmproj")}:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            low = fn.lower()
            if not low.endswith(".gguf"):
                continue
            if "mmproj" in low and key[:8] in low.replace("mmproj-", "") and not out["mmproj"]:
                out["mmproj"] = os.path.join(d, fn)
            if low.startswith("mtp-") and key[:8] in low and not out["drafter"]:
                out["drafter"] = os.path.join(d, fn)
    return out


def propose(path, want_ctx=None):
    """A profile for this model on this PC: context/KV that fit, MoE offload, helper files."""
    import llama_local
    gpu = llama_local.gpu_total_mib()
    want = int(want_ctx or (65536 if gpu else 16384))
    fit = llama_local.fit_context(path, want, "auto")
    info = describe(path)
    prof = {"model_file": path, "num_ctx": int(fit.get("ctx") or want), "kv_cache": fit.get("kv") or "q8_0",
            "cache_ram_mib": 1024, "ctx_checkpoints": 4}
    if fit.get("n_cpu_moe") is not None:
        prof["n_cpu_moe"] = int(fit["n_cpu_moe"])
    h = helpers_for(path)
    if h["mmproj"]:
        prof["mmproj"] = h["mmproj"]
    if h["drafter"]:
        prof["spec_draft_model"] = h["drafter"]
        prof["spec_ngram"] = "draft-mtp,ngram-simple"
    else:
        prof["spec_ngram"] = "ngram-simple"
    name = re.sub(r"[^a-z0-9.:-]+", "-", os.path.splitext(os.path.basename(path))[0].lower()).strip("-")
    notes = [n for n in (fit.get("note"), fit.get("ram_note")) if n]
    if not gpu:
        notes.append("No NVIDIA GPU detected: the model will run on the CPU (slow). Prefer a small model.")
    return {"name": name, "profile": prof, "fit": fit, "info": info, "helpers": h, "notes": notes, "gpu_mib": gpu}


def server_args(prof):
    """llama-server flags for a profile (the subset that decides whether and how it loads)."""
    args = ["--jinja", "-cram", str(int(prof.get("cache_ram_mib") or 1024)),
            "-ctxcp", str(int(prof.get("ctx_checkpoints") or 4))]
    kv = str(prof.get("kv_cache") or "f16")
    if kv not in ("f16", "auto"):
        args += ["-ctk", kv, "-ctv", str(prof.get("kv_cache_v") or kv)]
    if prof.get("n_cpu_moe") is not None:
        args += ["--n-cpu-moe", str(int(prof["n_cpu_moe"]))]
    if prof.get("ubatch"):
        args += ["-ub", str(int(prof["ubatch"]))]
    return args


def test_load(prof):
    import llama_local
    import help_api
    path = paths.resolve_model(prof.get("model_file") or "")
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": f"model file not found: {prof.get('model_file')}"}
    before = (help_api.gpu_info() or {}).get("vram_used_mib")
    t0 = time.time()
    try:
        llama_local.stop("main")                   # one GPU: the chat model makes room for the test
        base = llama_local.ensure(path, parallel=1, num_ctx=int(prof.get("num_ctx") or 8192),
                                  extra_args=server_args(prof), slot="test")
        load_s = time.time() - t0
        body = json.dumps({"prompt": "Write one sentence about the sea.", "n_predict": 48,
                           "temperature": 0.7}).encode()
        req = urllib.request.Request(base + "/completion", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.load(r)
        during = (help_api.gpu_info() or {}).get("vram_used_mib")
        t = out.get("timings") or {}
        return {"ok": True, "load_s": round(load_s, 1), "tokens_per_s": round(float(t.get("predicted_per_second") or 0), 1),
                "prompt_tokens_per_s": round(float(t.get("prompt_per_second") or 0), 1),
                "vram_mib": (during - before) if (during and before is not None) else None,
                "sample": (out.get("content") or "").strip()[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:600], "load_s": round(time.time() - t0, 1)}
    finally:
        try:
            llama_local.stop("test")
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------------- downloads
DOWNLOADS = {}          # id -> {url, path, total, done, status, error, started}
_dl_lock = threading.Lock()


def _download(did, url, dest):
    st = DOWNLOADS[did]
    part = dest + ".part"
    try:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "ZenHarness", **({"Range": f"bytes={have}-"} if have else {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0                                  # server ignored the range: start over
            total = int(r.headers.get("Content-Length") or 0) + have
            st.update(total=total, done=have, status="downloading")
            with open(part, "ab" if have else "wb") as f:
                while True:
                    if st.get("cancel"):
                        st["status"] = "paused"
                        return
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    st["done"] += len(chunk)
        with open(part, "rb") as f:
            if f.read(4) != b"GGUF":
                raise ValueError("the downloaded file is not a GGUF model")
        os.replace(part, dest)
        if st.get("auto_profile"):
            st["status"] = "setting up"
            auto_setup(dest, make_default=bool(st.get("make_default")))
        st["status"] = "done"
    except Exception as e:
        st.update(status="failed", error=str(e)[:300])


def auto_setup(path, make_default=False):
    """After a catalog download: save the proposed profile (and make it the default on first run)."""
    wb = _store()
    p = propose(path)
    with wb.STORE.lock:
        ag = wb.STORE.settings.setdefault("agent", {})
        ov = dict(ag.get("model_overrides") or {})
        prof = dict(p["profile"])
        if os.path.dirname(prof["model_file"]).lower() == paths.models_dir().lower():
            prof["model_file"] = os.path.basename(prof["model_file"])      # portable: relative to the models dir
        ov[p["name"]] = prof
        ag["model_overrides"] = ov
        if make_default or not ag.get("model"):
            ag["model"], ag["backend"] = p["name"], "llama"
    wb.STORE.save_settings()
    wb.BUS.send({"t": "settings", "settings": wb.STORE.settings})
    return p["name"]


def catalog():
    try:
        with open(paths.resource("model_catalog.json"), encoding="utf-8") as f:
            items = json.load(f).get("models") or []
    except (OSError, ValueError):
        return []
    import llama_local
    gpu, ram = llama_local.gpu_total_mib() or 0, (llama_local.system_ram_mib() or (0, 0))[0]
    out = []
    for m in items:
        m = dict(m)
        m["url"] = f"https://huggingface.co/{m['repo']}/resolve/main/{m['file']}"
        m["installed"] = os.path.isfile(os.path.join(paths.models_dir(), m["file"]))
        m["fits"] = gpu >= int(m.get("min_vram_mib") or 0) and (not ram or ram >= int(m.get("min_ram_mib") or 0))
        out.append(m)
    # the recommendation for this PC: the first that fits, in the catalog's order of preference
    pref = ["gpu8", "big", "big16", "small"] if gpu >= 7000 else ["small"]
    for tag in pref:
        hit = next((m for m in out if m.get("recommended_for") == tag and m["fits"]), None)
        if hit:
            hit["recommended"] = True
            break
    return out


def install_from_catalog(mid, make_default=True):
    m = next((x for x in catalog() if x["id"] == mid), None)
    if not m:
        raise ValueError("unknown catalog model")
    ids = []
    for extra in m.get("extras") or []:                  # helper files first (small): vision projector
        try:
            ids.append(start_download(f"https://huggingface.co/{m['repo']}/resolve/main/{extra['file']}"))
        except ValueError:
            pass                                          # already there
    if m["installed"]:
        auto_setup(os.path.join(paths.models_dir(), m["file"]), make_default=make_default)
        return ids
    did = start_download(m["url"])
    DOWNLOADS[did].update(auto_profile=True, make_default=make_default)
    return ids + [did]


def start_download(url):
    if not re.match(r"^https://", url or ""):
        raise ValueError("use an https:// link to a .gguf file")
    url = url.replace("/blob/", "/resolve/")             # a Hugging Face page link -> the file itself
    name = os.path.basename(url.split("?")[0])
    if not name.lower().endswith(".gguf") or not re.match(r"^[\w.+-]+$", name):
        raise ValueError("the link must point to a .gguf file")
    os.makedirs(paths.models_dir(), exist_ok=True)
    dest = os.path.join(paths.models_dir(), name)
    if os.path.exists(dest):
        raise ValueError(f"{name} is already in the models folder")
    did = name
    with _dl_lock:
        cur = DOWNLOADS.get(did)
        if cur and cur.get("status") == "downloading":
            return did
        DOWNLOADS[did] = {"id": did, "url": url, "path": dest, "total": 0, "done": 0, "status": "starting",
                          "started": time.time()}
    threading.Thread(target=_download, args=(did, url, dest), daemon=True).start()
    return did


def pick_file():
    """A native Windows 'Open' dialog (the UI is a web page and can't see file paths)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; "
          "$d.Filter = 'GGUF models (*.gguf)|*.gguf'; $d.Title = 'Add a model'; "
          "if ($d.ShowDialog() -eq 'OK') { [Console]::Out.Write($d.FileName) }")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                       timeout=600, creationflags=0x08000000)
    return r.stdout.strip()


# ---------------------------------------------------------------------------------------------------- routes
@routes.get("/api/models/list")
async def api_list(req):
    wb = _store()
    import llama_local

    def build():
        files = [describe(p) for p in _gguf_files(paths.models_dir())] if os.path.isdir(paths.models_dir()) else []
        ag = wb.STORE.settings.get("agent") or {}
        profs = {}
        for name, p in (ag.get("model_overrides") or {}).items():
            mf = paths.resolve_model(p.get("model_file") or "")
            profs[name] = {"profile": p, "exists": bool(mf and os.path.isfile(mf)), "path": mf}
        return {"models_dir": paths.models_dir(), "files": files, "profiles": profs, "default": ag.get("model") or "",
                "gpu_mib": llama_local.gpu_total_mib(), "ram_mib": (llama_local.system_ram_mib() or (0, 0))[0]}
    return web.json_response(await asyncio.to_thread(build))


@routes.post("/api/models/inspect")
async def api_inspect(req):
    b = await req.json()
    p = str(b.get("path") or "")
    if not p.lower().endswith(".gguf") or not os.path.isfile(p):
        return web.json_response({"error": "pick an existing .gguf file"}, status=400)
    try:
        return web.json_response(await asyncio.to_thread(propose, p, b.get("ctx")))
    except Exception as e:
        return web.json_response({"error": f"could not read the model: {e}"}, status=400)


@routes.post("/api/models/save")
async def api_save(req):
    wb = _store()
    b = await req.json()
    name = re.sub(r"[^A-Za-z0-9.:_-]+", "-", str(b.get("name") or "")).strip("-")[:80]
    prof = b.get("profile") or {}
    if not name or not isinstance(prof, dict) or not prof.get("model_file"):
        return web.json_response({"error": "a profile needs a name and a model file"}, status=400)
    with wb.STORE.lock:
        ag = wb.STORE.settings.setdefault("agent", {})
        ov = dict(ag.get("model_overrides") or {})
        ov[name] = {k: v for k, v in prof.items() if v not in (None, "")}
        ag["model_overrides"] = ov
        if b.get("make_default"):
            ag["model"] = name
            ag["backend"] = "llama"
    wb.STORE.save_settings()
    wb.BUS.send({"t": "settings", "settings": wb.STORE.settings})
    return web.json_response({"ok": True, "name": name})


@routes.post("/api/models/delete")
async def api_delete(req):
    wb = _store()
    name = str((await req.json()).get("name") or "")
    with wb.STORE.lock:
        ag = wb.STORE.settings.setdefault("agent", {})
        ov = dict(ag.get("model_overrides") or {})
        if name not in ov:
            return web.json_response({"error": "no such profile"}, status=404)
        ov.pop(name)
        ag["model_overrides"] = ov
        if ag.get("model") == name:
            ag["model"] = ""
    wb.STORE.save_settings()
    wb.BUS.send({"t": "settings", "settings": wb.STORE.settings})
    return web.json_response({"ok": True})


@routes.post("/api/models/test")
async def api_test(req):
    wb = _store()
    if wb.HOST.runs_active():
        return web.json_response({"ok": False, "error": "a chat is running — stop it or wait, then test"}, status=409)
    prof = (await req.json()).get("profile") or {}
    return web.json_response(await asyncio.to_thread(test_load, prof))


@routes.post("/api/models/pick")
async def api_pick(req):
    return web.json_response({"path": await asyncio.to_thread(pick_file)})


@routes.post("/api/models/download")
async def api_download(req):
    b = await req.json()
    try:
        return web.json_response({"ok": True, "id": start_download(str(b.get("url") or "").strip())})
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


@routes.post("/api/models/download/cancel")
async def api_download_cancel(req):
    d = DOWNLOADS.get(str((await req.json()).get("id") or ""))
    if d:
        d["cancel"] = True
    return web.json_response({"ok": bool(d)})


@routes.get("/api/models/catalog")
async def api_catalog(req):
    return web.json_response({"models": await asyncio.to_thread(catalog)})


@routes.post("/api/models/catalog/install")
async def api_catalog_install(req):
    b = await req.json()
    try:
        ids = await asyncio.to_thread(install_from_catalog, str(b.get("id") or ""), b.get("make_default", True) is not False)
        return web.json_response({"ok": True, "downloads": ids})
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


@routes.get("/api/models/downloads")
async def api_downloads(req):
    return web.json_response({"downloads": [{k: v for k, v in d.items() if k != "cancel"} for d in DOWNLOADS.values()]})


def install(app):
    app.add_routes(routes)
