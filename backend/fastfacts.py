"""Fast fact lookups: offline Wikipedia (milliseconds) and live web snippets (first search engine to answer, ~1-3 s)
in parallel, with a background prefetch so the web half is usually ready before it is needed.

- Stable facts (born, founded, height, capital, who wrote…): Wikipedia leads, the web cross-checks.
- Time-sensitive facts (current, latest, prices, who runs X now, recent years, news, scores): the web is required;
  Wikipedia (a dated snapshot) is background only.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=6)
_cache = {}                       # query -> (time, web rows)
_pending = {}                     # query -> Future
_lock = threading.Lock()
CACHE_S = 600

_TIME_RX = re.compile(
    r"\b(current(ly)?|latest|today|tonight|now|right now|this (week|month|year|season)|recent(ly)?|as of|so far|"
    r"upcoming|next (week|month|year)|price|prices|cost|costs|stock|share price|exchange rate|weather|forecast|news|"
    r"score|scores|standings|won|winner|wins|election|poll|ceo|president|prime minister|chancellor|leader|"
    r"governor|mayor|champion|record holder|release date|released|launch(ed)?|version|update|20[2-9]\d)\b", re.I)


def warm():
    """The search library's first call is slow (imports, engine handshakes): pay it once in the background."""
    def go():
        try:
            import searx_local
            searx_local.ensure()             # the local SearXNG starts with the app (~2-4 s, unseen)
        except Exception:
            pass
        try:
            _web("weather")
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def time_sensitive(text):
    """Does the question (or claim) depend on when it is asked? (prices, current office holders, news, recent
    years…) — then live web results are required, not just the encyclopedia snapshot."""
    return bool(_TIME_RX.search(text or ""))


def _web(query, n=6):
    import tools as webtools
    key = query.strip().lower()
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        rows = json.loads(webtools.web_search(query, n, fast=True))
    except Exception:
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and r.get("url")] if isinstance(rows, list) else []
    with _lock:
        _cache[key] = (time.time(), rows)
    return rows


def prefetch(query):
    """Start the web half in the background (no-op when cached or already running)."""
    key = (query or "").strip().lower()
    if not key:
        return
    with _lock:
        if key in _pending or (key in _cache and time.time() - _cache[key][0] < CACHE_S):
            return
        _pending[key] = _pool.submit(_web, query)


def web(query, wait=3.0):
    """Web snippet rows for a query: the prefetched result if it arrives within `wait` seconds, else a fresh search
    (bounded by the same wait). [] when nothing came back in time."""
    key = (query or "").strip().lower()
    with _lock:
        hit = _cache.get(key)
        fut = _pending.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    if fut is None:
        prefetch(query)
        with _lock:
            fut = _pending.get(key)
    try:
        return fut.result(timeout=max(0.0, wait)) if fut else []
    except Exception:
        return []
    finally:
        with _lock:
            if fut is not None and fut.done():
                _pending.pop(key, None)


def wiki(query, n=2, cfg=None, clear=True):
    """Offline Wikipedia articles for a query ([] when the file is missing); clear=True keeps only clear matches."""
    try:
        import encyclopedia as E
        if not E.available(cfg):
            return []
        arts = E.search(query, n + 1, cfg)
        if clear:
            arts = [a for a in arts if E.clear_match(a["title"], query)]
        return arts[:n]
    except Exception:
        return []


def lookup(query, cfg=None, wait=3.0, n_wiki=2):
    """Both sources in parallel -> {"wiki": [...], "web": [...], "time_sensitive": bool, "ms": int}."""
    t0 = time.time()
    web_on = (cfg or {}).get("web", True) is not False          # the composer's Web switch: off = Wikipedia only
    if web_on:
        prefetch(query)
    arts = wiki(query, n_wiki, cfg)
    rows = web(query, wait) if web_on else []
    return {"wiki": arts, "web": rows, "time_sensitive": time_sensitive(query), "ms": int((time.time() - t0) * 1000)}


def render(ev, wiki_chars=1200, web_rows=5):
    """Evidence as compact text, sources labelled with their kind and date."""
    import encyclopedia as E
    parts = []
    if ev.get("wiki"):
        parts.append(f"[Offline Wikipedia, snapshot {E.snapshot_date()}]\n" + E.render(ev["wiki"], max_chars=wiki_chars,
                                                                                         box_rows=18))
    rows = (ev.get("web") or [])[:web_rows]
    if rows:
        parts.append(f"[Live web results, {time.strftime('%d %B %Y')}]\n" + "\n".join(
            f"- {r.get('title', '')[:110]} — {r['url']}\n  {(r.get('snippet') or '')[:300]}" for r in rows))
    return "\n\n".join(parts)


def evidence_text(ev):
    """All the text of a lookup, for mechanical matching of claim keys."""
    t = [a.get("intro", "") + " " + " ".join(f"{k}: {v}" for k, v in a.get("infobox", [])) for a in ev.get("wiki") or []]
    t += [f"{r.get('title', '')} {r.get('snippet', '')} {r['url']}" for r in ev.get("web") or []]
    return "\n".join(t)
