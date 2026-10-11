"""DevTools for the agent's browser: network log, full console, element inspector (with event listeners), storage,
performance, breakpoints and pause-on-exception — what a developer gets from Chrome's DevTools panel, as text.

Everything here runs on the browser thread (browser_agent.MANAGER.call). The network/console logs are recorded per
page from the moment the page is attached (browser_agent calls attach() for every page). The debugger pieces use a
CDP session on the page; a paused page is always resumed before returning.
"""
import collections
import json
import re
import time

NET_KEEP, CON_KEEP = 600, 600
SECRET_HEADERS = re.compile(r"^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token|"
                            r"x-csrf-token|x-xsrf-token)$", re.I)


def _log(page):
    d = getattr(page, "_zen_dt", None)
    if d is None:
        d = {"net": collections.OrderedDict(), "con": collections.deque(maxlen=CON_KEEP), "seq": 0}
        page._zen_dt = d
    return d


def attach(page):
    """Start recording this page's network requests and console messages."""
    if getattr(page, "_zen_dt_on", False):
        return
    page._zen_dt_on = True
    d = _log(page)

    def on_request(req):
        d["seq"] += 1
        req._zen_id = d["seq"]
        d["net"][d["seq"]] = {"id": d["seq"], "req": req, "t0": time.time(), "status": None, "res": None,
                              "failed": None, "done": None}
        while len(d["net"]) > NET_KEEP:
            d["net"].popitem(last=False)

    def entry(req):
        return d["net"].get(getattr(req, "_zen_id", -1))

    def on_response(res):
        e = entry(res.request)
        if e:
            e["status"], e["res"] = res.status, res

    def on_finished(req):
        e = entry(req)
        if e:
            e["done"] = time.time()

    def on_failed(req):
        e = entry(req)
        if e:
            e["failed"], e["done"] = (req.failure or "failed"), time.time()

    def on_console(m):
        try:
            loc = m.location or {}
            where = f"{str(loc.get('url', '')).split('/')[-1].split('?')[0]}:{loc.get('lineNumber', '')}" \
                if loc.get("url") else ""
        except Exception:
            where = ""
        d["con"].append({"t": time.time(), "level": m.type, "text": m.text, "where": where})

    def on_pageerror(err):
        d["con"].append({"t": time.time(), "level": "uncaught", "text": str(err), "where": ""})

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_failed)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)


def _short(url, n=110):
    u = re.sub(r"^https?://", "", url)
    return u if len(u) <= n else u[:n - 30] + "…" + u[-29:]


def _size(e):
    try:
        v = e["res"].headers.get("content-length") if e["res"] else None
        return int(v) if v else None
    except Exception:
        return None


def _fmt_size(n):
    if n is None:
        return "-"
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1048576 else f"{n / 1048576:.1f} MB"


# ---------------------------------------------------------------------- actions
def network(page, filter=None, failed=False, limit=40):
    d = _log(page)
    rows = list(d["net"].values())
    if filter:
        rows = [e for e in rows if filter.lower() in e["req"].url.lower()]
    if failed:
        rows = [e for e in rows if e["failed"] or (e["status"] or 0) >= 400]
    total = len(rows)
    rows = rows[-int(limit or 40):]
    if not rows:
        return "No requests recorded" + (" matching that" if filter or failed else " for this page yet") + \
               " (recording starts when the page is opened; reload it to capture its first load)."
    out = [f"{total} request(s){' matching' if filter or failed else ''}; newest last. "
           f"id  status  method  type  size  time  url"]
    for e in rows:
        r = e["req"]
        st = f"FAILED ({e['failed']})" if e["failed"] else (str(e["status"]) if e["status"] else "pending")
        ms = f"{(e['done'] - e['t0']) * 1000:.0f} ms" if e["done"] else "…"
        out.append(f"#{e['id']}  {st}  {r.method}  {r.resource_type}  {_fmt_size(_size(e))}  {ms}  {_short(r.url)}")
    out.append("Details of one: browser_devtools action='request' id=<n>.")
    return "\n".join(out)


def _headers(h):
    return "\n".join(f"  {k}: {'[hidden]' if SECRET_HEADERS.match(k) else str(v)[:300]}" for k, v in sorted(h.items()))


def request(page, rid, max_chars=8000):
    e = _log(page)["net"].get(int(rid))
    if not e:
        return f"No request #{rid} (see action='network' for the ids)."
    r = e["req"]
    out = [f"#{e['id']} {r.method} {r.url}", f"type: {r.resource_type}"]
    if e["failed"]:
        out.append(f"FAILED: {e['failed']}")
    try:
        out.append("request headers:\n" + _headers(r.headers))
    except Exception:
        pass
    try:
        pd = r.post_data
        if pd:
            out.append("request body:\n" + pd[:2000])
    except Exception:
        pass
    res = e["res"]
    if res is not None:
        out.append(f"status: {res.status} {res.status_text}")
        try:
            out.append("response headers:\n" + _headers(res.headers))
        except Exception:
            pass
        try:
            t = r.timing
            if t and t.get("responseEnd", -1) >= 0:
                out.append("timing (ms from start): " + ", ".join(
                    f"{k.replace('Start', '').replace('End', ' end')} {t[k]:.0f}"
                    for k in ("domainLookupStart", "connectStart", "requestStart", "responseStart", "responseEnd")
                    if t.get(k, -1) >= 0))
        except Exception:
            pass
        try:
            body = res.body()
            ctype = str(res.headers.get("content-type", ""))
            if re.search(r"text|json|javascript|xml|html|css|svg", ctype) or not ctype:
                txt = body.decode("utf-8", "replace")
                out.append(f"response body ({len(body)} bytes):\n" + txt[:max_chars] +
                           (f"\n…[{len(txt) - max_chars} more characters]" if len(txt) > max_chars else ""))
            else:
                out.append(f"response body: {len(body)} bytes of {ctype} (binary, not shown)")
        except Exception as ex:
            out.append(f"response body: not available ({str(ex).splitlines()[0][:120]})")
    else:
        out.append("no response yet")
    return "\n".join(out)


def console(page, level=None, filter=None, limit=60):
    rows = list(_log(page)["con"])
    if level and level != "all":
        want = {"error": {"error", "uncaught"}, "warning": {"warning", "error", "uncaught"}}.get(level, {level})
        rows = [r for r in rows if r["level"] in want]
    if filter:
        rows = [r for r in rows if filter.lower() in r["text"].lower()]
    if not rows:
        return "No console messages" + (" matching that" if level or filter else " for this page yet") + "."
    total, rows = len(rows), rows[-int(limit or 60):]
    out, prev, rep = [f"{total} console message(s); newest last."], None, 0
    for r in rows:                                        # collapse identical repeats like DevTools does
        key = (r["level"], r["text"], r["where"])
        if key == prev:
            rep += 1
            continue
        if rep:
            out[-1] += f"  (×{rep + 1})"
        prev, rep = key, 0
        out.append(f"[{r['level']}] {' '.join(r['text'].split())[:400]}" + (f"  ({r['where']})" if r["where"] else ""))
    if rep:
        out[-1] += f"  (×{rep + 1})"
    return "\n".join(out)


_STYLE_PROPS = ["display", "visibility", "opacity", "position", "z-index", "pointer-events", "width", "height",
                "top", "left", "color", "background-color", "font-size", "overflow", "transform", "cursor"]


def _listeners(page, cdp, object_id):
    res = cdp.send("DOMDebugger.getEventListeners", {"objectId": object_id, "depth": 0})
    out = []
    for ls in res.get("listeners", []):
        where = ""
        try:
            scripts = getattr(page, "_zen_scripts", {})
            url = scripts.get(ls.get("scriptId"), "")
            where = f"{url.split('/')[-1].split('?')[0] or 'script'}:{int(ls.get('lineNumber', 0)) + 1}"
        except Exception:
            pass
        desc = ""
        h = ls.get("handler") or {}
        if h.get("description"):
            desc = " ".join(h["description"].split())[:120]
        out.append(f"  {ls.get('type')}{' (capture)' if ls.get('useCapture') else ''}{' (once)' if ls.get('once') else ''}"
                   f" → {where} {desc}".rstrip())
    return out


def _cdp(page):
    cdp = getattr(page, "_zen_cdp", None)
    if cdp is None:
        cdp = page.context.new_cdp_session(page)
        page._zen_cdp = cdp
        page._zen_scripts = {}
        cdp.on("Debugger.scriptParsed", lambda p: page._zen_scripts.__setitem__(p.get("scriptId"), p.get("url", "")))
        cdp.send("Debugger.enable")                       # needed to map listener handlers to file:line
        # never stop the page on its own (a `debugger;` statement would freeze it with nobody to resume):
        # pauses are allowed only while breakpoint()/exceptions() wait for one
        cdp.send("Debugger.setSkipAllPauses", {"skip": True})
    return cdp


def element(page, selector, styles=None):
    cdp = _cdp(page)
    r = cdp.send("Runtime.evaluate", {"expression": f"document.querySelector({json.dumps(selector)})"})
    obj = (r.get("result") or {})
    if obj.get("subtype") == "null" or "objectId" not in obj:
        n = page.evaluate("s => document.querySelectorAll(s).length", selector)
        return f"No element matches {selector!r} (querySelectorAll finds {n})."
    info = page.evaluate("""([s, props]) => {
      const el = document.querySelector(s); const cs = getComputedStyle(el); const b = el.getBoundingClientRect();
      const st = {}; for (const p of props) st[p] = cs.getPropertyValue(p);
      const atTop = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      return {html: el.outerHTML, count: document.querySelectorAll(s).length,
              box: [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)],
              styles: st, covered: !!atTop && atTop !== el && !el.contains(atTop) ?
                (atTop.tagName.toLowerCase() + (atTop.id ? '#' + atTop.id : '') +
                 (atTop.className && typeof atTop.className === 'string' ? '.' + atTop.className.split(' ')[0] : '')) : ''};
    }""", [selector, list(styles or _STYLE_PROPS)])
    html = info["html"]
    out = [f"{selector!r}: {info['count']} match(es); showing the first.",
           "html: " + (html if len(html) <= 1500 else html[:1500] + f"…[{len(html) - 1500} more]"),
           f"box (x, y, width, height): {info['box']}"]
    if info.get("covered"):
        out.append(f"COVERED: the element on top at its centre is {info['covered']} — clicks go there, not here.")
    out.append("computed styles: " + "; ".join(f"{k}: {v}" for k, v in info["styles"].items()))
    ls = _listeners(page, cdp, obj["objectId"])
    out.append("event listeners on it:" + ("\n" + "\n".join(ls) if ls else " NONE (a listener on a parent, the "
               "document or the window may still handle it: action='listeners' target='document' / 'window')"))
    return "\n".join(out)


def listeners(page, target="window"):
    cdp = _cdp(page)
    expr = {"window": "window", "document": "document"}.get(target, f"document.querySelector({json.dumps(target)})")
    obj = (cdp.send("Runtime.evaluate", {"expression": expr}).get("result") or {})
    if "objectId" not in obj:
        return f"Nothing found for {target!r}."
    ls = _listeners(page, cdp, obj["objectId"])
    return f"event listeners on {target}:" + ("\n" + "\n".join(ls) if ls else " none")


def storage(page):
    out = []
    try:
        try:
            cookies = page.context.cookies(page.url)
        except Exception:                                 # Qt's Chromium: no browser-level cookie access
            cookies = _cdp(page).send("Network.getCookies", {"urls": [page.url]}).get("cookies", [])
        out.append(f"cookies ({len(cookies)}):" + "".join(
            f"\n  {c['name']} = {str(c['value'])[:80]}  (domain {c.get('domain')}, path {c.get('path')}"
            f"{', httpOnly' if c.get('httpOnly') else ''})" for c in cookies[:40]))
    except Exception as ex:
        out.append(f"cookies: not available ({ex})")
    data = page.evaluate("""() => {
      const dump = (s) => { const o = {}; try { for (let i = 0; i < s.length; i++) { const k = s.key(i);
        o[k] = String(s.getItem(k)).slice(0, 300); } } catch (e) {} return o; };
      return {local: dump(localStorage), session: dump(sessionStorage)};
    }""")
    for name in ("local", "session"):
        kv = data.get(name) or {}
        out.append(f"{name}Storage ({len(kv)}):" + "".join(f"\n  {k} = {v}" for k, v in list(kv.items())[:60]))
    return "\n".join(out)


def performance(page, seconds=2):
    cdp = _cdp(page)
    cdp.send("Performance.enable")
    m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics").get("metrics", [])}
    fps = page.evaluate("""(ms) => new Promise(res => { let n = 0; const t0 = performance.now();
      const f = (t) => { n++; if (t - t0 < ms) requestAnimationFrame(f); else res(n * 1000 / (t - t0)); };
      requestAnimationFrame(f); setTimeout(() => res(n * 1000 / Math.max(1, performance.now() - t0)), ms + 1500); })""",
                        int(max(0.5, min(10, float(seconds or 2))) * 1000))
    mb = lambda k: f"{m.get(k, 0) / 1048576:.1f} MB"
    return (f"frames per second: {fps:.0f} (measured over {seconds}s; ~60 = smooth, <30 = janky, 0 = the page "
            f"isn't drawing — hidden tab or stopped loop)\n"
            f"JS heap: {mb('JSHeapUsedSize')} used of {mb('JSHeapTotalSize')}\n"
            f"DOM nodes: {int(m.get('Nodes', 0))}, event listeners: {int(m.get('JSEventListeners', 0))}, "
            f"documents: {int(m.get('Documents', 0))}\n"
            f"since load: {int(m.get('LayoutCount', 0))} layouts ({m.get('LayoutDuration', 0) * 1000:.0f} ms), "
            f"{int(m.get('RecalcStyleCount', 0))} style recalcs, script time {m.get('ScriptDuration', 0) * 1000:.0f} ms, "
            f"task time {m.get('TaskDuration', 0) * 1000:.0f} ms")


def _describe(cdp, obj):
    t = obj.get("type")
    if t in ("string",):
        return json.dumps(obj.get("value"))[:200]
    if "value" in obj:
        return json.dumps(obj.get("value"))[:200]
    if obj.get("unserializableValue"):
        return obj["unserializableValue"]
    if t == "undefined" or not obj:
        return "undefined" if obj else "(not set yet)"
    pv = obj.get("preview")
    if pv and pv.get("properties") is not None:
        inner = ", ".join(f"{p['name']}: {p.get('value', p.get('type'))}" for p in pv["properties"][:8])
        return f"{obj.get('description', t)} {{{inner}{', …' if pv.get('overflow') else ''}}}"[:300]
    return str(obj.get("description") or t)[:200]


def _paused_report(page, cdp, p, watch):
    frames = p.get("callFrames", [])
    scripts = getattr(page, "_zen_scripts", {})
    out = []
    reason = p.get("reason")
    if reason in ("exception", "promiseRejection"):
        out.append("PAUSED on an exception: " + _describe(cdp, p.get("data") or {}))
    else:
        out.append(f"PAUSED ({reason})")
    out.append("call stack (innermost first):")
    for f in frames[:8]:
        loc = f.get("location", {})
        url = f.get("url") or scripts.get(loc.get("scriptId"), "")
        out.append(f"  {f.get('functionName') or '(anonymous)'}  {url.split('/')[-1].split('?')[0]}:"
                   f"{loc.get('lineNumber', 0) + 1}")
    if frames:
        top = frames[0]
        for sc in top.get("scopeChain", [])[:2]:          # local, then the closest closure/block
            if sc.get("type") not in ("local", "closure", "block"):
                continue
            try:
                props = cdp.send("Runtime.getProperties", {"objectId": sc["object"]["objectId"], "ownProperties": True,
                                                           "generatePreview": True}).get("result", [])
            except Exception:
                continue
            vals = [f"  {pr['name']} = {_describe(cdp, pr.get('value') or {})}" for pr in props[:30]
                    if not pr["name"].startswith("__")]
            if vals:
                out.append(f"{sc['type']} variables:\n" + "\n".join(vals))
        for w in watch or []:
            try:
                r = cdp.send("Debugger.evaluateOnCallFrame", {"callFrameId": top["callFrameId"], "expression": w,
                                                              "generatePreview": True})
                out.append(f"watch {w} = " + (("THROWS " + _describe(cdp, r["exceptionDetails"].get("exception", {})))
                                              if r.get("exceptionDetails") else _describe(cdp, r.get("result", {}))))
            except Exception as ex:
                out.append(f"watch {w}: {str(ex).splitlines()[0][:120]}")
    return "\n".join(out)


def _wait_pause(page, cdp, trigger, timeout):
    box = {}
    handler = lambda p: box.setdefault("p", p)
    cdp.on("Debugger.paused", handler)
    cdp.send("Debugger.setSkipAllPauses", {"skip": False})
    try:
        if trigger:                                       # e.g. a click handler: run it without waiting for it
            cdp.send("Runtime.evaluate", {"expression": f"setTimeout(() => {{ {trigger} }}, 0)"})
        t_end = time.time() + timeout
        while "p" not in box and time.time() < t_end:
            page.wait_for_timeout(100)                    # pumps events while waiting
        return box.get("p")
    finally:
        try:
            cdp.remove_listener("Debugger.paused", handler)
        except Exception:
            pass
        if "p" not in box:
            try:
                cdp.send("Debugger.setSkipAllPauses", {"skip": True})
            except Exception:
                pass


def breakpoint(page, location, condition=None, watch=None, trigger=None, timeout=15):
    m = re.match(r"^(.*?):(\d+)$", str(location or "").strip())
    if not m:
        return "location must be 'file.js:LINE' (the file name as the page loads it, e.g. 'game.js:120')."
    fname, line = m.group(1).split("/")[-1].split("\\")[-1], int(m.group(2))
    cdp = _cdp(page)
    bp = cdp.send("Debugger.setBreakpointByUrl", {"lineNumber": line - 1,
                                                  "urlRegex": r"(^|/)" + re.escape(fname) + r"(\?.*)?$",
                                                  "condition": condition or ""})
    try:
        if not bp.get("locations"):
            loaded = sorted({u.split("/")[-1].split("?")[0] for u in page._zen_scripts.values() if u})
            note = f"No loaded script matches {fname!r} yet" + (f" (loaded: {', '.join(loaded[:15])})" if loaded else "") + \
                   "; the breakpoint still applies if it loads. "
        else:
            note = ""
        p = _wait_pause(page, cdp, trigger, float(timeout or 15))
        if not p:
            return (note + f"Line {line} of {fname} was not reached within {timeout}s. Make it run (trigger="
                    f"'<JS that causes it>', or click/press first), or check the line is inside code that executes.")
        return note + _paused_report(page, cdp, p, watch)
    finally:
        try:
            cdp.send("Debugger.removeBreakpoint", {"breakpointId": bp["breakpointId"]})
        except Exception:
            pass
        try:
            cdp.send("Debugger.resume")
        except Exception:
            pass
        try:
            cdp.send("Debugger.setSkipAllPauses", {"skip": True})
        except Exception:
            pass


def exceptions(page, trigger=None, timeout=15, caught=False, watch=None):
    cdp = _cdp(page)
    cdp.send("Debugger.setPauseOnExceptions", {"state": "all" if caught else "uncaught"})
    try:
        p = _wait_pause(page, cdp, trigger, float(timeout or 15))
        if not p:
            return f"No {'' if caught else 'uncaught '}exception within {timeout}s."
        return _paused_report(page, cdp, p, watch)
    finally:
        try:
            cdp.send("Debugger.setPauseOnExceptions", {"state": "none"})
        except Exception:
            pass
        try:
            cdp.send("Debugger.resume")
        except Exception:
            pass
        try:
            cdp.send("Debugger.setSkipAllPauses", {"skip": True})
        except Exception:
            pass
