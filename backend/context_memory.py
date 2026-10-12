"""What a long agent run must not forget, kept outside the conversation itself.

- Timeline of turning points: recorded by code as they happen (a plan made, a decision settled, tests going from
  failing to passing or back, an error first seen, a file created, a strategy reset, the user stepping in). A summary
  written later by a small model can drop or garble these; the timeline can't — it is the record compaction builds
  the summary from, and it is pinned word for word.
- Context map: after a compaction, a markdown file listing the turning points and everything archived (tool results,
  code sent, earlier messages) with its id, so the model can see what exists and restore one item
  (history_search entry='E12') instead of re-deriving it.
- Ledger: per step, what the prompt was made of (harness, pinned, history by kind, stubs) — so a step that went
  wrong can be traced to what was in context, or missing, at that moment.

Stored next to the chat's archive: <data>/agent_attachments/<chat>/archive/{timeline,ledger}.jsonl, CONTEXT_MAP.md
"""
import json
import os
import re
import threading
import time

_lock = threading.Lock()
TIMELINE_KEEP = 30           # turning points pinned / shown (the file keeps all)


def _dir(conv):
    try:
        import paths
        base = paths.data_dir()
    except Exception:
        base = os.environ.get("LWS_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "agent_attachments", re.sub(r"[^A-Za-z0-9_-]", "_", str(conv or "misc"))[:64], "archive")


def _append(conv, name, row):
    with _lock:
        os.makedirs(_dir(conv), exist_ok=True)
        with open(os.path.join(_dir(conv), name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read(conv, name):
    p = os.path.join(_dir(conv), name)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------------------------------------------------- timeline
def add_event(conv, kind, text, step=None, ref=None):
    """One turning point ('tests', 'decided', 'error', 'created', 'plan', 'done', 'reset', 'user', 'compacted')."""
    text = " ".join(str(text or "").split())[:300]
    if not text:
        return
    if kind == "tests":
        same = [e for e in events(conv) if e.get("kind") == kind][-1:]
        if same and same[0].get("text") == text:
            return                                       # the same test result again is not a turning point
    _append(conv, "timeline.jsonl", {"t": time.strftime("%H:%M"), "step": step, "kind": kind, "text": text,
                                     **({"ref": ref} if ref else {})})


def events(conv):
    return _read(conv, "timeline.jsonl")


def render_timeline(conv, limit=TIMELINE_KEEP):
    ev = events(conv)
    if not ev:
        return ""
    shown = ev[-limit:]
    lines = [f"- {e['t']}{' step ' + str(e['step']) if e.get('step') is not None else ''} [{e['kind']}] {e['text']}"
             + (f" (archived {e['ref']})" if e.get("ref") else "") for e in shown]
    more = f"(… {len(ev) - limit} earlier turning points in the context map)\n" if len(ev) > limit else ""
    return more + "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------ ledger
def record_step(conv, row):
    _append(conv, "ledger.jsonl", dict(row, t=time.strftime("%H:%M:%S")))


def ledger(conv):
    return _read(conv, "ledger.jsonl")


def record_gen(conv, row):
    """One row per model call: thinking / text / tool-call tokens, prompt tokens read vs cached, timings."""
    _append(conv, "gen.jsonl", dict(row, t=time.strftime("%H:%M:%S")))


def gen_log(conv):
    return _read(conv, "gen.jsonl")


# ------------------------------------------------------------------------------------------------------- context map
def write_map(conv, archive_entries, key_line=None):
    """CONTEXT_MAP.md: the turning points, then every archived item with its id and what it was. -> path."""
    ev = events(conv)
    out = ["# Context map",
           "",
           "Earlier parts of this conversation were condensed to save space. Nothing is lost: every item below is "
           "archived in full. Restore one with history_search(entry='E12'), or search them by keyword with "
           "history_search(query='...'). Read this before re-deriving something you may already have found.",
           "", "## Turning points (recorded by the app as they happened)", ""]
    out += [f"- {e['t']}{' step ' + str(e['step']) if e.get('step') is not None else ''} [{e['kind']}] {e['text']}"
            + (f" — {e['ref']}" if e.get("ref") else "") for e in ev] or ["(none recorded)"]
    out += ["", "## Archived items", "", "| id | kind | what |", "|---|---|---|"]
    for e in archive_entries:
        what = (key_line(e) if key_line else "") or " ".join(str(e.get("label") or "").split())[:140]
        out.append(f"| {e['id']} | {e.get('kind', '')} | {what.replace('|', '/')} |")
    path = os.path.join(_dir(conv), "CONTEXT_MAP.md")
    with _lock:
        os.makedirs(_dir(conv), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    return path


def save_snapshot(conv, history, cut, summary=None):
    """The conversation just before a compaction (and where it was cut) -> path. Kept for offline replay."""
    d = os.path.join(_dir(conv), "snapshots")
    with _lock:
        os.makedirs(d, exist_ok=True)
        n = len([f for f in os.listdir(d) if f.endswith(".json")]) + 1
        path = os.path.join(d, f"compaction_{n}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"conv": conv, "cut": cut, "summary_before": summary, "history": history,
                       "timeline": events(conv)}, f, ensure_ascii=False, default=str)
    return path


def snapshots(conv):
    d = os.path.join(_dir(conv), "snapshots")
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")),
                  key=os.path.getmtime) if os.path.isdir(d) else []


def map_path(conv):
    p = os.path.join(_dir(conv), "CONTEXT_MAP.md")
    return p if os.path.exists(p) else ""
