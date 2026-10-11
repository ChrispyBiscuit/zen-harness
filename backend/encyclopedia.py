"""Offline English Wikipedia (Kiwix ZIM, intro + infobox of every article) for fast fact lookups.

A 9B model hallucinates most facts it recalls from memory but reads well, so facts should come from text. This
module answers in milliseconds, offline: full-text search over the ZIM's own index, title lookup, and each article
as its intro text plus the infobox as 'field: value' lines (population, area, length, dates, …).

Default file: the newest ~/kiwix/wikipedia_en_*.zim (settings: encyclopedia_path).
"""
import glob
import os
import re
import threading

_lock = threading.Lock()
_arch = {"path": None, "archive": None}
import paths
DEFAULT_DIR = paths.knowledge_dir()
FORCE_OFF = False                 # set by the engine when settings switch it off


def zim_path(cfg=None):
    p = str((cfg or {}).get("encyclopedia_path") or "").strip()
    if FORCE_OFF or p.lower() in ("off", "none", "false"):
        return ""                                   # switched off (A/B tests, or by the user)
    if p and os.path.isfile(p):
        return p
    files = sorted(glob.glob(os.path.join(DEFAULT_DIR, "wikipedia_en_*.zim")), key=os.path.getmtime)
    return files[-1] if files else ""


def available(cfg=None):
    if not zim_path(cfg):
        return False
    try:
        import libzim  # noqa: F401
        return True
    except ImportError:
        return False


def _archive(cfg=None):
    from libzim.reader import Archive
    p = zim_path(cfg)
    if not p:
        raise RuntimeError("no offline Wikipedia file (~/kiwix/wikipedia_en_*.zim)")
    with _lock:
        if _arch["path"] != p:
            _arch.update(path=p, archive=Archive(p))
        return _arch["archive"]


def snapshot_date(cfg=None):
    m = re.search(r"(\d{4}-\d{2})\.zim$", zim_path(cfg))
    return m.group(1) if m else ""


def _clean(s):
    s = re.sub(r"\[\s*(?:\d+|[a-z]|note \d+|citation needed|nb \d+)\s*\]", "", s or "", flags=re.I)  # [1], [a]
    return re.sub(r"\s+", " ", s).strip()


def parse(html):
    """-> (intro text, [(field, value)]) from an article's HTML."""
    from lxml import html as lh
    try:
        doc = lh.fromstring(html)
    except Exception:
        return "", []
    for bad in doc.xpath("//style|//script|//sup[contains(@class,'reference')]|//span[contains(@class,'mw-editsection')]"):
        bad.getparent().remove(bad)
    box = []
    for table in doc.xpath("//table[contains(@class,'infobox')]")[:1]:
        section = ""
        for tr in table.xpath(".//tr"):
            th, td = tr.xpath("./th"), tr.xpath("./td")
            if th and not td:                           # a section heading: "Population (2021)", "Area"
                section = _clean(th[0].text_content())[:50]
                continue
            if th and td:
                k, v = _clean(th[0].text_content()), _clean(" ".join(x.text_content() for x in td))
                if k[:1] in "•·-–" or k.startswith(("•", " ")):
                    k = f"{section} {k.lstrip('•·-– ')}".strip()      # "• City" under "Population (2021)"
                else:
                    section = k if not v else section
                if k and v and len(k) < 80:
                    box.append((k, v[:300]))
        table.getparent().remove(table)
    paras = [_clean(p.text_content()) for p in doc.xpath("//p")]
    intro = " ".join(p for p in paras if len(p) > 40)[:2500]
    return intro, box


def article(entry):
    item = entry.get_item()
    intro, box = parse(bytes(item.content).decode("utf-8", "replace"))
    title = entry.title
    return {"title": title, "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "intro": intro, "infobox": box}


def lookup(title, cfg=None):
    """Exact (redirect-following) title lookup -> article dict or None."""
    a = _archive(cfg)
    for t in (title, title[:1].upper() + title[1:], title.title()):
        try:
            e = a.get_entry_by_title(t)
            while e.is_redirect:
                e = e.get_redirect_entry()
            return article(e)
        except KeyError:
            continue
        except Exception:
            break
    return None


_QWORDS = set("a an the of in on at to for and or is are was were be what which who whom whose when where why how "
               "does do did can could would should will tell me give list about from by with as than please much many "
               "most more less long longest tall tallest high highest big biggest large largest small smallest old oldest "
               "first second third year years height length area population size capital founded founder found "
               "discovered discover discovery invented inventor wrote written author painted composed born died death "
               "located where metres meters kilometres kilometers miles feet celsius degrees number".split())


_NUMWORDS = set("one two three four five six seven eight nine ten top".split())


def clear_match(title, question):
    """Is this article clearly what the question is about? A loosely related article misleads a small model more
    than no article (the 1993 Nobel Prize for "the first woman to win…"), so: every meaningful title word is in the
    question (a lone word needs 4+ letters), or 2+ match with at most one extra; and no number the question lacks."""
    qw = set(_words(question))
    content = {w for w in qw if w not in _QWORDS and w not in _NUMWORDS}
    bare = re.sub(r"\(.*?\)", "", title)
    if any(n not in qw for n in re.findall(r"\d+", bare)):
        return False                                   # "2026 Mount Elbrus tragedy", "1993 Nobel Prize…"
    tw = {w for w in _words(bare) if w not in _QWORDS and w not in _NUMWORDS and w not in ("the", "of", "in", "and")}
    if not tw:
        return False
    if tw <= content:
        return len(tw) > 1 or len(next(iter(tw))) >= 4
    return len(tw & content) >= 2 and len(tw - content) <= 1


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower())]


def search(query, n=3, cfg=None):
    """Find the articles a question is about -> up to n article dicts. Exact title first, then title suggestions and
    full-text hits for the question's content words, ranked by how well each title matches them."""
    from libzim.search import Query, Searcher
    from libzim.suggestion import SuggestionSearcher
    a = _archive(cfg)
    q = query.strip().rstrip("?")
    content = [w for w in _words(q) if w not in _QWORDS]
    keyq = " ".join(content) or q
    cands = []                                         # paths, in discovery order
    for searcher in (lambda s: SuggestionSearcher(a).suggest(s), lambda s: Searcher(a).search(Query().set_query(s))):
        for s in dict.fromkeys([q, keyq]):
            try:
                cands += list(searcher(s).getResults(0, n * 4))
            except Exception:
                pass
    scored, seen = [], set()
    exact = lookup(q, cfg) if len(q) < 80 else None
    if exact:
        scored.append((99, 0, exact))
        seen.add(exact["title"])
    # the names in the question, looked up directly by title (instant): "Calgary" for "Calgary's population (2021
    # census)" — suggestions alone offered only "Calgary East"
    names = re.findall(r"(?:[A-Z][\w'’.-]*(?:\s+(?:of|the|de|del|da|la|le|and|von|van|du)\s+|\s+))*[A-Z][\w'’.-]*"
                       r"(?:\s+(?:['’]?\d+\w*|[IVX]{1,4}\b))*", q)       # "The King of Fighters '98", "Rocky II"
    singles = [w for nm in names for w in nm.split() if w[:1].isupper() and len(w) > 2]      # "OpenAI" in "CEO of OpenAI"
    for name in dict.fromkeys(re.sub(r"['’]s\b", "", nm).strip() for nm in names + singles):
        if len(name) < 3 or name.lower() in _QWORDS or name in seen:
            continue
        art = lookup(name, cfg)
        if art and art["title"] not in seen:
            seen.add(art["title"])
            tw = _words(re.sub(r"\(.*?\)", "", art["title"]))
            scored.append((len(set(tw) & set(content)) * 3 - len([w for w in tw if w not in content]) + 2, 1, art))
    for i, p in enumerate(dict.fromkeys(cands)):
        try:
            e = a.get_entry_by_path(p)
            while e.is_redirect:
                e = e.get_redirect_entry()
        except Exception:
            continue
        if e.title in seen or ":" in e.title.split(" ")[0]:           # skip Portal:, Category:, …
            continue
        seen.add(e.title)
        tw = _words(re.sub(r"\(.*?\)", "", e.title))
        overlap = len(set(tw) & set(content))
        extra = len([w for w in tw if w not in content])
        paren = 1 if "(" in e.title else 0                            # "Eiffel Tower (Paris, Texas)" below the real one
        scored.append((overlap * 3 - extra - paren * 2, -i, e))
        if len(scored) >= n * 6:
            break
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for _s, _i, e in scored:
        art = e if isinstance(e, dict) else article(e)
        if art["intro"] or art["infobox"]:
            out.append(art)
        if len(out) >= n:
            break
    return out


def render(arts, max_chars=2400, box_rows=24):
    """Articles as compact text for the model: title, URL, infobox rows, intro."""
    parts = []
    for x in arts:
        rows = "\n".join(f"  {k}: {v}" for k, v in x["infobox"][:box_rows])
        parts.append(f"## {x['title']}\nSource: {x['url']} (offline Wikipedia {snapshot_date()})\n"
                     + (f"Infobox:\n{rows}\n" if rows else "") + f"{x['intro'][:max_chars]}")
    return "\n\n".join(parts)
