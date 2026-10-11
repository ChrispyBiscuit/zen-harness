"""Quote-then-answer, checked mechanically: factual answers end with exact quotes from what was read, and this module
verifies them — no model judgment.

  1. Every quote is in the sources 1:1 (only formatting normalised: quote marks, dashes, whitespace, markdown, case;
     "…" splits a quote into pieces that must each appear, in order).
  2. The answer is in the quotes: the answer's bold key facts (or, without bold, its figures) appear in a quote.
  3. The question's constraints are in the quotes: its years, named organisations/people/works, and position words
     ("fourth", "second-largest"). A real quote about the wrong subject ("selfie" — Oxford's 2013 word, not the
     American Dialect Society's 2014 one) fails here.
Failures go back to the model with the exact reason; after the retries the answer must say what it could not verify.
"""
import re
import unicodedata

_Q_LINE = re.compile(r"^\s*>\s*(.+)$", re.M)
_QUOTED = re.compile(r"[\"“”«](.{12,600}?)[\"“”»]")
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
_STOP = set("a an the of in on at to for and or is are was were be been what which who whom whose when where why how "
            "does do did can could would should will year years name named called".split())


def norm(s):
    """Formatting-only normalisation: NFKC, quote marks, dashes, markdown, whitespace, case."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.translate({ord(c): "'" for c in "‘’‚‛′`´"} | {ord(c): '"' for c in "“”„‟″«»"}
                    | {ord(c): "-" for c in "‐‑‒–—―−"})
    s = re.sub(r"[\"']", "", s)                          # quote marks of any kind don't count ('selfie' = “selfie”)
    s = re.sub(r"\[(\d+|[a-z])\]", " ", s)             # citation markers [1], [a]
    s = re.sub(r"[*_#|]+", " ", s)                       # markdown emphasis, tables, headings
    return re.sub(r"\s+", " ", s).strip().lower()


def extract(answer):
    """The quotes an answer gives as evidence: '> "…" — source' lines (the evidence block), else quoted spans."""
    quotes = []
    for line in _Q_LINE.findall(answer or ""):
        m = _QUOTED.search(line)
        q = m.group(1) if m else re.split(r"\s+[—–-]\s+(?:\[|https?://|source)", line, maxsplit=1)[0]
        q = q.strip().strip('"“”')
        if len(q) >= 12:
            quotes.append(q)
    return quotes


def _in(quote, text_n):
    """Is the quote in the (normalised) sources 1:1? '…' / '...' split it into ordered pieces."""
    parts = [p.strip() for p in re.split(r"\.\.\.|…", norm(quote)) if len(p.strip()) >= 6]
    pos = 0
    for p in parts or [norm(quote)]:
        i = text_n.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def constraints(question):
    """What a correct quote must mention: years, multi-word proper names (organisations, works, people), quoted
    titles, and position words."""
    q = question or ""
    out = [("year", y) for y in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", q)]
    for name in re.findall(r"(?:[A-Z][\w'’.-]+(?:\s+(?:of|the|de|for|and)\s+|\s+)){1,5}[A-Z][\w'’.-]+", q):
        words = [w for w in re.findall(r"[A-Za-z][\w'’-]+", name) if w.lower() not in _STOP]
        if len(words) >= 2 and words[0].lower() not in ("what", "which", "who", "when", "where", "how", "in"):
            out.append(("name", name.strip()))
    # quoted titles; a single quote counts only outside a word, so "Japan's … Davy's" is not a title
    for t in re.findall(r"[\"“]([^\"”]{4,80})[\"”]|(?<!\w)'([^']{4,80})'(?!\w)", q):
        t = t[0] or t[1]
        out.append(("name", t.strip()))
    for o in _ORDINALS:
        if re.search(rf"\b{o}\b", q, re.I) and o != "first":
            out.append(("position", o))
    return out


def _has(constraint, quotes_n, answer):
    kind, val = constraint
    if kind == "year":
        return val in quotes_n
    if kind == "name":
        words = [w.lower() for w in re.findall(r"[A-Za-z][\w'’-]+", val) if w.lower() not in _STOP]
        return sum(1 for w in words if w in quotes_n) >= max(1, (len(words) + 1) // 2)
    if kind == "position":                               # the ordinal stated, or the items listed and counted
        n = _ORDINALS.index(val) + 1
        listed = len(re.findall(r"^\s*(?:\d+[.)]|[-*•])\s+\S", answer or "", re.M))
        return val in quotes_n or listed >= n
    return True


def key_facts(answer):
    """What the answer asserts: its bold spans; without bold, its figures (numbers with 2+ digits)."""
    body = re.split(r"\n\s*(?:evidence|sources?)\s*:?\s*\n|\n\s*>", answer or "", maxsplit=1, flags=re.I)[0]
    bold = [b.strip() for b in re.findall(r"\*\*([^*]{1,80})\*\*", body)
            if not b.strip().endswith(":")]                    # "**Evidence:**", "**Answer:**" are labels, not facts
    if bold:
        return bold[:4]
    return re.findall(r"\b\d[\d,.]{1,}\b", body)[:4]


def check(answer, question, sources_text):
    """-> list of problems ([] = the answer stands on its quotes)."""
    quotes = extract(answer)
    if not quotes:
        return ["no evidence quotes: end the answer with '> \"exact sentence from the source\" — [source](url)' "
                "for each key fact"]
    text_n = norm(sources_text)
    problems = []
    bad = [q for q in quotes if not _in(q, text_n)]
    for q in bad[:3]:
        problems.append(f"this quote is not in any source you read (copy it exactly, word for word): \"{q[:160]}\"")
    good = [q for q in quotes if q not in bad]
    quotes_n = norm(" ".join(good))
    for k in key_facts(answer):
        kn = norm(k)
        nums = re.findall(r"\d[\d,.]*", kn)
        ok = kn in quotes_n or (nums and all(n.replace(",", "") in quotes_n.replace(",", "") for n in nums))
        if not ok and good:
            problems.append(f"your answer says \"{k}\" but none of your quotes states it")
    for c in constraints(question):
        if good and not _has(c, quotes_n, answer):
            what = {"year": "the year", "name": "", "position": "the position"}[c[0]]
            problems.append(f"the question is about {what} {c[1]!s}".replace("  ", " ")
                            + " but your quotes don't mention it — find the sentence that does (it may be a "
                              "different item on the page)" + (": list the items in order and count them"
                                                                if c[0] == "position" else ""))
    return problems
