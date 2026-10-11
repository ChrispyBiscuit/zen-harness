"""End-to-end tests for the headless harness: /v1 REST, /mcp (HTTP + stdio bridge), the run queue, the approval
policy, settings round-trip and the Startup shortcut.

Boots the real backend in-process on a spare port with a throwaway data dir and a stub engine (no model, no GPU),
so it is safe to run while the app is in use. Standalone: `venv\\Scripts\\python.exe tests/scripts/headless_api.py`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # backend/
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="zen_headless_")
PORT = int(os.environ.get("ZH_TEST_PORT") or 8776)
os.environ.update({"LWS_DATA_DIR": TMP, "LWS_PORT": str(PORT), "LWS_NO_TELEGRAM": "1", "LWS_NO_SCHEDULER": "1",
                   "LWS_NO_INDEXER": "1", "LWS_STARTUP_DIR": os.path.join(TMP, "Startup")})
with open(os.path.join(TMP, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"agent": {"backend": "llama", "model": "stub"}, "ui": {"notifications": False}}, f)

import agent_tools as at  # noqa: E402
import agentic  # noqa: E402

LIVE = {"now": 0, "max": 0}


class StubEngine:
    """Stands in for agentic.AgentEngine: emits events like the real one and asks for approval through the
    real approval decision (AgentEngine._decision) so allow-rules and read-only mode are exercised."""

    def __init__(self, cfg, emit, conv_id="", title="", host=None, subagent=False):
        self.cfg, self.emit, self.conv_id = cfg, emit, conv_id
        self.ctx = type("Ctx", (), {"emit": emit})()
        self.reg, self.mcp, self.session_ok, self.history, self.summary = None, None, set(), [], ""
        self.should_stop = lambda: False
        self.ask = lambda req: "deny"

    def prepare(self):
        self.reg = object()

    def import_state(self, st):
        pass

    def export_state(self):
        return {"stub": True}

    def close(self):
        pass

    def interject(self, text):
        pass

    def notify(self, text):
        pass

    def run(self, text, images=None):
        LIVE["now"] += 1
        LIVE["max"] = max(LIVE["max"], LIVE["now"])
        try:
            self.emit("status", text="Working…")
            if "SLOW" in text:
                for _ in range(30):
                    if self.should_stop():
                        self.emit("answer_end", text="(stopped)")
                        return
                    time.sleep(0.1)
            if "FAIL" in text:
                raise RuntimeError("boom")
            outcome = ""
            if "WRITE" in text:
                args = {"command": "Set-Content out.txt hi"}
                dec = REAL_ENGINE._decision(self, {"kind": at.EXEC}, "powershell", args)
                if dec == "ask":
                    rule = at.suggest_rule("powershell", args)
                    dec = self.ask({"id": "call_" + str(time.time_ns()), "name": "powershell", "kind": at.EXEC,
                                    "desc": "write a file", "warnings": [], "rule": rule,
                                    "rule_label": at.rule_label(rule)})
                outcome = f" [write:{dec}]"
            for w in ("Done ", "with ", "it."):
                self.emit("ans_delta", text=w)
            self.emit("answer_end", text=f"Answer to: {text}{outcome}")
        finally:
            LIVE["now"] -= 1


REAL_ENGINE = agentic.AgentEngine
agentic.AgentEngine = StubEngine
import web_backend as wb  # noqa: E402

BASE = f"http://127.0.0.1:{PORT}"
RESULTS = []


def http(method, path, body=None, token=None, headers=None, timeout=30):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw), r.headers
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), e.headers
        except Exception:
            return e.code, raw, e.headers


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   -> {detail}"), flush=True)


def wait_run(tok, rid, secs=20):
    _s, b, _h = http("GET", f"/v1/runs/{rid}?wait={secs}", token=tok, timeout=secs + 10)
    return b["run"]


def settings_file():
    with open(os.path.join(TMP, "settings.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    threading.Thread(target=wb.serve, kwargs={"port": PORT}, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/v1/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    import harness_api as api

    # ---- health, token, auth, origin
    s, b, _ = http("GET", "/v1/health")
    check("health is public and reports the queue", s == 200 and b["ok"] and b["queued"] == 0, b)
    tok = settings_file().get("headless", {}).get("token", "")
    check("a token is generated and saved on first start", len(tok) >= 32, tok)
    s, b, h = http("POST", "/v1/runs", {"input": "hi"})
    check("no token -> 401", s == 401 and "Bearer" in (h.get("WWW-Authenticate") or ""), (s, b))
    s, b, _ = http("POST", "/v1/runs", {"input": "hi"}, token="wrong")
    check("wrong token -> 401", s == 401, (s, b))
    s, b, _ = http("GET", "/v1/runs", token=tok, headers={"Origin": "https://evil.example"})
    check("cross-origin browser request -> 403", s == 403, (s, b))
    s, b, _ = http("GET", "/v1/runs", token=tok, headers={"Origin": "http://localhost:5173"})
    check("local origin is allowed", s == 200, (s, b))
    s, b, _ = http("POST", "/v1/runs", {"input": "   "}, token=tok)
    check("empty input -> 400", s == 400, (s, b))
    s, b, _ = http("POST", "/v1/runs", {"input": "x", "conversation_id": "nope"}, token=tok)
    check("unknown conversation -> 404", s == 404, (s, b))

    # ---- run lifecycle with wait
    s, b, _ = http("POST", "/v1/runs", {"input": "Say hello", "wait": 20}, token=tok, timeout=40)
    run = b["run"]
    check("POST /v1/runs with wait returns the finished run", s == 200 and run["status"] == "done"
          and run["result"] == "Answer to: Say hello", (s, b))
    s, b, _ = http("GET", f"/v1/runs/{run['id']}/events?since=0", token=tok)
    kinds = [e["kind"] for e in b["events"]]
    check("events were recorded (status, deltas, answer_end, end)", kinds[:1] == ["status"] and "ans_delta" in kinds
          and kinds[-2:] == ["answer_end", "end"], kinds)
    cid = run["conversation_id"]
    s, b, _ = http("GET", f"/v1/conversations/{cid}", token=tok)
    check("the run's conversation is a normal chat tagged source=api",
          s == 200 and b["source"] == "api" and [m["role"] for m in b["messages"]] == ["user", "assistant"], b)
    s, b, _ = http("GET", "/v1/conversations", token=tok)
    check("conversations list includes it", any(c["id"] == cid for c in b["conversations"]), b)

    # ---- continue the same conversation
    s, b, _ = http("POST", "/v1/runs", {"input": "And again", "conversation_id": cid, "wait": 20}, token=tok, timeout=40)
    check("a follow-up run continues the conversation", b["run"]["conversation_id"] == cid and b["run"]["status"] == "done", b)

    # ---- queue: runs never overlap
    LIVE["max"] = 0
    ids = [http("POST", "/v1/runs", {"input": f"SLOW job {i}"}, token=tok)[1]["run"]["id"] for i in range(3)]
    s, b, _ = http("GET", "/v1/health")
    check("queued runs are counted", b["queued"] + (1 if b["current_run"] else 0) >= 2, b)
    finals = [wait_run(tok, i, 30) for i in ids]
    check("three queued runs all finish", all(r["status"] == "done" for r in finals), finals)
    check("…one at a time (never two engine runs at once)", LIVE["max"] == 1, LIVE)
    order = sorted(finals, key=lambda r: r["started"])
    check("…in submission order", [r["id"] for r in order] == ids, order)

    # ---- the API waits for a desktop run
    conv = wb.STORE.new_conv("desktop chat", mode="agent")
    wb.HOST.start_agent(conv, "SLOW desktop work")
    time.sleep(0.3)
    s, b, _ = http("POST", "/v1/runs", {"input": "after desktop"}, token=tok)
    time.sleep(0.6)
    st = http("GET", f"/v1/runs/{b['run']['id']}", token=tok)[1]["run"]
    check("an API run waits while a desktop run uses the GPU", st["status"] == "queued", st)
    check("…and then runs", wait_run(tok, b["run"]["id"], 20)["status"] == "done")

    # ---- cancel: queued and running
    r1 = http("POST", "/v1/runs", {"input": "SLOW to cancel"}, token=tok)[1]["run"]
    r2 = http("POST", "/v1/runs", {"input": "queued to cancel"}, token=tok)[1]["run"]
    s, b, _ = http("POST", f"/v1/runs/{r2['id']}/cancel", token=tok)
    check("cancel a queued run", b["run"]["status"] == "cancelled", b)
    time.sleep(0.8)
    http("POST", f"/v1/runs/{r1['id']}/cancel", token=tok)
    check("cancel a running run", wait_run(tok, r1["id"], 10)["status"] == "cancelled")

    # ---- failure
    r = http("POST", "/v1/runs", {"input": "FAIL please", "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("an engine exception gives status failed with the error", r["status"] == "failed" and "boom" in r["error"], r)

    # ---- approval policy
    r = http("POST", "/v1/runs", {"input": "WRITE a file", "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("approval=deny: a write is denied without anyone asked", r["result"].endswith("[write:deny]"), r)
    ev = http("GET", f"/v1/runs/{r['id']}/events", token=tok)[1]["events"]
    check("…and the denial is in the event log", any("headless approval policy" in json.dumps(e) for e in ev), ev)
    wb.STORE.settings["agent"]["allow_rules"] = [{"tool": "powershell", "pattern": "*"}]   # desktop rule
    r = http("POST", "/v1/runs", {"input": "WRITE again", "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("desktop 'Always allowed' rules do not apply to API runs", r["result"].endswith("[write:deny]"), r)
    wb.STORE.settings["agent"]["allow_rules"] = []
    wb.STORE.settings["headless"]["allow_rules"] = [{"tool": "powershell", "pattern": "set-content *"}]
    r = http("POST", "/v1/runs", {"input": "WRITE allowed", "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("headless allow_rules auto-approve matching calls", r["result"].endswith("[write:auto]"), r)
    r = http("POST", "/v1/runs", {"input": "WRITE read-only", "read_only": True, "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("read_only forces plan mode (writes refused)", "[write:refuse:" in r["result"], r)
    wb.STORE.settings["headless"].update({"allow_rules": [], "approval": "ask", "approval_timeout": 1})
    r = http("POST", "/v1/runs", {"input": "WRITE ask", "wait": 10}, token=tok, timeout=20)[1]["run"]
    check("approval=ask: unanswered approvals are denied after the timeout", r["result"].endswith("[write:deny]"), r)
    wb.STORE.settings["headless"].update({"approval_timeout": 10})
    r = http("POST", "/v1/runs", {"input": "WRITE approved by a human"}, token=tok)[1]["run"]
    pend = []
    for _ in range(40):
        pend = http("GET", f"/v1/runs/{r['id']}", token=tok)[1]["run"]["pending_approvals"]
        if pend:
            break
        time.sleep(0.1)
    http("POST", "/api/approve", {"call_id": pend[0] if pend else "", "decision": "once"})
    check("approval=ask: a human approval in the app goes through",
          bool(pend) and wait_run(tok, r["id"], 10)["result"].endswith("[write:once]"), pend)
    wb.STORE.settings["headless"]["approval"] = "deny"

    # ---- SSE stream
    r = http("POST", "/v1/runs", {"input": "stream me"}, token=tok)[1]["run"]
    req = urllib.request.Request(f"{BASE}/v1/runs/{r['id']}/stream", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        sse = resp.read().decode()
    check("SSE stream sends events and ends", "event: answer_end" in sse and "event: end" in sse
          and resp.headers.get_content_type() == "text/event-stream", sse[:300])

    # ---- MCP over HTTP
    def mcp(msg, headers=None):
        return http("POST", "/mcp", msg, token=tok, headers=headers, timeout=60)
    s, b, h = mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    sid = h.get("Mcp-Session-Id")
    check("MCP initialize", s == 200 and b["result"]["serverInfo"]["name"] == "zen-headless-harness"
          and b["result"]["protocolVersion"] == "2025-06-18" and sid, b)
    s, b, _ = mcp({"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Mcp-Session-Id": sid})
    check("MCP notification -> 202", s == 202, s)
    s, b, _ = mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in b["result"]["tools"]]
    check("MCP tools/list", {"run_task", "get_run", "cancel_run", "list_runs", "list_conversations",
                             "get_conversation", "get_status"} <= set(names), names)
    s, b, _ = mcp({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "run_task", "arguments": {"task": "via mcp", "wait_seconds": 20}}})
    res = b["result"]
    check("MCP run_task returns the answer", not res["isError"] and res["content"][0]["text"].startswith("Answer to: via mcp")
          and res["structuredContent"]["status"] == "done", res)
    s, b, _ = mcp({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "get_conversation", "arguments": {"conversation_id": res["structuredContent"]["conversation_id"]}}})
    check("MCP get_conversation", "[user] via mcp\n\n[assistant] Done with it." in b["result"]["content"][0]["text"], b)
    s, b, _ = mcp({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_run", "arguments": {"run_id": "x"}}})
    check("MCP tool errors are isError results", b["result"]["isError"], b)
    s, b, _ = mcp({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    check("MCP unknown method -> -32601", b["error"]["code"] == -32601, b)
    s, b, _ = mcp([{"jsonrpc": "2.0", "id": 7, "method": "ping"}, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                                                   "params": {"name": "get_status", "arguments": {}}}])
    check("MCP batch", isinstance(b, list) and [x["id"] for x in b] == [7, 8], b)
    s, b, _ = http("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    check("MCP without token -> 401", s == 401, s)

    # ---- MCP stdio bridge (separate process, reads port/token from settings.json)
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "harness_mcp.py"), "--stdio"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy(), cwd=HERE)
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                            "clientInfo": {"name": "t", "version": "1"}}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run_task",
                                                                           "arguments": {"task": "via stdio", "wait_seconds": 20}}}]
    out, err = p.communicate(("\n".join(json.dumps(x) for x in lines) + "\n").encode(), timeout=60)
    got = [json.loads(x) for x in out.decode().splitlines() if x.strip()]
    check("stdio bridge: initialize, tools/list, run_task (notification gets no reply)",
          [g.get("id") for g in got] == [1, 2, 3] and got[2]["result"]["content"][0]["text"].startswith("Answer to: via stdio"),
          (got, err.decode()[-500:]))

    # ---- settings round-trip + Startup shortcut
    s, b, _ = http("PUT", "/api/settings", {"headless": {"autostart": True, "approval": "ask", "max_queue": 7,
                                                         "allow_rules": [{"tool": "write_file", "pattern": "C:\\tmp\\*"}]}})
    f = settings_file()["headless"]
    check("settings round-trip: saved to settings.json, token kept",
          s == 200 and f["approval"] == "ask" and f["max_queue"] == 7 and f["token"] == tok
          and f["allow_rules"][0]["tool"] == "write_file", f)
    import autostart
    check("autostart on creates the Startup shortcut", os.path.exists(autostart.shortcut_path()), b)
    s, b, _ = http("GET", "/api/headless/status")
    check("UI status reports the shortcut", b["autostart_registered"] is True and b["base_url"].endswith(str(PORT)), b)
    http("PUT", "/api/settings", {"headless": {"autostart": False, "approval": "deny"}})
    check("autostart off removes it", not os.path.exists(autostart.shortcut_path()))
    s, b, _ = http("GET", "/api/state")
    check("/api/state carries the headless settings to the UI", b["settings"]["headless"]["max_queue"] == 7, b.get("settings", {}).get("headless"))
    s, b, _ = http("POST", "/api/headless/token", {})
    new = b["token"]
    check("regenerate token", new != tok and settings_file()["headless"]["token"] == new, b)
    check("old token stops working", http("GET", "/v1/runs", token=tok)[0] == 401)
    tok = new
    http("POST", "/api/headless/pause", {"paused": True})
    r = http("POST", "/v1/runs", {"input": "paused one"}, token=tok)[1]["run"]
    time.sleep(0.8)
    check("pause: runs stay queued", http("GET", f"/v1/runs/{r['id']}", token=tok)[1]["run"]["status"] == "queued")
    http("POST", "/api/headless/pause", {"paused": False})
    check("resume: they run", wait_run(tok, r["id"], 10)["status"] == "done")
    wb.STORE.settings["headless"]["max_queue"] = 1
    api.set_paused(True)
    http("POST", "/v1/runs", {"input": "fills the queue"}, token=tok)
    s, b, _ = http("POST", "/v1/runs", {"input": "one too many"}, token=tok)
    check("full queue -> 409", s == 409, (s, b))
    api.set_paused(False)
    wb.STORE.settings["headless"]["api_enabled"] = False
    check("api_enabled=false -> 403", http("GET", "/v1/runs", token=tok)[0] == 403)
    wb.STORE.settings["headless"].update({"api_enabled": True, "mcp_enabled": False})
    check("mcp_enabled=false -> 403", http("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=tok)[0] == 403)
    wb.STORE.settings["headless"].update({"mcp_enabled": True, "rate_limit": 3})
    api._HITS.clear()
    codes = [http("GET", "/v1/runs", token=tok)[0] for _ in range(5)]
    check("rate limit -> 429", codes[:3] == [200] * 3 and codes[3] == 429, codes)
    wb.STORE.settings["headless"]["rate_limit"] = 120
    api._HITS.clear()

    # ---- resident shutdown guard, LAN guard
    wb.RESIDENT = True
    s, b, _ = http("POST", "/api/shutdown", {"force": True, "if_last": True})
    check("a resident service refuses window shutdown requests", s == 409, (s, b))
    wb.RESIDENT = False
    check("bound to loopback by default", wb.bind_host() == "127.0.0.1")
    import asyncio
    from aiohttp.test_utils import make_mocked_request
    from unittest import mock

    def remote_req(path, ip):
        tr = mock.Mock()
        tr.get_extra_info = lambda k, d=None: (ip, 5555) if k == "peername" else d
        return make_mocked_request("GET", path, transport=tr, headers={"Authorization": f"Bearer {tok}"})

    async def ok_handler(req):
        from aiohttp import web
        return web.json_response({"ok": True})

    async def lan_checks():
        a = await api.middleware(remote_req("/api/state", "192.168.1.50"), ok_handler)
        b_ = await api.middleware(remote_req("/v1/runs", "192.168.1.50"), ok_handler)
        c = await api.middleware(remote_req("/", "192.168.1.50"), ok_handler)
        return a.status, b_.status, c.status
    codes = asyncio.run(lan_checks())
    check("other devices: UI and /api blocked, /v1 allowed with token", codes == (403, 200, 403), codes)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        os._exit(code)
