"""Chats in Claude's data-export format (claude.ai → Settings → Privacy → Export data).

Claude's export is a zip (or several: conversations-000.zip, conversations-001.zip, …) holding conversations.json:
a list of {uuid, name, created_at, updated_at, chat_messages: [{uuid, sender: "human"|"assistant", text,
content: [{type: "text"|"thinking"|"tool_use"|"tool_result", ...}], created_at, attachments, files}]}.

  manifest_links(path)  the conversation zips a newer export's manifest-*.json links to (downloaded by the importer)
  read_any(path)        every Claude conversation in a .zip / .json / .jsonl file (other files in the zip are skipped)
  to_zazen(c)           one Claude conversation -> a Zazen conversation (a chat-mode thread)
  to_claude(c)          one Zazen conversation -> a Claude conversation (visible user/assistant turns only)
  write_zip(convs, p)   a zip with conversations.json in Claude's format
"""
import datetime as dt
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


def _ts(s, default=None):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return default


def _iso(t):
    return dt.datetime.fromtimestamp(float(t or 0), dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _is_claude_conv(o):
    return isinstance(o, dict) and isinstance(o.get("chat_messages"), list)


def _collect(data, out):
    if _is_claude_conv(data):
        out.append(data)
    elif isinstance(data, list):
        for x in data:
            _collect(x, out)
    elif isinstance(data, dict):
        for k in ("conversations", "data", "items"):
            if isinstance(data.get(k), list):
                _collect(data[k], out)


def _parse(raw, out):
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else raw
    try:
        _collect(json.loads(text), out)
    except ValueError:                                  # JSON Lines
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    _collect(json.loads(line), out)
                except ValueError:
                    pass


EXPORT_HOST = "claude.ai"
CHAT_CATEGORIES = ("conversations",)


def manifest_links(path):
    """The conversation zips a Claude export manifest (manifest-*.json: {data_files: [{export_url, category,
    filename}]}) links to -> [(filename, url)]; [] when the file isn't a manifest. Only claude.ai export links."""
    try:
        with open(path, "rb") as f:
            m = json.loads(f.read(1 << 20).decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(m, dict) or not isinstance(m.get("data_files"), list):
        return []
    out = []
    for d in m["data_files"]:
        if not isinstance(d, dict) or d.get("category") not in CHAT_CATEGORIES:
            continue
        url = str(d.get("export_url") or "")
        u = urllib.parse.urlparse(url)
        if u.scheme == "https" and u.hostname == EXPORT_HOST and u.path.startswith("/export/"):
            out.append((os.path.basename(str(d.get("filename") or "")) or f"conversations-{len(out):03d}.zip", url))
    return out


class LinkError(Exception):
    pass


def download(url, dest):
    """One export file -> dest. LinkError with a readable reason when the link has expired, was already used,
    or needs the browser's claude.ai sign-in."""
    req = urllib.request.Request(url, headers={"User-Agent": "Zazen/1.0 (chat import)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        raise LinkError(f"claude.ai answered {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LinkError(f"could not download it ({getattr(e, 'reason', e)})") from e
    if not zipfile.is_zipfile(dest):
        raise LinkError("claude.ai sent a web page instead of the zip (the link needs your browser's sign-in, "
                        "or it expired)")


def read_any(path):
    out = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith((".json", ".jsonl")) and not name.endswith("/"):
                    _parse(z.read(name), out)
                elif name.lower().endswith(".zip"):     # a zip of the per-category zips
                    nested = io.BytesIO(z.read(name))
                    if zipfile.is_zipfile(nested):
                        with zipfile.ZipFile(nested) as zz:
                            for n in zz.namelist():
                                if n.lower().endswith((".json", ".jsonl")):
                                    _parse(zz.read(n), out)
    else:
        with open(path, "rb") as f:
            _parse(f.read(), out)
    return out


def _blocks_text(m):
    """(answer text, thinking text) from a Claude message: content blocks if present, else its text field."""
    texts, thinking = [], []
    for b in m.get("content") or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text"):
            texts.append(b["text"])
        elif t == "thinking" and b.get("thinking"):
            thinking.append(b["thinking"])
        elif t == "tool_use":
            name = b.get("name") or "tool"
            inp = b.get("input")
            if isinstance(inp, dict) and inp.get("query"):
                texts.append(f"*[{name}: {inp['query']}]*")
            else:
                texts.append(f"*[used {name}]*")
    answer = "\n\n".join(texts).strip() or (m.get("text") or "").strip()
    return answer, "\n\n".join(thinking).strip()


def to_zazen(c):
    created = _ts(c.get("created_at"), None) or 0.0
    msgs = []
    for m in c.get("chat_messages") or []:
        role = "user" if m.get("sender") == "human" else "assistant"
        text, thinking = _blocks_text(m)
        atts = [a for a in (m.get("attachments") or []) if isinstance(a, dict)]
        names = [a.get("file_name") for a in atts if a.get("file_name")]
        names += [f.get("file_name") for f in (m.get("files") or []) if isinstance(f, dict) and f.get("file_name")]
        if not text and not names:
            continue
        msg = {"role": role, "content": text or "(see attached files)", "t": _ts(m.get("created_at"), created)}
        if thinking and role == "assistant":
            msg["thinking"] = thinking
        extracted = [f"[{a.get('file_name') or 'attachment'}]\n{a['extracted_content']}"
                     for a in atts if a.get("extracted_content")]
        if extracted:                                   # the model sees the file text; the transcript shows names
            msg["model_content"] = msg["content"] + "\n\n" + "\n\n".join(extracted)
        if names and text:
            msg["content"] += "\n\n" + " · ".join(f"📎 {n}" for n in names)
        msgs.append(msg)
    cid = str(c.get("uuid") or uuid.uuid4()).replace("-", "")
    return {"id": cid, "title": (c.get("name") or "").strip()[:80] or "Imported chat", "titled": True,
            "created": created, "updated": _ts(c.get("updated_at"), None) or (msgs[-1]["t"] if msgs else created),
            "mode": "chat", "messages": msgs, "claude_uuid": c.get("uuid") or "", "imported": "claude"}


def to_claude(c):
    chat = []
    for m in c.get("messages") or []:
        if m.get("kind") or m.get("role") not in ("user", "assistant") or not (m.get("content") or "").strip():
            continue
        t = _iso(m.get("t") or c.get("created"))
        content = []
        if m.get("thinking"):
            content.append({"type": "thinking", "thinking": m["thinking"], "start_timestamp": t, "stop_timestamp": t})
        content.append({"type": "text", "text": m["content"], "start_timestamp": t, "stop_timestamp": t, "citations": []})
        chat.append({"uuid": str(uuid.uuid4()), "text": m["content"], "content": content,
                     "sender": "human" if m["role"] == "user" else "assistant", "created_at": t, "updated_at": t,
                     "attachments": [], "files": [{"file_name": a.get("name")} for a in (m.get("attachments") or [])
                                                  if isinstance(a, dict) and a.get("name")]})
    cu = c.get("claude_uuid")
    if not cu:
        try:
            cu = str(uuid.UUID(str(c.get("id"))))           # Zazen ids are uuid4 hex
        except ValueError:
            cu = str(uuid.uuid4())
    return {"uuid": cu, "name": c.get("title") or "", "summary": "", "created_at": _iso(c.get("created")),
            "updated_at": _iso(c.get("updated") or c.get("created")), "account": {}, "chat_messages": chat}


def write_zip(convs, path):
    data = [to_claude(c) for c in convs]
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations.json", json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    return len(data)
