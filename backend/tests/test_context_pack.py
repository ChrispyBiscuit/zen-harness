"""focus_pack: function-level slices from several files, best match first, within a token budget."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import context_pack as cp

FILES = {
    "client.py": "import json\n\n\ndef send_expense(host, user, amount_cents, category):\n"
                 "    body = json.dumps({'user': user, 'amount_cents': amount_cents, 'category': category})\n"
                 "    return post(host + '/expenses', body)\n\n\ndef post(url, body):\n    return url, body\n",
    "server.py": "import db\n\n\nclass Handler:\n    def do_POST(self, payload):\n"
                 "        amount = int(payload['amount_cents'])\n        if amount <= 0:\n"
                 "            return 400, 'amount must be positive'\n"
                 "        return 201, db.add_expense(payload['user'], amount, payload['category'])\n\n"
                 "    def do_GET(self, path):\n        return 200, db.monthly_totals(path)\n",
    "db.py": "import sqlite3\n\n\ndef add_expense(user, amount_cents, category):\n"
             "    if amount_cents <= 0:\n        raise ValueError('amount_cents must be positive')\n"
             "    return ('INSERT', user, amount_cents, category)\n\n\ndef monthly_totals(month):\n"
             "    return ('SELECT category, sum(amount_cents)', month)\n",
    "weather.py": "def forecast(city):\n    return 'sunny in ' + city\n\n\ndef humidity(city):\n    return 40\n",
}


def _project():
    root = tempfile.mkdtemp()
    for name, text in FILES.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            f.write(text)
    return root


def test_units_are_functions_and_class_heads():
    units = cp.file_units("server.py", FILES["server.py"])
    syms = {u["symbol"] for u in units}
    assert {"Handler", "Handler.do_POST", "Handler.do_GET"} <= syms


def test_pack_brings_the_layers_together_and_leaves_noise_out():
    root = _project()
    text, chosen = cp.focus_pack(root, "a negative amount_cents gets through: where is the amount validated, "
                                       "in the server's POST handler and in db add_expense?", 600, embed=None)
    ranked = [u["symbol"] for u in chosen]                    # chosen in rank order, best first
    assert {"Handler.do_POST", "add_expense"} <= set(ranked[:3]), ranked
    for noise in ("forecast", "humidity"):
        assert noise not in ranked[:4], ranked
    assert "### server.py:" in text and "### db.py:" in text and "\tdef add_expense" in text   # numbered lines


def test_budget_is_respected():
    root = _project()
    _text, chosen = cp.focus_pack(root, "amount category user expense", 120, embed=None)
    assert sum(u["tokens"] for u in chosen) <= 120 and chosen


def test_index_rebuilds_when_a_file_changes():
    root = _project()
    cp.focus_pack(root, "forecast", 400, embed=None)
    with open(os.path.join(root, "weather.py"), "a", encoding="utf-8") as f:
        f.write("\n\ndef wind_speed(city):\n    return 12\n")
    _text, chosen = cp.focus_pack(root, "wind_speed for a city", 200, embed=None)
    assert "wind_speed" in {u["symbol"] for u in chosen}


def test_long_definitions_split_into_smaller_windows_with_a_head():
    body = "\n".join(f"    x{i} = {i}" for i in range(100))
    units = cp.file_units("big.py", "def big():\n" + body + "\n", max_lines=40)
    windows = [u for u in units if u["symbol"] == "big"]
    assert len(windows) == 3 and windows[0]["head"] and not any(w["head"] for w in windows[1:])
    assert all(w["end"] - w["start"] + 1 <= 40 for w in windows)


def test_link_boost_brings_in_what_a_top_hit_calls():
    root = tempfile.mkdtemp()
    files = {"checkout.py": "from tax import compute_tax\n\n\ndef handle_checkout(cart):\n"
                            "    subtotal = sum(cart)\n    return subtotal + compute_tax(subtotal)\n",
             "tax.py": "RATE = 0.08\n\n\ndef compute_tax(amount):\n    return round(amount * RATE, 2)\n",
             "misc.py": "def banner():\n    return 'welcome'\n"}
    for n, t in files.items():
        with open(os.path.join(root, n), "w", encoding="utf-8") as f:
            f.write(t)
    q = "the checkout handler sums the cart"
    _t, off = cp.focus_pack(root, q, 400, embed=None, link_seeds=0)
    _t, on = cp.focus_pack(root, q, 400, embed=None, link_seeds=5, link_boost=0.01)
    assert "compute_tax" not in {u["symbol"] for u in off}
    assert "compute_tax" in {u["symbol"] for u in on}


def test_tool_follow_up_brings_only_new_slices_and_reports_unknown_names(monkeypatch):
    import agent_tools as at
    monkeypatch.setattr(cp, "_default_embed", None)
    root = _project()
    c = at.Ctx([root])
    c.conv_id = "pack-tool"
    first = at.t_focus_pack({"goal": "where is a negative amount_cents rejected", "budget": 200}, c)
    assert "### db.py:" in first and "Missing something?" in first
    again = at.t_focus_pack({"goal": "amount", "names": ["monthly_totals", "no_such_function"], "budget": 400}, c)
    assert "monthly_totals" in again and "Not found in the project: no_such_function" in again
    assert again.count("def add_expense") == 0                    # shown in the first pack: left out


def test_writing_code_starts_a_background_index(monkeypatch):
    import agent_tools as at
    started = []
    monkeypatch.delenv("LWS_NO_CODE_INDEX", raising=False)
    monkeypatch.setattr(cp, "start_index", lambda root, **k: started.append(root))
    root = _project()
    c = at.Ctx([root])
    c.changed = {os.path.join(root, "db.py"): "edited"}
    at.refresh_code_index(c, os.path.join(root, "db.py"))
    assert started and os.path.normcase(started[0]) == os.path.normcase(root)
    started.clear()
    at.refresh_code_index(c, os.path.join(root, "notes.txt"))        # not code: nothing
    assert not started
