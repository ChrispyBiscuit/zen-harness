"""Slash commands: "/<name> <request>" runs the request with a chosen playbook or guide.

Two libraries can be invoked by name:
  * playbooks/*.md — task playbooks (normally matched automatically to each request)
  * guides/*.md    — longer reference guides (normally read on demand with read_guide)

    /hard_search find the 2014 annual report of X     -> the request + the hard_search guide, followed from the start
    /compare-options laptops for video editing        -> the request + that playbook (instead of automatic matching)
    /help  (or "/" or /commands)                      -> the list of commands

Names match case-insensitively, and "-" and "_" are interchangeable (/hard-search == /hard_search).
"""
import os
import re

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDES_DIR = __import__("paths").resource("guides")     # inside the bundle when installed
HELP_NAMES = ("help", "commands", "playbooks", "?")
MAX_GUIDE_CHARS = 24000          # the largest guide is ~15k characters; a cap so a huge file can't flood the context

_CMD_RX = re.compile(r"^\s*/([A-Za-z0-9_?-]*)(?:\s+(.*))?$", re.S)


def _key(name):
    return str(name or "").strip().lower().replace("_", "-")


def _guide_title(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def catalog():
    """[{name, kind: 'playbook'|'guide', title, summary}] sorted by kind then name."""
    out = []
    try:
        import playbooks
        off = set(playbooks.disabled()) if hasattr(playbooks, "disabled") else set()
        for name, pb in sorted(playbooks.load_all().items()):
            if name.startswith(getattr(playbooks, "NONE_PREFIX", "none-")) or name in off:
                continue
            out.append({"name": name, "kind": "playbook", "title": pb.get("title") or name,
                        "summary": pb.get("applies_when") or ""})
    except Exception:
        pass
    if os.path.isdir(GUIDES_DIR):
        for f in sorted(os.listdir(GUIDES_DIR)):
            if f.endswith(".md") and not f.startswith("_"):
                out.append({"name": f[:-3], "kind": "guide", "title": _guide_title(os.path.join(GUIDES_DIR, f)),
                            "summary": ""})
    return out


def find(name):
    """The catalog entry for a command name (guides win a tie with a playbook of the same name), or None."""
    k = _key(name)
    hits = [c for c in catalog() if _key(c["name"]) == k]
    return next((c for c in hits if c["kind"] == "guide"), hits[0] if hits else None)


def parse(text):
    """-> None (not a command), or {"help": True}, or {"entry": catalog entry, "rest": request text},
    or {"unknown": name} for a slash word that matches nothing (a path like /usr/bin is not treated as a command)."""
    m = _CMD_RX.match(str(text or ""))
    if not m:
        return None
    name, rest = m.group(1), (m.group(2) or "").strip()
    if name == "" or _key(name) in HELP_NAMES:
        return {"help": True, "rest": rest}
    if "/" in (rest.split()[0] if rest else "") or len(name) > 60:
        return None
    entry = find(name)
    if entry is None:
        return {"unknown": name, "rest": rest}
    return {"entry": entry, "rest": rest}


def named_in(text):
    """A playbook or guide the user names in plain words ("use the hard search playbook", "follow the excel guide")
    -> its catalog entry, else None. Needs the word playbook/guide in the message; the longest matching name wins."""
    t = " " + re.sub(r"[\s_\-]+", " ", str(text or "").lower()) + " "
    if not re.search(r"\b(playbook|playbooks|guide|guides)\b", t):
        return None
    best, best_len = None, 0
    for c in catalog():
        n = re.sub(r"[_\-]+", " ", c["name"].lower())
        # a directive in front of the name ("use the …", "follow your …", "apply …", "with the …") — a mere mention
        # ("the guide said excel is fine") is not a request to run it
        if len(n) >= 4 and len(n) > best_len and re.search(
                r"\b(use|using|follow|following|apply|run|with|load|invoke|per|via)\b(?:\s+\w+){0,2}\s+"
                + re.escape(n) + r"\b", t):
            best, best_len = c, len(n)
    return best


def body(entry):
    """The text to inject for a catalog entry."""
    if entry["kind"] == "guide":
        try:
            with open(os.path.join(GUIDES_DIR, entry["name"] + ".md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return ""
        return text if len(text) <= MAX_GUIDE_CHARS else text[:MAX_GUIDE_CHARS].rsplit("\n", 1)[0] + "\n…"
    import playbooks
    pb = playbooks.load_all().get(entry["name"]) or {}
    return pb.get("body") or ""


def help_text():
    cat = catalog()
    lines = ["**Slash commands** — type `/<name>` and then your request, e.g. `/hard_search find the 2014 annual "
             "report of X`. The request runs with that playbook or guide followed from the start.", "",
             "**Guides** (detailed reference):"]
    lines += [f"- `/{c['name']}` — {c['title']}" for c in cat if c["kind"] == "guide"]
    lines += ["", "**Task playbooks** (normally picked automatically; a command forces one):"]
    lines += [f"- `/{c['name']}` — {c['title']}" for c in cat if c["kind"] == "playbook"]
    lines += ["", "`/help` shows this list."]
    return "\n".join(lines)
