"""A small, dependency-free MCP (Model Context Protocol) client.

Supports the two standard transports:
  * stdio  - we start the server process and speak newline-delimited JSON-RPC over its pipes
  * http   - "streamable HTTP": POST JSON-RPC to one URL (reply is JSON or an SSE stream)

Only what an agent needs: initialize -> tools/list -> tools/call. Server config:
  {"name": "files", "transport": "stdio", "command": "npx", "args": ["-y", "..."], "env": {...}}
  {"name": "notes", "transport": "http",  "url": "http://127.0.0.1:8765/mcp", "headers": {...}}
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request

PROTOCOL = "2025-06-18"
_NO_WINDOW = 0x08000000


class MCPError(Exception):
    pass


def _result_text(res):
    """tools/call result -> (text, is_error). Non-text parts are summarised, not dropped."""
    parts = []
    for c in (res or {}).get("content") or []:
        t = c.get("type")
        if t == "text":
            parts.append(str(c.get("text", "")))
        elif t == "image":
            parts.append(f"[image {c.get('mimeType', '')}, {len(c.get('data', ''))} bytes of data]")
        elif t == "resource":
            r = c.get("resource") or {}
            parts.append(str(r.get("text") or f"[resource {r.get('uri', '')}]"))
        else:
            parts.append(f"[{t} content]")
    if not parts and (res or {}).get("structuredContent") is not None:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False))
    return "\n".join(parts), bool((res or {}).get("isError"))


class _Base:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = cfg.get("name") or "server"
        self.timeout = float(cfg.get("timeout", 60))
        self.tools = []
        self._id = 0
        self.ready = False
        self.error = ""

    def _next_id(self):
        self._id += 1
        return self._id

    def list_tools(self):
        out, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += res.get("tools") or []
            cursor = res.get("nextCursor")
            if not cursor:
                break
        self.tools = out
        return out

    def call_tool(self, name, arguments):
        res = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return _result_text(res)

    def _init_params(self):
        return {"protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "web-agent", "version": "1.0"}}


class StdioServer(_Base):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.proc = None
        self._pending = {}
        self._lock = threading.Lock()
        self._stderr = []

    def start(self):
        cmd = self.cfg.get("command") or ""
        args = [str(a) for a in (self.cfg.get("args") or [])]
        exe = shutil.which(cmd) or cmd
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.cfg.get("env") or {}).items()})
        try:
            self.proc = subprocess.Popen(
                [exe] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, cwd=self.cfg.get("cwd") or None,
                creationflags=_NO_WINDOW)
        except OSError as e:
            raise MCPError(f"cannot start '{cmd}': {e}")
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self.request("initialize", self._init_params())
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.ready = True

    def _send(self, msg):
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise MCPError(f"{self.name}: connection lost ({e})")

    def _read_out(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith(b"{"):
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                q = self._pending.get(msg["id"])
                if q is not None:
                    q.append(msg)
            elif "method" in msg and "id" in msg:      # a request from the server
                reply = {"jsonrpc": "2.0", "id": msg["id"]}
                if msg["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "not supported by this client"}
                try:
                    self._send(reply)
                except MCPError:
                    pass
        self.ready = False

    def _read_err(self):
        for line in self.proc.stderr:
            self._stderr = (self._stderr + [line.decode("utf-8", "replace").rstrip()])[-20:]

    def request(self, method, params):
        rid = self._next_id()
        box = []
        self._pending[rid] = box
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        t0 = time.time()
        while time.time() - t0 < self.timeout:
            if box:
                self._pending.pop(rid, None)
                msg = box[0]
                if "error" in msg:
                    raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result") or {}
            if self.proc.poll() is not None and not box:
                tail = " | ".join(self._stderr[-3:])
                raise MCPError(f"{self.name} exited. {tail}")
            time.sleep(0.01)
        self._pending.pop(rid, None)
        raise MCPError(f"{self.name}: no reply to {method} within {self.timeout:g}s")

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        self.ready = False


class HttpServer(_Base):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.session = None

    def start(self):
        self.request("initialize", self._init_params())
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
        except MCPError:
            pass
        self.ready = True

    def _post(self, msg, expect=True):
        hdr = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
        hdr.update({str(k): str(v) for k, v in (self.cfg.get("headers") or {}).items()})
        if self.session:
            hdr["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.cfg["url"], data=json.dumps(msg).encode(), headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self.session = sid
                if not expect:
                    return None
                ctype = r.headers.get("Content-Type", "")
                if "text/event-stream" in ctype:
                    for raw in r:
                        raw = raw.decode("utf-8", "replace").strip()
                        if raw.startswith("data:"):
                            try:
                                m = json.loads(raw[5:].strip())
                            except ValueError:
                                continue
                            if m.get("id") == msg.get("id") and ("result" in m or "error" in m):
                                return m
                    raise MCPError(f"{self.name}: stream ended without a reply")
                return json.loads(r.read().decode("utf-8", "replace"))
        except MCPError:
            raise
        except Exception as e:
            raise MCPError(f"{self.name}: {e}")

    def request(self, method, params):
        rid = self._next_id()
        msg = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if "error" in msg:
            raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    def close(self):
        self.ready = False


class MCPManager:
    """Owns every configured server: starts them on demand, namespaces their tools."""

    def __init__(self, configs):
        self.configs = [c for c in (configs or []) if c.get("enabled", True)]
        self.servers = {}
        self.errors = {}

    def start_all(self):
        for cfg in self.configs:
            name = cfg.get("name") or "server"
            if name in self.servers:
                continue
            srv = HttpServer(cfg) if (cfg.get("transport") == "http" or cfg.get("url")) \
                else StdioServer(cfg)
            try:
                srv.start()
                srv.list_tools()
                self.servers[name] = srv
            except MCPError as e:
                srv.close()
                self.errors[name] = str(e)
        return self.errors

    def tools(self):
        """[(server_name, tool_dict)] across the running servers."""
        return [(n, t) for n, s in self.servers.items() for t in s.tools]

    def call(self, server, tool, args):
        srv = self.servers.get(server)
        if not srv:
            raise MCPError(f"MCP server '{server}' is not running")
        return srv.call_tool(tool, args)

    def policy(self, server):
        for c in self.configs:
            if (c.get("name") or "server") == server:
                return c.get("policy", "ask")
        return "ask"

    def close_all(self):
        for s in self.servers.values():
            s.close()
        self.servers = {}


def discover_stdio_configs():
    """MCP servers already configured for other tools on this PC (read-only discovery)."""
    home = os.path.expanduser("~")
    paths = [os.path.join(home, ".claude.json"),
             os.path.join(home, ".claude", "settings.json"),
             os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")]
    found, seen = [], set()
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        blobs = [data.get("mcpServers") or {}]
        for proj in (data.get("projects") or {}).values():
            blobs.append((proj or {}).get("mcpServers") or {})
        for blob in blobs:
            for name, c in blob.items():
                if not isinstance(c, dict) or name in seen:
                    continue
                if c.get("command"):
                    seen.add(name)
                    found.append({"name": name, "transport": "stdio", "command": c["command"],
                                  "args": c.get("args") or [], "env": c.get("env") or {},
                                  "enabled": False, "policy": "ask", "source": p})
                elif c.get("url"):
                    seen.add(name)
                    found.append({"name": name, "transport": "http", "url": c["url"],
                                  "headers": c.get("headers") or {}, "enabled": False,
                                  "policy": "ask", "source": p})
    return found
