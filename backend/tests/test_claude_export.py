"""Chats in Claude's data-export format: read a zip/json, convert both ways (claude_export.py)."""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_export as ce

CLAUDE = [{
    "uuid": "3f2a6c1e-8b9d-4e7f-a1b2-c3d4e5f60718", "name": "Tide pools", "summary": "",
    "created_at": "2026-10-01T12:00:00.000000Z", "updated_at": "2026-10-01T12:05:00.000000Z", "account": {"uuid": "x"},
    "chat_messages": [
        {"uuid": "a", "sender": "human", "text": "What lives in tide pools?", "created_at": "2026-10-01T12:00:00Z",
         "content": [{"type": "text", "text": "What lives in tide pools?"}],
         "attachments": [{"file_name": "notes.txt", "file_size": 5, "file_type": "txt", "extracted_content": "clams"}],
         "files": []},
        {"uuid": "b", "sender": "assistant", "text": "", "created_at": "2026-10-01T12:01:00Z",
         "content": [{"type": "thinking", "thinking": "Think about intertidal life."},
                     {"type": "tool_use", "name": "web_search", "input": {"query": "tide pool animals"}},
                     {"type": "tool_result", "content": [{"type": "text", "text": "..."}]},
                     {"type": "text", "text": "Anemones, crabs and clams."}],
         "attachments": [], "files": []},
        {"uuid": "c", "sender": "human", "text": "", "content": [], "attachments": [], "files": []},   # empty: dropped
    ]}]


def test_zip_round_trip(tmp_path):
    src = tmp_path / "conversations-000.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("conversations.json", json.dumps(CLAUDE))
        z.writestr("users.json", json.dumps([{"uuid": "x", "full_name": "someone"}]))   # not a conversation
    found = ce.read_any(str(src))
    assert len(found) == 1
    c = ce.to_zazen(found[0])
    assert c["id"] == "3f2a6c1e8b9d4e7fa1b2c3d4e5f60718" and c["title"] == "Tide pools" and c["mode"] == "chat"
    assert [m["role"] for m in c["messages"]] == ["user", "assistant"]
    user, bot = c["messages"]
    assert "📎 notes.txt" in user["content"] and "clams" in user["model_content"]
    assert bot["content"].startswith("*[web_search: tide pool animals]*") and bot["content"].endswith("clams.")
    assert bot["thinking"] == "Think about intertidal life."
    assert c["created"] < c["updated"]

    out = tmp_path / "export.zip"
    assert ce.write_zip([c], str(out)) == 1
    back = ce.read_any(str(out))
    assert back[0]["uuid"] == CLAUDE[0]["uuid"] and back[0]["name"] == "Tide pools"
    assert [m["sender"] for m in back[0]["chat_messages"]] == ["human", "assistant"]
    again = ce.to_zazen(back[0])
    assert again["id"] == c["id"] and again["messages"][1]["thinking"] == bot["thinking"]


def test_zazen_chat_export_skips_internal_messages():
    conv = {"id": "0123456789abcdef0123456789abcdef", "title": "x", "created": 1.0, "updated": 2.0, "messages": [
        {"role": "user", "content": "hi", "t": 1.0},
        {"role": "assistant", "kind": "plan", "content": "internal"},
        {"role": "assistant", "content": "hello", "t": 2.0}]}
    out = ce.to_claude(conv)
    assert out["uuid"] == "01234567-89ab-cdef-0123-456789abcdef"
    assert [m["text"] for m in out["chat_messages"]] == ["hi", "hello"]


def test_plain_json_and_jsonl(tmp_path):
    j = tmp_path / "conversations.json"
    j.write_text(json.dumps({"conversations": CLAUDE}), encoding="utf-8")
    assert len(ce.read_any(str(j))) == 1
    l = tmp_path / "c.jsonl"
    l.write_text("\n".join(json.dumps(c) for c in CLAUDE * 2), encoding="utf-8")
    assert len(ce.read_any(str(l))) == 2


def _manifest(tmp_path, files):
    p = tmp_path / "manifest-x.json"
    p.write_text(json.dumps({"instructions": "…", "total_files": len(files), "data_files": files, "version": "1.0"}))
    return str(p)


def test_manifest_links_keep_only_claude_conversation_zips(tmp_path):
    p = _manifest(tmp_path, [
        {"export_url": "https://claude.ai/export/e/download/a", "category": "conversations", "filename": "conversations-000.zip"},
        {"export_url": "https://claude.ai/export/e/download/b", "category": "conversations", "filename": "../conversations-001.zip"},
        {"export_url": "https://claude.ai/export/e/download/c", "category": "projects", "filename": "projects-000.zip"},
        {"export_url": "https://evil.example/export/e/download/d", "category": "conversations", "filename": "x.zip"},
        {"export_url": "http://claude.ai/export/e/download/e", "category": "conversations", "filename": "y.zip"}])
    assert ce.manifest_links(p) == [("conversations-000.zip", "https://claude.ai/export/e/download/a"),
                                    ("conversations-001.zip", "https://claude.ai/export/e/download/b")]
    j = tmp_path / "conversations.json"
    j.write_text(json.dumps(CLAUDE))
    assert ce.manifest_links(str(j)) == []          # an ordinary export file is read, not followed


def test_download_checks_for_a_zip(tmp_path, monkeypatch):
    import io
    import urllib.error
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("conversations.json", json.dumps(CLAUDE))

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    bodies = {"zip": good.getvalue(), "page": b"<html>sign in</html>"}
    monkeypatch.setattr(ce.urllib.request, "urlopen", lambda req, timeout=0: Resp(bodies[req.full_url.rsplit("/", 1)[1]]))
    ce.download("https://claude.ai/export/e/download/zip", str(tmp_path / "a.zip"))
    assert len(ce.read_any(str(tmp_path / "a.zip"))) == 1
    with pytest.raises(ce.LinkError, match="web page"):
        ce.download("https://claude.ai/export/e/download/page", str(tmp_path / "b.zip"))

    def gone(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 410, "Gone", {}, None)
    monkeypatch.setattr(ce.urllib.request, "urlopen", gone)
    with pytest.raises(ce.LinkError, match="410"):
        ce.download("https://claude.ai/export/e/download/zip", str(tmp_path / "c.zip"))
