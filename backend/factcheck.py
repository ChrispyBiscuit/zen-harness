"""Critical-fact verification: every specific, checkable claim in a final answer must trace to something the agent
actually read in this chat (a tool result, a saved page, the user's own messages) or to a calculation.

The model lists its critical claims (structured call); this module checks each one MECHANICALLY — no model judgment:
the cited source must exist in the chat's evidence, and the claim's key detail (the name, the number, the date) must
appear in that source's text. Unsupported claims go back to the model to verify, remove, or label as unverified.
"""
import json
import os
import re

CRITICAL_FACTS = """A critical fact is a specific, checkable claim the user may rely on:
- numbers: amounts, money, prices, salaries, percentages, counts, measurements;
- dates and deadlines;
- identities: names of people or organisations, who works where, job titles, who said or did what;
- quotes and attributions;
- claims about your own result: "10 found", "real", "verified", "public", "all checked";
- anything legal, medical, financial or safety-related.
Not critical: general explanations, your own reasoning or recommendations (labelled as such), formatting."""

EXTRACT_PROMPT = CRITICAL_FACTS + """

List every critical fact in the draft answer above (and in any document or artifact text it presents). For each:
- claim: the claim in a few words
- key: the exact detail that must be checked, copied as written — the number, date, name, title or quoted words
  (for "X works at Google" the key is "Google"; for "$275k-$375k" the key is "$275k-$375k")
- source: the URL or file path you took it from, "user" if the user told you, "calculation" if a calculator/code
  result produced it, or "none" if it came from nowhere you can point to
At most 25 claims, the most important first. JSON only."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "maxItems": 25, "items": {
        "type": "object",
        "properties": {"claim": {"type": "string", "maxLength": 200}, "key": {"type": "string", "maxLength": 120},
                       "source": {"type": "string", "maxLength": 300}},
        "required": ["claim", "key", "source"], "additionalProperties": False}}},
    "required": ["claims"], "additionalProperties": False}

EVIDENCE_TOOLS = {"web_search", "web_fetch", "browser_open", "browser_read", "browser_click", "browser_links",
                  "read_document", "read_file", "search_documents", "query_data", "search_text"}
EVIDENCE_TOOLS.add("gather")
EVIDENCE_TOOLS.add("encyclopedia")
GATHER_RAW_MARK = "=== RAW RESULTS (what the workers actually read) ==="   # must match gopher.RAW_MARK
WRITE_TOOLS = {"write_file", "edit_file", "create_document", "artifact_create", "artifact_edit", "move_path",
               "make_dir"}               # their results ("created C:\\...") are evidence for claims about those files
CALC_TOOLS = {"calculate", "python_sandbox", "python_host", "query_data"}
_URL_RX = re.compile(r"https?://[^\s\]\)\"'<>|]+")
_PAGE_HEAD_RX = re.compile(r"(?m)^URL: (https?://[^\s\]\)\"'<>|]+)")    # browser page header
_SAVED_RX = re.compile(r"(?:saved at|full text:)\s+([A-Za-z]:[\\/][^\s\]]+?\.txt)")
_LABEL_RX = re.compile(r"\b(estimate[sd]?|estimated|approx(?:imately)?|roughly|unverified|not verified|could not "
                       r"verify|couldn'?t verify|assum(?:e|ption)|likely|probably|my (?:guess|view|assessment))\b", re.I)


def _norm(s):
    return " ".join(re.sub(r"[^\w$%.]+", " ", str(s or "").lower()).split())


def _numbers(s, loose=True):
    """Numbers in a text, normalised: '1,200' -> 1200, '$275k' -> 275000, '1.3 million' -> 1300000, '45%' -> 45."""
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|bn|b|thousand|million|billion)?\b", str(s or "").lower()):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}.get(m.group(2) or "", 1)
        out.append(v * mult)
        if mult != 1 and loose:
            out.append(v)                        # "275k" may be written "275" next to a "k" elsewhere
    return out


def _url_key(u):
    u = str(u or "").strip().rstrip(".,;:)").lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.rstrip("/")


def build_evidence(history):
    """-> (docs, user_text, calc_text). docs: {source key: text} for every URL / file the agent read in the chat;
    text around a URL in a result is credited to that URL; saved full results are read from disk."""
    docs, user_parts, calc_parts = {}, [], []

    def add(key, text):
        if key and text:
            docs[key] = (docs.get(key, "") + "\n" + text)[-400000:]

    def ingest(blob):
        """Read text: credited to everything, and each part to the URL it sits under."""
        add("*all*", blob)
        urls = [(mm.start(), mm.group(0)) for mm in _URL_RX.finditer(blob)
                if not blob[max(0, mm.start() - 4):mm.start()].rstrip().endswith("->")]
        for i, (pos, u) in enumerate(urls):
            end = urls[i + 1][0] if i + 1 < len(urls) else min(len(blob), pos + 20000)
            add(_url_key(u), blob[max(0, pos - 300):end])

    for m in history:
        role, c = m.get("role"), m.get("content")
        text = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
        if role == "user":
            # the app's background search for the message rides in a reminder block: it is evidence the agent read
            # (like a search result), not the user's words; other app notes are neither
            for blk in re.findall(r"<system-reminder>(.*?)</system-reminder>", text, flags=re.S):
                if re.search(r"Reference[^\n]{0,80}(found automatically|for any facts)", blk[:400]):
                    ingest(blk)
            user_parts.append(re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S))
            continue
        if role != "tool":
            continue
        name = m.get("name") or ""
        if name in CALC_TOOLS:
            calc_parts.append(text)
        if name not in EVIDENCE_TOOLS and name not in CALC_TOOLS and name not in WRITE_TOOLS:
            continue
        full = [text]
        for p in _SAVED_RX.findall(text):              # the full text behind a shortened result
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    full.append(f.read(400000))
            except OSError:
                pass
        blob = "\n".join(full)
        if name == "gather":                           # fan-out: only what the workers read counts, never
            blob = "\n".join(part.split(GATHER_RAW_MARK, 1)[1]  # a worker's own summary of it
                             for part in full if GATHER_RAW_MARK in part)
            if not blob:
                continue
        add("*all*", blob)
        # a browser page lists its links ('[12] link "San Jose" -> https://...'): those are not new sources,
        # so they must not split the page; the whole page belongs to its "URL:" header
        urls = [(mm.start(), mm.group(0)) for mm in _URL_RX.finditer(blob)
                if not blob[max(0, mm.start() - 4):mm.start()].rstrip().endswith("->")]
        for i, (pos, u) in enumerate(urls):            # text up to the next URL belongs to this URL
            end = urls[i + 1][0] if i + 1 < len(urls) else min(len(blob), pos + 20000)
            add(_url_key(u), blob[max(0, pos - 300):end])
        heads = list(_PAGE_HEAD_RX.finditer(blob))
        for i, mm in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(blob)
            add(_url_key(mm.group(1)), blob[mm.start():end])
        for p in re.findall(r"[A-Za-z]:[\\/][^\s\"'<>|]+\.\w{1,5}", text):
            add(_norm_path(p), blob)
    return docs, "\n".join(user_parts), "\n".join(calc_parts)


def _norm_path(p):
    return os.path.normcase(os.path.normpath(str(p).strip().strip("'\"")))


def _contains(key, text):
    """Does the claim's key detail appear in the text? Numbers by value (±0.5%), words all present."""
    if not key or not text:
        return False
    nums = _numbers(key, loose=False)
    if nums:
        have = _numbers(text)
        return all(any(abs(h - n) <= max(abs(n) * 0.005, 0.01) for h in have) for n in nums[:4] if n)
    # the key's words normalised exactly like the text ("Paisano's" -> "paisano s"): apostrophes and hyphens used
    # to stay in the key's words but not in the text, so a name like Paisano's, Sonny's or O'Brien never matched
    words = [w for w in _norm(key).split() if len(w) >= 3 and w not in {"the", "and", "inc", "llc", "ltd"}]
    if not words:
        return _norm(key) in _norm(text)
    t = _norm(text)
    return all(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words)


def check(claims, history, draft):
    """-> list of {claim, key, source, ok, why, matched} for the extracted claims."""
    docs, user_text, calc_text = build_evidence(history)
    allread = docs.get("*all*", "")
    out = []
    for c in claims[:25]:
        claim, key, src = str(c.get("claim") or ""), str(c.get("key") or ""), str(c.get("source") or "").strip()
        r = {"claim": claim, "key": key, "source": src, "ok": False, "why": "", "matched": ""}
        s = src.lower()
        url_key = _url_key(key) if _URL_RX.match(key.strip()) else ""
        if re.fullmatch(r"\d{1,3}", key.strip()):
            # a bare small number ("3") matches almost any page by accident ("30 March"): require it as a whole
            # number near a word of the claim ("3 … symphonies"), else treat it as unsupported
            words = [w for w in re.findall(r"[a-z]{4,}", claim.lower()) if w not in ("many", "number", "with", "that")]
            near = lambda text: any(re.search(rf"\b{key.strip()}\b[^.\n]{{0,60}}{re.escape(w[:5])}|"
                                              rf"{re.escape(w[:5])}[^.\n]{{0,60}}\b{key.strip()}\b", text.lower())
                                    for w in words)
            if not (near(allread) or near(user_text) or near(calc_text)):
                r["why"] = f"the number {key} does not appear next to what the claim counts in anything read"
                out.append(r)
                continue
        if not key.strip():
            r.update(ok=True, why="no specific detail")
        elif url_key and (url_key in docs or url_key in _url_key_text(allread)):
            # the claim IS a link (a repo, a page): it is verified when that page was opened or a search listed it —
            # a GitHub page does not print its own address, so "the key must appear in the page" can never pass,
            # and the model burned 19 steps re-searching links it had already opened
            r.update(ok=True, matched="that page was opened / listed in this chat")
        elif s in ("user", "the user", "user's message", "user resume", "user's resume") and _contains(key, user_text):
            r.update(ok=True, matched="the user's messages")
        elif s.startswith("calc") or s in ("calculation", "code", "python", "query"):
            if _contains(key, calc_text):
                r.update(ok=True, matched="a calculation in this chat")
            else:
                r["why"] = "no calculator/code result in this chat shows this number"
        elif s and s != "none":
            doc = None
            if _URL_RX.match(src):
                k = _url_key(src)
                doc = docs.get(k) or next((v for dk, v in docs.items() if dk != "*all*" and (dk.startswith(k) or k.startswith(dk))), None)
            elif re.match(r"[A-Za-z]:[\\/]", src):
                doc = docs.get(_norm_path(src))
            if doc is None:
                r["why"] = "the cited source was not read in this chat"
                if _contains(key, allread):
                    r.update(ok=True, why="", matched="another source read in this chat")
            elif _contains(key, doc):
                r.update(ok=True, matched=src)
            else:
                r["why"] = f"'{key}' does not appear in the cited source"
        else:
            r["why"] = "no source"
            if _contains(key, allread):
                r.update(ok=True, why="", matched="a source read in this chat (cite it)")
            elif _contains(key, user_text):
                r.update(ok=True, why="", matched="the user's messages")
        if not r["ok"] and _labelled(key, draft):
            r.update(ok=True, why="labelled as an estimate / unverified in the answer", matched="labelled")
        out.append(r)
    return out


def _url_key_text(text):
    """Every URL in a text, normalised like _url_key, joined (for 'was this link listed anywhere')."""
    return " ".join(_url_key(u) for u in _URL_RX.findall(text or ""))


def _labelled(key, draft):
    """The answer already marks this claim as an estimate / unverified (within the same sentence or line)."""
    d = str(draft or "")
    k = key.strip()
    if not k:
        return False
    i = d.lower().find(k.lower()[:40])
    if i < 0:
        return False
    a, b = max(0, i - 160), min(len(d), i + len(k) + 160)
    return bool(_LABEL_RX.search(d[a:b]))


def parse_claims(text):
    try:
        d = json.loads(re.search(r"\{.*\}", text or "", re.S).group(0))
        cl = d.get("claims") or []
        return [c for c in cl if isinstance(c, dict)]
    except (AttributeError, ValueError):
        return []


# ---- dates after today: data for them cannot have been observed yet (a forecast, projection or climate normal) ----
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                            "dec"])}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|" \
       r"nov(?:ember)?|dec(?:ember)?)\.?"
_DASH = r"\s*(?:-|–|—|to|through|thru)\s*"
# "October 16-29, 2026", "Oct 16 – Nov 2, 2026", "October 16, 2026"
_MDY_RX = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:{_DASH}(?:{_MON}\s+)?(\d{{1,2}})(?:st|nd|rd|th)?)?,?\s+(\d{{4}})\b", re.I)
# "16 October 2026", "16-29 October 2026"
_DMY_RX = re.compile(rf"\b(\d{{1,2}})(?:{_DASH}(\d{{1,2}}))?\s+{_MON},?\s+(\d{{4}})\b", re.I)
_ISO_RX = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def future_dates(text, today=None):
    """-> the explicit dates (with a year) in text that are after today, as written. Mechanical: month-day-year,
    day-month-year and ISO dates, ranges by their last day. Dates without a year are ignored (ambiguous)."""
    import datetime
    today = today or datetime.date.today()
    found = []

    def add(span, y, m, d):
        try:
            if datetime.date(int(y), int(m), int(d)) > today and span not in found:
                found.append(span)
        except ValueError:
            pass
    for mt in _MDY_RX.finditer(text or ""):
        mon2 = mt.group(3) or mt.group(1)
        add(mt.group(0).strip(), mt.group(5), _MONTHS[mon2.lower()[:3]], mt.group(4) or mt.group(2))
    for mt in _DMY_RX.finditer(text or ""):
        add(mt.group(0).strip(), mt.group(4), _MONTHS[mt.group(3).lower()[:3]], mt.group(2) or mt.group(1))
    for mt in _ISO_RX.finditer(text or ""):
        add(mt.group(0), mt.group(1), mt.group(2), mt.group(3))
    return found[:8]


# ---- where every number in an answer came from (no model judgment) ----------------------------------------------
# A figure in the final answer must appear in the user's messages, in something a tool returned (pages, files, data,
# calculator / code output), or be the verified result of an equation written in the answer itself. Anything else was
# worked out in the model's head — the usual source of "$16,879.63 instead of $16,881.03".
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_ANS_NUM_RX = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s*(?:k|m|mn|bn|b|thousand|million|billion)\b)?",
                         re.I)
_STRIP_RX = re.compile(r"https?://\S+|\[[^\]]*\]\([^)]*\)|[A-Za-z]:[\\/][^\s)\"']+|`[^`]*`|```[\s\S]*?```|"
                       r"^\s*\d+[.)]\s|\[\d+\]|#\d[\d,]*\d|#\d", re.M)     # "#1,868" is a rank, not a figure
_YEAR_CTX_RX = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{0,2},?\s*$|"
                          r"\b(in|since|by|of|from|year|until|through|to|and|fy|q[1-4]|before|after|during)\s*$|[-–]\s*$",
                          re.I)


def _delatex(text):
    """LaTeX number formatting -> plain: 1{,}250{,}025{,}000 and 1\\,250\\,025 -> 1,250,025,000 (it was read as
    the separate numbers 250 and 025)."""
    t = str(text or "")
    t = re.sub(r"(?<=\d)\{,\}(?=\d)", ",", t)
    t = re.sub(r"(?<=\d)\\[,;! ](?=\d)", ",", t)
    return t


def _answer_numbers(text):
    """-> [(raw, value, unit)] for the figures in an answer worth checking. Skipped: URLs, paths, code, citation
    markers, list numbering, small integers (counts like "top 3"), years in a date context."""
    t = _STRIP_RX.sub(" ", _delatex(text))
    out = []
    for m in _ANS_NUM_RX.finditer(t):
        whole, frac, suf = m.group(1), m.group(2) or "", (m.group(3) or "").strip().lower()
        if not frac and not suf and re.match(r"(st|nd|rd|th)\b", t[m.end():m.end() + 3], re.I):
            continue                                     # ordinals: "the 14th", "3rd place" — dates and ranks
        v = float((whole + frac).replace(",", ""))
        unit = 10 ** -len(frac[1:]) if frac else 1.0
        if suf:
            v, unit = v * _MULT[suf], unit * _MULT[suf]
        if not frac and not suf and v <= 12:
            continue                                     # counts, ordinals, months
        if not frac and not suf and "," not in whole and 1900 <= v <= 2100:
            before = t[max(0, m.start() - 16):m.start()]
            if _YEAR_CTX_RX.search(before) or re.match(r"\s*(census|report|survey|data|estimate|q[1-4]|[-–])", t[m.end():], re.I):
                continue                                 # a year
        out.append((m.group(0).strip(), v, unit))
    return out


def _evidence_numbers(history):
    """Every number the user gave or any tool returned in this chat (saved full results included)."""
    vals = set()
    for m in history:
        if m.get("role") not in ("user", "tool"):
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
        if m.get("role") == "user":
            text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
        blobs = [text]
        for p in _SAVED_RX.findall(text):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    blobs.append(f.read(400000))
            except OSError:
                pass
        for b in blobs:
            vals.update(round(x, 6) for x in _numbers(b, loose=True))
            vals.update(float(x) for x in re.findall(r"(?<![\d.])(\d+)(?:st|nd|rd|th)\b", b, re.I))   # "14th" -> 14
    return vals


def _matches(v, unit, vals):
    """v (as displayed, to `unit` precision) equals a known value, or is that value rounded to the shown precision."""
    tol = max(unit * 0.5 + 1e-9, abs(v) * 1e-9)
    return any(abs(v - e) <= tol or abs(v - e) <= max(1e-9, abs(e) * 1e-9) for e in vals) or \
        any(abs(v - e * 100) <= tol for e in vals if 0 < e < 1)          # 0.43 in a source shown as 43%


# equations written in the answer: "a × b = c", "(1 + r/n)^84 = 1.35037…", chains "a = b = c"
_EXPR_CHARS = r"[\d\s.,+\-*/×x÷^()$%]"
_EXPR_TAIL_RX = re.compile(rf"({_EXPR_CHARS}*\d{_EXPR_CHARS}*)$")
_RHS_RX = re.compile(r"^\s*\$?\s*(-?\d[\d,]*(?:\.\d+)?)\s*(\.\.\.|…)?")


def _safe_eval(expr):
    import ast
    import operator as op
    e = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace("$", "").replace(",", "")
    e = re.sub(r"(?<=\d)\s*x\s*(?=[\d(])", "*", e)
    e = re.sub(r"(\.\.\.|…)", "", e).replace("%", "/100")
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
           ast.USub: op.neg, ast.UAdd: op.pos}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 10000:
                raise ValueError("exponent too large")
            return ops[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.operand))
        raise ValueError("not arithmetic")
    return ev(ast.parse(e.strip(), mode="eval"))


def equation_issues(text):
    """-> (wrong, verified): equations in the text whose stated result is off by more than the precision shown
    ('…' = truncated), and the results that checked out (they count as calculated)."""
    wrong, verified = [], set()
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)        # list markers are not part of an expression
        parts = line.split("=")
        for i in range(len(parts) - 1):
            lm = _EXPR_TAIL_RX.search(parts[i])
            rm = _RHS_RX.match(parts[i + 1])
            if not lm or not rm:
                continue
            expr = lm.group(1).strip()
            if not re.search(r"\d\s*[-+*/×x÷^]\s*[\d(]|\)\s*[-+*/×x÷^]|\)\s*\^", expr):
                continue                                  # no operator between numbers: not an equation
            try:
                got = _safe_eval(expr)
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
                continue
            shown = rm.group(1).replace(",", "")
            stated = float(shown)
            dec = len(shown.split(".")[1]) if "." in shown else 0
            unit = 10 ** -dec
            tol = unit * (1.0 if rm.group(2) else 0.5) + abs(got) * 1e-9
            if abs(got - stated) <= tol:
                verified.add(round(stated, 6))
            else:
                exact = f"{got:,.{max(dec, 2)}f}".rstrip("0").rstrip(".") if abs(got) < 1e15 else str(got)
                wrong.append(f"{expr.strip()} = {rm.group(1)} (it is {exact})")
    return wrong[:6], verified


_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
_COUNT_RX = re.compile(r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
                       r"(?:[a-z-]+\s+)?([a-z]{4,})", re.I)
_COUNT_SKIP = {"years", "year", "days", "hours", "minutes", "seconds", "times", "percent", "people", "sources",
               "results", "steps", "points", "items", "things", "ways", "pages", "links", "examples", "months", "weeks"}


def count_issues(answer, history, extra_evidence=""):
    """Counts are facts: "3 symphonies", "two Grammys". Small numbers escape the figure check (they are everywhere —
    "30 March" contains a 3), so a count is supported only when a source states that number NEAR the same noun.
    Counts that echo the question are fine. -> unsupported count phrases."""
    user = " ".join((m.get("content") if isinstance(m.get("content"), str) else "") for m in history
                    if m.get("role") == "user")
    texts = [user, extra_evidence or ""]
    for m in history:
        if m.get("role") == "tool":
            c = m.get("content")
            t = c if isinstance(c, str) else ""
            texts.append(t)
            for p in _SAVED_RX.findall(t):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        texts.append(f.read(400000))
                except OSError:
                    pass
    blob = "\n".join(texts).lower()
    out = []
    for m in _COUNT_RX.finditer(_STRIP_RX.sub(" ", answer or "")):
        num, noun = m.group(1).lower(), m.group(2).lower()
        if noun in _COUNT_SKIP or noun.endswith("ly"):
            continue
        val = _NUMWORD.get(num, None) if not num.isdigit() else int(num)
        if val is None or val > 999 or (num.isdigit() and 1900 <= val <= 2100):
            continue
        if re.search(rf"\b{re.escape(num)}\b[^.\n]{{0,40}}{re.escape(noun[:5])}", user.lower()):
            continue                                     # the question's own count
        forms = [str(val)] + [w for w, v in _NUMWORD.items() if v == val]
        stem = re.escape(noun[:5])
        if not any(re.search(rf"\b{f}\b[^.\n]{{0,60}}{stem}|{stem}[^.\n]{{0,60}}\b{f}\b", blob) for f in forms):
            out.append(m.group(0).strip())
    return list(dict.fromkeys(out))[:6]


def _derived(history, extra_evidence=""):
    """Results of ONE operation (+ - × ÷) on two numbers the conversation already has: the user's, tool results, and
    earlier answers (a follow-up like "multiply that by 4" works on the last answer). A figure that equals one of these
    is verified arithmetic, not a guess — "230 × 20" answered as 4,600 used to be sent back as uncalculated."""
    ops = []
    for m in history[-12:]:
        if m.get("role") not in ("user", "tool", "assistant") or m.get("tool_calls"):
            continue
        c = m.get("content")
        t = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
        t = re.sub(r"<system-reminder>.*?</system-reminder>", " ", t, flags=re.S)
        if m.get("role") == "tool" and len(t) > 600:
            continue                                     # a page of numbers would make everything "derivable"
        ops += [x for x in _numbers(t, loose=False) if x]
    ops += [x for x in _numbers(extra_evidence or "", loose=False) if x][:10]
    ops = list(dict.fromkeys(round(x, 9) for x in ops))[-24:]
    out = set()
    for i, a in enumerate(ops):
        for b in ops[i + 1:]:
            out.update({round(a * b, 6), round(a + b, 6), round(abs(a - b), 6)})
            if b:
                out.add(round(a / b, 6))
            if a:
                out.add(round(b / a, 6))
    return out


def math_issues(answer, history, extra_evidence=""):
    """-> (wrong equations, unsupported figures). A figure is supported when the user gave it, a tool returned it
    (or it is that value rounded), or an equation in the answer that checks out produced it."""
    wrong, verified = equation_issues(answer)
    vals = _evidence_numbers(history)
    if extra_evidence:
        vals.update(round(x, 6) for x in _numbers(extra_evidence, loose=True))
    vals |= verified
    unsupported, derived = [], None
    for raw, v, unit in _answer_numbers(answer):
        if _matches(v, unit, vals) or raw in unsupported:
            continue
        if derived is None:
            derived = _derived(history, extra_evidence)
        if _matches(v, unit, derived):
            continue                                     # one operation on numbers already in the conversation
        unsupported.append(raw)
    return wrong, unsupported[:10]
