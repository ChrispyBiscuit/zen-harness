"""
Self-contained web tools — no API keys, no cloud search service.

  web_search(query)  -> keyless DuckDuckGo results (title, url, snippet)
  web_fetch(url)     -> clean main text of a page

These are the functions the local model calls. The only thing that leaves your
machine is the web request itself (inherent to any web search); no third-party
API key and no data sent to Ollama's servers.
"""
import json
import threading

# lxml (under trafilatura and python-docx/pptx) crashed the app with an access violation when several threads
# parsed at once (parallel page reading + the background file indexer). Parsing takes milliseconds: serialize it.
LXML_LOCK = threading.RLock()


def extract_html(html, **kw):
    """trafilatura.extract, one parse at a time."""
    import trafilatura
    if not html:
        return ""
    with LXML_LOCK:
        return trafilatura.extract(html, **kw) or ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


_BACKENDS = ("bing", "yahoo", "duckduckgo", "brave", "auto")     # raced in parallel; any that answer count
_STOP = {"the", "and", "for", "with", "what", "how", "who", "when", "where", "which", "does", "are", "was", "from",
         "that", "this", "best", "top", "list", "find", "about", "into", "your", "you", "can", "its", "per"}
_AD_RX = r"(utm_source=|[?&]gclid=|[?&]msclkid=|bing\.com/(aclk|ck/)|doubleclick|/aclk\?|googleadservices)"


def _terms(query):
    """Content words of a query (operators, quotes and stop words removed)."""
    import re
    q = re.sub(r"\b(site|filetype|intitle|inurl):\S+", " ", query.lower())
    q = re.sub(r"(^|\s)-\S+", " ", q)                       # excluded words
    return [w for w in re.findall(r"[a-z0-9][a-z0-9+#.-]*", q) if len(w) > 2 and w not in _STOP]


def clean_results(query, rows):
    """Drop ads, tracking redirects, off-site results for a site: query, and results unrelated to the query."""
    import re
    from urllib.parse import urlparse
    sites = [x.lower().lstrip("*.") for x in re.findall(r"\bsite:([^\s/]+)", query, re.I)]
    terms = _terms(query)
    out, seen = [], set()
    for r in rows:
        url = (r.get("url") or r.get("href") or "").strip()
        if not url.startswith("http") or re.search(_AD_RX, url, re.I):
            continue
        host = urlparse(url).netloc.lower()
        if sites and not any(host == d or host.endswith("." + d) for d in sites):
            continue                                          # the engine ignored site:
        key = re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()
        if key in seen:
            continue
        text = f"{r.get('title') or ''} {r.get('snippet') or r.get('body') or ''} {url}".lower()
        if terms and not any(t in text for t in terms):
            continue                                          # nothing to do with the query (dictionary pages…)
        seen.add(key)
        out.append({"title": r.get("title"), "url": url, "snippet": r.get("snippet") or r.get("body")})
    return out


_ENGINE_HEALTH = {}                 # backend -> [consecutive failures, time of last failure]
_QCACHE = {}                        # (query, n) -> (time, json): repeated searches within 10 min are instant
ENGINE_DEADLINE = 3.0               # seconds a search may take in total


def _healthy_backends():
    """Engines that have not failed 3+ times in a row in the last 10 minutes (all of them if none qualify)."""
    import time
    now = time.time()
    ok = [b for b in _BACKENDS if not (_ENGINE_HEALTH.get(b, [0, 0])[0] >= 3 and now - _ENGINE_HEALTH[b][1] < 600)]
    return ok or list(_BACKENDS)


def _engine_result(backend, good):
    import time
    h = _ENGINE_HEALTH.setdefault(backend, [0, 0])
    if good:
        h[0] = 0
    else:
        h[0] += 1
        h[1] = time.time()


def web_search(query: str, max_results: int = 5, fast: bool = False) -> str:
    """Search the web without an API key: the engines that currently work race in parallel (engines that keep
    failing are skipped for 10 minutes); results are cleaned (no ads, no off-topic or off-site rows) and the first
    good answers merged, within ENGINE_DEADLINE seconds. Returns a JSON list of results."""
    try:
        from ddgs import DDGS
    except ImportError:  # older package name
        from duckduckgo_search import DDGS
    import re
    import time
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    n = max(1, min(int(max_results or 5), 15))
    fixed = re.sub(r'"((?:site|filetype|intitle|inurl):[^"\s]+)"', r"\1", query)    # "site:x" -> site:x
    fixed = re.sub(r"\bsite:(https?://)?([^\s/]+)/\S*", r"site:\2", fixed)           # site: takes a domain, not a path

    def one(backend, q):
        with DDGS() as d:
            return backend, clean_results(fixed, d.text(q, max_results=max(n, 8), backend=backend) or [])

    ck = (fixed.lower(), n)
    hit = _QCACHE.get(ck)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    # primary: the local SearXNG (Google CSE + Bing + Yahoo merged, ~0.7 s, rarely empty); the engine race below
    # is the fallback when it is not installed / not up / returns nothing
    try:
        import searx_local
        rows = searx_local.search(fixed, max(n, 8))
        rows = clean_results(fixed, rows) if rows else []
        if rows:
            results = rows[:n]
            if fixed != query:
                results.insert(0, {"note": f"searched for: {fixed}"})
            out = json.dumps(results, ensure_ascii=False)
            _QCACHE[ck] = (time.time(), out)
            return out
    except Exception:
        pass
    backends = _healthy_backends()
    pool = ThreadPoolExecutor(max_workers=len(backends))
    pending = {pool.submit(one, b, fixed): b for b in backends}
    got, err, t0 = [], None, time.time()
    try:
        while pending and time.time() - t0 < ENGINE_DEADLINE:
            done, _rest = wait(set(pending), timeout=max(0.05, ENGINE_DEADLINE - (time.time() - t0)),
                               return_when=FIRST_COMPLETED)
            for f in done:
                b = pending.pop(f)
                try:
                    _b, rows = f.result()
                except Exception as e:
                    err = e
                    _engine_result(b, False)
                    continue
                _engine_result(b, bool(rows))
                if rows:
                    got.append((b, rows))
            # two engines answered (wider coverage), or one did and the others are slow; fast mode (fact checks):
            # the first engine with results wins
            if len(got) >= 2 or (got and (fast or time.time() - t0 > 1.5)):
                break
        if not got:
            for b in pending.values():                # still silent at the deadline: count as a failure
                _engine_result(b, False)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not got:
        return json.dumps({"error": f"search found nothing usable ({err or 'no results'}). Use different, simpler "
                                    f"words, or open a search engine in the browser."})
    merged, seen = [], set()                           # interleave the engines' results, best first
    for i in range(max(len(r) for _b, r in got)):
        for _b, rows in got:
            if i < len(rows):
                k = rows[i]["url"].rstrip("/").lower()
                if k not in seen:
                    seen.add(k)
                    merged.append(rows[i])
    results = merged[:n]
    if fixed != query:
        results.insert(0, {"note": f"searched for: {fixed}"})
    out = json.dumps(results, ensure_ascii=False)
    _QCACHE[ck] = (time.time(), out)
    return out


def web_fetch(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return its main text content (readable article text)."""
    import trafilatura
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            import requests
            downloaded = requests.get(url, headers={"User-Agent": UA},
                                      timeout=15).text
        text = extract_html(downloaded, include_links=False,
                                   include_comments=False) or ""
    except Exception as e:
        return f"(could not fetch {url}: {e})"
    text = text.strip()
    if blocked_page(text):
        return (f"[blocked] {url} returned a bot check or sign-in wall instead of its content, so nothing "
                f"useful was fetched. Try another source, or the in-app browser if the user is signed in there.")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]..."
    return text or f"(no extractable text at {url})"


_BLOCK_MARKERS = ("verify you are human", "enable javascript and cookies", "additional verification required",
                  "complete the security check", "réservé aux humains", "are you a robot", "unusual traffic",
                  "checking your browser", "access denied", "join linkedin", "sign in to view", "authwall",
                  "security systems to keep our site", "please enable cookies", "blocked by network security",
                  "agree & join", "accounts are required to access", "log in to continue", "sign up to see")


def blocked_page(text):
    """A short page that is a bot check / sign-in wall rather than content."""
    low = text[:3000].lower()
    return len(text) < 4000 and any(m in low for m in _BLOCK_MARKERS)


if __name__ == "__main__":
    # quick standalone smoke test (no model needed)
    print("web_search ->")
    print(web_search("who won the 2022 FIFA World Cup", 3))
    print("\nweb_fetch ->")
    hits = json.loads(web_search("python.org", 1))
    if hits and isinstance(hits, list):
        print(web_fetch(hits[0]["url"])[:400])
