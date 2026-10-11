"""Thinking library (Self-Discover style): before working on a request, the model selects 2-4 principles / reasoning
modules / frameworks from thinking_library.json and adapts them into a short reasoning frame for THIS request.
The frame is added to the turn as guidance for the plan, the checklist and the work.

Domain-neutral by design: entries describe how to think, never a particular task.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import paths
PATH = paths.resource("thinking_library.json")
_cache = {"mtime": None, "entries": []}

GUIDE = ("Use this frame privately to guide your thinking, plan and judgment calls; adapt it if a better path appears. "
         "Then write a natural answer for the user: don't name the frame, the library or its steps, don't lay the "
         "answer out as the frame's sections, and use only figures you were given or can source.")

KIND_TITLE = {"principle": "PRINCIPLES", "module": "REASONING MODULES", "framework": "FRAMEWORKS"}

SELECT_PROMPT = """THINKING FRAME. Before working on the request above, choose how to think about it.
From the thinking library below, pick the 2-4 entries that would most improve how this request is handled \
(pick none if it is trivial). Then write a reasoning frame adapted to THIS request: 3-6 numbered steps that say \
what to establish first, where the approach branches (if … then …), the key judgment calls, and how to check the \
result. Concrete to this request, under 120 words, no preamble. Do not answer the request itself.

{index}

Reply as JSON: {{"use": [entry ids], "frame": "the numbered steps"}}"""

SCHEMA = {"type": "object",
          "properties": {"use": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                         "frame": {"type": "string"}},
          "required": ["use", "frame"], "additionalProperties": False}


def entries():
    try:
        mt = os.path.getmtime(PATH)
    except OSError:
        return []
    if mt != _cache["mtime"]:
        with open(PATH, encoding="utf-8") as f:
            _cache.update(mtime=mt, entries=json.load(f).get("entries") or [])
    return _cache["entries"]


def index_text():
    """The compact index the model chooses from: one line per entry (id — name: when to use)."""
    out = []
    for kind in ("principle", "module", "framework"):
        rows = [e for e in entries() if e.get("kind") == kind]
        if rows:
            out.append(KIND_TITLE[kind] + ":")
            out += [f"- {e['id']} — {e['name']}: {e['when']}" for e in rows]
    return "\n".join(out)


def by_id(ids):
    """Exact ids; a wrong kind prefix ("M-tradeoff" for "F-tradeoff") or a name still finds the entry."""
    es = entries()
    exact = {e["id"].lower(): e for e in es}
    tail = {e["id"].split("-", 1)[1].lower(): e for e in es}
    names = {e["name"].lower(): e for e in es}
    out = []
    for i in ids:
        k = str(i).strip().lower()
        e = exact.get(k) or tail.get(k.split("-", 1)[-1]) or names.get(k)
        if e and e not in out:
            out.append(e)
    return out


def render(ids, frame):
    """The guidance block added to the turn: the adapted frame plus the chosen entries in compact form."""
    chosen = by_id(ids)
    if not frame.strip() and not chosen:
        return ""
    lines = ["THINKING FRAME for this request (from your thinking library):", frame.strip()]
    if chosen:
        lines.append("Drawn from:")
        for e in chosen:
            ex = f" Example: {e['example']}" if e.get("example") else ""
            lines.append(f"- {e['name']}: {e['form']}{ex}")
    lines.append(GUIDE)
    return "\n".join(lines)
