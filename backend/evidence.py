"""The evidence table: one CSV per chat where facts are recorded as rows instead of being held in the model's context.

Every row says what the figure measures (scope) and quotes the source. Rows are checked deterministically against
what was actually read in the chat (is the quote in that source, is the value in the quote), so aggregates computed
from the table with query_data / calculate rest on verified rows — no re-typing numbers, no mixing base and total.
"""
import csv
import os
import re
import threading

import factcheck

COLUMNS = ["id", "entity", "metric", "value", "num", "unit", "scope", "period", "source_url", "quote", "by", "verified"]
FIELDS = ["entity", "metric", "value", "unit", "scope", "period", "source_url", "quote"]
ROW_SCHEMA = {"type": "object", "properties": {
    "entity": {"type": "string", "description": "who/what the fact is about (job title, company, place, product…)"},
    "metric": {"type": "string", "description": "what is measured, e.g. 'average salary'"},
    "value": {"type": "string", "description": "the value exactly as the source states it, e.g. '$150,513'"},
    "unit": {"type": "string", "description": "e.g. USD/year, %, people"},
    "scope": {"type": "string", "description": "exactly what the figure covers: base or total pay, median or average, "
                                               "which place, group, sample"},
    "period": {"type": "string", "description": "date or period the figure refers to, as stated"},
    "source_url": {"type": "string", "description": "the URL (or file path) it came from"},
    "quote": {"type": "string", "description": "the sentence from the source that states it, copied exactly"}},
    "required": ["entity", "metric", "value", "scope", "source_url", "quote"]}
_LOCK = threading.Lock()


def table_path(ctx):
    """agent_attachments/<chat>/evidence.csv (the chat's own folder is always usable by query_data)."""
    import agent_tools as at
    conv = getattr(ctx, "conv_id", None) or "misc"
    import paths
    base = paths.data_dir()
    folder = os.path.join(base, "agent_attachments", at._safe_name(conv))
    os.makedirs(folder, exist_ok=True)
    if not ctx.inside(os.path.join(folder, "x")) and folder not in ctx.roots:
        ctx.roots.append(folder)
    return os.path.join(folder, "evidence.csv")


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _dup_key(row):
    """Same source + same value = the same fact, however its entity/metric/scope were worded."""
    src = str(row.get("source_url") or "").strip()
    src = factcheck._url_key(src) if factcheck._URL_RX.match(src) else src.lower()
    return (src, row.get("num") or _norm(row.get("value")))


def append(ctx, rows, by):
    """Add rows (dicts with FIELDS + 'verified'); returns them with their new ids (r1, r2, …). A row repeating a
    verified fact already in the table (same source, same value) is not added — averages would count it twice —
    and comes back with 'duplicate_of' set instead of an id."""
    path = table_path(ctx)
    with _LOCK:
        existing = read_rows(path)
        n = len(existing)
        seen = {_dup_key(r): r["id"] for r in existing if r.get("verified") == "yes"}
        new, out = [], []
        for r in rows:
            row = {k: str(r.get(k, "") or "").strip() for k in FIELDS}
            nums = factcheck._numbers(row["value"], loose=False)
            row.update(by=by, verified=str(r.get("verified", "")),
                       num=(f"{nums[0]:g}" if nums else ""))      # numeric value for SQL aggregates
            key = _dup_key(row)
            if key in seen:                                # (seen holds verified rows only)
                out.append(dict(row, id="", duplicate_of=seen[key]))
                continue
            n += 1
            row["id"] = f"r{n}"
            if row["verified"] == "yes":
                seen[key] = row["id"]
            new.append(row)
            out.append(row)
        write_header = not os.path.exists(path)
        if new:
            with open(path, "a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS)
                if write_header:
                    w.writeheader()
                w.writerows(new)
    return path, out


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w$€£%.,:/-]+", " ", str(s or "").lower())).strip()


def _quote_in(quote, text):
    """Is the quote in the text? Exact after normalising, else ≥85% of its words and every number in it present."""
    q, t = _norm(quote), _norm(text)
    if not q or not t:
        return False
    if q in t:
        return True
    words = [w for w in q.split() if len(w) > 2]
    if not words:
        return False
    nums = factcheck._numbers(quote, loose=False)
    have_nums = factcheck._numbers(text)
    if any(not any(abs(h - n) <= max(abs(n) * 0.005, 0.01) for h in have_nums) for n in nums if n):
        return False
    tset = set(t.split())
    return sum(1 for w in words if w in tset) / len(words) >= 0.85


def check(rows, docs):
    """-> one verdict per row: 'yes' or what is wrong. docs: factcheck.build_evidence(...)[0]."""
    out = []
    for r in rows:
        missing = [k for k in ("entity", "metric", "value", "scope", "source_url", "quote") if not str(r.get(k) or "").strip()]
        if missing:
            out.append("missing " + ", ".join(missing))
            continue
        src = str(r["source_url"]).strip()
        doc = None
        if factcheck._URL_RX.match(src):
            k = factcheck._url_key(src)
            doc = docs.get(k) or next((v for dk, v in docs.items() if dk != "*all*" and (dk.startswith(k) or k.startswith(dk))), None)
        elif re.match(r"[A-Za-z]:[\\/]", src):
            doc = docs.get(factcheck._norm_path(src))
        if doc is None:
            out.append("that source was not read in this chat")
        elif not _quote_in(r["quote"], doc):
            out.append("the quote is not in that source (copy the sentence exactly)")
        elif not factcheck._contains(str(r["value"]), r["quote"]):
            out.append("the value is not in the quote")
        elif (factcheck.future_dates(str(r.get("period") or ""))
              and not re.search(r"forecast|projected|projection|normal|expected|predicted", str(r["scope"]), re.I)):
            out.append("its period is after today, so it was not observed: put 'forecast' or 'normal' in scope, or "
                       "find observed data for the right period")
        else:
            out.append("yes")
    return out


def markdown(rows, not_found=()):
    """Rows as a small markdown table for the chat card / worker tile."""
    if not rows:
        return ""
    out = ["| Fact | Value | Scope | Source |", "|---|---|---|---|"]
    for r in rows[:20]:
        cell = lambda s: str(s or "").replace("|", "/").replace("\n", " ")[:80]
        src = str(r.get("source_url") or "")
        link = f"[{re.sub(r'^https?://(www[.])?', '', src)[:40]}]({src})" if src.startswith("http") else cell(src)
        ok = "" if r.get("verified") in ("yes", "", None) else " ⚠"
        out.append(f"| {cell(r.get('entity'))} — {cell(r.get('metric'))} | {cell(r.get('value'))}{ok} | "
                   f"{cell(r.get('scope'))} | {link} |")
    if not_found:
        out.append("\nNot found: " + "; ".join(str(x) for x in not_found)[:400])
    return "\n".join(out)


def summary(rows, limit=40):
    """A compact table for the model: id | entity | metric | value | scope | period | source | ok."""
    lines = []
    for r in rows[:limit]:
        src = re.sub(r"^https?://(www\.)?", "", r.get("source_url", ""))[:50]
        lines.append(f"{r['id']} | {r.get('entity', '')[:40]} | {r.get('metric', '')[:30]} | {r.get('value', '')} "
                     f"{r.get('unit', '')} | {r.get('scope', '')[:50]} | {r.get('period', '')[:20]} | {src} | "
                     f"{'verified' if r.get('verified') == 'yes' else 'UNVERIFIED: ' + r.get('verified', '')}")
    return "\n".join(lines)
