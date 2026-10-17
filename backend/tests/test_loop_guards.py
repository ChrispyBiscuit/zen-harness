"""Loop guards from the "[battery] How did US GDP growth change from 2019 to 2024?" run (27B, Agent mode): 58 tool
calls in 10 minutes and no answer — 24 rephrased encyclopedia lookups that never found a year-by-year table, and 22
available_tools calls asking {"query": "memory"} / {"enable": ["memory"]}, each answered as if it were the first."""
import json

import offline_suite as suite


def engine(web=True, **cfg):
    e = suite.engine(**cfg)
    e.ctx.web_off = not web                # what prepare() sets from the composer's Web switch
    e.turn, e._turn_calls = {"steps": 1, "tool_calls": 0}, {}
    e.ran = []
    real = e.reg.call

    def fake_call(name, args):          # no network, no offline Wikipedia install needed
        if name not in ("encyclopedia", "web_search", "web_fetch"):
            return real(name, args)
        e.ran.append((name, args))
        return f"## Some article\n{name} result for {json.dumps(args)}", None

    e.reg.call = fake_call
    return e


def call(e, name, args, i=[0]):
    i[0] += 1
    tc = suite.tc(name, args, cid=f"g{i[0]}")
    e.history.append({"role": "assistant", "content": "", "tool_calls": [tc]})
    return e._run_tool(tc)["text"]


def ency_queries(n):
    return [{"query": f"United States GDP growth {2019 + k} table"} for k in range(n)]


def test_encyclopedia_streak_moves_to_web_search_when_web_is_on():
    e = engine()
    assert e._web_available()
    texts = [call(e, "encyclopedia", q) for q in ency_queries(4)]
    assert "web_search next" in texts[1]                     # the heads-up comes before the limit
    assert texts[3].startswith("encyclopedia did not run") and "call web_search now" in texts[3]
    assert sum(1 for n, _a in e.ran if n == "encyclopedia") == 3
    call(e, "web_search", {"query": "US real GDP growth 2019-2024"})
    assert not call(e, "encyclopedia", {"query": "Bureau of Economic Analysis"}).startswith("encyclopedia did not run")


def test_encyclopedia_streak_answers_with_what_it_has_when_web_is_off():
    e = engine(web=False)
    assert not e._web_available()
    texts = [call(e, "encyclopedia", q) for q in ency_queries(6)]
    assert not any(t.startswith("encyclopedia did not run") for t in texts[:5])
    assert texts[5].startswith("encyclopedia did not run") and "Web access is off" in texts[5]
    assert "web_search" not in texts[5].split("Web access is off")[0]
    assert not e._force_answer
    call(e, "encyclopedia", {"query": "Economy of the United States"})
    assert e._force_answer, "refused again: the run answers without tools"


def test_repeated_enable_is_already_enabled_and_a_loop():
    e = engine()                                             # stable tools: everything is loaded already
    first = call(e, "available_tools", {"enable": ["memory"]})
    assert "memory_search" in first and not first.startswith("Already")
    again = call(e, "available_tools", {"enable": ["memory"]})
    assert again.startswith("Already enabled") and "loop" in again and "memory_search" in again
    assert e.turn.get("ask_loops") == 1
    call(e, "available_tools", {"enable": ["Memory"]})      # same request, different spelling
    assert e.turn.get("ask_loops") == 2 and "Recovery" in e.history[-1]["content"]
    for _ in range(2):
        call(e, "available_tools", {"enable": ["memory"]})
    assert e._force_answer


def test_repeated_catalog_query_is_answered_with_the_tool_names():
    e = engine()
    call(e, "available_tools", {"query": "memory"})
    again = call(e, "available_tools", {"query": "memory"})
    assert again.startswith("Already answered") and "memory_search" in again
    assert e.turn.get("ask_loops") == 1


def test_whole_catalog_may_be_seen_twice():
    e = engine()
    assert not call(e, "available_tools", {}).startswith("Already")    # the plan phase's own look
    assert not call(e, "available_tools", {}).startswith("Already")    # its result was shortened since
    third = call(e, "available_tools", {})
    assert third.startswith("Already shown") and "[memory]" in third


def test_gated_enable_of_an_always_on_group_says_already_enabled():
    e = engine(tool_gate=True, stable_tools=False)
    if not e._gated() or e._stable_tools():
        return
    assert call(e, "available_tools", {"enable": ["memory"]}).startswith("Enabled")
    e.turn = {"steps": 1, "tool_calls": 0}                    # a new request: not a repeat, but nothing new to add
    text = call(e, "available_tools", {"enable": ["memory"]})
    assert text != "Nothing to enable." and text.startswith("Already enabled (call them directly")
    assert "memory = memory_search" in text and not e.turn.get("ask_loops")
    again = call(e, "available_tools", {"enable": ["memory"]})
    assert "loop" in again and e.turn.get("ask_loops") == 1
