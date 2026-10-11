"""Qt side of Agent mode: worker thread, approval dialog, settings dialog, tool-call rendering."""
import copy
import html
import json
import os
import subprocess
import threading
import time

from PySide6.QtCore import QRect, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QKeySequence, QPainter, QSyntaxHighlighter,
                           QTextCharFormat, QTextFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

import agent_tools as at
import agentic
import mcp_client
import richtext
import theme

DEFAULT_AGENT = {
    "enabled": False, "backend": "llama", "model": "", "num_ctx": 16384, "max_steps": 25,
    "temperature": 0.4, "think": False, "vision": False,
    "roots": [os.path.expanduser("~\\Documents"), os.path.expanduser("~\\Desktop")],
    "auto_read": True, "auto_net": True, "mcp_servers": [],
}

KIND_LABEL = {at.READ: "read", at.NET: "web", at.WRITE: "changes files", at.EXEC: "runs a command",
              at.INPUT: "controls mouse/keyboard", at.MCP: "MCP tool"}
KIND_COLOR = {at.READ: "#5A6472", at.NET: "#5FB8E0", at.WRITE: "#E0B45F", at.EXEC: "#E0665F",
              at.INPUT: "#E0665F", at.MCP: "#B779E0"}


class AgentModeWorker(QThread):
    status = Signal(str)
    ans_delta = Signal(str)
    think_delta = Signal(str)
    think_end = Signal()
    tool_call = Signal(dict)
    tool_result = Signal(dict)
    tool_output = Signal(dict)
    files_changed = Signal(dict)
    answer_end = Signal(str)
    failed = Signal(str)
    tps_live = Signal(float)
    approval = Signal(dict)

    def __init__(self, engine, text):
        super().__init__()
        self.engine = engine
        self.text = text
        self._stop = threading.Event()
        self._answer = None
        self._answer_evt = threading.Event()
        engine.emit = self._emit
        engine.ask = self._ask
        engine.should_stop = self._stop.is_set
        engine.ctx.emit = self._emit

    def stop(self):
        self._stop.set()
        self._answer = "deny"
        self._answer_evt.set()

    def reply(self, decision):
        self._answer = decision
        self._answer_evt.set()

    def _ask(self, req):
        self._answer, _ = None, self._answer_evt.clear()
        self.approval.emit(req)
        while not self._answer_evt.wait(0.2):
            if self._stop.is_set():
                return "deny"
        return self._answer or "deny"

    def _emit(self, kind, /, **d):
        if kind == "status":
            self.status.emit(d.get("text", ""))
        elif kind == "ans_delta":
            self.ans_delta.emit(d.get("text", ""))
        elif kind == "think_delta":
            self.think_delta.emit(d.get("text", ""))
        elif kind == "think_end":
            self.think_end.emit()
        elif kind == "tool_call":
            self.tool_call.emit(d)
        elif kind == "tool_result":
            self.tool_result.emit(d)
        elif kind == "tool_output":
            self.tool_output.emit(d)
        elif kind == "files_changed":
            self.files_changed.emit(d.get("files") or {})
        elif kind == "stats":
            self.tps_live.emit(float(d.get("tps", 0)))

    def run(self):
        try:
            if self.engine.reg is None:
                self.engine.prepare()
            final = self.engine.run(self.text)
            self.answer_end.emit(final)
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


MONO = "Cascadia Mono, Consolas, Courier New, monospace"
TERM = dict(bg="#0A0C10", border="#232833", prompt="#3FB8A4", cmd="#E6E8EC", out="#B8C0CC",
            ok="#5FC48A", bad="#E0665F", run="#E0B45F", add_bg="#12301F", add_fg="#9BE0B0",
            del_bg="#3A1518", del_fg="#F0A0A0", hunk="#7C8794")
COLLAPSED_LINES = 30


def file_url(path):
    return QUrl.fromLocalFile(path).toString()


def file_link(path, label=None):
    """A clickable path (the chat opens it in the file viewer)."""
    return (f"<a href='{html.escape(file_url(path), quote=True)}' style='color:{theme.HTML['link']};"
            f"text-decoration:none'>{html.escape(label or os.path.basename(path) or path)}</a>")


def _pill(text, color):
    return f"<span style='color:{color};font-size:11px'>{html.escape(text)}</span>"


def _head(icon, title_html, right_html=""):
    return (f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td style='font-size:12.5px;color:{theme.HTML['bot_text']}'>{icon}&nbsp; {title_html}</td>"
            f"<td align='right' style='font-size:11px'>{right_html}</td></tr></table>")


def _lines_block(rows_html):
    return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:4px;"
            f"background-color:{TERM['bg']};border:1px solid {TERM['border']}'>"
            f"<tr><td style='padding:8px 12px'>{rows_html}</td></tr></table>")


def _pre(text, color, bg=None):
    style = f"margin:0;white-space:pre-wrap;font-family:{MONO};font-size:12.5px;color:{color}"
    if bg:
        return (f"<table width='100%' cellspacing='0' cellpadding='0'><tr><td bgcolor='{bg}'>"
                f"<pre style='{style}'>{html.escape(text) or '&nbsp;'}</pre></td></tr></table>")
    return f"<pre style='{style}'>{html.escape(text)}</pre>"


def _collapse(lines, expanded, row):
    """-> (visible lines, footer html). Long output shows its tail until expanded."""
    if expanded or len(lines) <= COLLAPSED_LINES:
        more = (f"<a href='texpand:{row}' style='color:{theme.HTML['link']};text-decoration:none'>"
                f"collapse</a>") if expanded and len(lines) > COLLAPSED_LINES else ""
        return lines, more
    hidden = len(lines) - COLLAPSED_LINES
    return lines[-COLLAPSED_LINES:], (f"<a href='texpand:{row}' style='color:{theme.HTML['link']};"
                                      f"text-decoration:none'>show all {len(lines)} lines "
                                      f"({hidden} hidden above)</a>")


def _link(href, text, color=None):
    return (f"<a href='{html.escape(href, quote=True)}' style='color:{color or theme.HTML['link']};"
            f"text-decoration:none'>{text}</a>")


def approval_html(t):
    """Inline approve/deny links on a card that is waiting for the user."""
    ap = t.get("approval") or {}
    cid = ap.get("call_id")
    if not t.get("awaiting") or not cid:
        return ""
    rule = ap.get("rule_label") or ""
    rule_txt = rule.split(": ", 1)[-1] if rule else ""
    links = [_link(f"approve:{cid}:once", "<b>Allow once</b>"),
             _link(f"approve:{cid}:session", "Allow for this chat"),
             _link(f"approve:{cid}:always", "Always allow " + html.escape(f"‘{rule_txt[:48]}’")),
             _link(f"approve:{cid}:deny", "<b>Deny</b>", TERM["bad"])]
    warn = "".join(f"<div style='color:#E0B45F;font-size:11.5px;margin-top:3px'>⚠ {html.escape(w)}</div>"
                   for w in ap.get("warnings") or [])
    return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:5px'><tr>"
            f"<td style='padding:6px 10px;background-color:#1D1A10;border:1px solid #5C4A1E;font-size:12.5px'>"
            f"<span style='color:{TERM['run']}'>Needs your approval:</span>&nbsp;&nbsp;"
            + " &nbsp;·&nbsp; ".join(links) + warn + "</td></tr></table>")


def _tracker_html(t, d):
    h = theme.HTML
    steps = d.get("steps") or []
    done = sum(1 for s in steps if s.get("status") in ("done", "skipped"))
    color = {"done": TERM["ok"], "in_progress": TERM["run"], "blocked": TERM["bad"], "skipped": h["dim"]}
    rows = "".join(
        f"<tr><td style='padding:1px 8px 1px 0;color:{color.get(s.get('status'), h['bot_text'])}'>"
        f"{at.STATUS_ICON.get(s.get('status'), '☐')}</td>"
        f"<td style='padding:1px 0;color:{h['dim'] if s.get('status') in ('done', 'skipped') else h['bot_text']}'>"
        f"{html.escape(s.get('step', ''))}</td></tr>" for s in steps)
    notes = d.get("notes") or []
    note_html = "".join(f"<div style='color:{h['dim']};font-size:11.5px'>• {html.escape(n)}</div>"
                        for n in notes[-3:])
    pct = int(100 * done / len(steps)) if steps else 0
    bar = (f"<table width='160' cellspacing='0' cellpadding='0' style='margin-top:2px'><tr>"
           f"<td width='{max(1, pct)}%' bgcolor='{TERM['ok']}' style='font-size:3px'>&nbsp;</td>"
           f"<td bgcolor='#232833' style='font-size:3px'>&nbsp;</td></tr></table>") if steps else ""
    state = "" if t.get("result") is not None else _pill(" updating…", h["dim"])
    head = _head("📋", f"<b>Tracker</b> {_pill(f'{done}/{len(steps)} done', h['dim'])}{state}",
                 file_link(d.get("path", ""), "open file") if d.get("path") else "")
    body = (f"<div style='font-size:13px;color:{h['bot_text']}'><b>{html.escape(d.get('goal') or '')}</b></div>"
            f"{bar}<table cellspacing='0' cellpadding='0' style='margin-top:4px;font-size:12.5px'>{rows}</table>"
            + (f"<div style='margin-top:4px;font-size:12px;color:{h['bot_text']}'>Next: "
               f"{html.escape(d.get('next') or '')}</div>" if d.get("next") else "") + note_html)
    return (f"<div style='margin:6px 18px'>{head}<table width='100%' cellspacing='0' cellpadding='0' "
            f"style='margin-top:4px;border:1px solid {TERM['border']}'><tr><td style='padding:8px 12px'>"
            f"{body}</td></tr></table></div>")


def compacted_html(msg, row):
    h = theme.HTML
    b, a = msg.get("before") or 0, msg.get("after") or 0
    sizes = f" ({b / 1000:.1f}k → {a / 1000:.1f}k tokens)" if b else ""
    toggle = _link(f"texpand:{row}", "hide summary" if msg.get("expanded") else "show summary")
    body = ""
    if msg.get("expanded"):
        body = (f"<div style='margin:6px 40px 0 40px;color:{h['dim']};font-size:12.5px;text-align:left'>"
                f"{html.escape(msg.get('summary', '')).replace(chr(10), '<br>')}</div>")
    return (f"<div style='margin:12px 18px;text-align:center;color:{h['dim']};font-size:12px'>"
            f"— Earlier conversation compressed automatically{sizes} · {toggle} —{body}</div>")


def task_done_html(msg):
    h = theme.HTML
    ok = msg.get("status") == "done"
    return (f"<div style='margin:6px 18px;padding:6px 10px;border-left:3px solid "
            f"{TERM['ok'] if ok else TERM['bad']};font-size:12.5px;color:{h['bot_text']}'>"
            f"{'✓' if ok else '■'} Delegated task <b>{html.escape(msg.get('title', ''))}</b> {html.escape(msg.get('status', ''))}"
            f" &nbsp;{_link('thread:' + msg.get('conv_id', ''), 'open thread')}"
            f"<div style='color:{h['dim']};font-size:12px;margin-top:3px'>{html.escape(msg.get('result', '')[:300])}</div></div>")


def stats_html(st):
    h = theme.HTML
    parts = [f"{st.get('steps', 0)} steps", f"{st.get('tool_calls', 0)} tool calls"]
    if st.get("context_used"):
        parts.append(f"context {st['context_used'] / 1000:.1f}k / {st.get('context_max', 0) / 1000:.0f}k")
    parts.append(f"{st.get('seconds', 0):.0f}s")
    if st.get("compactions"):
        parts.append(f"compressed ×{st['compactions']}")
    if st.get("failed_calls"):
        parts.append(f"{st['failed_calls']} failed")
    if st.get("advisor_cost"):
        parts.append(f"advisor ${st['advisor_cost']:.3f}")
    return (f"<div style='margin:-14px 18px 18px 18px;color:{h['dim']};font-size:11px'>"
            + " · ".join(parts) + "</div>")


def _terminal_html(t, d, row):
    h = theme.HTML
    shell = d.get("shell") or "powershell"
    title = {"cmd": "Command Prompt", "pwsh": "PowerShell 7"}.get(shell, "PowerShell")
    res = t.get("result")
    if t.get("awaiting"):
        status = _pill("● waiting for your approval", TERM["run"])
    elif res is None:
        status = _pill("● running…", TERM["run"])
    elif not t.get("ok", True) and d.get("exit") is None:
        status = _pill("✗ " + (res.splitlines() or ["failed"])[0][:80], TERM["bad"])
    elif d.get("stopped"):
        status = _pill(f"■ {d['stopped']}", TERM["bad"])
    else:
        rc = d.get("exit")
        secs = f" · {d['seconds']}s" if d.get("seconds") is not None else ""
        status = _pill(f"✓ exit 0{secs}" if rc == 0 else f"✗ exit {rc}{secs}",
                       TERM["ok"] if rc == 0 else TERM["bad"])
    prompt = (f"{d.get('cwd', '')}&gt;" if shell == "cmd" else f"PS {html.escape(d.get('cwd', ''))}&gt;")
    cmd_lines = (d.get("command") or "").splitlines() or [""]
    cmd_html = (f"<pre style='margin:0;white-space:pre-wrap;font-family:{MONO};font-size:12.5px'>"
                f"<span style='color:{TERM['prompt']}'>{prompt}</span> "
                f"<span style='color:{TERM['cmd']}'>{html.escape(cmd_lines[0])}</span>"
                + "".join(f"\n<span style='color:{TERM['prompt']}'>&gt;&gt;</span> "
                          f"<span style='color:{TERM['cmd']}'>{html.escape(c)}</span>" for c in cmd_lines[1:])
                + "</pre>")
    out = (d.get("output") or "").rstrip("\n")
    body = cmd_html
    foot = ""
    if out:
        shown, foot = _collapse(out.split("\n"), t.get("expanded"), row)
        body += _pre("\n".join(shown), TERM["out"])
    elif res is not None and d.get("exit") is not None:
        body += _pre("(no output)", h["dim"])
    if foot:
        body += f"<div style='font-size:11px;margin-top:4px'>{foot}</div>"
    return (f"<div style='margin:6px 18px'>{_head('⌨', f'<b>{title}</b>', status)}"
            f"{_lines_block(body)}{approval_html(t)}</div>")


def _diff_html(t, d, row):
    h = theme.HTML
    path = d.get("path", "")
    pending = d.get("pending")
    verb = ("New file" if d.get("created") else "Proposed edit") if pending else         ("Created" if d.get("created") else "Edited")
    stats = (f"<span style='color:{TERM['ok']}'>+{d.get('added', 0)}</span> "
             f"<span style='color:{TERM['bad']}'>−{d.get('removed', 0)}</span>")
    if pending:
        right = _pill("● waiting for approval" if t.get("awaiting") else "● applying…", TERM["run"])             if t.get("result") is None else _pill("✗ not applied", TERM["bad"])
    elif t.get("reverted"):
        right = _pill("↶ reverted", h["dim"])
    else:
        right = (f"<a href='revert:{row}' style='color:{h['link']};text-decoration:none'>↶ Revert</a>")
    title = (f"<b>{verb}</b> {file_link(path)} &nbsp;{stats} "
             f"<span style='color:{h['dim']};font-size:11px'>&nbsp;{html.escape(os.path.dirname(path))}</span>")
    lines = (d.get("diff") or "").split("\n") if d.get("diff") else []
    if not lines:
        return f"<div style='margin:6px 18px'>{_head('✎', title, right)}</div>"
    if t.get("expanded") or len(lines) <= COLLAPSED_LINES:          # diffs collapse to their head
        shown, foot = _collapse(lines, t.get("expanded"), row)
    else:
        shown = lines[:COLLAPSED_LINES]
        foot = (f"<a href='texpand:{row}' style='color:{h['link']};text-decoration:none'>"
                f"show all {len(lines)} diff lines</a>")
    rows = []
    for ln in shown:
        if ln.startswith("+"):
            rows.append(_pre(ln, TERM["add_fg"], TERM["add_bg"]))
        elif ln.startswith("-"):
            rows.append(_pre(ln, TERM["del_fg"], TERM["del_bg"]))
        elif ln.startswith("@@"):
            rows.append(_pre(ln, TERM["hunk"]))
        else:
            rows.append(_pre(ln, TERM["out"]))
    body = "".join(rows) + (f"<div style='font-size:11px;margin-top:4px'>{foot}</div>" if foot else "")
    return f"<div style='margin:6px 18px'>{_head('✎', title, right)}{_lines_block(body)}{approval_html(t)}</div>"


def tool_html(msg, row=0):
    """HTML for a stored tool-call message (msg['tool'] = {name, kind, desc, result, ok, display})."""
    h = theme.HTML
    t = msg.get("tool") or {}
    d = t.get("display") or {}
    typ = d.get("type")
    if typ == "terminal":
        return _terminal_html(t, d, row)
    if typ == "diff" and (t.get("ok", True) or d.get("pending")):
        return _diff_html(t, d, row)
    if typ == "tracker":
        return _tracker_html(t, d)
    if typ in ("document", "image") and t.get("ok", True):
        if typ == "image":
            info = f"{d.get('width')}×{d.get('height')}"
            icon, verb = "🖼", "Viewed image"
        else:
            u = d.get("unit", "")
            info = (f"{u}s {d['first']}–{d['last']} of {d['total']}" if u in ("page", "slide")
                    else f"{d['total']} sheet(s)" if u == "sheet" else f"{d.get('kind', '')}")
            icon, verb = "📄", "Read document"
        title = (f"<b>{verb}</b> {file_link(d['path'])} &nbsp;"
                 f"<span style='color:{h['dim']};font-size:11px'>{html.escape(info)}</span>")
        return f"<div style='margin:6px 18px'>{_head(icon, title)}</div>"
    if typ == "bg":
        res = t.get("result")
        state = (_pill("● waiting for approval", TERM["run"]) if t.get("awaiting") else
                 _pill("starting…", h["dim"]) if res is None else
                 _pill(("✓ " if t.get("ok", True) else "✗ ") + (d.get("id") or "") + " · see Tasks pane",
                       TERM["ok"] if t.get("ok", True) else TERM["bad"]))
        body = (f"<pre style='margin:0;white-space:pre-wrap;font-family:{MONO};font-size:12.5px'>"
                f"<span style='color:{TERM['prompt']}'>PS {html.escape(d.get('cwd') or '')}&gt;</span> "
                f"<span style='color:{TERM['cmd']}'>{html.escape(d.get('command') or '')}</span></pre>")
        return (f"<div style='margin:6px 18px'>{_head('⚙', '<b>Background process</b> ' + html.escape(d.get('name') or ''), state)}"
                f"{_lines_block(body)}{approval_html(t)}</div>")
    if typ == "delegate":
        state = _link("thread:" + (d.get("conv_id") or ""), "open thread")
        title = "<b>Delegated</b> " + html.escape(d.get("title") or "")
        return (f"<div style='margin:6px 18px'>{_head('⇢', title, state)}"
                f"<div style='color:{h['dim']};font-size:12px;margin:2px 0 0 22px'>{html.escape(d.get('task', '')[:300])}</div></div>")
    if typ == "advisor":
        cost = f" · ${d.get('cost', 0):.3f}" if d.get("cost") is not None else ""
        return (f"<div style='margin:6px 18px;padding:6px 10px;border-left:3px solid {KIND_COLOR[at.MCP]}'>"
                f"{_head('🧠', f'<b>Advisor</b> ' + _pill(html.escape(d.get('model', '')) + cost, h['dim']))}"
                f"<div style='color:{h['dim']};font-size:12px;margin:3px 0'>Q: {html.escape(d.get('question', ''))}</div>"
                f"<div style='font-size:13px;color:{h['bot_text']}'>{richtext.render(d.get('advice', ''))}</div></div>")
    if typ == "note":
        return (f"<div style='margin:6px 18px'>{_head('🧷', '<b>Remembered</b> ' + html.escape(d.get('text', '')), file_link(d.get('path', ''), 'memory file'))}</div>")
    if d.get("type") == "file" and t.get("ok", True):
        rng = (f"lines {d['start']}–{d['end']} of {d['total']}" if d.get("total") else "empty file")
        title = (f"<b>Read</b> {file_link(d['path'])} &nbsp;"
                 f"<span style='color:{h['dim']};font-size:11px'>{rng}</span>")
        return f"<div style='margin:6px 18px'>{_head('📄', title)}</div>"
    kind = t.get("kind", "?")
    col = KIND_COLOR.get(kind, h["dim"])
    res = t.get("result")
    if t.get("awaiting"):
        state = f"<div style='color:{TERM['run']};font-size:11px;margin-top:3px'>● waiting for your approval</div>"
    elif res is None:
        state = f"<div style='color:{h['dim']};font-size:11px;margin-top:3px'>working…</div>"
    else:
        shown = html.escape(res[:600] + ("…" if len(res) > 600 else "")).replace("\n", "<br>")
        state = (f"<div style='color:{h['dim'] if t.get('ok', True) else TERM['bad']};font-size:12px;"
                 f"margin-top:3px'>{'' if t.get('ok', True) else '✗ '}{shown}</div>")
    desc = html.escape((t.get("desc") or "")[:400]).replace("\n", "<br>")
    if d.get("type") == "pending_file" and d.get("path"):
        desc = html.escape(t.get("name", "")) + " " + html.escape(d["path"])
    return (f"<div style='margin:4px 18px;padding:6px 10px;border-left:3px solid {col};"
            f"background:{h['reason_bg']};font-size:12.5px;color:{h['bot_text']}'>"
            f"<b style='color:{col}'>🔧 {html.escape(t.get('name', ''))}</b> "
            f"<span style='color:{h['dim']};font-size:11px'>{KIND_LABEL.get(kind, kind)}</span>"
            f"<br>{desc}{state}{approval_html(t)}</div>")


def changes_html(msg, row=0):
    """'Files changed' summary card shown at the end of an agent turn."""
    h = theme.HTML
    files = msg.get("files") or {}
    rows = "".join(
        f"<tr><td style='padding:2px 10px 2px 0'>{file_link(p) if os.path.exists(p) else html.escape(os.path.basename(p))}</td>"
        f"<td style='padding:2px 10px 2px 0;color:{h['dim']};font-size:11.5px'>{html.escape(what)}</td>"
        f"<td style='color:{h['dim']};font-size:11px'>{html.escape(os.path.dirname(p))}</td></tr>"
        for p, what in files.items())
    undo = ""
    if msg.get("reverts"):
        undo = (_pill("↶ undone", h["dim"]) if msg.get("undone") else
                _link(f"undo:{row}", "↶ Undo all changes from this turn"))
    return (f"<div style='margin:6px 18px 4px 18px;padding:8px 12px;border:1px solid {TERM['border']};"
            f"font-size:12.5px;color:{h['bot_text']}'><b>Files changed ({len(files)})</b> &nbsp;{undo}"
            f"<table cellspacing='0' cellpadding='0' style='margin-top:4px'>{rows}</table></div>")


# ----------------------------------------------------------------------------- file viewer / editor
class _LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        self.ed.paint_gutter(e)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with a line-number gutter and the current line highlighted."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gutter = _LineNumbers(self)
        self.blockCountChanged.connect(lambda _n: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(13)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setStyleSheet(f"QPlainTextEdit{{background:{TERM['bg']};color:#DDE1E7;"
                           f"selection-background-color:#264F78;border:none}}")

    def gutter_width(self):
        return 14 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        r = self.contentsRect()
        self.gutter.setGeometry(QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def _highlight_line(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#151A22"))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    def paint_gutter(self, e):
        p = QPainter(self.gutter)
        p.fillRect(e.rect(), QColor("#0E1116"))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        cur = self.textCursor().blockNumber()
        while block.isValid() and top <= e.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= e.rect().top():
                p.setPen(QColor("#C8CED6" if block.blockNumber() == cur else "#4A5361"))
                p.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(),
                           Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            block = block.next()
            top = bottom

    def goto_line(self, n):
        blk = self.document().findBlockByNumber(max(0, n - 1))
        cur = self.textCursor()
        cur.setPosition(blk.position())
        self.setTextCursor(cur)
        self.centerCursor()


class PygmentsHighlighter(QSyntaxHighlighter):
    """Line-by-line pygments colouring (good enough for viewing/editing; multi-line strings
    are coloured per line)."""

    def __init__(self, doc, path):
        super().__init__(doc)
        self.lexer = None
        try:
            from pygments.lexers import get_lexer_for_filename
            self.lexer = get_lexer_for_filename(path, stripnl=False, ensurenl=False)
        except Exception:
            pass
        self._fmt = {}

    def _format(self, color):
        f = self._fmt.get(color)
        if f is None:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            self._fmt[color] = f
        return f

    def highlightBlock(self, text):
        if not self.lexer or not text:
            return
        from pygments import lex
        pos = 0
        for tt, val in lex(text, self.lexer):
            col = richtext._color(tt)
            if col and val.strip():
                self.setFormat(pos, len(val), self._format(col))
            pos += len(val)


class FileViewer(QDialog):
    """View or edit a file the agent touched (opened from links in the chat)."""
    _open = []                     # keep references so non-modal windows stay alive

    @classmethod
    def show_file(cls, path, parent=None, line=None):
        for w in cls._open:
            if os.path.normcase(w.path) == os.path.normcase(path):
                w.raise_(); w.activateWindow()
                if line:
                    w.ed.goto_line(line)
                return w
        w = cls(path, parent, line)
        cls._open.append(w)
        w.finished.connect(lambda _r, w=w: cls._open.remove(w) if w in cls._open else None)
        w.show()
        return w

    def __init__(self, path, parent=None, line=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(os.path.basename(path) + " — " + os.path.dirname(path))
        self.resize(940, 680)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.path_lbl = QLabel(path)
        self.path_lbl.setStyleSheet("color:#8A93A0")
        self.path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self.path_lbl, 1)
        self.edit_cb = QCheckBox("Edit")
        self.edit_cb.toggled.connect(lambda on: (self.ed.setReadOnly(not on), self.save_btn.setEnabled(on)))
        bar.addWidget(self.edit_cb)
        self.save_btn = QPushButton("Save")
        self.save_btn.setShortcut(QKeySequence.StandardKey.Save)
        self.save_btn.clicked.connect(self.save)
        self.save_btn.setEnabled(False)
        bar.addWidget(self.save_btn)
        for text, fn in (("Reload", self.load), ("Open externally", self.open_external),
                         ("Show in Explorer", self.reveal), ("Copy path", self.copy_path)):
            b = QPushButton(text); b.clicked.connect(fn); bar.addWidget(b)
        lay.addLayout(bar)
        self.ed = CodeEditor(self)
        self.ed.setReadOnly(True)
        lay.addWidget(self.ed, 1)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8A93A0;font-size:11px")
        lay.addWidget(self.status)
        self.hl = PygmentsHighlighter(self.ed.document(), path)
        self.ed.document().modificationChanged.connect(
            lambda m: self.setWindowTitle(("● " if m else "") + os.path.basename(path) + " — "
                                          + os.path.dirname(path)))
        self.load()
        if line:
            self.ed.goto_line(line)

    def load(self):
        if self.ed.document().isModified() and QMessageBox.question(
                self, "Reload", "Discard your unsaved changes?") != QMessageBox.StandardButton.Yes:
            return
        try:
            with open(self.path, "rb") as f:
                raw = f.read(5_000_001)
        except OSError as e:
            self.ed.setPlainText(""); self.status.setText(f"✗ {e}")
            return
        if b"\0" in raw[:4096]:
            self.ed.setPlainText("(binary file — use “Open externally”)")
            self.edit_cb.setEnabled(False)
            return
        self._crlf = b"\r\n" in raw[:65536]
        self.ed.setPlainText(raw[:5_000_000].decode("utf-8", "replace"))
        self.ed.document().setModified(False)
        st = os.stat(self.path)
        self.status.setText(f"{self.ed.blockCount()} lines · {st.st_size:,} bytes · modified "
                            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}"
                            + (" · truncated at 5 MB (read-only)" if len(raw) > 5_000_000 else ""))
        if len(raw) > 5_000_000:
            self.edit_cb.setChecked(False); self.edit_cb.setEnabled(False)

    def save(self):
        text = self.ed.toPlainText()
        if getattr(self, "_crlf", False):
            text = text.replace("\n", "\r\n")
        try:
            at._backup(self.path)
            with open(self.path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        self.ed.document().setModified(False)
        self.status.setText(f"✓ saved {time.strftime('%H:%M:%S')} (previous version kept in agent_trash/backups)")

    def open_external(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def reveal(self):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(self.path)])

    def copy_path(self):
        QApplication.clipboard().setText(self.path)
        self.status.setText("✓ path copied")

    def closeEvent(self, e):
        if self.ed.document().isModified():
            r = QMessageBox.question(self, "Unsaved changes", "Save changes before closing?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                e.ignore(); return
            if r == QMessageBox.StandardButton.Save:
                self.save()
        super().closeEvent(e)


def open_path(path, parent=None, line=None):
    """What clicking a file link does: text files open in the viewer, folders in Explorer,
    everything else (images, PDFs, …) in its default app."""
    if os.path.isdir(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return
    if not os.path.exists(path):
        QMessageBox.information(parent, "Not found", f"{path}\nno longer exists.")
        return
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".pdf", ".docx", ".xlsx", ".pptx",
               ".mp4", ".mp3", ".wav", ".zip", ".exe", ".msi"):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return
    FileViewer.show_file(path, parent, line)


class ApprovalDialog(QDialog):
    def __init__(self, req, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agent wants to act")
        self.setMinimumWidth(520)
        self.decision = "deny"
        lay = QVBoxLayout(self)
        kind = req.get("kind", "?")
        title = QLabel(f"<b>{html.escape(req.get('name', ''))}</b> — "
                       f"<span style='color:{KIND_COLOR.get(kind, '#888')}'>"
                       f"{KIND_LABEL.get(kind, kind)}</span>")
        lay.addWidget(title)
        box = QPlainTextEdit(req.get("desc", ""))
        box.setReadOnly(True)
        box.setMinimumHeight(140)
        lay.addWidget(box)
        for w in req.get("warnings") or []:
            lb = QLabel("⚠ " + w)
            lb.setWordWrap(True)
            lb.setStyleSheet("color:#E0B45F")
            lay.addWidget(lb)
        row = QHBoxLayout()
        for text, val in (("Allow once", "once"), ("Allow for this chat", "session"), ("Deny", "deny")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, v=val: self._done(v))
            row.addWidget(b)
        lay.addLayout(row)

    def _done(self, v):
        self.decision = v
        self.accept()

    def reject(self):
        self.decision = "deny"
        super().reject()


class AgentSettingsDialog(QDialog):
    def __init__(self, cfg, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agent settings")
        self.setMinimumWidth(680)
        cfg = {**copy.deepcopy(DEFAULT_AGENT), **cfg}
        self.cfg = cfg
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.backend = QComboBox()
        for lbl, v in (("llama.cpp (local, fastest)", "llama"), ("Ollama (local)", "ollama"),
                       ("OpenAI API", "openai")):
            self.backend.addItem(lbl, v)
        self.backend.setCurrentIndex(max(0, self.backend.findData(cfg["backend"])))
        form.addRow("Backend", self.backend)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(models)
        self.model.setCurrentText(cfg["model"] or (models[0] if models else ""))
        self.model.setToolTip("Local backends: an installed Ollama model name (llama.cpp reads the "
                              "same file). OpenAI: e.g. gpt-4.1-mini.")
        form.addRow("Model", self.model)
        self.ctx = QSpinBox(); self.ctx.setRange(4096, 65536); self.ctx.setSingleStep(2048)
        self.ctx.setValue(int(cfg["num_ctx"]))
        form.addRow("Context (tokens)", self.ctx)
        self.steps = QSpinBox(); self.steps.setRange(1, 100); self.steps.setValue(int(cfg["max_steps"]))
        form.addRow("Max steps per request", self.steps)
        self.temp = QDoubleSpinBox(); self.temp.setRange(0, 2); self.temp.setSingleStep(0.1)
        self.temp.setValue(float(cfg["temperature"]))
        form.addRow("Temperature", self.temp)
        self.think = QCheckBox("Let the model think (slower)"); self.think.setChecked(bool(cfg["think"]))
        form.addRow("", self.think)
        self.vision = QCheckBox("Model can see images (send screenshots to it)")
        self.vision.setChecked(bool(cfg["vision"]))
        form.addRow("", self.vision)
        self.roots = QPlainTextEdit("\n".join(cfg["roots"]))
        self.roots.setFixedHeight(70)
        form.addRow("Folders the agent may\nread / change (one per line)", self.roots)
        self.auto_read = QCheckBox("Don't ask before reading files / looking at the screen")
        self.auto_read.setChecked(bool(cfg["auto_read"]))
        form.addRow("", self.auto_read)
        self.auto_net = QCheckBox("Don't ask before web search / fetch")
        self.auto_net.setChecked(bool(cfg["auto_net"]))
        form.addRow("", self.auto_net)
        lay.addLayout(form)
        note = QLabel("Writing files, running commands and controlling the mouse/keyboard always "
                      "ask first. Nothing is permanently deleted (it goes to agent_trash).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8A93A0")
        lay.addWidget(note)

        lay.addWidget(QLabel("<b>MCP servers</b>"))
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["On", "Name", "Command or URL", "Arguments", "Policy"])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.setMinimumHeight(130)
        for s in cfg["mcp_servers"]:
            self._add_row(s)
        lay.addWidget(self.tbl)
        br = QHBoxLayout()
        for text, fn in (("Add", lambda: self._add_row({"name": "", "enabled": True})),
                         ("Remove", self._remove), ("Import from Claude…", self._import),
                         ("Test selected", self._test)):
            b = QPushButton(text); b.clicked.connect(fn); br.addWidget(b)
        br.addStretch(1)
        lay.addLayout(br)
        self.test_lbl = QLabel("")
        self.test_lbl.setWordWrap(True)
        lay.addWidget(self.test_lbl)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_row(self, s):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        on.setCheckState(Qt.CheckState.Checked if s.get("enabled", True) else Qt.CheckState.Unchecked)
        self.tbl.setItem(r, 0, on)
        self.tbl.setItem(r, 1, QTableWidgetItem(s.get("name", "")))
        self.tbl.setItem(r, 2, QTableWidgetItem(s.get("url") or s.get("command") or ""))
        self.tbl.setItem(r, 3, QTableWidgetItem(" ".join(str(a) for a in s.get("args") or [])))
        combo = QComboBox()
        for lbl, v in (("Ask each time", "ask"), ("Allow read-only tools", "read"), ("Allow all", "allow")):
            combo.addItem(lbl, v)
        combo.setCurrentIndex(max(0, combo.findData(s.get("policy", "ask"))))
        self.tbl.setCellWidget(r, 4, combo)
        self.tbl.item(r, 1).setData(Qt.ItemDataRole.UserRole, {k: s[k] for k in ("env", "headers", "cwd")
                                                               if k in s})

    def _remove(self):
        for r in sorted({i.row() for i in self.tbl.selectedItems()}, reverse=True):
            self.tbl.removeRow(r)

    def _import(self):
        have = {self.tbl.item(r, 1).text() for r in range(self.tbl.rowCount())}
        found = [s for s in mcp_client.discover_stdio_configs() if s["name"] not in have]
        if not found:
            self.test_lbl.setText("No other MCP servers found in your Claude config files.")
            return
        for s in found:
            self._add_row({**s, "enabled": False})
        self.test_lbl.setText(f"Added {len(found)} server(s), switched off. Tick the ones you want.")

    def _row_cfg(self, r):
        name = self.tbl.item(r, 1).text().strip()
        target = self.tbl.item(r, 2).text().strip()
        args = self.tbl.item(r, 3).text().strip()
        extra = self.tbl.item(r, 1).data(Qt.ItemDataRole.UserRole) or {}
        c = {"name": name, "enabled": self.tbl.item(r, 0).checkState() == Qt.CheckState.Checked,
             "policy": self.tbl.cellWidget(r, 4).currentData(), **extra}
        if target.lower().startswith(("http://", "https://")):
            c.update(transport="http", url=target)
        else:
            import shlex
            c.update(transport="stdio", command=target, args=shlex.split(args, posix=False) if args else [])
        return c

    def _test(self):
        rows = sorted({i.row() for i in self.tbl.selectedItems()}) or [0]
        if not self.tbl.rowCount():
            return
        c = self._row_cfg(rows[0]); c["enabled"] = True
        self.test_lbl.setText(f"Testing {c['name']}…")
        self.repaint()
        m = mcp_client.MCPManager([c])
        errs = m.start_all()
        if errs:
            self.test_lbl.setText("✗ " + "; ".join(errs.values()))
        else:
            names = [t.get("name") for _s, t in m.tools()]
            self.test_lbl.setText(f"✓ {c['name']}: {len(names)} tools — {', '.join(names[:12])}")
        m.close_all()

    def result_cfg(self, enabled):
        servers = [c for c in (self._row_cfg(r) for r in range(self.tbl.rowCount())) if c["name"]]
        return {**self.cfg, "enabled": bool(enabled), "backend": self.backend.currentData(),
                "model": self.model.currentText().strip(), "num_ctx": self.ctx.value(),
                "max_steps": self.steps.value(), "temperature": self.temp.value(),
                "think": self.think.isChecked(), "vision": self.vision.isChecked(),
                "roots": [ln.strip() for ln in self.roots.toPlainText().splitlines() if ln.strip()],
                "auto_read": self.auto_read.isChecked(), "auto_net": self.auto_net.isChecked(),
                "mcp_servers": servers}
