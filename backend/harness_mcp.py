"""The harness as an MCP server.

  POST /mcp on the running backend        streamable HTTP (JSON responses), bearer token from Settings → Headless
  python harness_mcp.py --stdio           stdio bridge for MCP clients that spawn a command: forwards each JSON-RPC
                                          line to /mcp, starting the tray service first if it is not running

Hand-written JSON-RPC (initialize, ping, tools/list, tools/call): the surface is small and it needs no new package.
Top-level imports stay light so the stdio bridge starts fast; the backend parts are imported inside install().
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_INFO = {"name": "zen-headless-harness", "title": "Zen Headless Harness", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {"name": "run_task",
     "description": "Give the Zen Harness agent a task (research, files, commands, writing…) and get its final "
                    "answer. Runs are queued and executed one at a time on the local GPU. If it is not finished "
                    "within wait_seconds you get the run_id and status instead; poll with get_run.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "What the agent should do, in plain language."},
         "conversation_id": {"type": "string", "description": "Continue this conversation instead of starting a new one."},
         "read_only": {"type": "boolean", "description": "Only investigate and propose; change nothing."},
         "wait_seconds": {"type": "number", "description": "How long to wait for the answer (default 300, max 600)."}},
         "required": ["task"]}},
    {"name": "get_run",
     "description": "Status and (when finished) the final answer of a run, optionally waiting for it to finish.",
     "inputSchema": {"type": "object", "properties": {
         "run_id": {"type": "string"}, "wait_seconds": {"type": "number", "description": "0-600 (default 0)."}},
         "required": ["run_id"]}},
    {"name": "cancel_run", "description": "Cancel a queued run or stop a running one.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
    {"name": "list_runs", "description": "Recent API runs, newest first.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "list_conversations", "description": "Recent conversations (app and API), newest first.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "get_conversation", "description": "The text messages of a conversation (last N with `last`).",
     "inputSchema": {"type": "object", "properties": {
         "conversation_id": {"type": "string"}, "last": {"type": "integer"}}, "required": ["conversation_id"]}},
    {"name": "get_status", "description": "Whether the harness is busy, paused, and how many runs are queued.",
     "inputSchema": {"type": "object", "properties": {}}},
]


# ============================================================================ server side (inside the backend)
class ToolFail(Exception):
    pass


async def _call_tool(name, a):
    import harness_api as api
    if name == "run_task":
        try:
            r = api.submit(str(a.get("task") or ""), a.get("conversation_id") or None, None, bool(a.get("read_only")))
        except ValueError as e:
            raise ToolFail(str(e))
        except KeyError:
            raise ToolFail("no such conversation")
        except OverflowError as e:
            raise ToolFail(str(e))
        await api.wait_finished(r, float(a.get("wait_seconds") or 300))
        return _run_result(r)
    if name == "get_run":
        r = api.RUNS.get(str(a.get("run_id") or ""))
        if r is None:
            raise ToolFail("no such run")
        await api.wait_finished(r, float(a.get("wait_seconds") or 0))
        return _run_result(r)
    if name == "cancel_run":
        r = api.RUNS.get(str(a.get("run_id") or ""))
        if r is None:
            raise ToolFail("no such run")
        return _run_result(api.cancel(r))
    if name == "list_runs":
        runs = [{k: v for k, v in r.public().items() if k not in ("result",)}
                for r in reversed(list(api.RUNS.values()))][:int(a.get("limit") or 20)]
        return json.dumps({"runs": runs}, ensure_ascii=False, default=str), {"runs": runs}
    if name == "list_conversations":
        import web_backend as wb
        with wb.STORE.lock:
            convs = [c for c in wb.STORE.convs if not c.get("parent")][:int(a.get("limit") or 20)]
        data = {"conversations": [api.conv_summary(c) for c in convs]}
        return json.dumps(data, ensure_ascii=False, default=str), data
    if name == "get_conversation":
        import web_backend as wb
        c = wb.STORE.get(str(a.get("conversation_id") or ""))
        if c is None:
            raise ToolFail("no such conversation")
        data = {**api.conv_summary(c), "messages": api.conv_messages(c, int(a.get("last") or 20))}
        text = "\n\n".join(f"[{m['role']}] {m['content']}" for m in data["messages"]) or "(no messages)"
        return text, data
    if name == "get_status":
        st = api.status()
        return json.dumps(st), st
    raise ToolFail(f"unknown tool {name}")


def _run_result(r):
    p = r.public()
    if r.status == "done":
        text = r.result or "(the agent finished without a written answer)"
        text += f"\n\n[run {r.id} · conversation {r.conv_id}]"
    elif r.finished:
        text = f"Run {r.id} {r.status}." + (f" {r.error}" if r.error else "") + (f"\n\n{r.result}" if r.result else "")
    else:
        text = (f"Run {r.id} is {r.status}" + (f" (position {p['queue_position']} in the queue)" if p["queue_position"] else "")
                + ". Call get_run with this run_id (and wait_seconds) to get the answer.")
    return text, {k: v for k, v in p.items()}


async def handle(msg):
    """One JSON-RPC message -> response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:                                  # notifications (initialized, cancelled…)
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return _ok(mid, {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                         "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                         "instructions": "Zen Headless Harness: a local agent with web research, file and command tools. "
                                         "Use run_task for whole tasks; it runs on this PC's GPU, one task at a time."})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return _error(mid, -32602, f"unknown tool: {name}")
        try:
            text, data = await _call_tool(name, params.get("arguments") or {})
            return _ok(mid, {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False})
        except ToolFail as e:
            return _ok(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
    return _error(mid, -32601, f"method not found: {method}")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def install(app):
    from aiohttp import web

    async def post(req):
        try:
            body = json.loads(await req.text())
        except Exception:
            return web.json_response(_error(None, -32700, "parse error"), status=400)
        sid = req.headers.get("Mcp-Session-Id") or uuid.uuid4().hex
        msgs = body if isinstance(body, list) else [body]
        out = [r for r in [await handle(m) for m in msgs] if r is not None]
        headers = {"Mcp-Session-Id": sid}
        if not out:
            return web.Response(status=202, headers=headers)
        return web.json_response(out if isinstance(body, list) else out[0], headers=headers,
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))

    async def other(req):                                 # no server-initiated stream; sessions need no teardown
        if req.method == "DELETE":
            return web.Response(status=204)
        return web.Response(status=405, headers={"Allow": "POST, DELETE"})

    app.router.add_post("/mcp", post)
    app.router.add_get("/mcp", other)
    app.router.add_delete("/mcp", other)


# ============================================================================ stdio bridge (separate process)
def service_config():
    """(port, token) from settings.json, the same way the backend reads them."""
    import paths
    path = paths.data("settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            h = json.load(f).get("headless") or {}
    except Exception:
        h = {}
    return int(os.environ.get("LWS_PORT") or h.get("port") or 8765), h.get("token") or ""


def _healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=3) as r:
            return json.load(r).get("ok", False)
    except Exception:
        return False


def start_service(port, wait=90):
    """Start the tray service (headless.py) detached, and wait until it answers."""
    import paths
    flags = 0x00000008 | 0x00000200 | 0x08000000            # detached, own process group, no window
    subprocess.Popen(paths.role_command("headless"), cwd=paths.work_dir(),
                     creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    end = time.time() + wait
    while time.time() < end:
        if _healthy(port):
            return True
        time.sleep(1)
    return False


def stdio_main():
    stdin = sys.stdin.buffer
    out = sys.stdout.buffer
    sid = None
    started = False

    def say(obj):
        out.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
        out.flush()

    for line in stdin:
        line = line.strip().removeprefix(b"\xef\xbb\xbf")      # a UTF-8 BOM some shells prepend
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            say(_error(None, -32700, "parse error"))
            continue
        mid = msg.get("id") if isinstance(msg, dict) else None
        port, token = service_config()                     # re-read: the token may be regenerated in Settings
        for attempt in (1, 2):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=line, method="POST", headers={
                "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}", **({"Mcp-Session-Id": sid} if sid else {})})
            try:
                with urllib.request.urlopen(req, timeout=660) as r:
                    sid = r.headers.get("Mcp-Session-Id") or sid
                    body = r.read()
                if body.strip():
                    out.write(body.strip() + b"\n")
                    out.flush()
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if mid is not None:
                    say(_error(mid, -32000, f"Zen Headless Harness answered HTTP {e.code}: {detail}"))
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                if attempt == 1 and not started and not _healthy(port):
                    started = True
                    if start_service(port):
                        port, token = service_config()     # a first start creates the token
                        continue
                if mid is not None:
                    say(_error(mid, -32000, f"Zen Headless Harness is not reachable on port {port}: {e}"))
                break


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        stdio_main()
    else:
        print(__doc__)
