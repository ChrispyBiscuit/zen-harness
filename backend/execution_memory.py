"""Experimental, code-owned action history for one agent conversation.

The journal records what the harness actually dispatched and observed. Its small
projection is prompt material; full call arguments and results stay on disk. A
model's checklist or proposed next action is never treated as test evidence.
"""
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid

import context_memory

_LOCK = threading.RLock()
_RECONCILED = set()
VERSION = 1
RECENT_KEEP = 40


def _root(conv):
    return os.path.join(context_memory._dir(conv), "execution-memory")


def _state_path(conv):
    return os.path.join(_root(conv), "state.json")


def _load(conv):
    state = None
    try:
        with open(_state_path(conv), encoding="utf-8") as f:
            state = json.load(f)
        if state.get("version") != VERSION:
            state = None
    except (OSError, ValueError, AttributeError):
        pass
    if state is None:
        state = {"version": VERSION, "revision": 0, "pending": {}, "recent": []}
    # A crash can occur between appending an event and replacing the projection.
    # Rebuild once per process from the append-only journal, then use the small
    # projection on ordinary prompt construction.
    if conv not in _RECONCILED:
        events = _events(conv)
        if events:
            pending, recent = {}, []
            for row in events:
                if row.get("phase") == "started":
                    pending[row["id"]] = row
                elif row.get("phase") == "finished":
                    pending.pop(row["id"], None)
                    recent.append(row)
            state["pending"] = pending
            state["recent"] = recent[-RECENT_KEEP:]
            _save(conv, state)
        _RECONCILED.add(conv)
    return state


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="action-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _save(conv, state):
    state["revision"] += 1
    _write(_state_path(conv), json.dumps(state, ensure_ascii=False, indent=2))


def _event(conv, row):
    path = os.path.join(_root(conv), "actions.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def begin(conv, call_id, tool, args, step=None):
    """Persist intent before dispatch so an interrupted tool is not mistaken for success."""
    if not conv:
        return None
    aid = "A" + uuid.uuid4().hex[:12].upper()
    with _LOCK:
        state = _load(conv)
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        _write(os.path.join(_root(conv), "inputs", aid + ".json"), raw)
        row = {"id": aid, "call_id": call_id, "tool": tool, "phase": "started",
               "step": step, "at": time.time(), "args_sha256": hashlib.sha256(raw.encode()).hexdigest(),
               "args_excerpt": " ".join(raw.split())[:110]}
        _event(conv, row)
        state["pending"][aid] = row
        _save(conv, state)
    return aid


def finish(conv, aid, ok, result, *, refused=False, step=None, file_path=None):
    """Persist the exact result and a bounded projection. `ok` is tool execution, not task success."""
    if not (conv and aid):
        return
    result = str(result or "")
    with _LOCK:
        state = _load(conv)
        started = state["pending"].pop(aid, None)
        if started is None:
            return
        _write(os.path.join(_root(conv), "outputs", aid + ".txt"), result)
        status = "refused" if refused else "failed" if not ok else "observed"
        row = {**started, "phase": "finished", "status": status, "step": step,
               "at": time.time(), "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
               "excerpt": " ".join(result.split())[:180]}
        if file_path and os.path.isfile(file_path):
            try:
                with open(file_path, "rb") as f:
                    row["file_sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
                row["file"] = os.path.abspath(file_path)
            except OSError:
                pass
        _event(conv, row)
        state["recent"] = (state["recent"] + [row])[-RECENT_KEEP:]
        _save(conv, state)


def state(conv):
    with _LOCK:
        return _load(conv)


def brief(conv, tracker=None, limit=8):
    """A small volatile tail view, independent of the stable system/tool prefix."""
    import agent_tools as at
    s = state(conv)
    t = at.upgrade_tracker(tracker) if tracker else None
    rows = [f"Execution record r{s['revision']} (tool outcomes are observations, not task completion):"]
    if t:
        cur = at.tracker_current(t)
        rows.append("Current work: " + (f"{cur[0]} {cur[1]}" if cur else "no open checklist item"))
        rows.append("Next action: " + (str(t.get("next") or
             "NEEDS SELECTION; record the next concrete action via update_tracker alongside real work")))
        if t.get("next"):
            rows[-1] += " (model-proposed, not verified)"
    if s["pending"]:
        rows.append("Interrupted/unresolved calls: " + ", ".join(
            f"{a} {v['tool']}" for a, v in list(s["pending"].items())[-3:]))
    if s["recent"]:
        last = s["recent"][-1]
        rows.append(f"Most recent completed tool: {last['tool']} (action {last['id']}, "
                    f"execution status: {last['status']}).")
        rows.append("Recent actions (exact inputs/results: history_search entry='A…'):")
        for item in s["recent"][-max(1, min(int(limit), 12)):]:
            rows.append(f"{item['id']} {item['tool']} {item.get('args_excerpt', '')[:75]} "
                        f"[{item['status']}]: {item['excerpt'][:180]}")
    else:
        rows.append("No tool actions recorded in Test Mode yet.")
    return "\n".join(rows)[:3600]


def get(conv, aid):
    aid = str(aid or "").strip().upper().lstrip("#")
    if not (aid.startswith("A") and len(aid) == 13 and all(c in "0123456789ABCDEF" for c in aid[1:])):
        return None
    with _LOCK:
        rows = _events(conv)
        item = next((r for r in reversed(rows) if r.get("id") == aid), None)
        if item is None:
            return None
        def read(folder, suffix):
            try:
                with open(os.path.join(_root(conv), folder, aid + suffix), encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return ""
        return {**item, "arguments": read("inputs", ".json"), "result": read("outputs", ".txt")}


def _events(conv):
    path = os.path.join(_root(conv), "actions.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            rows = []
            for line in f:
                try:
                    if line.strip():
                        rows.append(json.loads(line))
                except ValueError:
                    continue  # a torn final append cannot erase earlier actions
            return rows
    except OSError:
        return []


def search(conv, query, limit=5):
    q = str(query or "").strip().lower()
    if not q:
        return []
    with _LOCK:
        rows = [r for r in _events(conv) if r.get("phase") == "finished"]
    hits = [r for r in rows if q in (r.get("tool", "") + " " + r.get("args_excerpt", "") +
                                      " " + r.get("excerpt", "") +
                                      " " + r.get("file", "")).lower()]
    return hits[-max(1, min(int(limit), 20)):][::-1]
