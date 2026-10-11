"""Local SearXNG (native Windows, no Docker): the primary keyless web search.

Installed at ~/searxng (its own venv, local_settings.yml: 127.0.0.1:8888, JSON on, limiter off, 1.5 s engine
timeout, engines that answer from here: Google CSE, Bing, Yahoo). Measured: median 0.68 s, ~34 results, 0 empty in
12 queries, ~95 MB RAM — vs the ddgs engine race: 2.8 s median and 4 of 10 queries empty.

The app starts it on first use and ties it to its own lifetime (Windows job object), like the model servers.
"""
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request

HOME = os.path.join(os.path.expanduser("~"), "searxng")
URL = os.environ.get("LWS_SEARXNG_URL", "http://127.0.0.1:8888")
_lock = threading.Lock()
_proc = {"p": None, "failed_at": 0.0}


def installed():
    return os.path.isfile(os.path.join(HOME, ".venv", "Scripts", "python.exe")) and \
        os.path.isfile(os.path.join(HOME, "local_settings.yml"))


def is_up(timeout=0.6):
    try:
        with urllib.request.urlopen(URL + "/healthz", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        try:
            with urllib.request.urlopen(URL + "/", timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False


def ensure(wait=10.0):
    """Start it if installed and not already running (another app instance may run it). -> is it up?"""
    if is_up():
        return True
    if not installed():
        return False
    with _lock:
        if is_up():
            return True
        if time.time() - _proc["failed_at"] < 120:          # it just failed: don't retry on every search
            return False
        p = _proc["p"]
        if p is None or p.poll() is not None:
            env = dict(os.environ, SEARXNG_SETTINGS_PATH=os.path.join(HOME, "local_settings.yml"))
            log = open(os.path.join(HOME, "searxng.log"), "w", encoding="utf-8")
            p = subprocess.Popen([os.path.join(HOME, ".venv", "Scripts", "python.exe"), "-m", "searx.webapp"],
                                 cwd=HOME, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=0x08000000)              # no console window
            try:
                import llama_local
                llama_local._bind_to_app(p)                         # dies with the app, however it ends
            except Exception:
                pass
            _proc["p"] = p
        t0 = time.time()
        while time.time() - t0 < wait:
            if is_up(0.4):
                return True
            if p.poll() is not None:
                break
            time.sleep(0.3)
        _proc["failed_at"] = time.time()
        return False


def search(query, n=10, timeout=4.0):
    """-> [{"title", "url", "snippet"}] or None when SearXNG is unavailable (the caller falls back)."""
    if not ensure():
        return None
    u = URL + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    try:
        with urllib.request.urlopen(u, timeout=timeout) as r:
            d = json.load(r)
    except Exception:
        return None
    rows = []
    for x in d.get("results") or []:
        if x.get("url"):
            rows.append({"title": x.get("title") or "", "url": x["url"], "snippet": x.get("content") or ""})
        if len(rows) >= n:
            break
    return rows
