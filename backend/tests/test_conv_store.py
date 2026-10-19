"""One file per chat (web_backend.Store): 2,800 imported chats in one 58 MB file were rewritten every 1.5 s during a
run (0.8 s each, under the lock every request waits on)."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def conv(i, n=2):
    return {"id": f"c{i}", "title": f"chat {i}", "created": i, "updated": i,
            "messages": [{"role": "user", "content": f"m{j}"} for j in range(n)]}


def store_at(tmp_path, monkeypatch):
    import web_backend as wb
    monkeypatch.setattr(wb, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(wb, "CONV_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(wb, "CONV_DIR", str(tmp_path / "conversations"))
    return wb, wb.Store()


def test_the_old_single_file_moves_into_one_file_per_chat(tmp_path, monkeypatch):
    (tmp_path / "conversations.json").write_text(json.dumps([conv(3), conv(2), conv(1), {"id": "empty", "messages": []}]))
    wb, st = store_at(tmp_path, monkeypatch)
    assert [c["id"] for c in st.convs] == ["c3", "c2", "c1"]
    st.save_convs()
    assert sorted(os.listdir(tmp_path / "conversations")) == ["_order.json", "c1.json", "c2.json", "c3.json"]
    assert (tmp_path / "conversations.json").exists()               # left as a backup (and for a rollback)
    _, again = store_at(tmp_path, monkeypatch)                     # restart: read from the folder, same order
    assert [c["id"] for c in again.convs] == ["c3", "c2", "c1"]


def test_only_changed_new_and_deleted_chats_touch_the_disk(tmp_path, monkeypatch):
    (tmp_path / "conversations.json").write_text(json.dumps([conv(i) for i in range(5)]))
    wb, st = store_at(tmp_path, monkeypatch)
    st.save_convs()
    d = tmp_path / "conversations"
    before = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
    time.sleep(0.05)
    c2 = st.get("c2")
    c2["messages"][0]["content"] = "edited in place"              # a streaming edit: same length, same 'updated'
    st.touch(c2)
    st.convs.insert(0, conv(9))                                    # an import / a new chat (no touch)
    st.convs = [c for c in st.convs if c["id"] != "c4"]           # a delete
    st.save_convs()
    after = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
    assert "c4.json" not in after and "c9.json" in after
    assert after["c2.json"] != before["c2.json"]                    # the touched chat was rewritten
    assert after["c0.json"] == before["c0.json"] and after["c1.json"] == before["c1.json"]    # the rest were not
    assert json.loads((d / "c2.json").read_text())["messages"][0]["content"] == "edited in place"
    assert json.loads((d / "_order.json").read_text())[0] == "c9"


def test_a_change_without_touch_is_still_saved(tmp_path, monkeypatch):
    (tmp_path / "conversations.json").write_text(json.dumps([conv(1)]))
    wb, st = store_at(tmp_path, monkeypatch)
    st.save_convs()
    st.get("c1")["messages"].append({"role": "assistant", "content": "added by a run"})   # no touch()
    st.save_convs()
    saved = json.loads((tmp_path / "conversations" / "c1.json").read_text())
    assert saved["messages"][-1]["content"] == "added by a run"


def test_lookup_by_id_follows_list_replacements(tmp_path, monkeypatch):
    (tmp_path / "conversations.json").write_text(json.dumps([conv(i) for i in range(3)]))
    wb, st = store_at(tmp_path, monkeypatch)
    assert st.get("c1")["title"] == "chat 1"
    st.convs = [conv(7)]                                           # delete all + import
    assert st.get("c1") is None and st.get("c7")["title"] == "chat 7"
