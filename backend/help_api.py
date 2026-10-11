"""Help, About and diagnostics for the UI (alpha release plan, phase C1).

  GET  /api/help/docs            -> {"pages": [{slug, title}]}     (docs/README.md order)
  GET  /api/help/doc/{slug}      -> {slug, title, markdown}
  GET  /api/help/about           -> version, install/data/models/logs folders, GPU, RAM, llama.cpp found
  POST /api/help/open            -> {"which": "data"|"models"|"logs"|"install"} opens the folder in Explorer
  GET  /api/help/diagnostics     -> {"text": ...} for bug reports (tokens and keys removed)
"""
import os
import platform
import re
import subprocess
import sys

from aiohttp import web

import paths

routes = web.RouteTableDef()
_SLUG = re.compile(r"^[a-z0-9-]{1,60}$")


def docs_dir():
    for d in (paths.resource("docs"), os.path.join(os.path.dirname(paths.APP_DIR), "docs")):
        if os.path.isfile(os.path.join(d, "README.md")):
            return d
    return paths.resource("docs")


def version():
    for p in (paths.resource("VERSION"), os.path.join(os.path.dirname(paths.APP_DIR), "VERSION")):
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return "dev"


def _title(md, fallback):
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return m.group(1).strip() if m else fallback


def doc_pages():
    d = docs_dir()
    try:
        index = open(os.path.join(d, "README.md"), encoding="utf-8").read()
    except OSError:
        return []
    order = re.findall(r"\]\(([a-z0-9-]+)\.md\)", index)
    files = [f[:-3] for f in sorted(os.listdir(d)) if f.endswith(".md") and f != "README.md"]
    slugs = [s for s in order if s in files] + [s for s in files if s not in order]
    out = []
    for s in slugs:
        try:
            md = open(os.path.join(d, s + ".md"), encoding="utf-8").read()
        except OSError:
            continue
        out.append({"slug": s, "title": _title(md, s.replace("-", " ").title())})
    return out


def gpu_info():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                             creationflags=0x08000000).stdout.strip()
        if out:
            name, total, used, driver = [x.strip() for x in out.splitlines()[0].split(",")]
            return {"name": name, "vram_mib": int(float(total)), "vram_used_mib": int(float(used)), "driver": driver}
    except Exception:
        pass
    return None


def ram_mib():
    try:
        import psutil
        return int(psutil.virtual_memory().total / 1048576)
    except Exception:
        return None


def folders():
    return {"install": paths.EXE_DIR, "data": paths.data_dir(), "models": paths.models_dir(),
            "logs": paths.logs_dir(), "llama": paths.llama_dir()}


def about():
    import llama_local
    return {"name": "Zen Harness", "version": version(), "frozen": paths.FROZEN, "folders": folders(),
            "llama_cpp_found": llama_local.available(), "gpu": gpu_info(), "ram_mib": ram_mib(),
            "python": platform.python_version(), "windows": platform.version()}


_SECRET_RX = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|sk-ant-[A-Za-z0-9_-]{12,}|csk-[a-z0-9]{12,}|gsk_[A-Za-z0-9]{12,}|"
                        r"hf_[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{12,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{20,})")


def redact(text, extra=()):
    text = _SECRET_RX.sub("[redacted]", text)
    for s in extra:
        if s and len(s) >= 8:
            text = text.replace(s, "[redacted]")
    return text


def _tail(path, n=120):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def diagnostics(settings):
    a = about()
    ag = (settings or {}).get("agent") or {}
    hl = (settings or {}).get("headless") or {}
    lines = [f"Zen Harness {a['version']} ({'installed' if a['frozen'] else 'dev checkout'})",
             f"Windows {a['windows']} · Python {a['python']} · RAM {a['ram_mib']} MiB",
             f"GPU: {a['gpu'] or 'none detected (CPU only)'}",
             f"llama.cpp found: {a['llama_cpp_found']}",
             "Folders: " + ", ".join(f"{k}={v}" for k, v in a["folders"].items()),
             f"Model: {ag.get('model') or '(none)'} · backend {ag.get('backend')} · profiles: "
             + ", ".join(sorted((ag.get('model_overrides') or {}).keys())),
             f"API: rest={hl.get('api_enabled', True)} mcp={hl.get('mcp_enabled', True)} lan={hl.get('lan', False)}",
             "", "---- app_server.log (tail) ----", _tail(os.path.join(paths.logs_dir(), "app_server.log")),
             "---- model server log (tail) ----", _tail(paths.data("llama_state", "swarm_server.log"), 60)]
    return redact("\n".join(lines), extra=[hl.get("token") or ""])


@routes.get("/api/help/docs")
async def api_docs(req):
    return web.json_response({"pages": doc_pages(), "version": version()})


@routes.get("/api/help/doc/{slug}")
async def api_doc(req):
    slug = req.match_info["slug"]
    if not _SLUG.match(slug):
        return web.json_response({"error": "bad page name"}, status=400)
    p = os.path.join(docs_dir(), slug + ".md")
    if not os.path.isfile(p):
        return web.json_response({"error": "no such page"}, status=404)
    md = open(p, encoding="utf-8").read()
    return web.json_response({"slug": slug, "title": _title(md, slug), "markdown": md})


@routes.get("/api/help/about")
async def api_about(req):
    import asyncio
    return web.json_response(await asyncio.to_thread(about))


@routes.post("/api/help/open")
async def api_open(req):
    b = await req.json()
    d = folders().get(str(b.get("which") or ""))
    if not d:
        return web.json_response({"error": "unknown folder"}, status=400)
    os.makedirs(d, exist_ok=True)
    os.startfile(d)                                    # noqa: S606 — a folder we chose, opened in Explorer
    return web.json_response({"ok": True, "path": d})


@routes.get("/api/help/diagnostics")
async def api_diag(req):
    import asyncio
    import web_backend as wb
    return web.json_response({"text": await asyncio.to_thread(diagnostics, wb.STORE.settings)})


def install(app):
    app.add_routes(routes)
