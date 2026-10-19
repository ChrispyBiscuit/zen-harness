"""
Local web-search agent: your Ollama model + our own web tools.

The model runs locally via Ollama (http://localhost:11434). It decides when to
call web_search / web_fetch; we execute those on your machine and feed the
results back, looping until it produces an answer. No API keys.

Usage:
    python agent.py                      # interactive
    python agent.py "your question"      # one-shot
"""
import re
import sys
import json
import time
import datetime
import ollama

import tools

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _strip_leaked_think(text):
    """When thinking is disabled, this qwen3 build can still emit a reasoning block
    into content (often an orphan </think> with no opening tag). Drop everything up
    to and including the last </think>, plus any well-formed <think>..</think>."""
    low = text.lower()
    if "</think>" in low:
        text = text[low.rfind("</think>") + len("</think>"):]
    return _THINK_BLOCK.sub("", text).strip()

MODEL = "qwen3:30b-a3b"     # swap to "qwen3:14b" for a faster search agent
NUM_CTX = 16384             # search results are large; give it room
MAX_STEPS = 6              # safety cap on tool-call rounds

SYSTEM = (
    "You are a helpful assistant running locally, with an OPTIONAL web-search "
    "capability. Today's date is {today}.\n"
    "You have three tools: web_search (find pages), web_fetch (read a page) and calculate (exact maths). "
    "YOU decide whether a question needs them — most do not.\n"
    "- Answer DIRECTLY from your own knowledge, with no search, for general facts, "
    "explanations, definitions, reasoning, math, coding, writing, and casual "
    "conversation. Do not search when you already know the answer.\n"
    "- Use web_search/web_fetch ONLY when the question needs information that is "
    "recent, fast-changing, or beyond your training (current events, news, prices, "
    "latest software versions, live status), or when you must verify a specific "
    "uncertain fact.\n"
    "When you DO search: prefer primary/official sources, trust the fetched results "
    "over your own memory (your training may be out of date — never dismiss a recent "
    "date as an 'error'), and end your answer with a 'Sources:' list of the URLs you "
    "used. When you do NOT search, just answer normally — no Sources list. Be concise."
)

# Plain chat only has the two web tools. Without this note models say "I'm a text-only AI and can't
# access your computer", which is wrong for this app: Agent mode has PowerShell, files and screen.
CHAT_MODE_NOTE = (
    "\n\nYou are in this app's Chat mode, where your only tools are web_search, web_fetch and calculate. The app "
    "also has an Agent mode (the 'Agent' checkbox at the top of the window) in which you DO get "
    "PowerShell, file reading/creating/editing, screen control and MCP tools on the user's PC. If the "
    "user asks you to run a command, use PowerShell, check processes, or read or change files on "
    "their computer, do not claim you are unable to: tell them to tick the 'Agent' checkbox and ask "
    "again."
)

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for a query. Returns a JSON list of "
                       "{title, url, snippet}.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "the search query"},
            "max_results": {"type": "integer",
                            "description": "how many results (1-10)"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch a web page URL and return its main readable text.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the URL to fetch"},
        }, "required": ["url"]}}},
]

def _calculate(expression="", **_):
    """The agent's exact calculator (arithmetic, percentages, units, dates, equations) for chat mode."""
    import calc
    try:
        return calc.render(calc.calculate(str(expression).strip(), 15))
    except calc.CalcError as e:
        return f"Error: {e}. Examples: '2^64', 'x^2-5x+6=0', '65 mph to m/s', 'days between 2026-03-03 and 2026-06-09'"


TOOL_SCHEMAS.append({"type": "function", "function": {
    "name": "calculate",
    "description": "Exact calculator: arithmetic, percentages, compound interest, unit conversions ('65 mph to m/s'), "
                   "day counts ('days between 2026-03-03 and 2026-06-09'), equations. Use it for any number you would "
                   "otherwise work out in your head.",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}})

DISPATCH = {"web_search": tools.web_search, "web_fetch": tools.web_fetch, "calculate": _calculate}


def run_agent(question: str, model: str = MODEL, verbose: bool = True) -> str:
    client = ollama.Client()
    today = datetime.date.today().strftime("%B %d, %Y")
    messages = [{"role": "system", "content": SYSTEM.format(today=today)},
                {"role": "user", "content": question}]

    for step in range(MAX_STEPS):
        resp = client.chat(model=model, messages=messages,
                           tools=TOOL_SCHEMAS, options={"num_ctx": NUM_CTX})
        message = resp.message
        # record the assistant turn as-is (ollama re-serializes it correctly)
        messages.append(message)

        calls = message.tool_calls or []
        if not calls:
            return (message.content or "").strip()

        for call in calls:
            fn = call.function.name
            args = call.function.arguments or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if verbose:
                print(f"  → {fn}({dict(args)})", file=sys.stderr, flush=True)
            func = DISPATCH.get(fn)
            result = func(**args) if func else f"(unknown tool {fn})"
            messages.append({"role": "tool", "tool_name": fn,
                             "content": str(result)[:8000]})

    return "(stopped: reached the tool-call step limit without a final answer)"


def preload_model(model: str = MODEL, emit=None, num_ctx: int = NUM_CTX):
    """Load the model into memory now (emits 'loading'/'loaded'/'error').
    No-op if it's already resident. Safe to call at app startup."""
    def say(kind, data=None):
        if emit:
            emit(kind, data)
    try:
        client = ollama.Client()
        backend = model
        try:
            if any(m.model == backend for m in client.ps().models):
                return  # already warm
        except Exception:
            pass
        say("loading", backend)
        t0 = time.time()
        client.generate(model=backend, prompt="",
                        options={"num_ctx": num_ctx}, keep_alive="30m")
        say("loaded", round(time.time() - t0, 1))
    except Exception as e:
        say("error", f"could not load model: {e}")


CHAT_SUMMARY_PROMPT = (
    "Summarize this conversation so it can continue without the original messages. Keep the user's "
    "questions, requests, preferences and facts about them, the key answers given, and any open "
    "threads. Be specific; at most 350 words. Do not add anything that was not said.")


def compress_history(history, model, num_ctx, summary="", force=False, base=None):
    """Automatic chat compression. If the history would use more than ~60% of the context window,
    summarise all but the most recent part. -> (new_summary, n_old) where history[:n_old] is now
    covered by new_summary, or (None, 0) when nothing needed compressing."""
    def size(msgs):
        return sum(len(m.get("content") or "") for m in msgs) // 3 + 8 * len(msgs)
    budget = int(num_ctx * 0.6)
    if (not force and size(history) <= budget) or len(history) < 4:
        return None, 0
    keep, acc = len(history), 0
    for i in range(len(history) - 1, 0, -1):            # keep the newest ~25% of the window verbatim
        acc += size([history[i]])
        if history[i].get("role") == "user":
            keep = i
            if acc > num_ctx * 0.25:
                break
    if keep < 2:
        return None, 0
    lines = ([f"EARLIER SUMMARY: {summary}"] if summary else []) + \
        [f"[{m['role']}] {(m.get('content') or '')[:3000]}" for m in history[:keep]]
    text = "\n".join(lines)
    if len(text) > num_ctx * 2:
        text = text[:num_ctx // 2] + "\n…\n" + text[-num_ctx * 3 // 2:]
    msgs = [{"role": "user", "content": f"{CHAT_SUMMARY_PROMPT}\n\n{text}"}]
    if base:
        body = {"messages": msgs, "max_tokens": 900, "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": False}}
        with _llama_post(base, body) as r:
            out = _strip_leaked_think(json.load(r)["choices"][0]["message"].get("content") or "")
        return (out, keep) if out else (None, 0)
    r = ollama.Client().chat(model=model, stream=False, think=False, messages=msgs,
                             options={"num_ctx": num_ctx, "temperature": 0.2, "num_predict": 900})
    out = _strip_leaked_think(r.message.content or "")
    return (out, keep) if out else (None, 0)


def _llama_post(base, body, timeout=600):
    import urllib.request
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _llama_turn(base, messages, think, temperature, say, stopped):
    """One streamed turn on a llama.cpp server (OpenAI-compatible) with the chat tools.
    -> (content, [{"id", "name", "arguments"}], had_thinking, was_stopped)"""
    body = {"messages": messages, "tools": TOOL_SCHEMAS, "temperature": temperature, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": bool(think)}}
    content, calls, had_thinking, n_tok, t_first, t_emit, usage = [], {}, False, 0, None, 0.0, None
    with _llama_post(base, body) as r:
        for raw in r:
            if stopped():
                return "".join(content), [], had_thinking, True
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            usage = chunk.get("usage") or usage
            if not chunk.get("choices"):
                continue
            d = chunk["choices"][0].get("delta") or {}
            if d.get("content") or d.get("reasoning_content"):
                n_tok += 1
                now = time.time()
                t_first = t_first or now
                if now - t_emit >= 0.4:          # live tokens/s for the app's meter
                    t_emit = now
                    say("tps_live", round(n_tok / max(0.25, now - t_first), 1))
            if d.get("reasoning_content"):
                had_thinking = True
                say("think_delta", d["reasoning_content"])
            if d.get("content"):
                content.append(d["content"])
                say("ans_delta", d["content"])
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["name"] += fn.get("name") or ""
                c["arguments"] += fn.get("arguments") or ""
    if usage:
        gen = int(usage.get("completion_tokens") or n_tok)
        say("stats", {"model": "llama.cpp", "gen_tokens": gen, "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                      "gen_tps": round(gen / max(0.25, time.time() - (t_first or time.time())), 1), "prompt_tps": 0.0})
    return "".join(content), [calls[i] for i in sorted(calls)], had_thinking, False


def _run_llama_chat(base, messages, think, temperature, max_steps, say, stopped):
    """run_agent_events' chat loop on the agent model's llama.cpp server."""
    for _ in range(max_steps):
        if stopped():
            say("answer_end")
            return
        say("status", "Thinking…" if think else "Working…")
        content, calls, had_thinking, was_stopped = _llama_turn(base, messages, think, temperature, say, stopped)
        if had_thinking:
            say("think_end")
        if was_stopped or not calls:
            say("answer_end")
            return
        say("tool_turn")
        messages.append({"role": "assistant", "content": content, "tool_calls": [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            if c["name"] == "web_search":
                say("status", f"🔍 Searching: {args.get('query', '')}")
            elif c["name"] == "web_fetch":
                say("status", f"📄 Reading: {args.get('url', '')}")
            func = DISPATCH.get(c["name"])
            try:
                result = func(**args) if func else f"(unknown tool {c['name']})"
            except Exception as e:
                result = f"(tool error: {e})"
            messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": str(result)[:8000]})
    say("ans_delta", "(Reached the tool-call limit without a final answer.)")
    say("answer_end")


def run_agent_events(question: str, model: str = MODEL, emit=None,
                     num_ctx: int = NUM_CTX, think: bool = False,
                     temperature: float = 0.7, system_prompt: str = None,
                     max_steps: int = MAX_STEPS, history: list = None,
                     should_stop=None, base=None):
    """
    Same agent loop, but reports progress through emit(kind, data):
      emit('status',   'human-readable status line')
      emit('thinking', 'model chain-of-thought')   # only when think=True
      emit('answer',   'final answer text')         # terminal
      emit('error',    'error text')                # terminal
    Designed to be driven from a GUI worker thread.
    base: the agent model's llama.cpp server; given, the chat runs there instead of on Ollama.
    """
    def say(kind, data=None):
        if emit:
            emit(kind, data)

    try:
        if base:
            today = datetime.date.today().strftime("%B %d, %Y")
            import answer_shapes
            sys_text = (system_prompt if system_prompt is not None else SYSTEM).replace("{today}", today) \
                + CHAT_MODE_NOTE + "\n" + answer_shapes.TEXT
            messages = [{"role": "system", "content": sys_text}]
            messages += [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")]
            messages.append({"role": "user", "content": question})
            _run_llama_chat(base, messages, think, temperature, max_steps, say,
                            lambda: bool(should_stop and should_stop()))
            return
        client = ollama.Client()

        backend = model

        # --- ensure the model is resident in memory, with a visible load phase ---
        already_loaded = False
        try:
            already_loaded = any(m.model == backend for m in client.ps().models)
        except Exception:
            already_loaded = False
        if not already_loaded:
            say("loading", backend)
            t0 = time.time()
            try:
                # empty prompt => Ollama just loads the model into memory
                client.generate(model=backend, prompt="",
                                options={"num_ctx": num_ctx}, keep_alive="30m")
            except Exception as e:
                say("error", f"could not load model: {e}")
                return
            say("loaded", round(time.time() - t0, 1))

        today = datetime.date.today().strftime("%B %d, %Y")
        sys_text = (system_prompt if system_prompt is not None else SYSTEM)
        import answer_shapes
        sys_text = sys_text.replace("{today}", today) + CHAT_MODE_NOTE + "\n" + answer_shapes.TEXT
        tools_arg = TOOL_SCHEMAS
        messages = [{"role": "system", "content": sys_text}]
        for turn in (history or []):
            if turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": question})

        def stopped():
            return bool(should_stop and should_stop())

        for _ in range(max_steps):
            if stopped():
                say("answer_end")
                return
            say("status", "Thinking…" if think else "Working…")
            content_parts, tool_calls = [], []
            had_thinking = False

            # The GUI checkbox genuinely enables/disables thinking. When ON, think=True
            # cleanly separates reasoning (streamed as think_delta) from the answer. When
            # OFF, we both pass think=False AND append qwen3's /no_think soft-switch to the
            # current user turn, which actually suppresses reasoning at the source (ollama's
            # think=False alone can still leak a <think> block into content on this build).
            call_messages = messages
            if not think and messages and messages[-1].get("role") == "user":
                last = messages[-1]
                call_messages = messages[:-1] + [
                    {**last, "content": (last.get("content") or "") + " /no_think"}]
            stream = client.chat(model=backend, messages=call_messages,
                                 tools=tools_arg,
                                 options={"num_ctx": num_ctx,
                                          "temperature": temperature},
                                 think=think, stream=True)
            was_stopped = False
            n_tok, t_first, t_emit, last_chunk = 0, None, 0.0, None
            for chunk in stream:
                last_chunk = chunk
                if getattr(chunk.message, "content", None) or getattr(chunk.message, "thinking", None):
                    n_tok += 1
                    _now = time.time()
                    if t_first is None:
                        t_first = _now
                    if _now - t_emit >= 0.4:          # live tokens/s for the app's meter
                        t_emit = _now
                        say("tps_live", round(n_tok / max(0.25, _now - t_first), 1))
                if stopped():                 # user pressed Stop mid-stream
                    was_stopped = True
                    try:
                        stream.close()
                    except Exception:
                        pass
                    break
                m = chunk.message
                th = getattr(m, "thinking", None)
                if th:
                    had_thinking = True
                    say("think_delta", th)
                if m.content:
                    content_parts.append(m.content)
                    # When thinking is off we can't stream live: leaked reasoning has no
                    # opening <think> tag, so we can't tell it from the answer mid-stream.
                    # Buffer now, strip, and emit the clean answer once the turn completes.
                    if think:
                        say("ans_delta", m.content)
                if m.tool_calls:
                    tool_calls.extend(m.tool_calls)

            _ec = getattr(last_chunk, "eval_count", None) or n_tok
            _ed = getattr(last_chunk, "eval_duration", None)
            _pc = getattr(last_chunk, "prompt_eval_count", None)
            _pd = getattr(last_chunk, "prompt_eval_duration", None)
            if _ec:
                say("stats", {"model": backend, "gen_tokens": _ec,
                              "gen_tps": round(_ec / (_ed / 1e9), 1) if _ed else
                              round(n_tok / max(0.25, time.time() - (t_first or time.time())), 1),
                              "prompt_tokens": _pc or 0,
                              "prompt_tps": round(_pc / (_pd / 1e9), 1) if _pc and _pd else 0.0})
            content = "".join(content_parts)
            if not think:
                content = _strip_leaked_think(content)
                if content:
                    say("ans_delta", content)
            messages.append({"role": "assistant", "content": content,
                             "tool_calls": tool_calls})
            if had_thinking:
                say("think_end")

            if was_stopped:
                say("answer_end")
                return
            if not tool_calls:
                say("answer_end")
                return

            # this turn was a tool call, not the final answer
            say("tool_turn")
            for call in tool_calls:
                fn = call.function.name
                args = call.function.arguments or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if fn == "web_search":
                    say("status", f"🔍 Searching: {args.get('query', '')}")
                elif fn == "web_fetch":
                    say("status", f"📄 Reading: {args.get('url', '')}")
                func = DISPATCH.get(fn)
                try:
                    result = func(**args) if func else f"(unknown tool {fn})"
                except Exception as e:
                    result = f"(tool error: {e})"
                messages.append({"role": "tool", "tool_name": fn,
                                 "content": str(result)[:8000]})

        say("ans_delta", "(Reached the tool-call limit without a final answer.)")
        say("answer_end")
    except Exception as e:
        say("error", f"{type(e).__name__}: {e}")


def main():
    if len(sys.argv) > 1:
        print(run_agent(" ".join(sys.argv[1:])))
        return
    print(f"Local web-search agent ({MODEL}). Ctrl+C to quit.\n")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        print("\n" + run_agent(q) + "\n")


if __name__ == "__main__":
    main()
