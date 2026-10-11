"""Fan-out gathering: the main model hands up to 4 independent research briefs to small "gopher" workers.

The workers are one llama-server with a slot per worker, running a small tool-calling model (MiniCPM5-2B) on the
CPU (16 threads; llama.cpp still borrows the GPU briefly to read prompts, ~0.25 GB). They run in parallel while the
main model stays loaded on the GPU. Each worker has a short prompt, only research tools and a step limit, and
returns its report plus the raw tool results: the main model judges the raw evidence, and the fact check only
counts the raw part (a worker's summary is never evidence).
"""
import copy
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import agent_tools as at

# read-only on purpose: a 2B model must not change files or run commands unsupervised, in parallel
WORKER_TOOLS = ("web_search", "web_fetch", "read_document", "read_file", "search_documents", "calculate",
                "list_dir", "find_files", "search_text")
MAX_STEPS = 4                 # default tool-call budget per worker when the main agent gives none
MIN_CALLS, MAX_CALLS = 3, 6   # the main agent picks each worker's budget within these bounds


def clamp_calls(n):
    try:
        return max(MIN_CALLS, min(MAX_CALLS, int(n)))
    except (TypeError, ValueError):
        return MAX_STEPS
RAW_MARK = "=== RAW RESULTS (what the workers actually read) ==="
# A worker's own context must stay small: on the CPU, generation slows as context grows (measured: 14.6 tok/s at 2k
# tokens, 8.1 at 4k; workers reached 9k and ~3 tok/s). The full results still go back to the main model.
RESULT_CHARS = 2500           # of each new tool result in the worker's view
KEEP_FULL = 2                 # the latest results stay as above; older ones shrink to OLD_CHARS
OLD_CHARS = 400

SYSTEM = """You are a research agent working for a lead agent, which will check your findings and write the final \
answer. Today is {today}.
You own the OBJECTIVE in the brief below — not a procedure. Starting points in the brief are only suggestions: if a \
source is blocked, needs a sign-in, or turns out to be about something else (another job, place, company or year), \
drop it and find another that serves the objective. Use the context to judge what is relevant. Stay inside your \
objective; other workers cover the rest. Tools:
- Online facts: start with web_search (give 2-3 phrasings in queries=[...]). Then web_fetch the most promising result \
URLs (urls=[...] reads several pages at once, question=... returns the relevant passages) to confirm facts on the page \
itself. Use only URLs that appear in results. Never guess a URL.
- Local files: list_dir / find_files to locate them, read_file / read_document to read them, search_text or \
search_documents to search inside them. Use the exact paths you are given or find.
- Copy facts exactly as the source states them: numbers, units, dates, names, and what they refer to (place, year, \
which group, base or total, average or median). If the source measures something different from what the brief \
asks, report what it really measures — never relabel a figure to fit the brief. Do not round, convert or combine.
- At most {steps} tool calls. Stop as soon as you have what the brief asks for.
- Make tool calls WITHOUT any commentary: no "Let me…", no summary of what you found, no plan — only the call. \
Every word costs time. Write text only once, in the final report.
- Older tool results are shortened to save space, so note the key facts in your mind as you read them.
When you are done, reply WITHOUT a tool call, with JSON only:
{{"facts": [{{"entity": "who/what", "metric": "what is measured", "value": "as stated, e.g. $150,513", "unit": "", \
"scope": "exactly what it covers (base or total, median or average, place, group)", "period": "date or period as \
stated", "source_url": "URL or file path", "quote": "the sentence from the source that states it, copied exactly"}}], \
"not_found": ["anything from the objective you could not establish, and what you tried"]}}"""


def _post(base, body, timeout=300):
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_worker(base, brief, ctx, should_stop=lambda: False, on_step=lambda *a: None, max_steps=MAX_STEPS,
               live=None, on_update=lambda: None):
    """One worker: a short tool loop. -> {report, calls: [(name, args, result)], steps, seconds, error}
    live: a dict kept current for the UI (status, action, steps, tokens, tps, report); on_update() after changes."""
    t0 = time.time()
    live = live if live is not None else {}
    live.update(status="running", action="reading the brief", steps=[], tokens=0, tps=0.0, started=t0, ended=None,
                report="", error="")
    on_update()
    wctx = copy.copy(ctx)                      # own display / call id; shares roots, conv folder, stop flag
    wctx.display = None
    reg = at.Registry(wctx, None, enabled=set(WORKER_TOOLS))
    tools = [s for s in reg.schemas(list(WORKER_TOOLS))]
    for s in tools:                            # workers do not keep a checklist: no progress field
        s["function"]["parameters"] = dict(s["function"]["parameters"])
        props = dict(s["function"]["parameters"].get("properties") or {})
        props.pop("progress", None)
        s["function"]["parameters"]["properties"] = props
    msgs = [{"role": "system", "content": SYSTEM.format(today=time.strftime("%A %d %B %Y"), steps=max_steps)},
            {"role": "user", "content": brief}]
    calls, seen, report, error, rows, nf = [], {}, "", "", [], []
    used = 0                                     # tool calls spent (the budget counts calls, not model turns)
    for turn in range(max_steps * 2 + 2):        # safety bound; the call budget ends the loop first
        if should_stop():
            error = "stopped"
            break
        last = used >= max_steps or turn >= max_steps * 2 + 1
        # no hidden thinking: on the CPU every token costs ~0.1 s, and the server flag alone does not switch it off
        body = {"messages": msgs, "max_tokens": 700, "temperature": 0.3,          # a JSON report with quotes is long
                "chat_template_kwargs": {"enable_thinking": False}}
        if not last:
            body["tools"] = tools
        else:
            msgs.append({"role": "user", "content": "Tool budget used up. Write your report now as the JSON (facts, "
                                                    "not_found), without a tool call."})
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "report", "schema": REPORT_SCHEMA}}
        live["action"] = "writing the report" if last else "deciding the next step"
        on_update()
        try:
            resp = _post(base, body)
            msg = resp["choices"][0]["message"]
            tm = resp.get("timings") or {}
            live["tokens"] += int(tm.get("predicted_n") or 0)
            if tm.get("predicted_per_second"):
                live["tps"] = round(float(tm["predicted_per_second"]), 1)
        except Exception as e:
            error = "stopped" if should_stop() else f"{type(e).__name__}: {e}"[:200]
            break
        tcs = msg.get("tool_calls") or []
        if not tcs:
            report = (msg.get("content") or "").strip()
            rows, nf = parse_report(report)
            break
        msgs.append({"role": "assistant", "content": "", "tool_calls": tcs})    # commentary dropped from context
        _shrink_old(msgs)
        room = max(1, min(3, max_steps - used))
        for tc in tcs[:room]:
            used += 1
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            args.pop("progress", None)
            for k in ("path", "file", "folder", "dir"):     # small models copy list_dir's "f " / "d " marker
                v = args.get(k)
                if isinstance(v, str) and re.match(r"^[fd] \S", v):
                    args[k] = v[2:]
            key = json.dumps([name, args], sort_keys=True)
            if reg.canonical(name) not in WORKER_TOOLS:
                out = f"Error: '{name}' is not available here. Use: {', '.join(WORKER_TOOLS)}."
            elif key in seen:
                out = "Already done — the result is above. Use a different query or URL, or write your report."
            else:
                on_step(name, args)
                short = json.dumps(args, ensure_ascii=False)[:160]
                live["action"] = f"{name} {short}"
                rec = {"name": name, "args": short, "secs": None, "preview": ""}
                live["steps"].append(rec)
                on_update()
                ts = time.time()
                try:
                    out, _img = reg.call(name, args)
                except at.ToolError as e:
                    out = f"Error: {e}"
                except Exception as e:
                    out = f"Error: {type(e).__name__}: {e}"
                seen[key] = True
                if reg.canonical(name) == "list_dir":
                    out = _full_paths(out)
                body = re.sub(r"^\[Full result \([\d,]+ characters\) saved at [^\]]*\]\s*", "", str(out))   # content, not the save note
                rec.update(secs=round(time.time() - ts, 1), preview=_gist(body, 400) if len(body) > 400 else " ".join(body.split()),
                           ok=not str(out).startswith("Error"))
                on_update()
                calls.append((reg.canonical(name), args, _full(out)))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id") or name, "name": name,
                         "content": at.clip(out, RESULT_CHARS)})
        for extra in tcs[room:]:              # answer every call id, or the template breaks
            msgs.append({"role": "tool", "tool_call_id": extra.get("id") or "x", "name": extra["function"]["name"],
                         "content": "Skipped: over your call budget for this step."})
    live.update(status="error" if error and error != "stopped" else ("stopped" if error else "done"),
                action="", report=(_evidence.markdown(rows, nf) if rows else report) or "(no report)", error=error,
                ended=time.time())
    on_update()
    return {"report": report or "(no report)", "rows": rows, "not_found": nf, "calls": calls, "steps": len(calls),
            "seconds": round(time.time() - t0, 1), "error": error}


import evidence as _evidence
REPORT_SCHEMA = {"type": "object", "properties": {
    "facts": {"type": "array", "items": _evidence.ROW_SCHEMA},
    "not_found": {"type": "array", "items": {"type": "string"}}}, "required": ["facts", "not_found"]}


def parse_report(text):
    """The worker's JSON report -> (rows, not_found). Prose (a model that ignored the format) -> ([], [])."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return [], []
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return [], []
    rows = [r for r in (d.get("facts") or []) if isinstance(r, dict)]
    return rows, [str(x) for x in (d.get("not_found") or [])]


def _shrink_old(msgs):
    """Keep the latest KEEP_FULL tool results as they are; cut older ones to OLD_CHARS (the worker already used them,
    and re-reading them on every step is what slows a CPU model down)."""
    tools = [m for m in msgs if m.get("role") == "tool"]
    for m in tools[:-KEEP_FULL] if len(tools) > KEEP_FULL else []:
        c = m.get("content") or ""
        if len(c) > OLD_CHARS + 60 and "[older result shortened:" not in c[-80:]:
            m["content"] = _gist(c) + f"\n…[older result shortened from {len(c)} characters: its fact lines are kept above]"


_FACT_RX = re.compile(r"\d|https?://|\$|€|£|%")


def _gist(text, limit=OLD_CHARS * 2):
    """The lines of an old result that carry facts (numbers, money, dates, URLs), up to `limit` characters — so a
    worker still has the figures it read earlier when it writes its report, without re-reading whole pages."""
    keep, n = [], 0
    for ln in text.split("\n"):
        s = " ".join(ln.split())
        if not s or not _FACT_RX.search(s):
            continue
        s = s[:240]
        if n + len(s) > limit:
            break
        keep.append(s)
        n += len(s) + 1
    return "\n".join(keep) or text[:OLD_CHARS]


def _full_paths(out):
    """list_dir shows 'f name' / 'd name' under the folder; small models glue those markers into paths
    ('C:\\x/f a.txt'). Give them complete paths to copy instead."""
    lines = out.split("\n")
    if not lines or not re.match(r"^[A-Za-z]:[\\/]", lines[0].strip()):
        return out
    folder = lines[0].strip().rstrip("\\/")
    res = [lines[0]]
    for ln in lines[1:]:
        m = re.match(r"^(\s*)([fd]) (.+?)(\s{2,}\(.*\))?\s*$", ln)
        if m:
            kind = "folder" if m.group(2) == "d" else "file"
            res.append(f"{m.group(1)}{folder}\\{m.group(3).strip()}  ({kind}{', ' + m.group(4).strip()[1:-1] if m.group(4) else ''})")
        else:
            res.append(ln)
    return "\n".join(res)


def _full(out):
    """A saved long result starts with '[Full result … saved at <path> …]': the raw evidence is the whole file."""
    m = re.match(r"\[Full result \([\d,]+ characters\) saved at (.+?) — ", out)
    if m:
        try:
            with open(m.group(1), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            pass
    return out


def gather(base, briefs, ctx, should_stop=lambda: False, status=lambda s: None, max_steps=MAX_STEPS, on_update=None,
           control=None):
    """Run up to len(briefs) workers in parallel. -> the text the main model gets (reports first, raw after).
    on_update(states): called with every worker's live state whenever one changes (for the UI tiles).
    control: {"stop": set of worker indexes} — the lead agent took those objectives over; they end at their next
    step and report what they had found so far."""
    control = control if control is not None else {"stop": set()}
    t0 = time.time()
    done, lock = [0], threading.Lock()
    states = [{"i": i, "title": b["title"], "brief": b["brief"], "status": "queued"} for i, b in enumerate(briefs)]
    last_push = [0.0]

    def push(force=False):
        if on_update is None:
            return
        now = time.time()
        if not force and now - last_push[0] < 0.4:        # at most a few updates a second
            return
        last_push[0] = now
        try:
            on_update([dict(s, steps=list(s.get("steps") or [])) for s in states])
        except Exception:
            pass

    def one(i_b):
        i, b = i_b
        budget = clamp_calls(b.get("max_calls") or max_steps)       # the main agent's per-objective call budget
        states[i]["max_calls"] = budget
        mine_stop = lambda: should_stop() or i in control["stop"]
        r = run_worker(base, b["brief"], ctx, mine_stop,
                       on_step=lambda n, a: status(f"Worker {i + 1} ({b['title'][:40]}): {n}"), max_steps=budget,
                       live=states[i], on_update=lambda: push(True))    # a few events per step: send them all
        if i in control["stop"] and not should_stop():       # taken over by the lead agent
            r["error"] = "taken over by the lead agent"
            r["report"] = ("(Stopped: the lead agent took over this objective. Partial findings are in the raw "
                           "results below.)")
            states[i].update(status="taken over", report=r["report"], error=r["error"])
            push(True)
        with lock:
            done[0] += 1
            status(f"Workers: {done[0]}/{len(briefs)} done")
        return r

    push(True)
    with ThreadPoolExecutor(max_workers=len(briefs)) as ex:       # all at once (the engine never sends more than the slots)
        results = list(ex.map(one, enumerate(briefs)))
    push(True)
    secs = time.time() - t0
    head = [f"{len(briefs)} workers finished in {secs:.0f}s. Their reports are below, then the raw results they read. "
            "A report is only a worker's summary — check figures against the raw results (or read the saved file) "
            "before you rely on them. If what came back is not enough, call gather again with sharper objectives "
            "(what is still missing, sources to try or avoid) and a fitting max_calls, and keep searching yourself "
            "meanwhile."]
    table = None
    for i, (b, r) in enumerate(zip(briefs, results), 1):
        rows = r.get("rows") or []
        if rows:                    # facts as rows: checked against this worker's own raw results, into the table
            import factcheck
            docs = factcheck.build_evidence([{"role": "tool", "name": n, "content": out} for n, _a, out in r["calls"]])[0]
            for row, v in zip(rows, _evidence.check(rows, docs)):
                row["verified"] = v
            table, new = _evidence.append(ctx, rows, by=f"worker {i}")
            dups = [x for x in new if x.get("duplicate_of")]
            body = "Rows added to the evidence table:\n" + _evidence.summary([x for x in new if not x.get("duplicate_of")])
            if dups:
                body += "\nAlready in the table (not added again): " + ", ".join(x["duplicate_of"] for x in dups)
            if r.get("not_found"):
                body += "\nNot found: " + "; ".join(r["not_found"])[:600]
        else:
            body = "Report:\n" + r["report"]
        head.append(f"\n## Worker {i}: {b['title']}\nBrief: {b['brief'][:300]}\n"
                    f"({r['steps']} of {clamp_calls(b.get('max_calls') or max_steps)} tool calls, {r['seconds']}s"
                    f"{', ' + r['error'] if r['error'] else ''})\n{body}")
    if table:
        head.insert(1, f"\nEvidence table (CSV): {table} — compute from it with query_data, files={{\"evidence\": "
                       f"\"{table}\"}}, using verified rows and keeping scopes apart.")
    raw = [RAW_MARK]
    for i, r in enumerate(results, 1):
        for name, args, out in r["calls"]:
            raw.append(f"\n--- worker {i}: {name} {json.dumps(args, ensure_ascii=False)[:300]}\n{out}")
    return "\n".join(head) + "\n\n" + "\n".join(raw), results
