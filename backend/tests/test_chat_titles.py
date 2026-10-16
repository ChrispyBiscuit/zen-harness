"""New chats get a model-written title from the request's interpretation, once, and never over the user's own name."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine


def test_interpretation_emits_a_cleaned_title():
    e = engine(interpret_first=True)
    e.history = [{"role": "user", "content": "build a roommate expense tracker"}]
    e._turn_user_msg = e.history[0]
    e._apply_interpretation({"goal": "an expense tracker roommates share", "title": '"Roommate expense tracker."',
                             "conditions": []})
    titles = [d["text"] for k, d in e._emits if k == "title"]
    assert titles == ["Roommate expense tracker"]
    e2 = engine(interpret_first=True)
    e2.history = [{"role": "user", "content": "x"}]
    e2._turn_user_msg = e2.history[0]
    e2._apply_interpretation({"goal": "x", "title": "", "conditions": []})
    assert not [1 for k, _ in e2._emits if k == "title"]


def test_backend_renames_only_the_first_request_and_never_a_user_name():
    import web_backend as wb
    conv = {"id": "t1", "title": "I want an expense tracker my roommates and I can", "messages": [
        {"role": "user", "content": "I want an expense tracker..."}]}
    w = wb.ConvWriter(conv)
    w.event("title", {"text": "Roommate expense tracker"})
    assert conv["title"] == "Roommate expense tracker" and conv["titled"]
    w2 = wb.ConvWriter({"id": "t2", "title": "My name", "titled": True, "messages": [{"role": "user", "content": "x"}]})
    w2.event("title", {"text": "Model name"})
    assert w2.conv["title"] == "My name"
    later = {"id": "t3", "title": "First", "messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]}
    w3 = wb.ConvWriter(later)
    w3.event("title", {"text": "Second"})
    assert later["title"] == "First"
