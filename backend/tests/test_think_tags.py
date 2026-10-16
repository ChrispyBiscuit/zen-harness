"""Think tags inside a streamed reply (agentic.AgentEngine._split_think_tags): models whose chat template opens <think>
for them (Ternary Bonsai 27B…) stream thinking and answer as one text, split by a bare </think>."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agentic  # noqa: E402


def run(chunks, prefill=False, delta="ans_delta", held=None):
    """Feed text chunks through the splitter like _call_openai does; return (thinking, answer, events)."""
    events = []
    fake = types.SimpleNamespace(_think_prefill=prefill, ctx=types.SimpleNamespace(conv_id="t"),
                                 emit=lambda kind, **kw: events.append((kind, kw.get("text", ""))))
    split = agentic.AgentEngine._split_think_tags.__get__(fake)
    tag = {"in": prefill, "pend": "", "routed": "", "shown": ""}
    thinking, answer = "", ""
    for c in chunks:
        before = len(events)
        r, a = split({"content": c}, tag, delta, held)
        for kind, text in events[before:]:          # what the server does with think_retro: shown answer -> thinking
            if kind == "think_retro" and answer.endswith(text):
                answer, thinking = answer[:-len(text)], thinking + text
        if tag.pop("_held_reset", False):
            answer = ""
        thinking += r
        answer += a
        tag["shown"] += a
    if tag["pend"]:
        (thinking if tag["in"] else answer)  # noqa: B018  (the caller flushes it; mirror that here)
        if tag["in"]:
            thinking += tag["pend"]
        else:
            answer += tag["pend"]
    return thinking, answer, events, fake


def test_orphan_end_tag_split_across_chunks_moves_the_shown_text_to_thinking():
    thinking, answer, events, fake = run(["Let me think about bands.\n", "Little Richard...\n</th", "ink>\n\n**Top 5**"])
    assert answer == "**Top 5**"
    assert "Let me think about bands." in thinking and "Little Richard" in thinking and "</think>" not in thinking
    assert ("think_retro", "Let me think about bands.\nLittle Richard...\n") in events
    assert fake._think_prefill is True          # the next reply from this model starts as thinking


def test_prefilled_model_routes_to_thinking_until_the_tag():
    thinking, answer, events, _ = run(["Checking the files", " first.\n</think>\n", "Done: 3 files."], prefill=True)
    assert thinking == "Checking the files first.\n"
    assert answer == "Done: 3 files."
    assert not any(k == "think_retro" for k, _ in events)


def test_explicit_think_block():
    thinking, answer, _, _ = run(["<think>plan", " it</think>", "Answer."])
    assert thinking == "plan it" and answer == "Answer."


def test_plain_reply_is_untouched_and_angle_brackets_survive():
    thinking, answer, _, _ = run(["Use a <", "div> and x < 3 ", "</div>"])
    assert thinking == "" and answer == "Use a <div> and x < 3 </div>"


def test_reasoning_channel_carrying_the_end_tag():
    events = []
    fake = types.SimpleNamespace(_think_prefill=False, ctx=types.SimpleNamespace(conv_id="t"),
                                 emit=lambda kind, **kw: events.append(kind))
    split = agentic.AgentEngine._split_think_tags.__get__(fake)
    tag = {"in": False, "pend": "", "routed": "", "shown": ""}
    r, a = split({"reasoning_content": "weighing options</think>\n\nThe answer"}, tag, "ans_delta", None)
    assert r == "weighing options" and a == "The answer"
