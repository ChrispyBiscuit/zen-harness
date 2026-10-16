"""Place names to coordinates for the map blocks in answers (```map, see plans/answer-blocks.md).

OpenStreetMap's Nominatim, by its usage policy: one request a second at most, an identifying User-Agent, and every
answer cached (on disk, so a place is looked up once ever; misses are cached too, for a day). Only the place name
leaves the machine. The caller refuses when the agent's web access is off."""
import json
import os
import threading
import time

import requests

URL = "https://nominatim.openstreetmap.org/search"
UA = "Zazen/1.0 (desktop AI assistant; map blocks; https://github.com/ChrispyBiscuit/zen-harness)"
MISS_TTL = 86400

_lock = threading.Lock()
_last = [0.0]
_cache = None
_path = None


def _load(path):
    global _cache, _path
    if _cache is None or _path != path:
        _path = path
        try:
            with open(path, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save():
    try:
        tmp = _path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
        os.replace(tmp, _path)
    except Exception:
        pass


def _key(name):
    return " ".join(str(name).lower().split())[:200]


def lookup(names, cache_path, fetch=None, budget=8.0):
    """{name: {"lat", "lon", "label"} or None} for each name. Cached answers come back at once; new ones are looked
    up one a second until `budget` seconds pass (the rest come back None and are not cached, so a later call gets
    them). `fetch(name) -> dict|None` replaces the network, for tests."""
    fetch = fetch or _nominatim
    out, t0 = {}, time.monotonic()
    with _lock:
        cache = _load(cache_path)
        changed = False
        for name in names[:25]:
            k = _key(name)
            if not k:
                continue
            hit = cache.get(k)
            if hit is not None and (hit.get("lat") is not None or time.time() - hit.get("t", 0) < MISS_TTL):
                out[name] = hit if hit.get("lat") is not None else None
                continue
            if time.monotonic() - t0 > budget:
                out[name] = None
                continue
            wait = _last[0] + 1.05 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
            try:
                res = fetch(name)
            except Exception:
                out[name] = None            # network trouble: not cached, try again next time
                continue
            cache[k] = dict(res, t=time.time()) if res else {"lat": None, "t": time.time()}
            changed = True
            out[name] = cache[k] if res else None
        if changed:
            _save()
    return {n: (None if v is None else {"lat": v["lat"], "lon": v["lon"], "label": v.get("label", "")})
            for n, v in out.items()}


def _nominatim(name):
    r = requests.get(URL, params={"q": name, "format": "jsonv2", "limit": 1},
                     headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=6)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    return {"lat": float(rows[0]["lat"]), "lon": float(rows[0]["lon"]), "label": rows[0].get("display_name", "")}
