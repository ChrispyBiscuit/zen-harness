"""edit_file by line numbers, insertion, several edits per call, and verbatim copying from another file."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_tools as at


def _file(text, name="m.py"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    c = at.Ctx([d])
    return p, c


def _text(p):
    return open(p, encoding="utf-8", newline="").read()


def test_replace_lines_without_retyping_old():
    p, c = _file("a = 1\nb = 2\nc = 3\nd = 4\n")
    out = at.t_edit_file({"path": p, "start_line": 2, "end_line": 3, "new": "b = 20\nc = 30"}, c)
    assert _text(p) == "a = 1\nb = 20\nc = 30\nd = 4\n" and "Lines" in out


def test_expect_guards_against_stale_line_numbers():
    p, c = _file("a = 1\nb = 2\n")
    with pytest.raises(at.ToolError) as e:
        at.t_edit_file({"path": p, "start_line": 2, "end_line": 2, "new": "x", "expect": "b = 3"}, c)
    assert "not what you expected" in str(e.value) and _text(p) == "a = 1\nb = 2\n"


def test_insert_after_and_top():
    p, c = _file("def f():\n    return 1\n")
    at.t_edit_file({"path": p, "insert_after": 0, "new": "import os"}, c)
    at.t_edit_file({"path": p, "insert_after": 3, "new": "\nprint(f())"}, c)
    assert _text(p) == "import os\ndef f():\n    return 1\n\nprint(f())\n"


def test_several_edits_in_one_call_use_the_original_numbers():
    p, c = _file("1\n2\n3\n4\n5\n")
    at.t_edit_file({"path": p, "edits": [{"start_line": 1, "end_line": 1, "new": "one\nuno"},
                                         {"start_line": 4, "end_line": 5, "new": "four"},
                                         {"old": "3\n", "new": "three\n"}]}, c)
    assert _text(p) == "one\nuno\n2\nthree\nfour\n"


def test_overlapping_edits_are_refused():
    p, c = _file("1\n2\n3\n")
    with pytest.raises(at.ToolError):
        at.t_edit_file({"path": p, "edits": [{"start_line": 1, "end_line": 2, "new": "x"},
                                             {"start_line": 2, "end_line": 3, "new": "y"}]}, c)


def test_new_from_copies_lines_verbatim():
    p, c = _file("x = 0\n")
    src = os.path.join(os.path.dirname(p), "page.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("intro\nRATE = 0.0825  # exact figure\nTAX_NAME = 'Sales tax'\noutro\n")
    at.t_edit_file({"path": p, "insert_after": 1, "new_from": {"path": src, "start_line": 2, "end_line": 3}}, c)
    assert _text(p) == "x = 0\nRATE = 0.0825  # exact figure\nTAX_NAME = 'Sales tax'\n"


def test_crlf_files_keep_crlf_and_old_text_mode_still_works():
    p, c = _file("a\r\nb\r\n")
    at.t_edit_file({"path": p, "start_line": 2, "end_line": 2, "new": "B1\nB2"}, c)
    assert _text(p) == "a\r\nB1\r\nB2\r\n"
    at.t_edit_file({"path": p, "old": "a", "new": "A"}, c)
    assert _text(p).startswith("A\r\n")


def test_not_found_suggests_line_numbers():
    p, c = _file("alpha\nbeta\n")
    with pytest.raises(at.ToolError) as e:
        at.t_edit_file({"path": p, "old": "gamma", "new": "x"}, c)
    assert "start_line/end_line" in str(e.value)
