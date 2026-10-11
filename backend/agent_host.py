"""Delegated sub-agent tasks: bookkeeping shared by the GUI host and a headless host (tests, bench).

A host gives an engine's tools four calls: delegate(), task_info(), wait(), tasks_of().
TaskBook is thread-safe and Qt-free; HeadlessHost runs sub-agents in plain threads.
"""
import copy
import itertools
import threading
import time
import uuid

import agent_tools as at

_ids = itertools.count(1)


class TaskBook:
    def __init__(self):
        self.tasks = {}
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.listeners = []           # fn(event, info) — "started" / "updated" / "finished"

    def _notify(self, event, info):
        for fn in list(self.listeners):
            try:
                fn(event, info)
            except Exception:
                pass

    def new(self, title, task, parent, conv_id):
        with self.lock:
            tid = f"t{next(_ids)}"
            self.tasks[tid] = {"id": tid, "title": title, "task": task, "parent": parent, "conv_id": conv_id,
                               "status": "running", "result": "", "started": time.time(), "ended": None,
                               "needs_approval": False, "waiters": 0, "delivered": False}
            info = copy.deepcopy(self.tasks[tid])
        self._notify("started", info)
        return info

    def update(self, tid, **kw):
        with self.cond:
            t = self.tasks.get(tid)
            if not t:
                return
            t.update(kw)
            self.cond.notify_all()
            info = copy.deepcopy(t)
        self._notify("updated", info)

    def finish(self, tid, status, result):
        """-> True if nobody was waiting (so the parent should be notified separately)."""
        with self.cond:
            t = self.tasks.get(tid)
            if not t:
                return False
            t.update(status=status, result=result, ended=time.time(), needs_approval=False)
            self.cond.notify_all()
            info = copy.deepcopy(t)
            unwatched = t["waiters"] == 0
        self._notify("finished", info)
        return unwatched

    def info(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return None
            info = copy.deepcopy(t)
            if t["status"] != "running":
                t["delivered"] = True
        info["tracker"] = at.load_tracker(at.default_tracker_path(info["conv_id"]))
        return info

    def wait(self, tid, timeout, should_stop=lambda: False):
        end = time.time() + timeout
        with self.cond:
            t = self.tasks.get(tid)
            if not t:
                return None
            t["waiters"] += 1
            try:
                while t["status"] == "running" and time.time() < end and not should_stop():
                    self.cond.wait(timeout=min(1.0, max(0.05, end - time.time())))
            finally:
                t["waiters"] -= 1
        info = self.info(tid)
        if info and info["status"] == "running":
            info["status"] = f"still running after {timeout}s (call wait_task again, or keep working)"
        return info

    def of_parent(self, parent):
        with self.lock:
            return [copy.deepcopy(t) for t in self.tasks.values() if t["parent"] == parent]

    def all(self):
        with self.lock:
            return [copy.deepcopy(t) for t in self.tasks.values()]


def subagent_prompt(task, context):
    return (f"{task}" + (f"\n\nContext from the agent that delegated this:\n{context}" if context else "")
            + "\n\nWhen you are done, reply with your complete report.")


class HeadlessHost:
    """Runs delegated tasks in threads with an approval callback (default: approve everything)."""

    def __init__(self, cfg, ask=lambda req: "once", max_running=3, log=print):
        self.cfg = cfg
        self.ask = ask
        self.book = TaskBook()
        self.max_running = max_running
        self.parents = {}             # conv_id -> engine (to notify when a task finishes)
        self.engines = {}
        self.log = log

    # ---- engine-facing API
    def delegate(self, task, title, context, parent):
        import agentic
        running = sum(1 for t in self.book.all() if t["status"] == "running")
        if running >= self.max_running:
            raise at.ToolError(f"{running} delegated tasks are already running (limit {self.max_running}); "
                               f"wait for one first")
        conv_id = "sub_" + uuid.uuid4().hex[:10]
        info = self.book.new(title, task, parent, conv_id)
        eng = agentic.AgentEngine(dict(self.cfg), lambda *a, **k: None, ask=self.ask, conv_id=conv_id,
                                  title=title, host=self, subagent=True)
        self.engines[info["id"]] = eng

        def run():
            try:
                eng.prepare()
                result = eng.run(subagent_prompt(task, context))
                status = "stopped" if result == "(stopped)" else "done"
            except Exception as e:
                result, status = f"{type(e).__name__}: {e}", "failed"
            if self.book.finish(info["id"], status, result):
                p = self.parents.get(parent)
                if p:
                    p.notify(f"delegated task {info['id']} '{title}' finished ({status}). Its report:\n{result[:4000]}")
            self.log(f"[task {info['id']} {status}] {title}")

        threading.Thread(target=run, daemon=True).start()
        return info

    def task_info(self, tid):
        return self.book.info(tid)

    def wait(self, tid, timeout, should_stop=lambda: False):
        return self.book.wait(tid, timeout, should_stop)

    def tasks_of(self, parent):
        return self.book.of_parent(parent)
