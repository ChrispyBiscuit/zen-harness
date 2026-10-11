"""Playbooks: short, general how-to-approach guides for kinds of tasks, matched to each new request.

A playbook is a markdown file in playbooks/ with a small front matter block:

    ---
    name: compare-options
    title: Compare options and recommend one
    triggers: compare, which is better, pros and cons, recommend, versus
    examples:
      - "compare three project management tools for a small team"
      - "which laptop should I buy for video editing under $1500"
    applies_when: the user wants a decision between alternatives
    ---
    **Done looks like:** ...

The front matter is what gets indexed (title + triggers + examples + applies_when): matching is on the *intent*
of a request, never on the body. The body is injected into the prompt when the playbook matches. Experience notes
("source X worked", "site Y is a dead end") are kept per playbook in the memory store and shown under it.

Everything here is pattern-level and general-purpose: no playbook is about one specific task.
"""
import hashlib
import json
import os
import re
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYBOOKS_DIR = os.path.join(APP_DIR, "playbooks")
STATE_PATH = os.path.join(os.environ.get("LWS_DATA_DIR") or APP_DIR, "playbooks_state.json")
KIND, NOTE_KIND = "playbook", "playbook_note"
MAX_INJECT_CHARS = 2600            # ≈ 600 tokens: one playbook, or one and a trimmed second
NOTES_SHOWN = 5
DEFAULT_MIN_SIM = 0.55
NONE_PREFIX = "none-"              # sink playbooks: when one of these matches best, nothing is injected

_cache = {"mtimes": {}, "books": {}}


# ---------------------------------------------------------------- files
def _parse(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        return None
    head, body = m.group(1), m.group(2).strip()
    meta, cur = {}, None
    for line in head.splitlines():
        if re.match(r"^\s+-\s+", line) and cur:
            meta.setdefault(cur, [])
            if isinstance(meta[cur], list):
                meta[cur].append(line.strip()[1:].strip().strip('"'))
            continue
        mm = re.match(r"^([\w-]+):\s*(.*)$", line)
        if mm:
            cur = mm.group(1)
            val = mm.group(2).strip()
            meta[cur] = val if val else []
    name = str(meta.get("name") or os.path.splitext(os.path.basename(path))[0]).strip()
    ex = meta.get("examples") or []
    if isinstance(ex, str):
        ex = [ex]
    return {"name": name, "title": str(meta.get("title") or name), "triggers": str(meta.get("triggers") or ""),
            "examples": [str(e) for e in ex], "applies_when": str(meta.get("applies_when") or ""),
            "body": body, "path": path, "mtime": os.path.getmtime(path)}


def load_all(force=False):
    """{name: playbook} from the playbooks folder (re-read only when a file changed)."""
    if not os.path.isdir(PLAYBOOKS_DIR):
        return {}
    files = sorted(f for f in os.listdir(PLAYBOOKS_DIR) if f.endswith(".md") and not f.startswith("_"))
    mt = {f: os.path.getmtime(os.path.join(PLAYBOOKS_DIR, f)) for f in files}
    if not force and mt == _cache["mtimes"]:
        return _cache["books"]
    books = {}
    for f in files:
        try:
            pb = _parse(os.path.join(PLAYBOOKS_DIR, f))
        except OSError:
            pb = None
        if pb:
            books[pb["name"]] = pb
    _cache.update(mtimes=mt, books=books)
    return books


def _state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"disabled": []}


def _save_state(st):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
    except OSError:
        pass


def disabled():
    return set(_state().get("disabled") or [])


def set_enabled(name, on):
    st = _state()
    d = set(st.get("disabled") or [])
    (d.discard if on else d.add)(name)
    st["disabled"] = sorted(d)
    _save_state(st)


def save_body(name, body):
    """Edit a playbook's body in place (front matter unchanged)."""
    pb = load_all().get(name)
    if not pb:
        raise KeyError(name)
    with open(pb["path"], encoding="utf-8") as f:
        raw = f.read()
    m = re.match(r"^(---\s*\n.*?\n---\s*\n)", raw, re.S)
    with open(pb["path"], "w", encoding="utf-8", newline="") as f:
        f.write(m.group(1) + body.strip() + "\n")
    load_all(force=True)


# ---------------------------------------------------------------- index
def _surfaces(pb):
    """What a request is matched against — several short phrasings of the intent, never the method. Each is
    embedded on its own, so a request only has to resemble one of them."""
    out = [f"{pb['title']}: {pb['applies_when']}".strip(": "), pb["title"] + " — " + pb["triggers"]]
    out += [e for e in pb["examples"] if e.strip()]
    return [" ".join(s.split()) for s in out if s.strip()]


def _surface(pb):
    return "\n".join(_surfaces(pb))


def index(store, force=False):
    """Upsert every playbook into the memory store (kind 'playbook'); remove entries for deleted files.
    Cheap when nothing changed (a content hash is kept in the item's tags)."""
    books = load_all(force)
    have = {}
    for r in store.list(KIND, 5000):
        have.setdefault(r["source"], []).append(r)
    changed = 0
    for name, pb in books.items():
        h = "h:" + hashlib.md5(_surface(pb).encode("utf-8")).hexdigest()[:12]
        rows = have.pop(name, [])
        if rows and all(r.get("tags") == h for r in rows) and len(rows) == len(_surfaces(pb)):
            continue
        for r in rows:
            store.delete(r["id"])
        for i, text in enumerate(_surfaces(pb)):
            store.add(KIND, text, tags=h, source=name, title=pb["title"], pos=i)
        changed += 1
    for name, rows in have.items():                      # playbook file removed
        for r in rows:
            store.delete(r["id"])
        changed += 1
    return changed


# ---------------------------------------------------------------- matching
def rank(store, request, k=16):
    """-> [(name, sim)] best first, one entry per playbook (its best-matching phrasing); sinks included."""
    request = " ".join(str(request or "").split())
    if len(request) < 8:
        return []
    books = load_all()
    sims = {}
    for h in store.search(request, (KIND,), k=max(k, 40), min_sim=0.0):
        src = h.get("source")
        if src in books:
            sims.setdefault(src, []).append(h.get("sim") or 0.0)
    out = []
    for n, ss in sims.items():
        ss.sort(reverse=True)
        # a playbook that matches several of its phrasings beats one lucky word in a single phrasing:
        # the second-best phrasing counts, and a playbook with only one hit is scored as if its second were weak
        second = ss[1] if len(ss) > 1 else 0.45
        out.append((n, round(0.65 * ss[0] + 0.35 * second, 3)))
    return sorted(out, key=lambda x: -x[1])


def match(store, request, min_sim=DEFAULT_MIN_SIM):
    """-> [(playbook, sim)] best first: the top playbook at or above min_sim, plus a second when it is nearly as
    good and a different pattern. [] when nothing is close enough, or when small talk matches best."""
    if len(" ".join(str(request or "").split()).split()) < 3:
        return []
    books, off = load_all(), disabled()
    ranked = [(n, s) for n, s in rank(store, request) if n not in off]
    if not ranked or ranked[0][1] < min_sim:
        return []
    if ranked[0][0].startswith(NONE_PREFIX):
        return []
    out = [(books[ranked[0][0]], ranked[0][1])]
    if len(ranked) > 1 and not ranked[1][0].startswith(NONE_PREFIX) and \
            ranked[1][1] >= max(min_sim + 0.07, ranked[0][1] - 0.02):   # a second only when it is nearly as strong
        out.append((books[ranked[1][0]], ranked[1][1]))
    return out


# ---------------------------------------------------------------- experience notes
_PATH_RX = re.compile(r"[A-Za-z]:[\\/]|\\\\|/Users/|/home/|\b(?:[A-Z][a-z]+ ){2}[A-Z][a-z]+\b")


def notes_for(store, name, limit=NOTES_SHOWN):
    rows = [r for r in store.list(NOTE_KIND, 500) if r.get("source") == name]
    return rows[:limit]


def add_note(store, name, text, auto=False):
    """One short reusable lesson for this kind of task (where to look, what is a dead end). Rejects notes that
    carry task-specific data (file paths, full personal names) and near-duplicates."""
    text = " ".join(str(text or "").split())
    if not text or name not in load_all():
        raise ValueError("unknown playbook or empty note")
    if len(text) > 300:
        raise ValueError("keep a note under 300 characters: one reusable lesson")
    if _PATH_RX.search(text):
        raise ValueError("notes are where to look / what to avoid for this KIND of task — no file paths or "
                         "people's names (that belongs in the answer or in memory_save)")
    for r in notes_for(store, name, 50):
        if r["text"].lower() == text.lower():
            return r["id"]
    try:
        dup = [r for r in store.search(text, (NOTE_KIND,), k=3) if r.get("source") == name and (r.get("sim") or 0) >= 0.9]
    except Exception:
        dup = []
    if dup:
        return dup[0]["id"]
    return store.add(NOTE_KIND, text, tags="auto" if auto else "", source=name, title=time.strftime("%Y-%m-%d"))


def auto_dead_end(store, name, domain, why="requires sign-in"):
    try:
        return add_note(store, name, f"Dead end: {domain} {why} — use other sources.", auto=True)
    except ValueError:
        return None


# ---------------------------------------------------------------- rendering
def render(store, matches):
    """The prompt block for the matched playbook(s), under MAX_INJECT_CHARS."""
    parts = []
    budget = MAX_INJECT_CHARS
    for i, (pb, _sim) in enumerate(matches):
        body = pb["body"]
        if i > 0 and len(body) > budget - 200:
            body = body[:max(0, budget - 200)].rsplit("\n", 1)[0] + "\n…"
        notes = notes_for(store, pb["name"])
        block = f"Playbook for this kind of task — \"{pb['title']}\" (follow it; adapt the specifics):\n{body}"
        if notes:
            block += "\n\nFrom earlier runs of this kind of task:\n" + "\n".join(
                f"- [{n.get('title') or ''}] {n['text']}" for n in notes)
        if len(block) > budget:
            block = block[:budget].rsplit("\n", 1)[0] + "\n…"
        parts.append(block)
        budget -= len(block) + 2
        if budget < 300:
            break
    return "\n\n".join(parts)
