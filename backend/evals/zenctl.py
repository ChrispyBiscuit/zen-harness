"""Drive a running Zen Harness from the command line (or from an AI assistant): send tasks, watch them, stop them,
read and change settings. Talks to the app's local HTTP API on this PC only; standard library only.

  python zenctl.py health
  python zenctl.py models                              model profiles, which files are present, the current default
  python zenctl.py settings [key ...]                  agent settings (all, or the named keys)
  python zenctl.py set model=ternary-bonsai-2-27b effort=high memory_recall=false   (agent settings; values are JSON)
  python zenctl.py send "task text" [--conv ID] [--mode agent|chat]
  python zenctl.py status ID [--last 12]              running?, tool calls, the last messages
  python zenctl.py watch ID [--every 60]               print new activity until the run ends
  python zenctl.py stop ID
  python zenctl.py note ID "text"                       a message to a running agent (read at its next step)

The port is 8765 unless ZEN_PORT says otherwise (Settings > Connect apps shows it).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('ZEN_PORT', '8765')}"


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method or ('POST' if data else 'GET')} {path}: HTTP {e.code} "
                         f"{'(this app version has no such route)' if e.code == 404 else e.read()[:300]!r}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Zen Harness is not reachable at {BASE} ({e}). Is the app running? (ZEN_PORT for "
                         "another port)")


def _line(m, width=220):
    if m.get("kind") == "tool":
        t = m.get("tool") or {}
        a = t.get("args") or {}
        what = a.get("path") or a.get("command") or a.get("url") or a.get("query") or ""
        res = str(t.get("result") or "").replace("\n", " ")
        return f"TOOL {t.get('name')}{'' if t.get('ok', True) else ' (FAILED)'} {str(what)[:80]} | {res[:width - 100]}"
    text = str(m.get("content") or m.get("text") or "").replace("\n", " ")
    who = m.get("kind") or m.get("role") or "?"
    return f"{who.upper()}: {text[:width]}"


def status(cid, last=12):
    c = api(f"/api/conv/{cid}")
    msgs = c.get("messages") or []
    tools = [m for m in msgs if m.get("kind") == "tool"]
    failed = sum(1 for m in tools if not (m.get("tool") or {}).get("ok", True))
    print(f"chat {cid}: {'RUNNING' if c.get('running') else 'idle'} · {len(msgs)} messages · "
          f"{len(tools)} tool calls ({failed} failed)")
    for m in msgs[-last:]:
        print("  " + _line(m))
    return c


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    opt = {}
    pos = []
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            opt[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            pos.append(rest[i])
            i += 1
    if cmd == "health":
        print(json.dumps(api("/v1/health"), indent=1))
    elif cmd == "models":
        d = api("/api/models/list")
        print(f"default model: {d.get('default') or '(none)'} · models folder: {d.get('models_dir')} · "
              f"GPU {d.get('gpu_mib')} MiB · RAM {d.get('ram_mib')} MiB")
        for name, p in sorted((d.get("profiles") or {}).items()):
            print(f"  {'✓' if p.get('exists') else ' '} {name:28} {p.get('path')}{'  (built-in)' if p.get('preset') else ''}")
    elif cmd == "settings":
        ag = api("/api/state")["settings"]["agent"]
        print(json.dumps({k: ag.get(k) for k in pos} if pos else ag, indent=1, ensure_ascii=False))
    elif cmd == "set":
        upd = {}
        for kv in pos:
            k, _, v = kv.partition("=")
            try:
                upd[k] = json.loads(v)
            except ValueError:
                upd[k] = v                          # a bare word: a string
        ag = api("/api/settings", {"agent": upd}, method="PUT")["settings"]["agent"]
        print(json.dumps({k: ag.get(k) for k in upd}, indent=1))
    elif cmd == "send":
        r = api("/api/send", {"text": pos[0], "mode": opt.get("mode", "agent"),
                              **({"conv_id": opt["conv"]} if opt.get("conv") else {})})
        print(json.dumps(r))
    elif cmd == "note":
        print(json.dumps(api("/api/send", {"text": pos[1], "mode": "agent", "conv_id": pos[0]})))
    elif cmd == "status":
        status(pos[0], int(opt.get("last", 12)))
    elif cmd == "watch":
        seen, every = 0, int(opt.get("every", 60))
        while True:
            c = api(f"/api/conv/{pos[0]}")
            msgs = c.get("messages") or []
            for m in msgs[seen:]:
                print(time.strftime("%H:%M ") + _line(m), flush=True)
            seen = len(msgs)
            if not c.get("running"):
                print("run finished")
                break
            time.sleep(every)
    elif cmd == "stop":
        print(json.dumps(api("/api/stop", {"conv_id": pos[0]})))
    else:
        raise SystemExit(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
