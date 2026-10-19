"""Speech mode: a small model (qwen3 1.7B / 0.6B) fully on the GPU, tuned for fast, verified spoken answers.

Fixed pipeline per question (no open-ended agent loop):
  1. decide tools   — ≤150 tokens, JSON-constrained: a web search query and/or a calculator expression.
                      Search is the default whenever facts are involved.
  2. run tools      — search + calculator in parallel
  3. plan the reply — ≤150 tokens, grounded in the tool results
  4. answer         — short, spoken style, streamed (read aloud by the app)
The agent-mode models are unloaded while speech mode runs (and the speech model is unloaded when agent mode starts).
"""
import json
import re
import threading
import time
import urllib.request

import llama_local

SLOT = "speech"
CTX = 8192
DEFAULT_MODEL = "qwen3:1.7b"

DECIDE = """You choose tools before answering a spoken question. Today is {today}.
- search: a web search query. Use it to VERIFY the answer whenever it depends on facts — names, dates, numbers, \
places, prices, news, definitions, how-tos, anything that could be wrong or out of date. When in doubt, search.
- calculate: an expression for an exact calculator (arithmetic, percentages, unit conversions like "65 mph to m/s", \
dates like "days between 2026-01-01 and 2026-03-01"). Use it for ANY math, even simple.
Leave a field empty only for greetings, small talk, opinions, or when the conversation already contains the answer.
Reply with JSON only."""

PLAN = """Plan a short spoken answer to the user's last message. Use ONLY the tool results below for facts (not \
memory); if they don't answer the question, plan to say so briefly. Write up to 4 terse bullets: the direct answer \
first, then the key supporting fact(s) and which source they came from. No preamble."""

ANSWER = """You are a voice assistant. Speak naturally, like a person talking: 1–3 short sentences unless the user \
asked for more. Lead with the answer. No markdown, lists, headings, URLs or emojis. Use the calculator's numbers \
exactly. If the sources disagree or don't answer, say so in a few words. Follow the plan."""

SCHEMA = {"type": "object", "properties": {
    "needs_search": {"type": "boolean"}, "search": {"type": "string"},
    "needs_math": {"type": "boolean"}, "calculate": {"type": "string"}},
    "required": ["needs_search", "search", "needs_math", "calculate"], "additionalProperties": False}
SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|yo|thanks?|thank you|thx|good (morning|afternoon|evening|night)|how are you|"
                        r"what'?s up|bye|goodbye|see you|ok(ay)?|cool|nice|great|cheers)\b[\s\w,!?.']{0,30}$", re.I)


class SpeechError(Exception):
    pass


def ensure_model(model, status=lambda s: None):
    """Unload the agent / Ollama models and start the speech model on the GPU. -> base URL."""
    for slot in ("main", "exec"):
        if llama_local.running(slot):
            status("Unloading the agent model for speech mode…")
            llama_local.stop(slot)
    base = llama_local.ensure(model, 1, CTX, status, extra_args=["--jinja", "-ctk", "q8_0", "-ctv", "q8_0"], slot=SLOT)
    _warm_browser()
    return base


_warmed = {"done": False}


def _warm_browser():
    """Start the off-screen browser in the background so the first search isn't a cold start."""
    if _warmed["done"]:
        return
    _warmed["done"] = True

    def go():
        try:
            import browser_agent
            browser_agent.MANAGER.call(browser_agent.MANAGER._start, agent=False, timeout=60)
        except Exception:
            _warmed["done"] = False
    threading.Thread(target=go, daemon=True).start()


def unload():
    if llama_local.running(SLOT):
        llama_local.stop(SLOT)


def _post(base, body, timeout=120):
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _complete(base, messages, max_tokens, schema=None, temperature=0.3):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.9, "top_k": 20,
            "min_p": 0.0, "chat_template_kwargs": {"enable_thinking": False}}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "tools", "schema": schema}}
    with _post(base, body) as r:
        out = json.load(r)
    return out["choices"][0]["message"].get("content") or ""


def _stream(base, messages, max_tokens, on_text, should_stop, temperature=0.5):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.9, "top_k": 20,
            "min_p": 0.0, "stream": True, "chat_template_kwargs": {"enable_thinking": False}}
    text = ""
    with _post(base, body) as r:
        for raw in r:
            if should_stop():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                d = json.loads(data)["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError):
                continue
            if d.get("content"):
                text += d["content"]
                on_text(d["content"])
    return text


def _clean_expr(e):
    """'64.50 * 0.18 = 11.58' -> '64.50 * 0.18'; prose -> '' (the calculator only gets math)."""
    e = " ".join(str(e or "").split())
    if "=" in e and not re.search(r"[a-z]\s*=|solve|==", e):
        e = e.split("=")[0].strip()
    words = re.findall(r"[A-Za-z]{3,}", e)
    units = {"mph", "kmh", "km", "miles", "mile", "feet", "foot", "ft", "inches", "inch", "meters", "meter", "kg", "lbs",
             "pounds", "degf", "degc", "days", "between", "and", "to", "in", "of", "weeks", "hours", "minutes", "sqrt",
             "log", "sin", "cos", "tan", "percent", "liters", "gallons", "cups", "ounces", "grams", "seconds", "years"}
    if not re.search(r"\d", e) or sum(w.lower() not in units for w in words) > 2:
        return ""
    return e


def _search(query, budget=8.0):
    """Race the keyless search against the browser search; the first useful answer wins (≤ budget seconds)."""
    import queue as _q
    import tools
    out = _q.Queue()

    def keyless():
        try:
            rows = json.loads(tools.web_search(query, 5))
            out.put(rows if isinstance(rows, list) and rows else None)
        except Exception:
            out.put(None)

    def browser():
        try:
            import browser_agent
            _engine, rows = browser_agent.MANAGER.search(query, 5)
            out.put(rows or None)
        except Exception:
            out.put(None)
    threads = [threading.Thread(target=keyless, daemon=True), threading.Thread(target=browser, daemon=True)]
    for t in threads:
        t.start()
    end, pending = time.time() + budget, len(threads)
    while pending and time.time() < end:
        try:
            rows = out.get(timeout=max(0.05, end - time.time()))
        except _q.Empty:
            break
        pending -= 1
        if rows:
            rows = [r for r in rows if r.get("url")]
            return {"results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                                 "snippet": (r.get("snippet") or "")[:400]} for r in rows[:5]]}
    return {"error": "search did not answer in time"}


def _calc(expr):
    import calc
    try:
        r = calc.calculate(expr)
        return {"result": calc.render(r), "display": r}
    except Exception as e:
        return {"error": str(e)}


def run(text, history, emit_event, should_stop, model=DEFAULT_MODEL, base=None):
    """One spoken turn. emit(kind, **data) uses the agent event names (status, tool_call, tool_result, plan_*,
    ans_delta, turn_stats). emit_event(name, data_dict). base: an already-running llama.cpp server (the agent
    model's) to use instead of starting the small speech model. -> answer text."""
    def emit(_name, **d):
        emit_event(_name, d)
    t0 = time.time()
    base = base or ensure_model(model, lambda s: emit("status", text=s))
    convo = history[-8:]
    today = time.strftime("%A %d %B %Y")

    # 1) decide tools
    emit("status", text="Choosing tools…")
    if SMALL_TALK.match(text):
        choice = {"needs_search": False, "needs_math": False}   # greetings / thanks: answer straight away
    else:
        raw = _complete(base, [{"role": "system", "content": DECIDE.format(today=today)}] + convo +
                        [{"role": "user", "content": text}], 150, SCHEMA)
        try:
            choice = json.loads(raw)
        except ValueError:
            choice = {"needs_search": True, "search": text}     # unreadable decision: verify by searching the question
    q = (choice.get("search") or text).strip() if choice.get("needs_search") else ""
    expr = _clean_expr(choice.get("calculate") or "") if choice.get("needs_math") else ""
    t_decide = time.time() - t0

    # 2) run tools in parallel
    results = {}
    jobs = []
    if q:
        emit("tool_call", id="s_search", name="web_search", args={"query": q}, desc=f"web_search: {json.dumps({'query': q})}",
             kind="net", display=None)
        jobs.append(threading.Thread(target=lambda: results.__setitem__("search", _search(q))))
    if expr:
        emit("tool_call", id="s_calc", name="calculate", args={"expression": expr},
             desc=f"calculate: {json.dumps({'expression': expr})}", kind="read", display=None)
        jobs.append(threading.Thread(target=lambda: results.__setitem__("calc", _calc(expr))))
    if jobs:
        emit("status", text="Searching…" if q else "Calculating…")
    for j in jobs:
        j.start()
    for j in jobs:
        j.join(30)
    if q:
        s = results.get("search") or {"error": "no result"}
        emit("tool_result", id="s_search", ok="error" not in s, text=json.dumps(s, ensure_ascii=False)[:4000], display=None)
    if expr:
        c = results.get("calc") or {"error": "no result"}
        disp = {"type": "math", "expression": expr, **c["display"]} if c.get("display") else None
        emit("tool_result", id="s_calc", ok="error" not in c, text=c.get("result") or c.get("error", ""), display=disp)
        if "error" in c:
            expr = ""                                         # a failed calculation is not evidence: leave it out
    t_tools = time.time() - t0

    facts = []
    if q:
        s = results.get("search") or {}
        if s.get("results"):
            facts.append("Web search for " + json.dumps(q) + ":\n" + "\n".join(
                f"[{i + 1}] {r['title']} — {r['snippet']} ({r['url']})" for i, r in enumerate(s["results"])))
        else:
            facts.append(f"Web search for {json.dumps(q)} failed: {s.get('error', 'no results')}")
    if expr:
        c = results.get("calc") or {}
        facts.append(f"Calculator: {expr} = {c.get('result') or 'error: ' + c.get('error', '?')}")
    tool_block = "\n\n".join(facts) or "(no tools used)"
    unverified = bool(q) and not (results.get("search") or {}).get("results")
    if unverified:
        tool_block += ("\n\nIMPORTANT: the web search failed, so NOTHING could be verified. Start by saying you couldn't "
                       "check online, then give your best answer and call it unverified. Never state a fact as certain.")

    # 3) plan the reply
    emit("status", text="Planning the answer…")
    emit("plan_start", budget=150)
    plan = _stream(base, [{"role": "system", "content": PLAN}] + convo +
                   [{"role": "user", "content": f"{text}\n\nTool results:\n{tool_block}"}], 150,
                   lambda d: emit("plan_delta", text=d), should_stop, temperature=0.2)
    emit("plan_end", tokens=None, seconds=round(time.time() - t0 - t_tools, 1), text=plan)
    t_plan = time.time() - t0

    # 4) answer
    emit("status", text="Speaking…")
    first = {}

    rate = {"n": 0, "t0": None, "last": 0.0}

    def on_text(d):
        if "t" not in first:
            first["t"] = time.time() - t0
        emit("ans_delta", text=d)
        now = time.time()
        rate["t0"] = rate["t0"] or now
        rate["n"] += 1
        if now - rate["last"] > 1.0 and now - rate["t0"] > 0.5:      # live speed for the top bar
            rate["last"] = now
            emit("stats", tps=rate["n"] / (now - rate["t0"]))
    answer = _stream(base, [{"role": "system", "content": ANSWER}] + convo +
                     [{"role": "user", "content": f"{text}\n\nTool results:\n{tool_block}\n\nPlan:\n{plan}"}], 260,
                     on_text, should_stop)
    emit("turn_stats", steps=4, tool_calls=len(jobs), seconds=round(time.time() - t0, 1),
         speech={"decide": round(t_decide, 2), "tools": round(t_tools - t_decide, 2), "plan": round(t_plan - t_tools, 2),
                 "first_word": round(first.get("t", 0), 2), "model": model})
    return answer
