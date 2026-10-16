"""Answer blocks (plans/answer-blocks.md): the shapes reach every prompt that writes answers, redrafts keep them,
chat mode can calculate, the geocoder caches and rate-limits without the network, and pictures reach the model."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent  # noqa: E402
import agent_tools  # noqa: E402
import agentic  # noqa: E402
import answer_shapes  # noqa: E402
import geocode  # noqa: E402


def test_every_agent_prompt_has_one_shapes_slot_and_formats():
    for p in (agentic.SYSTEM, agentic.LEAN_SYSTEM, agentic.BASIC_SYSTEM):
        assert p.count("{shapes}") == 1
    out = agentic.LEAN_SYSTEM.format(user="u", today="t", extra="", shapes=answer_shapes.TEXT)
    assert "```chart" in out and "```map" in out and '"series"' in out   # JSON braces survive as a value


def test_shapes_examples_parse_as_json():
    import re
    bodies = re.findall(r"```(chart|map|facts|timeline|cards)\n(.+?)\n```", answer_shapes.TEXT, re.S)
    assert {b[0] for b in bodies} == {"chart", "map", "facts", "timeline", "cards"}
    for _lang, body in bodies:
        json.loads(body)


def test_keep_rule_is_in_the_redraft_passes():
    src = open(agentic.__file__, encoding="utf-8").read()
    assert src.count("answer_shapes.KEEP") == 3


def test_chat_mode_has_calculate():
    names = [t["function"]["name"] for t in agent.TOOL_SCHEMAS]
    assert "calculate" in names and "calculate" in agent.DISPATCH
    assert agent.DISPATCH["calculate"](expression="2*(3+4)").strip() == "14"
    assert agent.DISPATCH["calculate"](expression="2*((").startswith("Error")
    assert "calculate" in agent.CHAT_MODE_NOTE


def test_geocode_caches_and_spaces_requests():
    calls = []

    def fake(name):
        calls.append((name, time.monotonic()))
        return None if name == "Nowhere" else {"lat": 41.9, "lon": -87.6, "label": name}

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "geo.json")
        geocode._cache = None
        geocode._last[0] = 0.0
        out = geocode.lookup(["Pequod's Pizza, Chicago", "Nowhere"], path, fetch=fake)
        assert out["Pequod's Pizza, Chicago"]["lat"] == 41.9 and out["Nowhere"] is None
        assert len(calls) == 2 and calls[1][1] - calls[0][1] >= 1.0          # Nominatim: one request a second
        geocode._cache = None                                                  # read back from disk
        again = geocode.lookup(["pequod's pizza,  chicago", "Nowhere"], path, fetch=fake)
        assert len(calls) == 2, "cached (misses too, for a day)"
        assert again["pequod's pizza,  chicago"]["lon"] == -87.6 and again["Nowhere"] is None


def test_geocode_network_error_is_not_cached():
    def boom(_name):
        raise OSError("offline")

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "geo.json")
        geocode._cache = None
        geocode._last[0] = 0.0
        assert geocode.lookup(["Somewhere"], path, fetch=boom) == {"Somewhere": None}
        got = []
        geocode.lookup(["Somewhere"], path, fetch=lambda n: got.append(n) or {"lat": 1.0, "lon": 2.0})
        assert got == ["Somewhere"]


def test_geocode_refused_when_web_is_off():
    import asyncio
    import web_backend

    class Req:
        async def json(self):
            return {"names": ["Paris"]}

    old = web_backend.STORE.settings.get("agent", {}).get("web", True)
    web_backend.STORE.settings.setdefault("agent", {})["web"] = False
    try:
        r = asyncio.run(web_backend.api_geocode(Req()))
        assert r.status == 403
    finally:
        web_backend.STORE.settings["agent"]["web"] = old


def test_picture_note_lists_subject_pictures_only():
    import deep_search
    deep_search.PAGE_IMAGES["https://pequods.example/"] = {"src": "https://pequods.example/p.jpg", "name": "Pequod's"}
    deep_search.PAGE_IMAGES["https://news.example/top10"] = {"src": "https://news.example/b.jpg", "name": "Top 10",
                                                              "listing": True}
    note = agent_tools._picture_note("[1] https://pequods.example/ [2] https://news.example/top10")
    assert "https://pequods.example/p.jpg" in note and "b.jpg" not in note and "![caption]" in note
    assert agent_tools._picture_note("no links here") == ""


def test_hint_picks_the_block_for_the_question():
    cases = {
        "Where are the 5 largest football stadiums in Europe?": "map",
        "Good ramen shops near Shinjuku station in Tokyo": "map",
        "How did US GDP growth change from 2019 to 2024?": "chart",
        "Timeline of the Apollo program": "timeline",
        "Draw a simple flowchart of how a bill becomes law": "mermaid",
        "Quick facts about Mount Everest": "facts",
        "Kindle vs Kobo, which should I buy?": "cards",
        "Compare the iPhone 16 and Pixel 9 on battery": "table",
        "What does a Shiba Inu look like?": "image",
        "What is 17% of 2,340?": "",
        "hi! how's it going?": "",
        "Explain recursion in two sentences.": "",
        "fix the failing test in main.py": "",
    }
    for q, want in cases.items():
        assert answer_shapes.kind(q) == want, (q, answer_shapes.kind(q))
    assert answer_shapes.hint("hi") == ""
    assert "```map" in answer_shapes.hint("Where is the Louvre?")
    assert answer_shapes.kind("x" * 700 + " where is it") == ""        # long tasks are not lookups


def test_mermaid_is_in_the_shapes_and_fences_open_lines():
    import re
    assert "```mermaid\nflowchart" in answer_shapes.TEXT
    for line in answer_shapes.TEXT.splitlines():
        if "```" in line:
            assert line.startswith("```") or "``` followed" in line or "```chart/" in line, line
