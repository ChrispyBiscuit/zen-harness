"""deep_search: one tool call does a whole research round, the way research agents do it.

  1. several differently-worded queries (the question itself + the agent's own variants) are searched in parallel
  2. results are merged and ranked (pages that several queries find rank higher)
  3. the top pages are fetched in parallel and their readable text extracted (HTML and PDF)
  4. the text is cut into passages and the passages most relevant to the question are kept
     (bge-small embeddings from the memory store, on the CPU, plus keyword overlap)
  5. -> numbered sources with their best passages, ready to cite, plus other results worth a look

No API key, no GPU, no extra model call. A typical round takes 10-25 s.
"""
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import tools as webtools

UA = webtools.UA
PASSAGE = 700           # characters per passage
MAX_PAGE = 60000        # characters of text kept per page


def _terms(text):
    return set(webtools._terms(text))


def _title(t):
    """Some engines glue several titles together ('A ...B ...C'): keep the first."""
    t = " ".join(str(t or "").split())
    m = re.match(r"(.{12,}?)(?:\.\.\.|…)(?=[A-Z0-9])", t)
    if m:
        return m.group(1).strip() + "…"
    m = re.match(r"(.{12,}?(?:[?!)]|\b(?:19|20)\d\d))(?=[A-Z][a-z])", t)    # 'One?Two', 'Examples for 2026Technical…'
    return m.group(1).strip() if m else t


PAGE_DEADLINE = 5.0                 # at most this long for pages…
PAGE_ENOUGH = 3                     # …and stop waiting early once this many are read (after PAGE_MIN_WAIT)
PAGE_MIN_WAIT = 2.0


# Each page's own preview image (og:image / twitter:image: the picture a site chooses for link previews) — the most
# relevant image available for a search answer, from a page that was actually read. url -> image url.
PAGE_IMAGES = {}
_IMG_META = re.compile(r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:image(?::secure_url|:url)?|twitter:image(?::src)?)["'][^>]*>""", re.I)
_IMG_CONTENT = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)
_IMG_JUNK = re.compile(r"logo|favicon|sprite|placeholder|default[-_]?(og|share|image)|blank\.|pixel|avatar|icon[-_.]|/icons?/", re.I)


def page_image(html, base):
    """The page's own preview image -> absolute URL, or '' (logos, icons and placeholders don't count)."""
    from urllib.parse import urljoin
    for tag in _IMG_META.findall(html[:200000]):
        m = _IMG_CONTENT.search(tag)
        if not m:
            continue
        src = urljoin(base, m.group(1).strip().replace("&amp;", "&"))
        if src.startswith("http") and not _IMG_JUNK.search(src) and not src.lower().endswith((".svg", ".ico", ".gif")):
            return src
    return ""


_META_TAG = re.compile(r"""<meta[^>]+(?:property|name)\s*=\s*["'](og:site_name|og:title|application-name)["'][^>]*>""", re.I)
_TITLE_TAG = re.compile(r"<title[^>]*>([^<]{1,300})</title>", re.I)
# article / listing pages ("The 20 best pizza places in Hong Kong") are not one place
_LISTING = re.compile(r"\b(best|top \d+|\d+ (best|great)|guide|review(s)? of|where to|places to|things to|list of|ranked)\b", re.I)


def page_names(html):
    """-> (name, publisher, is_listing): what a page calls itself, from its preview tags or <title>."""
    import html as _html
    meta = {}
    for m in _META_TAG.finditer(html[:200000]):
        c = _IMG_CONTENT.search(m.group(0))
        if c:
            meta.setdefault(m.group(1).lower(), _html.unescape(c.group(1)).strip())
    title = meta.get("og:title") or ""
    if not title:
        t = _TITLE_TAG.search(html[:200000])
        title = _html.unescape(t.group(1)).strip() if t else ""
    site = meta.get("og:site_name") or meta.get("application-name") or ""
    listing = bool(_LISTING.search(title))
    # "Paisano's Pizza – Authentic New York Style Pizza in Hong Kong" -> "Paisano's Pizza"
    first = re.split(r"\s+[|–—·:]\s+|\s+-\s+", title)[0].strip() if title else ""
    name = title if listing else (site if site and len(site) <= 60 else first or site)
    return name[:90], (site if site != name else "")[:60], listing


def images_for(urls):
    """[{src, page, name, publisher, listing}] for the pages that had a preview image, in the given order, without
    duplicates."""
    out, seen = [], set()
    for u in urls:
        d = PAGE_IMAGES.get(u)
        if isinstance(d, str):
            d = {"src": d}
        if d and d.get("src") and d["src"] not in seen:
            seen.add(d["src"])
            out.append({"src": d["src"], "page": u, "name": d.get("name", ""), "publisher": d.get("publisher", ""),
                        "listing": bool(d.get("listing"))})
    return out


def _fetch(url, timeout=5):
    """-> (text, error). Readable text of an HTML page or a PDF."""
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=timeout)
    except Exception as e:
        return "", f"could not fetch ({type(e).__name__})"
    if r.status_code >= 400:
        return "", f"HTTP {r.status_code}"
    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        try:
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(r.content))
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:30])
        except Exception as e:
            return "", f"unreadable PDF ({type(e).__name__})"
    else:
        try:
            img = page_image(r.text, r.url or url)
            if img:
                name, pub, listing = page_names(r.text)
                PAGE_IMAGES[url] = {"src": img, "name": name, "publisher": pub, "listing": listing}
                if len(PAGE_IMAGES) > 2000:                 # bounded: oldest entries go first
                    PAGE_IMAGES.pop(next(iter(PAGE_IMAGES)))
        except Exception:
            pass
        try:
            import trafilatura
            text = webtools.extract_html(r.text, include_links=False, include_comments=False)
        except Exception as e:
            return "", f"could not extract ({type(e).__name__})"
    text = text.strip()
    if webtools.blocked_page(text):
        return "", "bot check or sign-in wall"
    if len(text) < 200:
        return "", "almost no text"
    return text[:MAX_PAGE], ""


def _passages(text):
    """Paragraph-aligned chunks of about PASSAGE characters."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 > PASSAGE and cur:
            out.append(cur)
            cur = ""
        while len(p) > PASSAGE * 1.5:                     # a huge paragraph: split it on sentences
            cut = p.rfind(". ", 0, PASSAGE)
            cut = cut + 1 if cut > PASSAGE // 3 else PASSAGE
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        cur = (cur + "\n" + p).strip()
    if cur:
        out.append(cur)
    return [c for c in out if len(c) > 60]


EMBED_MAX = 40                      # chunks embedded per search (~80 ms each on this CPU; the rest by keywords)


def _rank_passages(question, chunks):
    """Score (source_index, passage) pairs: keyword overlap for all; meaning (embeddings) for a shortlist —
    the best keyword matches plus the first chunks of each page, so a page with no keyword hit still gets a look.
    Embedding every chunk of six long pages took 10-30 s on the CPU; the shortlist takes ~1-2 s."""
    terms = _terms(question)
    kw = [len(terms & _terms(t)) / max(len(terms), 1) for _i, t in chunks]
    short, seen_page = set(), {}
    for j in sorted(range(len(chunks)), key=lambda j: -kw[j])[:EMBED_MAX - 2 * len({i for i, _c in chunks})]:
        short.add(j)
    for j, (i, _c) in enumerate(chunks):              # first two chunks of every page
        if seen_page.get(i, 0) < 1:
            short.add(j)
            seen_page[i] = seen_page.get(i, 0) + 1
    short = sorted(short)[:EMBED_MAX]
    scores = [k * 0.6 for k in kw]                    # keyword-only score for chunks not embedded
    try:
        import memory_store
        st = memory_store.store()
        q = st.embed([question], query=True)
        m = st.embed([chunks[j][1][:400] for j in short])
        if q is not None and m is not None:
            for j, s in zip(short, (m @ q[0]).tolist()):
                scores[j] = s + 0.25 * kw[j]
    except Exception:
        pass
    return scores


RETRY_IN_BROWSER = ("HTTP 403", "HTTP 429", "HTTP 999", "HTTP 401", "bot check", "almost no text",
                    "could not extract", "could not fetch")
_BROWSER_WALLED = {}               # host -> time it blocked even the real browser (skip it for 30 min)


def _host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def read_with_browser(url, read_page, max_chars=MAX_PAGE):
    """-> (text, error). The page loaded in a background tab of the real browser."""
    if time.time() - _BROWSER_WALLED.get(_host(url), 0) < 1800:
        return "", "sign-in wall or block (seen recently)"
    try:
        r = read_page(url, max_chars)
    except Exception as e:
        return "", f"browser failed ({type(e).__name__})"
    text = (r or {}).get("text", "").strip()
    if webtools.blocked_page(text):
        _BROWSER_WALLED[_host(url)] = time.time()
        return "", "sign-in wall or bot check, also in the browser"
    if len(text) < 200:
        return "", "almost no text, also in the browser"
    return text, ""


def deep_search(question, queries=None, sources=6, should_stop=lambda: False, info=None, fallback=None,
                browser=None, memory=None):
    """sources = pages to read (0 = quick: just the merged result list). info (dict) receives queries / pages / timing
    for the chat card. fallback(query) -> rows is tried when the keyless engines find nothing (the real browser)."""
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("give the search query or question")
    sources = 6 if sources is None else max(0, min(int(sources), 10))
    info = info if info is not None else {}
    qs = [question] + [" ".join(str(q).split()) for q in (queries or []) if str(q).strip()]
    seen_q, uniq = set(), []
    for q in qs:
        if q.lower() not in seen_q:
            seen_q.add(q.lower())
            uniq.append(q)
    qs = uniq[:4]                                   # main query + 3 variants: more costs time, adds little
    tm = {}                                         # where the time goes (shown in the result line)
    t0 = time.time()

    # 1-2) search the queries (two at a time); reciprocal-rank fusion across queries
    found, failed_q = {}, []

    def add(rows):
        for rank, r in enumerate([x for x in rows if x.get("url")]):
            key = r["url"].split("#")[0].rstrip("/").lower()
            e = found.setdefault(key, {"url": r["url"], "title": _title(r.get("title")),
                                       "snippet": r.get("snippet") or "", "score": 0.0, "hits": 0})
            e["score"] += 1.0 / (60 + rank)
            e["hits"] += 1

    def search(q):
        try:
            rows = json.loads(webtools.web_search(q, 10))
        except Exception:
            rows = {"error": "search failed"}
        return None if isinstance(rows, dict) else rows

    # all queries at once (each races the engines that currently work, within a few seconds)
    t_s = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(search, q): q for q in qs}
        for f in as_completed(futs):
            rows = f.result()
            if rows is None:
                failed_q.append(futs[f])
            else:
                add(rows)
    # narrow queries (quotes, site:, filetype:) that found nothing: one relaxed wave, only when results are thin
    relaxed = []
    for q in failed_q:
        r = " ".join(re.sub(r'(?:\b(?:site|filetype|intitle|inurl):\S+)|"', " ", q).split())
        if r and r.lower() != q.lower() and r.lower() not in {x.lower() for x in qs + relaxed}:
            relaxed.append(r)
    if relaxed and len(found) < 8 and not should_stop():
        with ThreadPoolExecutor(max_workers=4) as pool:
            for rows in pool.map(search, relaxed[:3]):
                if rows:
                    add(rows)
        info["relaxed"] = relaxed[:3]
    if not found and fallback is not None:
        for q in qs[:2]:
            try:
                rows = fallback(q)
            except Exception:
                rows = None
            if rows:
                add(rows)
                info["engine_note"] = "results from the browser"
                break
    info.update(queries=qs, results=len(found))
    if not found:
        return (f"web_search found nothing for: {' | '.join(qs)}. Try different words (fewer, more specific) or the "
                f"browser.")
    memory = memory if memory is not None else {}
    shown, read_before = memory.setdefault("shown", set()), memory.setdefault("read", set())
    key = lambda u: u.split("#")[0].rstrip("/").lower()
    for e in found.values():
        if key(e["url"]) in read_before:
            e["score"] *= 0.05                    # already read earlier in this chat: last
        elif key(e["url"]) in shown:
            e["score"] *= 0.4                     # already listed earlier: behind anything new
    ranked = sorted(found.values(), key=lambda e: e["score"], reverse=True)
    already = [e for e in ranked if key(e["url"]) in read_before]
    ranked = [e for e in ranked if key(e["url"]) not in read_before]
    if sources == 0:                               # quick mode: the list only
        info.update(pages=[], seconds=round(time.time() - t0, 1),
                    links=[{"title": e["title"], "url": e["url"]} for e in ranked[:10]])
        return "\n".join([f"web_search (quick): {len(qs)} queries, {len(found)} results. Queries: " + " | ".join(qs), ""]
                         + [f"- {e['title'][:100]} — {e['url']}" + (f"\n  {e['snippet'][:220]}" if e["snippet"] else "")
                            for e in ranked[:12]]
                         + ["", "Read a page in full with web_fetch, or search with pages > 0 to have the top pages "
                                "read for you."])
    # at most 2 pages per site, so one site can't crowd out the rest
    per_site, picks = {}, []
    site_cap = sources if any("site:" in q.lower() for q in qs) else 2
    for e in ranked:
        host = urlparse(e["url"]).netloc.lower().removeprefix("www.")
        if per_site.get(host, 0) >= site_cap:
            continue
        per_site[host] = per_site.get(host, 0) + 1
        picks.append(e)
        if len(picks) >= sources + 3:              # spares for pages that fail to load
            break

    tm["search"] = time.time() - t_s
    # 3) fetch the pages at once
    t_f = time.time()
    pages, skipped = [], []
    pool = ThreadPoolExecutor(max_workers=max(1, len(picks)))
    futs = {pool.submit(_fetch, e["url"]): e for e in picks}
    try:
        for f in as_completed(futs, timeout=PAGE_DEADLINE):
            e = futs[f]
            text, err = f.result()
            if err:
                skipped.append(f"{e['url']} ({err})")
            else:
                pages.append(dict(e, text=text))
            waited = time.time() - t_f
            if should_stop() or len(pages) >= sources or (len(pages) >= min(PAGE_ENOUGH, sources) and waited >= PAGE_MIN_WAIT):
                break
    except Exception:                               # the deadline passed: keep what arrived
        pass
    for f, e in futs.items():
        if not f.done():
            skipped.append(f"{e['url']} (slow — not read)")
    pool.shutdown(wait=False, cancel_futures=True)
    tm["pages"] = time.time() - t_f
    # pages that blocked the plain fetch or came back empty: try them in the real browser (background tab) —
    # only when too few pages were readable, at most 2, within 20 s
    t_b = time.time()
    if browser is not None and len(pages) < max(2, sources // 2):
        retry = [s for s in skipped if any(k in s for k in RETRY_IN_BROWSER)]
        for s in retry[:2]:
            if len(pages) >= sources or should_stop() or time.time() - t_b > 20:
                break
            url = s.rsplit(" (", 1)[0]
            e = next((x for x in picks if x["url"] == url), None)
            text, err = read_with_browser(url, browser)
            skipped.remove(s)
            if err:
                skipped.append(f"{url} ({err})")
            elif e is not None:
                pages.append(dict(e, text=text, via="browser"))
    if time.time() - t_b > 0.5:
        tm["browser"] = time.time() - t_b
    pages.sort(key=lambda e: e["score"], reverse=True)
    read_before.update(key(p["url"]) for p in pages[:sources])
    shown.update(key(e["url"]) for e in ranked[:16])
    pages = pages[:sources]

    # 4) keep the passages that answer the question
    t_r = time.time()
    chunks = [(i, c) for i, p in enumerate(pages) for c in _passages(p["text"])[:80]][:600]
    best = {}
    if chunks:
        scores = _rank_passages(question, chunks)
        order = sorted(range(len(chunks)), key=lambda j: scores[j], reverse=True)
        budget, used = max(2500, 7300 - 230 * len(pages) - 1300), 0
        per_page = 3 if len(pages) <= 5 else 2       # spread the budget over more pages
        for j in order:
            i, c = chunks[j]
            if len(best.get(i, [])) >= per_page or used + len(c) > budget:
                continue
            best.setdefault(i, []).append((scores[j], c))
            used += len(c)

    tm["rank"] = time.time() - t_r
    # 5) report
    secs = time.time() - t0
    info["images"] = [dict(i, title=next((p["title"] for p in pages if p["url"] == i["page"]), ""))
                      for i in images_for([p["url"] for p in pages])]
    info.update(seconds=round(secs, 1), pages=[{"title": p["title"], "url": p["url"]} for p in pages],
                links=[{"title": e["title"], "url": e["url"]} for e in ranked if e["url"] not in {p["url"] for p in pages}][:6],
                skipped=len(skipped))
    info["timing"] = {k: round(v, 1) for k, v in tm.items()}
    lines = [f"web_search: {len(qs)} queries, {len(found)} results, read {len(pages)} pages in {secs:.0f}s "
             f"({', '.join(f'{k} {v:.0f}s' for k, v in tm.items())}).",
             "Queries: " + " | ".join(qs), ""]
    n, empty = 0, []
    for i, p in enumerate(pages):
        if not best.get(i):
            empty.append(p)
            continue
        n += 1
        lines.append(f"[{n}] {p['title'] or p['url']}" + (" (read in the browser)" if p.get("via") else "")
                     + f"\n    {p['url']}")
        for _s, c in sorted(best.get(i, []), key=lambda x: -x[0]):
            lines.append("    > " + c.replace("\n", "\n    > "))
        lines.append("")
    if empty:
        lines.append("Also read, nothing close to the question: " + "; ".join(
            f"{(p['title'] or '')[:50]} ({urlparse(p['url']).netloc.removeprefix('www.')})" for p in empty))
        lines.append("")
    rest = [e for e in ranked if e["url"] not in {p["url"] for p in pages}][:6]
    if rest:
        lines.append("Other results (not read):")
        lines += [f"- {e['title'][:90]} — {e['url']}" + (f"\n  {e['snippet'][:160]}" if e["snippet"] else "")
                  for e in rest]
    if already:
        lines.append("\nAlready read earlier in this chat (not repeated): " + "; ".join(
            urlparse(e["url"]).netloc.removeprefix("www.") + urlparse(e["url"]).path[:40] for e in already[:6]))
    if skipped:
        lines.append("\nCould not read: " + "; ".join(skipped[:6]))
    if failed_q:
        lines.append("Queries with no results: " + " | ".join(failed_q))
    lines.append("\nCite sources by their [number] and URL. Passages are quotes from the pages. To dig further, "
                 "call web_search again with new queries for what is still missing (web_fetch only when you need one "
                 "specific page in full).")
    out = "\n".join(lines)
    if len(out) > 7800:
        out = "\n".join(l for l in lines if not (l.startswith("  ") and not l.startswith("    ")))
    if len(out) > 7800:
        out = out[:7700] + "\n…(cut to fit; the full result is saved)"
    return out


def fetch_many(urls, question="", max_chars=7000, browser=None, save_dir=None, should_stop=lambda: False, info=None):
    """Read several pages at once (HTML and PDF; blocked or empty ones retried in the real browser).
    With a question: the most relevant passages of each page. Without: the start of each page.
    The full text of every page is saved to save_dir so nothing is lost to the size limit."""
    import os
    info = info if info is not None else {}
    seen, todo = set(), []
    for u in urls:
        u = str(u or "").strip()
        k = u.split("#")[0].rstrip("/").lower()
        if re.match(r"^https?://", u, re.I) and k not in seen:
            seen.add(k)
            todo.append(u)
    todo = todo[:8]
    if not todo:
        raise ValueError("give 'urls': a list of http(s) URLs (up to 8)")
    t0 = time.time()
    res = {}
    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
        futs = {pool.submit(_fetch, u): u for u in todo}
        for f in as_completed(futs):
            res[futs[f]] = f.result()
    if browser is not None:                                # blocked / empty: the real browser, one at a time
        t_b = time.time()
        for u in todo:
            text, err = res[u]
            if err and any(k in err for k in RETRY_IN_BROWSER) and time.time() - t_b < 45 and not should_stop():
                t2, e2 = read_with_browser(u, browser)
                res[u] = (t2, "") if t2 else ("", e2)
                if t2:
                    res[u] = (t2, "")
                    info.setdefault("via_browser", []).append(u)
    ok = [u for u in todo if res[u][0]]
    saved = {}
    if save_dir:
        try:
            os.makedirs(save_dir, exist_ok=True)
            for u in ok:
                name = re.sub(r"[^\w.-]+", "_", (urlparse(u).netloc + urlparse(u).path).strip("/"))[:80] or "page"
                p = os.path.join(save_dir, f"page_{len(os.listdir(save_dir)) + 1}_{name}.txt")
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(u + "\n\n" + res[u][0])
                saved[u] = p
        except OSError:
            pass
    per_page = max(500, min(max_chars, 7200 // max(1, len(ok)))) if ok else 0
    best = {}
    if question and ok:
        chunks = [(u, c) for u in ok for c in _passages(res[u][0])][:900]
        if chunks:
            scores = _rank_passages(question, chunks)
            used = {u: 0 for u in ok}
            for j in sorted(range(len(chunks)), key=lambda j: scores[j], reverse=True):
                u, c = chunks[j]
                if used[u] + len(c) <= per_page:
                    best.setdefault(u, []).append(c)
                    used[u] += len(c)
    lines = [f"web_fetch: read {len(ok)} of {len(todo)} pages in {time.time() - t0:.0f}s"
             + (f" (passages most relevant to: {question})" if question else "") + ".", ""]
    for i, u in enumerate(ok, 1):
        text = res[u][0]
        lines.append(f"[{i}] {u}" + (" (read in the browser)" if u in info.get("via_browser", []) else "")
                     + (f"\n    full text: {saved[u]}" if u in saved else ""))
        if question:
            body = "\n".join(best.get(u, [])) or "(nothing close to the question on this page)"
        else:
            body = text[:per_page] + ("\n…" if len(text) > per_page else "")
        lines.append("    > " + body.replace("\n", "\n    > "))
        lines.append("")
    bad = [f"{u} ({res[u][1]})" for u in todo if not res[u][0]]
    if bad:
        lines.append("Could not read: " + "; ".join(bad))
    info["images"] = images_for(ok)
    info.update(type="web_hits", queries=[question] if question else [], results=len(todo),
                pages=[{"title": urlparse(u).netloc.removeprefix("www.") + urlparse(u).path[:50], "url": u} for u in ok],
                links=[], seconds=round(time.time() - t0, 1))
    out = "\n".join(lines)
    return out if len(out) <= 7800 else out[:7700] + "\n…(cut to fit; full texts are saved in the files above)"
