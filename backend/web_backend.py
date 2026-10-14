"""Backend for the web UI: serves web_dist/, a JSON API and a WebSocket that pushes live updates.

Conversations and settings use the same files as the Qt window (conversations.json, settings.json),
so history carries over; don't run both windows at the same time.

  python web_backend.py            serve on http://127.0.0.1:8765 (open it in a browser)
  python app_web.py                the same, inside a native desktop window
"""
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import sys
import urllib.request
import uuid

from aiohttp import WSMsgType, web

sys.modules.setdefault("web_backend", sys.modules[__name__])   # run as a script: harness_api imports this same module

import _zen_pending
_zen_pending.apply_pending(os.path.dirname(os.path.abspath(__file__)))

import agent
import agent_host
import agent_tools as at
import agentic
import bg_tasks
import doc_extract
from agent_config import DEFAULT_AGENT

APP_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
DIST = os.environ.get("LWS_DIST") or paths.resource("web_dist")     # LWS_DIST: test a UI build elsewhere
DATA_DIR = paths.data_dir()       # LWS_DATA_DIR / %LOCALAPPDATA%\ZenHarness when installed (see paths.py)
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
CONV_PATH = os.path.join(DATA_DIR, "conversations.json")
ATTACH = paths.data("agent_attachments")
PORT = int(os.environ.get("LWS_PORT", "8765"))
INLINE_DOC_CHARS = 14000          # attached documents up to this size go straight into the message

DEFAULT_SETTINGS = {"temperature": 0.7, "num_ctx": 8192, "max_steps": 6, "system_prompt": agent.SYSTEM,
                    "agent": DEFAULT_AGENT, "ui": {"mode": "agent", "think": True, "chat_model": ""}}


# ============================================================================ store
class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.settings = self._load_settings()
        self.convs = self._load_convs()
        self.dirty = set()             # conv ids with unsent changes
        self.meta_dirty = False
        self._save_at = 0.0

    def _load_settings(self):
        s = copy.deepcopy(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            s.update(data)                                     # keeps keys we don't know (swarm, ...)
        except Exception:
            pass
        s["agent"] = {**copy.deepcopy(DEFAULT_AGENT), **(s.get("agent") or {})}
        if not s["agent"].get("_compact_default_v1"):
            s["agent"]["compact_prompt"] = True
            s["agent"]["_compact_default_v1"] = True
        s["ui"] = {**DEFAULT_SETTINGS["ui"], **(s.get("ui") or {})}
        if not s["agent"].get("_v2"):                          # one-time upgrade for the new build
            if s["agent"].get("backend") == "llama" and int(s["agent"].get("num_ctx") or 0) < 131072:
                s["agent"]["num_ctx"] = 131072
            s["agent"]["screen_control"] = False
            s["agent"]["_v2"] = True
        if not s["agent"].get("model") and s["agent"].get("backend", "llama") == "llama":
            # models the installer downloaded: start on the first built-in profile whose file is here, instead of
            # the first-run "download a model" welcome
            from agent_config import default_preset
            name = default_preset()
            if name:
                s["agent"]["model"] = name
        return s

    def _load_convs(self):
        try:
            with open(CONV_PATH, encoding="utf-8") as f:
                data = json.load(f)
            convs = [c for c in data if isinstance(c, dict) and c.get("messages")]
        except Exception:
            convs = []
        for c in convs:                                        # a crash can leave a live message
            for m in c["messages"]:
                if m.get("streaming"):
                    m["streaming"] = False
                if m.get("kind") == "tool":
                    t = m.get("tool") or {}
                    if t.get("result") is None:
                        t["result"], t["ok"], t["awaiting"] = "(interrupted)", False, False
        return convs

    def save_settings(self):
        with self.lock:
            data = json.dumps(self.settings, indent=2)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, SETTINGS_PATH)

    def save_convs(self):
        with self.lock:
            data = json.dumps([c for c in self.convs if c.get("messages")], ensure_ascii=False)
        tmp = CONV_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, CONV_PATH)

    def touch(self, conv, meta=False):
        with self.lock:
            self.dirty.add(conv["id"])
            if meta:
                self.meta_dirty = True
            if not self._save_at:
                self._save_at = time.time() + 1.5

    def get(self, cid):
        with self.lock:
            return next((c for c in self.convs if c["id"] == cid), None)

    def new_conv(self, title="New chat", **extra):
        c = {"id": uuid.uuid4().hex, "title": title, "created": time.time(), "updated": time.time(),
             "messages": [], **extra}
        with self.lock:
            self.convs.insert(0, c)
            self.meta_dirty = True
        return c


STORE = Store()
# one port for the UI, /api, /v1 and /mcp: LWS_PORT, else Settings → Headless, else 8765
PORT = int(os.environ.get("LWS_PORT") or (STORE.settings.get("headless") or {}).get("port") or PORT)
RESIDENT = False         # True in the tray service (headless.py): never idle-exits, refuses /api/shutdown from windows


# ============================================================================ push to clients
class Bus:
    def __init__(self):
        self.clients = set()
        self.loop = None
        self.sent = {}                 # conv id -> [hash per message]

    def send(self, obj):
        if not self.loop or not self.clients:
            return
        data = json.dumps(obj, ensure_ascii=False, default=str)
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, data), self.loop)

    async def _send(self, ws, data):
        try:
            await ws.send_str(data)
        except Exception:
            self.clients.discard(ws)

    def flush(self):
        """Send changed messages of dirty conversations (and the thread list if it changed)."""
        with STORE.lock:
            dirty, STORE.dirty = STORE.dirty, set()
            meta, STORE.meta_dirty = STORE.meta_dirty, False
            if STORE._save_at and time.time() > STORE._save_at:
                STORE._save_at = 0.0
                save = True
            else:
                save = False
            payloads = []
            for cid in dirty:
                c = STORE.get(cid)
                if not c:
                    continue
                prev = self.sent.get(cid, [])
                hashes, upd = [], {}
                for i, m in enumerate(c["messages"]):
                    js = json.dumps(m, ensure_ascii=False, default=str)
                    h = hashlib.md5(js.encode("utf-8", "replace")).hexdigest()
                    hashes.append(h)
                    if i >= len(prev) or prev[i] != h:
                        upd[i] = json.loads(js)
                self.sent[cid] = hashes
                if upd or len(prev) != len(hashes):
                    payloads.append({"t": "msgs", "conv": cid, "len": len(hashes), "upd": upd})
        for p in payloads:
            self.send(p)
        if meta or dirty:
            self.send({"t": "threads", "threads": thread_list()})
        if save:
            threading.Thread(target=STORE.save_convs, daemon=True).start()


BUS = Bus()


def thread_list():
    waiting = HOST.waiting_convs()
    with STORE.lock:
        out = []
        for c in STORE.convs:
            if not c.get("messages") and not HOST.running(c["id"]):
                continue
            last = next((m for m in reversed(c["messages"]) if m.get("content") and not m.get("kind")), None)
            run = HOST.runs.get(c["id"])
            out.append({"id": c["id"], "title": c.get("title") or "New chat", "updated": c.get("updated") or
                        c.get("created"), "running": HOST.running(c["id"]), "waiting": c["id"] in waiting,
                        "run_started": run.started if run else None, "run_ended": getattr(run, "ended", None),
                        "parent": c.get("parent"), "task_id": c.get("task_id"), "mode": c.get("mode"),
                        "snippet": (last or {}).get("content", "")[:90].replace("\n", " ")})
        return out


# ============================================================================ runs
def _draft_display(name, raw):
    """Card content for a tool call that is still being written."""
    import agentic
    f = lambda k: agentic.partial_json_field(raw, k)
    if name in ("python_sandbox", "python_host"):
        return {"type": "python", "code": f("code") or "", "output": "", "images": [], "files": [],
                "sandbox": name == "python_sandbox", "running": True, "drafting": True}
    if name in ("powershell", "bg_start"):
        return {"type": "terminal", "shell": "powershell", "cwd": "", "command": f("command") or "", "output": "",
                "exit": None, "drafting": True}
    if name in ("write_file", "edit_file", "create_document", "artifact_create", "artifact_edit"):
        body = f("content") or f("new") or ""
        return {"type": "draft", "name": name, "target": f("path") or f("title") or f("id") or "", "text": body[-6000:],
                "chars": len(body)}
    return {"type": "draft", "name": name, "target": "", "text": raw[-3000:], "chars": len(raw)}


def _bump(m):
    m["_v"] = m.get("_v", 0) + 1


class ConvWriter:
    """Applies engine events to a conversation's messages (Qt-free port of the GUI controller)."""

    def __init__(self, conv, think_visible=True):
        self.conv = conv
        self.live = None
        self.tool_msgs = {}
        self.tracker_msg = None
        self.stats = None
        self.final = ""
        self.pending = set()
        self.status = ""
        self.think_visible = think_visible

    @property
    def msgs(self):
        return self.conv["messages"]

    def finish_live(self):
        if self.live is not None:
            self.live["streaming"] = False
            if not self.live["content"].strip() and not self.live["thinking"].strip():
                if self.live in self.msgs:
                    self.msgs.remove(self.live)
            _bump(self.live)
        self.live = None

    def _live(self):
        if self.live is None:
            self.live = {"role": "assistant", "content": "", "thinking": "", "streaming": True}
            if getattr(self, "pending_images", None):     # the search's images head the answer while it streams
                self.live["images"] = self.pending_images
            self.msgs.append(self.live)
        return self.live

    def event(self, kind, d):
        with STORE.lock:
            self._event(kind, d)
        STORE.touch(self.conv, meta=kind in ("answer_end", "failed"))
        if self.conv.get("source") == "api":          # the /v1 event log of API runs
            sys.modules["harness_api"].on_event(self.conv, kind, d)

    def _event(self, kind, d):
        prior_messages = len(self.msgs)
        if kind == "status":
            self.status = d.get("text", "")
            BUS.send({"t": "status", "conv": self.conv["id"], "text": self.status})
        elif kind == "stats":
            BUS.send({"t": "tps", "conv": self.conv["id"], "tps": round(float(d.get("tps", 0)), 1)})
        elif kind == "context":
            self.conv["ctx"] = {"used": d.get("used", 0), "max": d.get("max", 0), "compact": d.get("compact", 0),
                                "ts": time.time()}
            # "t" is the message type: it must come last (the timestamp under "t" used to overwrite it, so the
            # gauge never received a live update and showed empty until the chat was reloaded)
            BUS.send({**self.conv["ctx"], "t": "ctx", "conv": self.conv["id"]})
        elif kind == "ans_delta":
            self._live()["content"] += d.get("text", "")
        elif kind == "workers":            # fan-out workers' live state: the tiles above the composer
            self.conv["workers"] = {"model": d.get("model", ""), "items": d.get("workers") or [], "ts": time.time()}
            BUS.send({**self.conv["workers"], "t": "workers", "conv": self.conv["id"]})    # "t" is the message type
        elif kind == "answer_retract":
            self.finish_live()             # the answer just shown failed a final check: keep it, folded, as a draft
            last = next((m for m in reversed(self.msgs) if m.get("role") == "assistant" and not m.get("kind")
                         and not m.get("tool") and (m.get("content") or "").strip()), None)
            if last is not None:
                last["kind"], last["reason"] = "draft", d.get("reason") or "revised after checks"
                _bump(last)
        elif kind == "factcheck":
            self.finish_live()
            self.msgs.append({"role": "assistant", "kind": "factcheck", "content": "", "round": d.get("round"),
                              "total": d.get("total"), "ok": d.get("ok"), "items": d.get("items") or []})
        elif kind == "playbook":
            self.finish_live()
            self.msgs.append({"role": "assistant", "kind": "playbook", "content": d.get("title") or d.get("name") or "",
                              "name": d.get("name"), "also": d.get("also") or [], "notes": d.get("notes") or 0,
                              "sim": d.get("sim")})
        elif kind == "interpretation":
            self.finish_live()
            self.msgs.append({"role": "assistant", "kind": "interpretation", "content": d.get("goal") or "",
                              **{k: d.get(k) for k in ("deliverable", "constraints", "conditions", "ambiguity",
                                                       "reading", "ask", "question", "irreversible", "implicit",
                                                       "seconds")}})
        elif kind == "plan_start":
            self.finish_live()
            self.plan_msg = {"role": "assistant", "kind": "plan", "content": "", "streaming": True,
                             "budget": d.get("budget")}
            self.msgs.append(self.plan_msg)
        elif kind == "plan_delta":
            if getattr(self, "plan_msg", None) is not None:
                self.plan_msg["content"] += d.get("text", "")
        elif kind == "plan_end":
            pm = getattr(self, "plan_msg", None)
            if pm is not None:
                pm["streaming"] = False
                pm["tokens"], pm["seconds"] = d.get("tokens"), d.get("seconds")
                if d.get("text"):
                    pm["content"] = d["text"]
                if not pm["content"].strip() and pm in self.msgs:
                    self.msgs.remove(pm)
                _bump(pm)
            self.plan_msg = None
        elif kind == "think_delta":
            self._live()["thinking"] += d.get("text", "")
        elif kind == "tool_draft":
            self.finish_live()
            m = {"role": "assistant", "kind": "tool", "content": d.get("name", ""), "id": d.get("id"),
                 "tool": {"name": d.get("name"), "kind": "draft", "desc": "", "result": None, "drafting": True,
                          "display": _draft_display(d.get("name"), "")}}
            self.msgs.append(m)
            self.tool_msgs[d.get("id")] = m
        elif kind == "tool_draft_delta":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None and m["tool"].get("drafting"):
                m["tool"]["display"] = _draft_display(d.get("name"), d.get("args") or "")
                _bump(m)
        elif kind == "tool_draft_end":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None and m["tool"].get("drafting"):
                m["tool"].update(drafting=False, result="(not run — " + (d.get("note") or "discarded") + ")", ok=False)
                _bump(m)
        elif kind == "tool_call" and d.get("id") in self.tool_msgs and self.tool_msgs[d["id"]]["tool"].get("drafting"):
            m = self.tool_msgs[d["id"]]                  # the draft card becomes the real tool card
            m["content"] = d.get("name", "")
            draft = m["tool"].get("display") or {}
            keep = draft if draft.get("type") in ("python", "terminal") else None    # those layouts become the real card
            m["tool"] = {"name": d.get("name"), "kind": d.get("kind"), "desc": d.get("desc"), "result": None,
                         "display": d.get("display") or keep, "by": d.get("by") or "", "t0": time.time()}
            _bump(m)
        elif kind == "tool_call":
            self.finish_live()
            if d.get("name") == "update_tracker" and self.tracker_msg is not None:
                self.tool_msgs[d.get("id")] = self.tracker_msg
                self.tracker_msg["tool"]["result"] = None
                self.tracker_msg["tool"]["t0"] = time.time()
                self.tracker_msg["tool"].pop("t1", None)
                _bump(self.tracker_msg)
            else:
                m = {"role": "assistant", "kind": "tool", "content": d.get("name", ""), "id": d.get("id"),
                     "tool": {"name": d.get("name"), "kind": d.get("kind"), "desc": d.get("desc"),
                              "result": None, "display": d.get("display"), "by": d.get("by") or "",
                              "t0": time.time()}}           # start time: the UI shows "running · 45 s" from it
                self.msgs.append(m)
                self.tool_msgs[d.get("id")] = m
                if d.get("name") == "update_tracker":
                    self.tracker_msg = m
        elif kind == "tool_output":
            m = self.tool_msgs.get(d.get("id"))
            disp = (m or {}).get("tool", {}).get("display")
            if disp is not None:
                disp["output"] = (disp.get("output", "") + d.get("text", ""))[-60000:]
                _bump(m)
        elif kind == "tool_result":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None:
                t = m["tool"]
                t["result"] = d.get("text", "") or "(no output)"
                t["ok"] = bool(d.get("ok", True))
                t["t1"] = time.time()                     # end time: the finished card shows the duration
                t["awaiting"] = False
                if d.get("display"):
                    t["display"] = d["display"]
                elif (t.get("display") or {}).get("type") == "pending_file" or (t.get("display") or {}).get("pending"):
                    t["display"] = None
                if d.get("image") is not None:
                    t["has_image"] = True
                _bump(m)
        elif kind == "approval":
            m = self.tool_msgs.get(d.get("id"))
            if m is not None:
                m["tool"]["awaiting"] = True
                m["tool"]["approval"] = {"rule_label": d.get("rule_label"), "warnings": d.get("warnings") or [],
                                         "call_id": d.get("id")}
                _bump(m)
            self.pending.add(d.get("id"))
            notify("Approval needed", f"{self.conv.get('title', 'Agent')}: {d.get('name', 'an action')} is waiting "
                                      f"for your OK", self.conv["id"])
        elif kind == "compacted":
            self.finish_live()
            self.msgs.append({"role": "assistant", "kind": "compacted", "content": "",
                              "summary": d.get("summary", ""), "before": d.get("before"), "after": d.get("after"),
                              "t": time.time()})
        elif kind == "files_changed":
            self.finish_live()
            self.msgs.append({"role": "assistant", "kind": "changes", "content": "",
                              "files": d.get("files") or {}, "reverts": d.get("reverts") or []})
        elif kind == "turn_stats":
            self.stats = d
        elif kind == "images":
            self.pending_images = d.get("images") or []
            if self.live is not None:
                self.live["images"] = self.pending_images
                _bump(self.live)
        elif kind == "answer_end":
            self.finish_live()
            self.final = d.get("text", "")
            if not d.get("compact_only"):
                # the answer already streamed may sit above a fact-check card: update it in place (the check
                # can append a "Not verified" note) instead of posting the same answer a second time
                shown = None
                for m in reversed(self.msgs):
                    if m.get("role") == "user" or m.get("tool") or m.get("kind") not in (None, "factcheck"):
                        break
                    if not m.get("kind") and (m.get("content") or "").strip():
                        shown = m
                        break
                if shown is not None:
                    if self.final and self.final.strip() != shown["content"].strip() \
                            and self.final.strip().startswith(shown["content"].strip()[:200]):
                        shown["content"] = self.final
                        _bump(shown)
                elif self.final:
                    self.msgs.append({"role": "assistant", "content": self.final, "thinking": ""})
                target = next((m for m in reversed(self.msgs[-4:]) if m.get("role") == "assistant"
                               and not m.get("kind") and (m.get("content") or "").strip()), None)
                if target is not None and self.stats:
                    target["stats"] = self.stats
                    _bump(target)
                if target is not None and d.get("images"):        # the answer's image carousel
                    target["images"] = d["images"]
                    _bump(target)
            self.conv["updated"] = time.time()
        elif kind == "failed":
            self.finish_live()
            self.final = "⚠ " + d.get("text", "")
            self.msgs.append({"role": "assistant", "content": self.final, "error": True})
        # Timestamp every newly created model event at its source. This also
        # covers plans, tool calls and streamed replies without relying on a
        # browser clock or guessing times when old chats are loaded.
        for message in self.msgs[prior_messages:]:
            message.setdefault("t", time.time())


class GpuMonitor:
    """Samples nvidia-smi once a second (GPU %, VRAM) and pushes it to the UI."""

    def __init__(self):
        self.last = {}

    def sample(self):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                 creationflags=0x08000000).stdout.strip().splitlines()[0]
            util, used, total, temp, power = [x.strip() for x in out.split(",")]
            self.last = {"gpu": int(float(util)), "vram_used": int(float(used)), "vram_total": int(float(total)),
                         "temp": int(float(temp)) if temp.replace(".", "").isdigit() else None,
                         "power": float(power) if power.replace(".", "").isdigit() else None, "ts": time.time()}
        except Exception:
            self.last = {"error": "nvidia-smi unavailable", "ts": time.time()}
        try:                                          # CPU % (since the last sample) and system RAM (MB), for the top bar
            import psutil
            vm = psutil.virtual_memory()
            self.last.update(cpu=round(psutil.cpu_percent(interval=None)),
                             ram_used=int((vm.total - vm.available) / 1048576), ram_total=int(vm.total / 1048576))
        except Exception:
            pass
        return self.last

    def loop(self):
        while True:
            BUS.send({"t": "sys", **self.sample()})
            time.sleep(1.0)


GPU = GpuMonitor()


class Run:
    """One agent (or chat) turn running in a thread."""

    def __init__(self, conv, kind, target):
        self.conv, self.kind = conv, kind
        self.started = time.time()
        self.writer = ConvWriter(conv)
        self.stop_evt = threading.Event()
        self.engine = None
        self.thread = threading.Thread(target=self._wrap, args=(target,), daemon=True)
        self.done = False

    def _wrap(self, target):
        try:
            target(self)
        except Exception as e:
            self.writer.event("failed", {"text": f"{type(e).__name__}: {e}"})
        finally:
            with STORE.lock:
                self.writer.finish_live()
            self.done = True
            self.ended = time.time()
            HOST.run_finished(self)

    def start(self):
        self.thread.start()

    def running(self):
        return not self.done


class Host:
    """Engines per conversation, runs, approvals and delegated tasks (the engine 'host' API)."""

    def __init__(self):
        self.book = agent_host.TaskBook()
        self.engines = {}              # conv id -> (engine, cfg key)
        self.runs = {}                 # conv id -> Run
        self.approvals = {}            # call id -> (Run, Event, box)
        self.lock = threading.RLock()
        self.book.listeners.append(lambda *a: BUS.send({"t": "tasks"}))
        bg_tasks.MANAGER.listeners.append(lambda *a: BUS.send({"t": "tasks"}))

    def running(self, cid):
        r = self.runs.get(cid)
        return bool(r and r.running())

    def runs_active(self):
        return any(r.running() for r in list(self.runs.values()))

    def waiting_convs(self):
        with self.lock:
            return {r.conv["id"] for r, _e, _b in self.approvals.values()}

    # ---- agent engines
    def cfg_for(self, conv):
        cfg = copy.deepcopy(STORE.settings["agent"])
        cfg["extra_roots"] = [os.path.join(ATTACH, conv["id"])]
        api = sys.modules.get("harness_api")
        return api.apply_cfg(conv, cfg) if api else cfg     # API runs: the headless allow-list only

    def engine_for(self, conv):
        cfg = self.cfg_for(conv)
        key = json.dumps({k: v for k, v in cfg.items() if k not in ("enabled", "allow_rules", "plan_mode", "permissions")},
                         sort_keys=True, default=str)
        have = self.engines.get(conv["id"])
        if have and have[1] == key:
            have[0].cfg["allow_rules"] = cfg.get("allow_rules", [])
            have[0].cfg["plan_mode"] = cfg.get("plan_mode", False)
            have[0].cfg["permissions"] = cfg.get("permissions", "ask")
            return have[0]
        state = have[0].export_state() if have else conv.get("agent_state")
        # The atomic lock journal wins over the delayed conversation save on restart.
        if have:
            try:
                have[0].close()
            except Exception:
                pass
        eng = agentic.AgentEngine(cfg, lambda *a, **k: None, conv_id=conv["id"], title=conv.get("title", ""),
                                  host=self, subagent=bool(conv.get("parent")))
        eng.import_state(state)
        if not state:                  # carry over a chat that started in Chat mode
            eng.history = [{"role": m["role"], "content": m.get("model_content") or m["content"]}
                           for m in conv["messages"][:-1]
                           if m.get("content") and not m.get("kind") and m["role"] in ("user", "assistant")]
        self.engines[conv["id"]] = (eng, key)
        return eng



    def start_agent(self, conv, text, images=None, compact_only=False):
        r = self.runs.get(conv["id"])
        if r and r.running():
            if r.engine is not None:
                if compact_only:
                    r.engine.request_compact()
                else:
                    r.engine.interject(text)
            return "interjected"
        eng = self.engine_for(conv)

        def target(run):
            run.engine = eng
            eng.emit = lambda kind, /, **d: run.writer.event(kind, d)
            eng.ctx.emit = eng.emit
            eng.should_stop = run.stop_evt.is_set
            eng.ask = lambda req: self._ask(run, req)
            if eng.reg is None:
                eng.prepare()
            if compact_only:
                before = (len(eng.history), eng.summary, sum(len(str(m.get("content") or "")) for m in eng.history))
                eng.request_compact()
                eng._maybe_compact()
                after = (len(eng.history), eng.summary, sum(len(str(m.get("content") or "")) for m in eng.history))
                if after == before:          # too short to compress: say so instead of doing nothing silently
                    run.writer.event("answer_end", {"text": f"Nothing to compress yet — this conversation is only about "
                                                           f"{eng._history_tokens():,} tokens."})
                else:
                    run.writer.event("answer_end", {"text": "", "compact_only": True})
                return
            try:
                eng.run(text, images)
            finally:                          # the turn is over: stop what the agent started (keep=True excepted)
                try:
                    import bg_tasks
                    stopped = bg_tasks.MANAGER.stop_owned(conv["id"])
                    if stopped:
                        run.writer.event("status", {"text": f"Stopped {len(stopped)} background process(es) the "
                                                            f"agent started"})
                except Exception:
                    pass
        run = Run(conv, "agent", target)
        run.engine = eng
        self.runs[conv["id"]] = run
        run.start()
        STORE.touch(conv, meta=True)
        return "started"

    COMFORT_PROMPT = (
        "Write an original, heartfelt story of about 2,000 words, in at least eight scenes: a little clam leaves the safety of its tide pool and "
        "goes on a hero's journey — the call to adventure, a reluctant start, a wise mentor, trials, a dark moment "
        "where everything seems lost, and a hard-won return — and through dire odds and painful adversity learns the "
        "true meaning of friendship. Give the clam and its companions names and distinct personalities; let the "
        "danger feel real and the losses hurt; end warmly and hopefully, with the friendship being what saves it. "
        "Write it as a story with a title and scene headings, in vivid but simple prose, with dialogue. Take your time with each scene; do not rush the ending. No preamble.")

    def start_story(self, conv, mode):
        """/comfort: a ~2,000-token story from whichever model is loaded for this mode (no tools, no thinking)."""
        def target(run):
            w = run.writer
            msgs = [{"role": "system", "content": "You are a gifted storyteller who writes warm, moving stories."},
                    {"role": "user", "content": self.COMFORT_PROMPT}]
            if mode == "speech":
                import speech_engine
                model = STORE.settings["ui"].get("speech_model") or speech_engine.DEFAULT_MODEL
                base = speech_engine.ensure_model(model, lambda s: w.event("status", {"text": s}))
                w.event("status", {"text": "Telling a story…"})
                text = speech_engine._stream(base, msgs, 3000, lambda d: w.event("ans_delta", {"text": d}),
                                             run.stop_evt.is_set, temperature=0.9)
            else:
                eng = self.engine_for(conv)
                eng.emit = lambda kind, /, **d: w.event(kind, d)
                eng.ctx.emit = eng.emit
                eng.should_stop = run.stop_evt.is_set
                if eng.reg is None:
                    eng.prepare()
                w.event("status", {"text": "Telling a story…"})
                call = eng._call_ollama if eng.cfg.get("backend") == "ollama" else eng._call_openai
                msg, _n, _p = call(msgs, tools=False, max_tokens=3000, no_think=True)
                text = msg.get("content") or ""
            w.event("answer_end", {"text": text})
        run = Run(conv, "story", target)
        self.runs[conv["id"]] = run
        run.start()
        STORE.touch(conv, meta=True)

    def start_chat(self, conv, text, model, think):
        s = STORE.settings

        def target(run):
            w = run.writer
            visible = [m for m in conv["messages"][:-1] if m.get("content") and not m.get("kind")]
            summ = conv.get("chat_summary") or {}
            start = 0
            history = []
            if summ.get("text"):
                start = min(int(summ.get("upto", 0)), len(visible))
                history = [{"role": "user", "content": "[Summary of our earlier conversation]\n" + summ["text"]},
                           {"role": "assistant", "content": "Got it."}]
            src = visible[start:]
            history += [{"role": m["role"], "content": m.get("model_content") or m["content"]} for m in src]
            try:
                new_sum, n_old = agent.compress_history(history[2 if summ.get("text") else 0:], model,
                                                        int(s["num_ctx"]), summ.get("text", ""),
                                                        bool(conv.pop("force_compact", False)))
            except Exception:
                new_sum, n_old = None, 0
            if new_sum:
                with STORE.lock:
                    conv["chat_summary"] = {"text": new_sum, "upto": start + n_old}
                    conv["messages"].insert(len(conv["messages"]) - 1, {
                        "role": "assistant", "kind": "compacted", "content": "", "summary": new_sum,
                        "t": time.time()})
                history = [{"role": "user", "content": "[Summary of our earlier conversation]\n" + new_sum},
                           {"role": "assistant", "content": "Got it."}] + history[(2 if summ.get("text") else 0) + n_old:]

            def emit(kind, data=None):
                if kind == "status":
                    w.event("status", {"text": data})
                elif kind == "loading":
                    w.event("status", {"text": f"Loading {data}…"})
                elif kind == "ans_delta":
                    w.event("ans_delta", {"text": data})
                elif kind == "think_delta":
                    w.event("think_delta", {"text": data})
                elif kind == "tool_turn":
                    with STORE.lock:
                        if w.live is not None:
                            w.live["content"] = ""
                elif kind == "tps_live":
                    w.event("stats", {"tps": float(data)})
                elif kind == "error":
                    w.event("failed", {"text": str(data)})
            agent.run_agent_events(text, model=model, emit=emit, think=think, temperature=s["temperature"],
                                   num_ctx=s["num_ctx"], max_steps=s["max_steps"],
                                   system_prompt=s["system_prompt"], history=history,
                                   should_stop=run.stop_evt.is_set)
            w.event("answer_end", {"text": ""})
        run = Run(conv, "chat", target)
        self.runs[conv["id"]] = run
        run.start()
        STORE.touch(conv, meta=True)

    def start_speech(self, conv, text):
        import speech_engine

        def target(run):
            w = run.writer
            visible = [m for m in conv["messages"][:-1] if m.get("content") and not m.get("kind")
                       and m.get("role") in ("user", "assistant")]
            history = [{"role": m["role"], "content": (m.get("model_content") or m["content"])[:1500]} for m in visible[-8:]]
            model = STORE.settings["ui"].get("speech_model") or speech_engine.DEFAULT_MODEL
            final = speech_engine.run(text, history, w.event, run.stop_evt.is_set, model)
            w.event("answer_end", {"text": final})
        run = Run(conv, "speech", target)
        self.runs[conv["id"]] = run
        run.start()
        STORE.touch(conv, meta=True)

    def stop(self, cid):
        r = self.runs.get(cid)
        if r and r.running():
            r.stop_evt.set()
            with self.lock:
                for call_id, (rr, evt, box) in list(self.approvals.items()):
                    if rr is r:
                        box["decision"] = "deny"
                        evt.set()

    # ---- approvals
    def _ask(self, run, req):
        api = sys.modules.get("harness_api")
        policy = api.approval_decision(run, req) if api else None   # API runs have nobody to click the card
        deadline = policy[1] if isinstance(policy, tuple) else None
        if policy == "deny":
            run.writer.event("status", {"text": f"{req.get('name')}: denied by the headless approval policy"})
            return "deny"
        evt, box = threading.Event(), {}
        cid = req.get("id")
        with self.lock:
            self.approvals[cid] = (run, evt, box)
        run.writer.event("approval", req)
        tid = run.conv.get("task_id")
        if tid:
            self.book.update(tid, needs_approval=True)
        BUS.send({"t": "approval", "conv": run.conv["id"], "title": run.conv.get("title"),
                  "name": req.get("name"), "desc": (req.get("desc") or "")[:200]})
        STORE.touch(run.conv, meta=True)
        while not evt.wait(0.25):
            if run.stop_evt.is_set() or (deadline and time.time() > deadline):
                box["decision"] = "deny"
                break
        with self.lock:
            self.approvals.pop(cid, None)
        decision = box.get("decision", "deny")
        with STORE.lock:
            m = run.writer.tool_msgs.get(cid)
            if m is not None:
                m["tool"]["awaiting"] = False
                if decision == "deny":
                    m["tool"]["result"], m["tool"]["ok"] = "You denied this action.", False
                _bump(m)
        if tid and not any(r is run for r, _e, _b in self.approvals.values()):
            self.book.update(tid, needs_approval=False)
        STORE.touch(run.conv, meta=True)
        return decision

    def resolve(self, call_id, decision):
        with self.lock:
            item = self.approvals.get(call_id)
        if not item:
            return False
        item[2]["decision"] = decision
        item[1].set()
        return True

    # ---- when a run ends
    def run_finished(self, run):
        conv = run.conv
        if run.engine is not None:
            try:
                with STORE.lock:
                    conv["agent_state"] = run.engine.export_state()
            except Exception:
                pass
        tid = conv.get("task_id")
        if tid:
            final = run.writer.final or ""
            status = "stopped" if final == "(stopped)" else ("failed" if final.startswith("⚠") else "done")
            unwatched = self.book.finish(tid, status, final)
            info = self.book.info(tid) or {}
            parent = STORE.get(conv.get("parent"))
            if parent is not None:
                with STORE.lock:
                    parent["messages"].append({"role": "assistant", "kind": "task_done", "content": "",
                                               "task_id": tid, "title": info.get("title", ""), "status": status,
                                               "conv_id": conv["id"], "result": final[:600]})
                STORE.touch(parent, meta=True)
                pe = self.engines.get(parent["id"])
                if unwatched and pe:
                    pe[0].notify(f"delegated task {tid} '{info.get('title', '')}' finished ({status}). "
                                 f"Its report:\n{final[:4000]}")
                    if not self.running(parent["id"]) and STORE.settings["agent"].get("auto_continue", True):
                        self.start_agent(parent, "(A background task you delegated has finished. Continue with "
                                                 "the original request using its report.)")
        STORE.touch(conv, meta=True)
        BUS.send({"t": "run_end", "conv": conv["id"]})
        if not conv.get("task_id"):                      # sub-agent threads report to their parent instead
            final = (run.writer.final or "").strip()
            if final and final != "(stopped)":
                notify(("Scheduled: " if conv.get("scheduled") else "Done: ") + conv.get("title", "Agent")[:60],
                       " ".join(final.split())[:200], conv["id"])

    # ---- delegation API (called from engine threads)
    def delegate(self, task, title, context, parent):
        limit = int(STORE.settings["agent"].get("max_delegates") or 3)
        running = sum(1 for t in self.book.all() if t["status"] == "running")
        if running >= limit:
            raise at.ToolError(f"{running} delegated tasks are already running (limit {limit}); wait for one")
        conv = STORE.new_conv(f"⇢ {title}", parent=parent, mode="agent")
        info = self.book.new(title, task, parent, conv["id"])
        with STORE.lock:
            conv["task_id"] = info["id"]
            conv["messages"].append({"role": "user", "content": f"Delegated task from the main thread:\n\n{task}"
                                     + (f"\n\nContext:\n{context}" if context else "")})
        self.start_agent(conv, agent_host.subagent_prompt(task, context))
        return {**info, "conv_id": conv["id"]}

    def task_info(self, tid):
        return self.book.info(tid)

    def wait(self, tid, timeout, should_stop=lambda: False):
        return self.book.wait(tid, timeout, should_stop)

    def tasks_of(self, parent):
        return self.book.of_parent(parent)


HOST = Host()


# ============================================================================ attachments
def _attachment_block(conv_id, atts):
    """Copy staged uploads into the thread's folder and describe them for the model.
    -> (model text, image bytes list, visible list)."""
    dest_dir = os.path.join(ATTACH, conv_id)
    os.makedirs(dest_dir, exist_ok=True)
    blocks, images, visible = [], [], []
    for a in atts:
        src = a.get("path")
        if not src or not os.path.isfile(src):
            continue
        name = os.path.basename(src)
        dest = os.path.join(dest_dir, name)
        if os.path.abspath(src) != os.path.abspath(dest):
            if os.path.exists(dest):
                base, ext = os.path.splitext(name)
                dest = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")
            shutil.copy2(src, dest)
        kind = doc_extract.kind_of(dest)
        visible.append({"name": os.path.basename(dest), "path": dest, "kind": kind,
                        "size": os.path.getsize(dest)})
        if kind == "image":
            with open(dest, "rb") as f:
                images.append(f.read())
            blocks.append(f"[Attached image: {dest}]")
            continue
        try:
            info = doc_extract.extract(dest, max_chars=INLINE_DOC_CHARS)
            head = f"[Attached file: {doc_extract.summary_line(dest, info)} — saved at {dest}]"
            if info["note"]:
                head += f"\nNote: {info['note']}"
            if info["truncated"] or info["chars"] >= INLINE_DOC_CHARS:
                head += ("\n(Only the beginning is shown. Use read_document with start/end or sheet to read the "
                         "rest.)")
            blocks.append(f"{head}\n<document name=\"{os.path.basename(dest)}\">\n{info['text']}\n</document>")
        except doc_extract.DocError as e:
            blocks.append(f"[Attached file: {dest} — could not extract text: {e}]")
    return "\n\n".join(blocks), images, visible


# ============================================================================ helpers
_OPENAI_MODELS = {"t": 0, "list": []}


def openai_models():
    """OpenAI chat models that can drive the agent (tool calls through the chat API), newest families first."""
    import swarm
    key = swarm.api_key()
    if not key:
        return []
    if time.time() - _OPENAI_MODELS["t"] < 3600 and _OPENAI_MODELS["list"]:
        return _OPENAI_MODELS["list"]
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ids = [m["id"] for m in json.load(resp).get("data", [])]
    except Exception:
        return _OPENAI_MODELS["list"]
    rx = re.compile(r"^(gpt-4\.1(-mini|-nano)?|gpt-5(\.\d)?(-mini|-nano|-luna|-sol|-terra)?|gpt-6(\.\d)?-(luna|sol|astra)"
                    r"|o3|o4-mini)$")
    keep = [i for i in ids if rx.match(i)]

    def order(m):
        fam = re.match(r"^(gpt|o)-?([\d.]+)?", m)
        ver = float(re.sub(r"[^\d.]", "", (fam.group(2) or "0")).strip(".") or 0) if fam else 0
        return (-ver, m)
    _OPENAI_MODELS.update(t=time.time(), list=sorted(keep, key=order))
    return _OPENAI_MODELS["list"]


def list_models():
    try:
        import ollama
        names = [m.model for m in ollama.Client().list().models]
    except Exception:
        names = []
    names = [n for n in names if "embed" not in n.lower()]
    import llama_local
    # tuned profiles (settings agent.model_overrides with a model_file on disk, e.g. ornith-1.5:9b): listed by name so
    # picking one uses its profile (drafter, context, cache) rather than the bare .gguf file
    try:
        from agent_config import model_presets, model_profile
        ag = STORE.settings.get("agent") or {}
        ov = {k: model_profile(ag, k) for k in set(model_presets()) | set(ag.get("model_overrides") or {})}
        profiles = [k for k, v in ov.items() if isinstance(v, dict) and v.get("model_file")
                    and os.path.isfile(paths.resolve_model(v["model_file"])) and k not in names]
    except Exception:
        profiles = []
    return (sorted(names) + sorted(profiles)
            + llama_local.local_gguf_models())


def allowed_path(p):
    p = os.path.abspath(p)
    roots = [os.path.abspath(r) for r in STORE.settings["agent"].get("roots") or []] + [APP_DIR, ATTACH, DATA_DIR]
    return any(os.path.splitdrive(p)[0].lower() == os.path.splitdrive(r)[0].lower()
               and os.path.commonpath([p, r]) == r for r in roots)


def tasks_payload():
    out = []
    waiting = HOST.waiting_convs()
    for t in HOST.book.all():
        out.append({"type": "thread", "id": t["id"], "title": t["title"], "conv_id": t["conv_id"],
                    "status": "needs approval" if t["conv_id"] in waiting else t["status"],
                    "started": t["started"], "ended": t["ended"], "parent": t["parent"],
                    "result": (t.get("result") or "")[:400],
                    "tracker": at.load_tracker(at.default_tracker_path(t["conv_id"]))})
    for cid, r in HOST.runs.items():
        if r.running() and not r.conv.get("task_id"):
            out.append({"type": "run", "id": cid, "title": r.conv.get("title"), "conv_id": cid,
                        "status": "needs approval" if cid in waiting else "running",
                        "started": r.conv.get("updated") or time.time(), "ended": None, "mode": r.kind})
    for p in bg_tasks.MANAGER.list():
        out.append({"type": "bg", "id": p["id"], "title": p["name"], "status": "running" if p["running"]
                    else f"exit {p['exit']}", "started": p["started"], "ended": p["ended"], "command": p["command"],
                    "cwd": p["cwd"], "pid": p["pid"], "log": p["log"], "conv_id": p["owner"]})
    return out


# ============================================================================ HTTP API
routes = web.RouteTableDef()


def J(obj, status=200):
    return web.json_response(obj, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/state")
async def api_state(req):
    s = STORE.settings
    return J({"settings": s, "threads": thread_list(), "models": await asyncio.to_thread(list_models),
              "defaults": {"agent": DEFAULT_AGENT}, "user": os.environ.get("USERNAME", ""),
              "openai": bool(__import__("swarm").api_key()),
              "openai_models": await asyncio.to_thread(openai_models)})


STARTED = time.time()
NOTIFIER = None          # fn(title, body, conv_id) set by app_web.py; shows a tray notification when unfocused


def notify(title, body, conv_id=None):
    if NOTIFIER is None or not STORE.settings["ui"].get("notifications", True):
        return
    try:
        NOTIFIER(title, body[:240], conv_id)
    except Exception as e:
        print("notify error:", e, flush=True)


def code_mtime():
    """Newest modification time of the app's code (to spot a server running stale code)."""
    files = [f for f in os.listdir(APP_DIR) if f.endswith(".py")] + ["web_dist/index.html"]
    return max((os.path.getmtime(os.path.join(APP_DIR, f)) for f in files if os.path.exists(os.path.join(APP_DIR, f))),
               default=0)


@routes.get("/api/build")
async def api_build(req):
    return J({"started": STARTED, "code_mtime": code_mtime(), "busy": any(r.running() for r in HOST.runs.values()),
              "pid": os.getpid()})


NOTES, _NOTE_SEQ = [], [0]     # notifications for the desktop window when the server runs in its own process


def queue_note(title, body, conv_id=None):
    _NOTE_SEQ[0] += 1
    NOTES.append({"seq": _NOTE_SEQ[0], "title": title, "body": body, "conv": conv_id or ""})
    del NOTES[:-20]


@routes.get("/api/notes")
async def api_notes(req):
    since = int(req.query.get("since") or 0)
    return J({"notes": [n for n in NOTES if n["seq"] > since], "seq": _NOTE_SEQ[0]})


@routes.post("/api/shutdown")
async def api_shutdown(req):
    try:
        body = await req.json()
    except Exception:
        body = {}
    if RESIDENT and not body.get("service"):     # the tray service outlives windows; only its own Quit stops it
        return J({"ok": False, "error": "the resident service keeps running (quit it from the tray)"}, 409)
    if body.get("force"):                      # the desktop window was closed: stop everything, like before
        if body.get("if_last") and len(BUS.clients) > 1:
            return J({"ok": False, "error": "another window is still open"}, 409)
    elif any(r.running() for r in HOST.runs.values()):
        return J({"ok": False, "error": "an agent run is in progress"}, 409)

    def bye():
        time.sleep(0.3)
        try:
            HOST_stop_all()
            STORE.save_convs()
        finally:
            os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return J({"ok": True})


@routes.get("/api/commands")
async def api_commands(req):
    """Slash commands for the composer's autocomplete: every playbook and guide that '/<name>' can invoke."""
    import slash_commands
    return J({"commands": await asyncio.to_thread(slash_commands.catalog)})


@routes.get("/api/fit")
async def api_fit(req):
    import llama_local
    a = STORE.settings["agent"]
    model = req.query.get("model") or a.get("model") or ""
    ctx = int(req.query.get("ctx") or a.get("num_ctx") or 131072)
    kv = req.query.get("kv") or a.get("kv_cache") or "auto"
    exe, exe_ctx = req.query.get("exec") or "", int(req.query.get("exec_ctx") or 32768)
    reserve = await asyncio.to_thread(llama_local.vram_need_mib, exe, exe_ctx, "q8_0") if exe else 0
    fan = a.get("fanout") or {}
    if req.query.get("fan", "1" if fan.get("enabled") and fan.get("model") else "") == "1" and fan.get("gpu_assist"):
        reserve += int(fan.get("reserve_mib") or 256)     # GPU-assisted workers borrow the GPU to read prompts
    fit = await asyncio.to_thread(llama_local.fit_context, model, ctx, kv, reserve)
    fit["reserve_mib"] = reserve
    return J(fit)


@routes.get("/api/browser/frame")
async def api_browser_frame(req):
    import browser_agent
    frame, seq, agent = browser_agent.MANAGER.latest_frame()
    if not frame:
        return web.Response(status=204)
    return web.Response(body=frame, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Seq": str(seq), "X-Agent": "1" if agent else "0"})


@routes.get("/api/browser/state")
async def api_browser_state(req):
    import browser_agent
    return J(await asyncio.to_thread(browser_agent.MANAGER.state))


@routes.post("/api/browser/host")
async def api_browser_host(req):
    """The app window announces its browser (DevTools relay); the agent's browser then runs inside the window."""
    import browser_agent
    b = await req.json()
    ep = str(b.get("endpoint", ""))
    if ep and not re.fullmatch(r"http://127\.0\.0\.1:\d{2,5}", ep):  # this computer only ('' = window closed)
        return J({"error": "endpoint must be http://127.0.0.1:<port>"}, 400)
    browser_agent.set_host(ep)
    return J({"ok": True})


@routes.post("/api/browser/input")
async def api_browser_input(req):
    import browser_agent
    b = await req.json()
    kind = b.pop("kind", "")
    try:
        st = await asyncio.to_thread(browser_agent.MANAGER.user_input, kind, **b)
    except Exception as e:
        return J({"error": str(e)[:300]}, 400)
    return J(st or {})


@routes.get("/api/sys")
async def api_sys(req):
    return J(GPU.last or await asyncio.to_thread(GPU.sample))


@routes.get("/api/conv/{cid}")
async def api_conv(req):
    c = STORE.get(req.match_info["cid"])
    if not c:
        return J({"error": "not found"}, 404)
    with STORE.lock:
        data = json.loads(json.dumps({k: v for k, v in c.items() if k != "agent_state"}, default=str))
    data["running"] = HOST.running(c["id"])
    data["status"] = (HOST.runs.get(c["id"]).writer.status if HOST.running(c["id"]) else "")
    return J(data)


@routes.post("/api/send")
async def api_send(req):
    b = await req.json()
    text = (b.get("text") or "").strip()
    atts = b.get("attachments") or []
    mode = b.get("mode") or STORE.settings["ui"].get("mode", "agent")
    conv = STORE.get(b.get("conv_id")) if b.get("conv_id") else None
    if conv is None:
        conv = STORE.new_conv((text or (atts[0]["name"] if atts else "New chat"))[:48], mode=mode)
    if text == "/comfort":
        if HOST.running(conv["id"]):
            return J({"conv_id": conv["id"], "result": "busy"})
        with STORE.lock:
            conv["messages"].append({"role": "user", "content": "/comfort — tell me a story", "t": time.time()})
        STORE.touch(conv, meta=True)
        HOST.start_story(conv, mode)
        return J({"conv_id": conv["id"], "result": "started"})
    if text == "/compact":
        if mode == "agent" or conv.get("mode") == "agent":
            HOST.start_agent(conv, "", compact_only=True)
        else:
            conv["force_compact"] = True
        return J({"conv_id": conv["id"], "result": "compacting"})
    model_text, images, visible = (await asyncio.to_thread(_attachment_block, conv["id"], atts)) if atts else ("", [], [])
    full = text + (("\n\n" + model_text) if model_text else "")
    user_msg = {"role": "user", "content": text or "(see attached files)", "t": time.time()}
    if visible:
        user_msg["attachments"] = visible
        user_msg["model_content"] = full
    running = HOST.running(conv["id"])
    if running:
        user_msg["interjection"] = True
    with STORE.lock:
        conv["messages"].append(user_msg)
        conv["updated"] = time.time()
        if not running:
            conv["mode"] = mode
    STORE.touch(conv, meta=True)
    if mode == "agent":
        result = HOST.start_agent(conv, full or "(see attached files)", images or None)
    elif mode == "speech":
        if running:
            return J({"conv_id": conv["id"], "result": "busy"})
        if any(r.running() and r.kind == "agent" for r in HOST.runs.values()):
            with STORE.lock:
                conv["messages"].append({"role": "assistant", "content": "An agent task is still running and speech "
                                         "mode needs the GPU it is using. Stop that task (or wait for it), then ask again.",
                                         "error": True, "t": time.time()})
            STORE.touch(conv, meta=True)
            return J({"conv_id": conv["id"], "result": "busy"})
        HOST.start_speech(conv, full)
        result = "started"
    else:
        if running:
            return J({"conv_id": conv["id"], "result": "busy"})
        model = b.get("model") or STORE.settings["ui"].get("chat_model") or (list_models() or ["qwen3:30b-a3b"])[0]
        HOST.start_chat(conv, full, model, bool(b.get("think", True)))
        result = "started"
    return J({"conv_id": conv["id"], "result": result})


@routes.post("/api/stop")
async def api_stop(req):
    b = await req.json()
    HOST.stop(b.get("conv_id"))
    return J({"ok": True})


@routes.post("/api/approve")
async def api_approve(req):
    b = await req.json()
    return J({"ok": HOST.resolve(b.get("call_id"), b.get("decision", "deny"))})


@routes.post("/api/rule")
async def api_rule(req):
    b = await req.json()
    with STORE.lock:
        STORE.settings["agent"].setdefault("allow_rules", []).append(b["rule"])
    STORE.save_settings()
    return J({"ok": True})


@routes.post("/api/revert")
async def api_revert(req):
    b = await req.json()
    conv = STORE.get(b.get("conv_id"))
    if not conv:
        return J({"error": "not found"}, 404)
    try:
        with STORE.lock:
            m = conv["messages"][int(b["index"])]
        if m.get("kind") == "tool":
            msg = at.revert(m["tool"]["display"])
            with STORE.lock:
                m["tool"]["reverted"] = True
                _bump(m)
        elif m.get("kind") == "changes":
            msgs = []
            for d in reversed(m.get("reverts") or []):
                try:
                    msgs.append(at.revert(d))
                except at.ToolError as e:
                    msgs.append(f"skipped: {e}")
            with STORE.lock:
                m["undone"] = True
                _bump(m)
                for t in conv["messages"]:
                    if t.get("kind") == "tool" and (t["tool"].get("display") or {}) in (m.get("reverts") or []):
                        t["tool"]["reverted"] = True
                        _bump(t)
            msg = "; ".join(msgs)
        else:
            return J({"error": "nothing to revert"}, 400)
    except (at.ToolError, KeyError, IndexError, ValueError) as e:
        return J({"error": str(e)}, 400)
    STORE.touch(conv)
    return J({"ok": True, "message": msg})


@routes.delete("/api/conv/{cid}")
async def api_delete(req):
    cid = req.match_info["cid"]
    HOST.stop(cid)
    with STORE.lock:
        STORE.convs = [c for c in STORE.convs if c["id"] != cid]
        STORE.meta_dirty = True
        STORE._save_at = time.time()
    return J({"ok": True})


@routes.post("/api/conv/{cid}/rename")
async def api_rename(req):
    b = await req.json()
    c = STORE.get(req.match_info["cid"])
    if c:
        with STORE.lock:
            c["title"] = (b.get("title") or c["title"])[:80]
        STORE.touch(c, meta=True)
    return J({"ok": True})


@routes.put("/api/settings")
async def api_settings(req):
    b = await req.json()
    with STORE.lock:
        for k, v in b.items():
            if k in ("agent", "ui", "headless"):
                STORE.settings[k] = {**(STORE.settings.get(k) or {}), **v}
            else:
                STORE.settings[k] = v
    STORE.save_settings()
    err = await asyncio.to_thread(sys.modules["harness_api"].on_settings_saved) if "headless" in b else ""
    BUS.send({"t": "settings", "settings": STORE.settings})
    return J({"ok": True, "settings": STORE.settings, **({"warning": err} if err else {})})


@routes.post("/api/upload")
async def api_upload(req):
    reader = await req.multipart()
    out = []
    stage = os.path.join(ATTACH, "_staging", uuid.uuid4().hex[:10])
    os.makedirs(stage, exist_ok=True)
    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue
        name = os.path.basename(part.filename).replace("..", "_")
        path = os.path.join(stage, name)
        size = 0
        with open(path, "wb") as f:
            while True:
                chunk = await part.read_chunk(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    return J({"error": f"{name} is larger than 200 MB"}, 413)
                f.write(chunk)
        out.append({"name": name, "path": path, "size": size, "kind": doc_extract.kind_of(path)})
    return J({"files": out})


@routes.get("/api/file")
async def api_file(req):
    p = req.query.get("path", "")
    if not p or not allowed_path(p):
        return J({"error": "that path is outside the allowed folders"}, 403)
    if not os.path.isfile(p):
        return J({"error": "not found"}, 404)
    if req.query.get("raw"):
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        return web.FileResponse(p, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})
    st = os.stat(p)
    with open(p, "rb") as f:
        raw = f.read(5_000_001)
    binary = b"\0" in raw[:4096]
    return J({"path": p, "size": st.st_size, "mtime": st.st_mtime, "binary": binary,
              "truncated": len(raw) > 5_000_000, "crlf": b"\r\n" in raw[:65536],
              "text": "" if binary else raw[:5_000_000].decode("utf-8", "replace"),
              "kind": doc_extract.kind_of(p)})


@routes.post("/api/file")
async def api_file_save(req):
    b = await req.json()
    p = b.get("path", "")
    if not p or not allowed_path(p) or not os.path.isfile(p):
        return J({"error": "cannot save there"}, 403)
    text = b.get("text", "")
    if b.get("crlf"):
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    at._backup(p)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return J({"ok": True, "mtime": os.stat(p).st_mtime})


@routes.get("/api/doc")
async def api_doc(req):
    p = req.query.get("path", "")
    if not p or not allowed_path(p):
        return J({"error": "outside the allowed folders"}, 403)
    try:
        info = await asyncio.to_thread(doc_extract.extract, p, None, None, None, 60000)
    except doc_extract.DocError as e:
        return J({"error": str(e)}, 400)
    return J(info)


@routes.get("/api/preview")
async def api_preview(req):
    """An Office file rendered to PDF by the installed Office app (cached per file version), for the file viewer."""
    p = req.query.get("path", "")
    if not p or not allowed_path(p) or not os.path.isfile(p):
        return J({"error": "not found or outside the allowed folders"}, 404)
    try:
        pdf = await asyncio.to_thread(doc_extract._office_convert, p, ".pdf")
    except Exception as e:
        return J({"error": str(e)[:300]}, 400)
    if req.query.get("check"):
        return J({"ok": True})
    return web.FileResponse(pdf, headers={"Content-Type": "application/pdf", "Cache-Control": "no-cache"})


@routes.post("/api/open")
async def api_open(req):
    b = await req.json()
    p = os.path.abspath(b.get("path", ""))
    if not os.path.exists(p):
        return J({"error": "not found"}, 404)
    if b.get("how") == "reveal":
        subprocess.Popen(["explorer", "/select,", p])
    else:
        os.startfile(p)
    return J({"ok": True})


@routes.get("/api/tasks")
async def api_tasks(req):
    return J({"tasks": tasks_payload()})


@routes.get("/api/bg/output")
async def api_bg_output(req):
    try:
        p = bg_tasks.MANAGER.get(req.query.get("id", ""))
    except KeyError as e:
        return J({"error": str(e)}, 404)
    return J({"lines": p.tail(int(req.query.get("n", 400))), "info": p.info()})


@routes.post("/api/bg/stop")
async def api_bg_stop(req):
    b = await req.json()
    try:
        bg_tasks.MANAGER.stop(b.get("id"))
    except KeyError as e:
        return J({"error": str(e)}, 404)
    return J({"ok": True})


@routes.post("/api/tasks/clear")
async def api_tasks_clear(req):
    bg_tasks.MANAGER.clear_finished()
    with HOST.book.lock:
        for k in [k for k, t in HOST.book.tasks.items() if t["status"] != "running"]:
            del HOST.book.tasks[k]
    return J({"ok": True})


@routes.get("/ws")
async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(req)
    BUS.clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        BUS.clients.discard(ws)
    return ws


async def index(req):
    path = os.path.join(DIST, "index.html")
    if not os.path.exists(path):
        return web.Response(text="The web UI has not been built yet (npm run build in the frontend).",
                            content_type="text/plain")
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- long-term memory & conversation search
def memory_indexer():
    """Keep the conversation-search index current: every 45 s, index new messages of conversations that are not
    running (incremental; the first pass over old chats takes a while, in the background)."""
    import memory_store
    try:
        st = memory_store.store()
        st.warm()
    except Exception as e:
        print("memory store unavailable:", e, flush=True)
        return
    try:
        import playbooks
        n = playbooks.index(st)
        if n:
            print(f"playbooks: {n} indexed", flush=True)
    except Exception as e:
        print("playbook index error:", e, flush=True)
    time.sleep(20)                                   # let the app finish starting first
    last_files = 0.0
    while True:
        try:
            with STORE.lock:
                convs = [{"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated"),
                          "messages": list(c.get("messages") or [])}
                         for c in STORE.convs if not HOST.running(c["id"])]
                live = {c["id"] for c in STORE.convs}
            st.prune_conversations(live)
            st.index_conversations(convs)
            try:
                import playbooks
                playbooks.index(st)                  # picks up edited / added playbook files
            except Exception:
                pass
            a = STORE.settings["agent"]
            if a.get("file_index", True) and time.time() - last_files > 600 and not HOST.runs_active():
                last_files = time.time()
                roots = [os.path.abspath(r) for r in a.get("roots") or []]
                n, ch = st.index_files(roots, should_stop=lambda: HOST.runs_active()
                                       or not STORE.settings["agent"].get("file_index", True))
                if n:
                    print(f"file index: {n} files, {ch} chunks", flush=True)
        except Exception as e:
            print("memory index error:", e, flush=True)
        time.sleep(45)


@routes.post("/api/stt")
async def api_stt(req):
    import stt
    data = await req.read()
    try:
        text = await asyncio.to_thread(stt.transcribe, data, STORE.settings["ui"].get("stt_model") or "base.en")
    except stt.STTError as e:
        return J({"error": str(e)}, 400)
    return J({"text": text})


@routes.get("/api/tts/voices")
async def api_tts_voices(req):
    import tts
    if not tts.available():
        return J({"available": False, "voices": []})
    return J({"available": True, "voices": await asyncio.to_thread(tts.voices)})


@routes.post("/api/tts")
async def api_tts(req):
    import tts
    b = await req.json()
    ui = STORE.settings["ui"]
    try:
        wav = await asyncio.to_thread(tts.synth, b.get("text", ""), b.get("voice") or ui.get("tts_voice") or "af_heart",
                                      b.get("speed") or ui.get("tts_speed") or 1.0)
    except tts.TTSError as e:
        return J({"error": str(e)}, 400)
    return web.Response(body=wav, content_type="audio/wav", headers={"Cache-Control": "no-store"})


@routes.post("/api/formula_ocr")
async def api_formula_ocr(req):
    import formula_ocr
    import tempfile
    reader = await req.multipart()
    part = await reader.next()
    if part is None:
        return J({"error": "no image"}, 400)
    data = await part.read(decode=False)
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        r = await asyncio.to_thread(formula_ocr.read, path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return J(r, 200 if r.get("latex") else 500)


@routes.get("/api/artifacts")
async def api_artifacts(req):
    import artifacts
    return J({"items": await asyncio.to_thread(artifacts.list_artifacts, req.query.get("conv", ""))})


@routes.get("/api/artifact")
async def api_artifact(req):
    import artifacts
    q = req.query
    try:
        meta, text, v = await asyncio.to_thread(artifacts.read, q.get("conv", ""), q.get("id", ""), q.get("v"))
    except artifacts.ArtifactError as e:
        return J({"error": str(e)}, 404)
    return J({"meta": meta, "content": text, "version": v,
              "path": artifacts.version_path(q.get("conv", ""), meta["id"], v)})


@routes.get("/api/memory")
async def api_memory(req):
    import memory_store
    st = memory_store.store()
    q = req.query.get("q", "").strip()
    scope = req.query.get("scope", "memory")
    kinds = ("memory", "chat") if scope == "all" else (scope,)
    if q:
        rows = await asyncio.to_thread(st.search, q, kinds, 30)
    else:
        rows = await asyncio.to_thread(st.list, "memory", 500)
    return J({"items": rows, "counts": {"memory": st.count("memory"), "chat": st.count("chat"), "files": st.file_stats()},
              "embeddings": st._model is not None, "embed_error": st._model_err})


@routes.get("/api/playbooks")
async def api_playbooks(req):
    import memory_store
    import playbooks
    st = memory_store.store()
    books = playbooks.load_all()
    off = playbooks.disabled()
    notes = {}
    for r in st.list(playbooks.NOTE_KIND, 1000):
        notes.setdefault(r.get("source"), []).append({"id": r["id"], "text": r["text"], "date": r.get("title"),
                                                      "auto": r.get("tags") == "auto"})
    items = [{"name": b["name"], "title": b["title"], "triggers": b["triggers"], "examples": b["examples"],
              "applies_when": b["applies_when"], "body": b["body"], "enabled": b["name"] not in off,
              "notes": notes.get(b["name"], [])} for b in books.values()]
    items.sort(key=lambda x: x["title"].lower())
    return J({"items": items, "min_sim": float(STORE.settings["agent"].get("playbook_min_sim") or playbooks.DEFAULT_MIN_SIM),
              "enabled": STORE.settings["agent"].get("playbooks", True)})


@routes.get("/api/playbooks/match")
async def api_playbooks_match(req):
    import memory_store
    import playbooks
    q = req.query.get("q", "").strip()
    if not q:
        return J({"matches": []})
    st = memory_store.store()
    thr = float(STORE.settings["agent"].get("playbook_min_sim") or playbooks.DEFAULT_MIN_SIM)
    ranked = await asyncio.to_thread(playbooks.rank, st, q)
    books, off = playbooks.load_all(), playbooks.disabled()
    fired = {p["name"] for p, _s in playbooks.match(st, q, min_sim=thr)}
    return J({"matches": [{"name": n, "title": books[n]["title"], "sim": round(s, 3), "fires": n in fired,
                           "off": n in off} for n, s in ranked[:6]], "min_sim": thr})


@routes.put("/api/playbooks/{name}")
async def api_playbook_edit(req):
    import playbooks
    b = await req.json()
    name = req.match_info["name"]
    if name not in playbooks.load_all():
        return J({"ok": False, "error": "unknown playbook"}, 404)
    if "enabled" in b:
        playbooks.set_enabled(name, bool(b["enabled"]))
    if isinstance(b.get("body"), str) and b["body"].strip():
        playbooks.save_body(name, b["body"])
    return J({"ok": True})


@routes.delete("/api/playbooks/notes/{nid}")
async def api_playbook_note_delete(req):
    import memory_store
    try:
        await asyncio.to_thread(memory_store.store().delete, int(req.match_info["nid"]))
    except KeyError as e:
        return J({"ok": False, "error": str(e)}, 404)
    return J({"ok": True})


@routes.post("/api/memory")
async def api_memory_add(req):
    import memory_store
    b = await req.json()
    mid = await asyncio.to_thread(memory_store.store().add, "memory", str(b.get("text", "")), str(b.get("tags", "")))
    return J({"ok": True, "id": mid})


@routes.put("/api/memory/{mid}")
async def api_memory_edit(req):
    import memory_store
    b = await req.json()
    try:
        row = await asyncio.to_thread(memory_store.store().update, int(req.match_info["mid"]), b.get("text"),
                                      b.get("tags"))
    except KeyError as e:
        return J({"ok": False, "error": str(e)}, 404)
    return J({"ok": True, "item": row})


@routes.delete("/api/memory/{mid}")
async def api_memory_delete(req):
    import memory_store
    try:
        await asyncio.to_thread(memory_store.store().delete, int(req.match_info["mid"]))
    except KeyError as e:
        return J({"ok": False, "error": str(e)}, 404)
    return J({"ok": True})


def start_scheduled(task):
    """Run a scheduled task in a new conversation."""
    import scheduler
    conv = STORE.new_conv(f"⏰ {task['name']}"[:48], mode="agent")
    with STORE.lock:
        conv["scheduled"] = task["id"]
        conv["messages"].append({"role": "user", "content": f"(Scheduled task '{task['name']}', {task['when']})\n\n"
                                                           f"{task['prompt']}", "t": time.time()})
    STORE.touch(conv, meta=True)
    HOST.start_agent(conv, f"This is a scheduled task ('{task['name']}', runs {task['when']}). Nobody is watching "
                           f"live: do the work, then give a concise report.\n\n{task['prompt']}")
    BUS.send({"t": "schedules", "items": scheduler.all_tasks()})
    return conv["id"]


@routes.get("/api/schedules")
async def api_schedules(req):
    import scheduler
    return J({"items": scheduler.all_tasks()})


@routes.post("/api/schedules")
async def api_schedule_add(req):
    import scheduler
    b = await req.json()
    try:
        it = scheduler.add(b.get("name", ""), b.get("prompt", ""), b.get("when", ""))
    except scheduler.ScheduleError as e:
        return J({"ok": False, "error": str(e)}, 400)
    BUS.send({"t": "schedules", "items": scheduler.all_tasks()})
    return J({"ok": True, "item": it})


@routes.put("/api/schedules/{sid}")
async def api_schedule_edit(req):
    import scheduler
    b = await req.json()
    try:
        if b.get("run_now"):
            task = next(t for t in scheduler.all_tasks() if t["id"] == req.match_info["sid"])
            cid = start_scheduled(task)
            scheduler.update_last_conv(task["id"], cid)
            return J({"ok": True, "conv_id": cid})
        it = scheduler.update(req.match_info["sid"], **{k: v for k, v in b.items() if k in ("name", "prompt", "when", "enabled")})
    except (scheduler.ScheduleError, StopIteration) as e:
        return J({"ok": False, "error": str(e) or "not found"}, 400)
    BUS.send({"t": "schedules", "items": scheduler.all_tasks()})
    return J({"ok": True, "item": it})


@routes.delete("/api/schedules/{sid}")
async def api_schedule_delete(req):
    import scheduler
    try:
        scheduler.delete(req.match_info["sid"])
    except scheduler.ScheduleError as e:
        return J({"ok": False, "error": str(e)}, 404)
    BUS.send({"t": "schedules", "items": scheduler.all_tasks()})
    return J({"ok": True})


async def _ticker(app):
    async def loop():
        while True:
            await asyncio.sleep(0.1)
            try:
                await asyncio.to_thread(BUS.flush)
            except Exception as e:
                print("flush error:", e, flush=True)
    app["ticker"] = asyncio.create_task(loop())
    threading.Thread(target=GPU.loop, daemon=True).start()
    if not os.environ.get("LWS_NO_INDEXER"):         # tests: no embedding model, no file indexing
        threading.Thread(target=memory_indexer, daemon=True, name="memory-indexer").start()
    if not os.environ.get("LWS_NO_SCHEDULER"):
        import scheduler
        threading.Thread(target=scheduler.loop, args=(start_scheduled,), daemon=True, name="scheduler").start()


async def _shutdown(app):
    HOST_stop_all()
    await asyncio.to_thread(STORE.save_convs)


def HOST_stop_all():
    for cid in list(HOST.runs):
        HOST.stop(cid)
    bg_tasks.MANAGER.stop_all()
    for mod in ("sandbox", "py_kernel"):
        try:
            __import__(mod).shutdown_all()
        except Exception:
            pass
    try:
        import browser_agent
        browser_agent.MANAGER.close()
    except Exception:
        pass
    try:
        import llama_local
        llama_local.stop()
    except Exception:
        pass


def make_app():
    app = web.Application(client_max_size=210 * 1024 * 1024)
    app.add_routes(routes)
    import harness_api                         # /v1 REST API, /mcp, the API run queue (plans/headless-harness-prd.md)
    harness_api.install(app)
    import help_api                            # Help pages, About, diagnostics
    import models_api                          # Settings → Models: inspect, profile, test-load, download
    help_api.install(app)
    models_api.install(app)
    app.router.add_get("/", index)
    if os.path.isdir(DIST):
        app.router.add_static("/", DIST, show_index=False)
    app.on_startup.append(_ticker)
    app.on_startup.append(_reap_models)
    app.on_shutdown.append(_shutdown)
    return app


async def _reap_models(app):
    """Model servers left running by an earlier run of the app that crashed: free the GPU before anything loads."""
    import llama_local
    killed = await asyncio.to_thread(llama_local.reap_orphans)
    if killed:
        print(f"stopped {len(killed)} orphaned llama-server process(es): {killed}", flush=True)
    try:                                             # and processes the agent started in a session that never cleaned up
        import bg_tasks
        n = await asyncio.to_thread(bg_tasks.MANAGER.reap_leftovers)
        if n:
            print(f"stopped {n} leftover process(es) the agent started in an earlier session", flush=True)
    except Exception:
        pass


def bind_host():
    """127.0.0.1, or every interface when Settings → Headless → LAN is on (only /v1 and /mcp answer other devices)."""
    return "0.0.0.0" if (STORE.settings.get("headless") or {}).get("lan") else "127.0.0.1"


def serve(port=None, ready=None, host=None):
    """Run the server (blocking). ready() is called once it is listening."""
    port, host = port or PORT, host or bind_host()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    BUS.loop = loop
    runner = web.AppRunner(make_app())
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, host, port)
    loop.run_until_complete(site.start())
    print(f"Local Web Search UI on http://{host}:{port}", flush=True)
    if ready:
        ready()
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(runner.cleanup())


def serve_headless(idle_exit=300):
    """The server in its own process (started by app_web.py): a crash of the desktop window no longer takes
    agent runs or model servers down. Exits by itself after `idle_exit` s with no window/browser connected and
    no run in progress, so it never lingers holding a model."""
    global NOTIFIER
    NOTIFIER = queue_note
    idle_exit = float(os.environ.get("LWS_IDLE_EXIT") or idle_exit)

    def watchdog():
        idle_since = time.time()
        while True:
            time.sleep(15)
            if BUS.clients or any(r.running() for r in HOST.runs.values()):
                idle_since = time.time()
            elif time.time() - idle_since > idle_exit:
                print("no window connected and nothing running: exiting", flush=True)
                try:
                    HOST_stop_all()
                    STORE.save_convs()
                finally:
                    os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()
    serve()


if __name__ == "__main__":
    serve()
