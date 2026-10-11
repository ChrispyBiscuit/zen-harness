"""Agent runs in the GUI: one controller per running thread, so several threads (the one on screen,
delegated sub-agents, chats you switched away from) can work at the same time.

  AgentHost      owns the engines (one per conversation), the delegated-task book, pending approvals
                 and background processes; it is the 'host' the delegation tools talk to.
  RunController  drives one agent turn and writes its progress into its conversation's messages.
  TasksPane      side panel listing background processes and delegated threads.
"""
import copy
import html
import json
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

import agent_host
import agent_tools as at
import agentic
import bg_tasks


class RunWorker(QThread):
    """Runs engine.run() off the GUI thread; every engine event arrives as event(kind, data)."""
    event = Signal(str, dict)

    def __init__(self, engine, text, images=None, compact_only=False):
        super().__init__()
        self.engine, self.text, self.images, self.compact_only = engine, text, images, compact_only
        self._stop = threading.Event()
        self._answer = None
        self._answer_evt = threading.Event()
        engine.emit = self._emit
        engine.ask = self._ask
        engine.should_stop = self._stop.is_set
        engine.ctx.emit = self._emit

    def stop(self):
        self._stop.set()
        self._answer = "deny"
        self._answer_evt.set()

    def reply(self, decision):
        self._answer = decision
        self._answer_evt.set()

    def _ask(self, req):
        self._answer = None
        self._answer_evt.clear()
        self.event.emit("approval", dict(req))
        while not self._answer_evt.wait(0.2):
            if self._stop.is_set():
                return "deny"
        return self._answer or "deny"

    def _emit(self, kind, /, **d):
        self.event.emit(kind, d)

    def run(self):
        try:
            if self.engine.reg is None:
                self.engine.prepare()
            if self.compact_only:
                self.engine.request_compact()
                self.engine._maybe_compact()
                self.event.emit("answer_end", {"text": "", "compact_only": True})
                return
            self.engine.run(self.text, self.images)
        except Exception as e:
            self.event.emit("failed", {"text": f"{type(e).__name__}: {e}"})


class RunController(QObject):
    def __init__(self, host, conv, engine, text, images=None, compact_only=False):
        super().__init__()
        self.host, self.win, self.conv, self.engine = host, host.win, conv, engine
        self.worker = RunWorker(engine, text, images, compact_only)
        self.worker.event.connect(self._on_event)
        self.worker.finished.connect(self._on_finished)
        self.live = None              # assistant message being streamed
        self.tool_msgs = {}           # call id -> tool message
        self.tracker_msg = None       # this turn's tracker card (updated in place)
        self.stats = None
        self.done = False
        self.final = ""
        self.pending = set()          # call ids waiting for approval

    # ---- helpers
    @property
    def msgs(self):
        return self.conv["messages"]

    def start(self):
        self.worker.start()

    def running(self):
        return not self.done

    def stop(self):
        self.worker.stop()

    def reply(self, call_id, decision):
        m = self.tool_msgs.get(call_id)
        if m is not None:
            m["tool"]["awaiting"] = False
            if decision == "deny":
                m["tool"]["result"], m["tool"]["ok"] = "You denied this action.", False
            self._bump(m)
        self.pending.discard(call_id)
        self.host.approval_done(self, call_id)
        self.worker.reply(decision)
        self.win.conv_changed(self.conv)

    def _bump(self, m):
        m["_v"] = m.get("_v", 0) + 1

    def _changed(self, soft=False):
        self.win.conv_changed(self.conv, soft=soft)

    def _finish_live(self):
        if self.live is not None:
            self.live["streaming"] = False
            if not self.live["content"].strip() and not self.live["thinking"].strip():
                try:
                    self.msgs.remove(self.live)
                except ValueError:
                    pass
            self.live["collapsed"] = not self.win.think_cb.isChecked()
            self._bump(self.live)
        self.live = None

    def _live(self):
        if self.live is None:
            self.live = {"role": "assistant", "content": "", "thinking": "", "streaming": True,
                         "collapsed": False}
            self.msgs.append(self.live)
        return self.live

    # ---- engine events
    def _on_event(self, kind, d):
        cur = self.conv is self.win.current
        if kind == "status":
            if cur:
                self.win.on_status(d.get("text", ""))
        elif kind == "stats":
            if cur:
                self.win.on_tps_live(float(d.get("tps", 0)))
        elif kind == "ans_delta":
            lv = self._live()
            lv["content"] += d.get("text", "")
            if cur and self.win.status_lbl.text() in ("Thinking…", ""):
                self.win.status_lbl.setText("✍️ Writing…")
            self._changed(soft=True)
        elif kind == "think_delta":
            self._live()["thinking"] += d.get("text", "")
            self._changed(soft=True)
        elif kind == "think_end":
            if self.live is not None and self.live["thinking"] and not self.live["thinking"].endswith("\n"):
                self.live["thinking"] += "\n"
        elif kind == "tool_call":
            self._finish_live()
            if d.get("name") == "update_tracker" and self.tracker_msg is not None:
                self.tool_msgs[d.get("id")] = self.tracker_msg
                self.tracker_msg["tool"]["result"] = None
            else:
                m = {"role": "assistant", "kind": "tool", "content": d.get("name", ""),
                     "tool": {"name": d.get("name"), "kind": d.get("kind"), "desc": d.get("desc"),
                              "result": None, "display": d.get("display")}}
                self.msgs.append(m)
                self.tool_msgs[d.get("id")] = m
                if d.get("name") == "update_tracker":
                    self.tracker_msg = m
            if cur:
                self.win.status_lbl.setText(f"🔧 {d.get('name')}…")
            self._changed()
        elif kind == "tool_output":
            m = self.tool_msgs.get(d.get("id"))
            disp = (m or {}).get("tool", {}).get("display")
            if disp is not None:
                disp["output"] = (disp.get("output", "") + d.get("text", ""))[-60000:]
                self._bump(m)
                self._changed(soft=True)
        elif kind == "tool_result":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None:
                t = m["tool"]
                t["result"] = d.get("text", "") or "(no output)"
                t["ok"] = bool(d.get("ok", True))
                t["awaiting"] = False
                if d.get("display"):
                    t["display"] = d["display"]
                elif (t.get("display") or {}).get("type") in ("pending_file",) or \
                        (t.get("display") or {}).get("pending"):
                    t["display"] = None
                if d.get("image") is not None:
                    t["result"] += "  [image]"
                self._bump(m)
            self._changed()
            self.win._save_soon()
        elif kind == "approval":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None:
                m["tool"]["awaiting"] = True
                m["tool"]["approval"] = {"rule_label": d.get("rule_label"), "warnings": d.get("warnings") or [],
                                         "call_id": d.get("id")}
                self._bump(m)
            self.pending.add(d.get("id"))
            self.host.approval_needed(self, d)
            self._changed()
        elif kind == "interjected":
            pass                                   # the GUI already showed the user's message
        elif kind == "compacted":
            self._finish_live()
            self.msgs.append({"role": "assistant", "kind": "compacted", "content": "",
                              "summary": d.get("summary", ""), "before": d.get("before"),
                              "after": d.get("after"), "t": time.time()})
            self._changed()
        elif kind == "rule_added":
            self.win.agent_rule_added(d.get("rule"))
        elif kind == "files_changed":
            self._finish_live()
            self.msgs.append({"role": "assistant", "kind": "changes", "content": "",
                              "files": d.get("files") or {}, "reverts": d.get("reverts") or []})
            self._changed()
        elif kind == "turn_stats":
            self.stats = d
        elif kind == "answer_end":
            self._finish_live()
            self.final = d.get("text", "")
            if not d.get("compact_only"):
                last = next((m for m in reversed(self.msgs) if m.get("role") != "user"), None)
                text_msgs = [m for m in self.msgs[-3:] if m.get("role") == "assistant" and not m.get("kind")
                             and m.get("content", "").strip()]
                if self.final and not (last and not last.get("kind") and last.get("content", "").strip()):
                    self.msgs.append({"role": "assistant", "content": self.final, "thinking": ""})
                    text_msgs = [self.msgs[-1]]
                target = text_msgs[-1] if text_msgs else None
                if target is not None and self.stats:
                    target["stats"] = self.stats
                    self._bump(target)
            self._changed()
        elif kind == "failed":
            self._finish_live()
            self.final = "⚠ " + d.get("text", "")
            self.msgs.append({"role": "assistant", "content": self.final})
            self._changed()

    def _on_finished(self):
        self._finish_live()
        self.done = True
        try:
            self.conv["agent_state"] = self.engine.export_state()
        except Exception:
            pass
        self.conv["updated"] = time.time()
        for cid in list(self.pending):
            self.host.approval_done(self, cid)
        self.host.run_finished(self)


class AgentHost(QObject):
    """GUI-side host: engines per conversation, delegated threads, approvals, background processes."""
    changed = Signal()                     # tasks / processes / approvals changed (Tasks pane)
    _gui_call = Signal(object)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.book = agent_host.TaskBook()
        self.engines = {}                  # conv id -> (engine, cfg key)
        self.runs = {}                     # conv id -> RunController (running or last)
        self.approvals = {}                # call id -> RunController
        self._gui_call.connect(self._do_gui_call, Qt.ConnectionType.QueuedConnection)
        bg_tasks.MANAGER.listeners.append(lambda ev, info: self.changed.emit())
        self.book.listeners.append(lambda ev, info: self.changed.emit())

    # ---- calling into the GUI thread from engine threads
    def _do_gui_call(self, job):
        fn, box, evt = job
        try:
            box["result"] = fn()
        except Exception as e:
            box["error"] = e
        evt.set()

    def _in_gui(self, fn):
        if QThread.currentThread() is self.thread():
            return fn()
        box, evt = {}, threading.Event()
        self._gui_call.emit((fn, box, evt))
        evt.wait(60)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ---- engines and runs
    def cfg_for(self, conv):
        cfg = copy.deepcopy(self.win._agent_cfg())
        cfg["extra_roots"] = [os.path.join(at.APP_DIR, "agent_attachments", conv["id"])]
        return cfg

    def engine_for(self, conv):
        cfg = self.cfg_for(conv)
        key = json.dumps({k: v for k, v in cfg.items() if k not in ("enabled", "allow_rules")},
                         sort_keys=True, default=str)
        have = self.engines.get(conv["id"])
        if have and have[1] == key:
            have[0].cfg["allow_rules"] = cfg.get("allow_rules", [])
            return have[0]
        state = have[0].export_state() if have else conv.get("agent_state")
        if have:
            try:
                have[0].close()
            except Exception:
                pass
        sub = bool(conv.get("subagent"))
        eng = agentic.AgentEngine(cfg, lambda *a, **k: None, conv_id=conv["id"], title=conv.get("title", ""),
                                  host=self, subagent=sub)
        eng.import_state(state)
        if not state:      # first agent turn in an existing chat: carry over the visible text
            eng.history = [{"role": m["role"], "content": m.get("model_content") or m["content"]}
                           for m in conv["messages"][:-1]
                           if m.get("content") and not m.get("kind") and m["role"] in ("user", "assistant")]
        self.engines[conv["id"]] = (eng, key)
        return eng

    def running(self, conv):
        r = self.runs.get(conv["id"]) if conv else None
        return bool(r and r.running())

    def start(self, conv, text, images=None):
        """Start an agent turn in conv, or deliver text to the turn already running there."""
        r = self.runs.get(conv["id"])
        if r and r.running():
            r.engine.interject(text)
            return "interjected"
        eng = self.engine_for(conv)
        r = RunController(self, conv, eng, text, images)
        self.runs[conv["id"]] = r
        r.start()
        self.changed.emit()
        return "started"

    def compact(self, conv):
        if self.running(conv):
            self.runs[conv["id"]].engine.request_compact()
            return
        eng = self.engine_for(conv)
        r = RunController(self, conv, eng, "", compact_only=True)
        self.runs[conv["id"]] = r
        r.start()

    def stop(self, conv):
        r = self.runs.get(conv["id"])
        if r and r.running():
            r.stop()

    def stop_all(self):
        for r in self.runs.values():
            if r.running():
                r.stop()
        for r in self.runs.values():
            r.worker.wait(3000)
        for eng, _k in self.engines.values():
            try:
                eng.close()
            except Exception:
                pass
        bg_tasks.MANAGER.stop_all()

    def drop_engines(self):
        """Settings changed: idle engines are rebuilt (keeping their history) on the next turn."""
        for cid in list(self.engines):
            if not self.running({"id": cid}):
                eng, _k = self.engines.pop(cid)
                conv = self.win.conv_by_id(cid)
                if conv is not None:
                    conv["agent_state"] = eng.export_state()
                try:
                    eng.close()
                except Exception:
                    pass

    def run_finished(self, r):
        conv = r.conv
        tid = conv.get("task_id")
        if tid:
            status = "stopped" if r.final == "(stopped)" else ("failed" if r.final.startswith("⚠") else "done")
            unwatched = self.book.finish(tid, status, r.final)
            info = self.book.info(tid) or {}
            parent = self.win.conv_by_id(conv.get("parent"))
            if parent is not None:
                parent["messages"].append({"role": "assistant", "kind": "task_done", "content": "",
                                           "task_id": tid, "title": info.get("title", ""),
                                           "status": status, "conv_id": conv["id"],
                                           "result": (r.final or "")[:600]})
                self.win.conv_changed(parent)
                pe = self.engines.get(parent["id"])
                if unwatched and pe:
                    pe[0].notify(f"delegated task {tid} '{info.get('title', '')}' finished ({status}). "
                                 f"Its report:\n{(r.final or '')[:4000]}")
                    if not self.running(parent) and self.win._agent_cfg().get("auto_continue", True):
                        self.start(parent, "(A background task you delegated has finished. Continue with "
                                           "the original request using its report.)")
        self.win.agent_run_finished(conv)
        self.changed.emit()

    # ---- approvals
    def approval_needed(self, r, req):
        self.approvals[req.get("id")] = r
        tid = r.conv.get("task_id")
        if tid:
            self.book.update(tid, needs_approval=True)
        self.win.agent_needs_approval(r.conv, req)
        self.changed.emit()

    def approval_done(self, r, call_id):
        self.approvals.pop(call_id, None)
        tid = r.conv.get("task_id")
        if tid and not r.pending:
            self.book.update(tid, needs_approval=False)
        self.changed.emit()

    def resolve(self, call_id, decision):
        r = self.approvals.get(call_id)
        if r is not None:
            r.reply(call_id, decision)
            return True
        return False

    def waiting_convs(self):
        return {r.conv["id"] for r in self.approvals.values()}

    # ---- delegation API (called from engine threads)
    def delegate(self, task, title, context, parent):
        cfg = self.win._agent_cfg()
        limit = int(cfg.get("max_delegates") or 3)
        running = sum(1 for t in self.book.all() if t["status"] == "running")
        if running >= limit:
            raise at.ToolError(f"{running} delegated tasks are already running (limit {limit}); wait for one")

        def make():
            conv = self.win.new_thread(f"⇢ {title}", parent=parent)
            info = self.book.new(title, task, parent, conv["id"])
            conv["task_id"] = info["id"]
            conv["messages"].append({"role": "user", "content": f"Delegated task from the main thread:\n\n{task}"
                                     + (f"\n\nContext:\n{context}" if context else "")})
            self.start(conv, agent_host.subagent_prompt(task, context))
            return info
        return self._in_gui(make)

    def task_info(self, tid):
        return self.book.info(tid)

    def wait(self, tid, timeout, should_stop=lambda: False):
        return self.book.wait(tid, timeout, should_stop)

    def tasks_of(self, parent):
        return self.book.of_parent(parent)


# ----------------------------------------------------------------------------- Tasks pane
def _ago(t):
    return _dur(time.time() - t)


def _dur(sec):
    s = int(sec)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s" if s < 3600 else f"{s // 3600}h {s % 3600 // 60}m"


class TasksPane(QWidget):
    """Background processes and delegated threads, with live output and controls."""

    def __init__(self, host, win):
        super().__init__()
        self.host, self.win = host, win
        self.setObjectName("tasks_pane")
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel("<b>Tasks</b>")
        head.addWidget(t)
        head.addStretch(1)
        self.clear_btn = QPushButton("Clear finished")
        self.clear_btn.clicked.connect(self._clear)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)
        self.list = QListWidget()
        self.list.setObjectName("taskslist")
        self.list.currentItemChanged.connect(lambda *_: self._show_detail())
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        lay.addWidget(self.list, 2)
        self.detail_lbl = QLabel("")
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.detail_lbl.setStyleSheet("color:#98A1AE;font-size:12px")
        lay.addWidget(self.detail_lbl)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        f = QFont("Cascadia Mono")
        f.setPixelSize(12)
        self.out.setFont(f)
        self.out.setStyleSheet("QPlainTextEdit{background:#0A0C10;color:#B8C0CC;border:1px solid #232833}")
        lay.addWidget(self.out, 3)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open thread")
        self.open_btn.clicked.connect(self._open)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        self.log_btn = QPushButton("Open log")
        self.log_btn.clicked.connect(self._log)
        for b in (self.open_btn, self.stop_btn, self.log_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        host.changed.connect(self.refresh)
        self._items = []

    def entries(self):
        out = []
        waiting = self.host.waiting_convs()
        for t in self.host.book.all():
            st = t["status"]
            if t["conv_id"] in waiting:
                st = "needs approval"
            out.append({"type": "thread", "key": t["id"], "title": t["title"], "status": st,
                        "started": t["started"], "ended": t["ended"], "conv_id": t["conv_id"], "info": t})
        for conv_id, r in self.host.runs.items():         # main-thread agent runs in other chats
            conv = r.conv
            if conv.get("task_id") or not r.running():
                continue
            out.append({"type": "run", "key": conv_id, "title": conv.get("title") or "Chat",
                        "status": "needs approval" if conv_id in waiting else "running",
                        "started": conv.get("updated") or time.time(), "ended": None, "conv_id": conv_id})
        for p in bg_tasks.MANAGER.list():
            out.append({"type": "bg", "key": p["id"], "title": p["name"],
                        "status": "running" if p["running"] else f"exit {p['exit']}",
                        "started": p["started"], "ended": p["ended"], "info": p})
        return out

    def counts(self):
        e = self.entries()
        return (sum(1 for x in e if x["status"] in ("running", "needs approval")),
                sum(1 for x in e if x["status"] == "needs approval"))

    def refresh(self):
        if not self.isVisible():
            return
        entries = self.entries()
        cur = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        icon = {"thread": "⇢", "run": "◆", "bg": "⚙"}
        for e in entries:
            dur = _ago(e["started"]) if not e["ended"] else f"took {_dur(e['ended'] - e['started'])}"
            mark = "●" if e["status"] in ("running",) else "⚠" if e["status"] == "needs approval" else \
                "✓" if e["status"] in ("done", "exit 0") else "■"
            it = QListWidgetItem(f"{mark} {icon[e['type']]} {e['title'][:38]}\n    {e['status']} · {dur}")
            it.setData(Qt.ItemDataRole.UserRole, (e["type"], e["key"]))
            self.list.addItem(it)
            if (e["type"], e["key"]) == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self._items = entries
        self._show_detail()

    def _selected(self):
        it = self.list.currentItem()
        if not it:
            return None
        key = it.data(Qt.ItemDataRole.UserRole)
        return next((e for e in self._items if (e["type"], e["key"]) == key), None)

    def _show_detail(self):
        e = self._selected()
        self.open_btn.setEnabled(bool(e and e.get("conv_id")))
        self.stop_btn.setEnabled(bool(e and e["status"] in ("running", "needs approval")))
        self.log_btn.setEnabled(bool(e and e["type"] == "bg"))
        if not e:
            self.detail_lbl.setText("Background processes and delegated threads show up here.")
            self.out.setPlainText("")
            return
        if e["type"] == "bg":
            p = e["info"]
            self.detail_lbl.setText(f"<b>{html.escape(p['name'])}</b> ({p['id']}, pid {p['pid']})<br>"
                                    f"{html.escape(p['cwd'])}<br><code>{html.escape(p['command'][:200])}</code>")
            text = "\n".join(bg_tasks.MANAGER.get(p["id"]).tail(400))
        else:
            conv = self.win.conv_by_id(e["conv_id"]) or {}
            self.detail_lbl.setText(f"<b>{html.escape(e['title'])}</b> — {html.escape(e['status'])}"
                                    + (" · <span style='color:#E0B45F'>open the thread to approve</span>"
                                       if e["status"] == "needs approval" else ""))
            lines = []
            for m in conv.get("messages", [])[-25:]:
                if m.get("kind") == "tool":
                    t = m["tool"]
                    lines.append(f"🔧 {t.get('name')}: {(t.get('desc') or '')[:120]}")
                elif m.get("role") == "assistant" and m.get("content") and not m.get("kind"):
                    lines.append(f"› {m['content'][:400]}")
            tr = at.load_tracker(at.default_tracker_path(e["conv_id"]))
            if tr:
                lines = ["TRACKER", at.tracker_prompt(tr), ""] + lines
            text = "\n".join(lines)
        if self.out.toPlainText() != text:
            sb = self.out.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 4
            self.out.setPlainText(text)
            if at_end:
                sb.setValue(sb.maximum())

    def _open(self):
        e = self._selected()
        if e and e.get("conv_id"):
            self.win.open_conv(e["conv_id"])

    def _stop(self):
        e = self._selected()
        if not e:
            return
        if e["type"] == "bg":
            bg_tasks.MANAGER.stop(e["key"])
        else:
            conv = self.win.conv_by_id(e["conv_id"])
            if conv:
                self.host.stop(conv)
        self.refresh()

    def _log(self):
        e = self._selected()
        if e and e["type"] == "bg":
            import agent_ui
            agent_ui.open_path(e["info"]["log"], self.win)

    def _clear(self):
        bg_tasks.MANAGER.clear_finished()
        with self.host.book.lock:
            for k in [k for k, t in self.host.book.tasks.items() if t["status"] != "running"]:
                del self.host.book.tasks[k]
        self.refresh()
