"""Markdown -> Qt rich-text HTML for assistant messages (headings, lists, tables, quotes, task
lists, inline code, syntax-highlighted code blocks with a language label and a Copy link).

Qt's QTextDocument understands only a CSS subset, so everything is inline-styled tables/spans.
Unclosed code fences (a reply still streaming) render as an open code block, which is what we want.
"""
import hashlib
import html as _h
import re
from collections import OrderedDict
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt
from pygments import lex
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.token import Token
from pygments.util import ClassNotFound

MONO = "Cascadia Mono, Consolas, Courier New, monospace"
C = dict(text="#DDE1E7", dim="#7C8794", link="#3FB8A4", code_bg="#12161D", code_head="#1A2029",
         code_border="#2A323D", inline_bg="#1C222B", inline_fg="#E6B673", quote="#3FB8A4",
         rule="#2A323D", th_bg="#1A2029", head="#F0F2F5")

# One Dark-ish token colours
TOK = [(Token.Comment, "#6B7685"), (Token.Keyword.Constant, "#D19A66"), (Token.Keyword, "#C678DD"),
       (Token.Name.Builtin, "#56B6C2"), (Token.Name.Function, "#61AFEF"), (Token.Name.Class, "#E5C07B"),
       (Token.Name.Decorator, "#E5C07B"), (Token.Name.Tag, "#E06C75"), (Token.Name.Attribute, "#D19A66"),
       (Token.Name.Variable, "#E06C75"), (Token.Literal.String, "#98C379"), (Token.Literal.Number, "#D19A66"),
       (Token.Operator.Word, "#C678DD"), (Token.Operator, "#56B6C2"), (Token.Generic.Deleted, "#E06C75"),
       (Token.Generic.Inserted, "#98C379"), (Token.Generic.Heading, "#61AFEF"), (Token.Generic.Subheading, "#56B6C2")]

_md = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False}).enable(
    ["table", "strikethrough"])

_codes = OrderedDict()          # copy-link id -> code text (bounded)
_hl_cache = OrderedDict()

ALIASES = {"sh": "bash", "shell": "bash", "zsh": "bash", "ps": "powershell", "ps1": "powershell",
           "js": "javascript", "ts": "typescript", "py": "python", "yml": "yaml", "md": "markdown",
           "c++": "cpp", "": ""}


def code_for(cid):
    return _codes.get(cid)


# Windows paths: C:\dir\file.txt or C:/dir/file.txt (no spaces; wrap those in a markdown link)
_PATH = r"[A-Za-z]:[\\/](?:[^\s<>\"'|?*`\[\]()]*[^\s<>\"'|?*`\[\]().,;:!])?"
_PATH_FULL = re.compile(rf"^{_PATH}$")
_BARE_PATH = re.compile(rf"(?<![\w/\\]){_PATH}")


def path_url(path):
    """file:/// URL for a local path (the chat opens these in the file viewer)."""
    p = _h.unescape(unquote(path)).replace("\\", "/")
    return "file:///" + quote(p, safe="/:")


def _path_link(text, label_html=None):
    return (f"<a href='{path_url(text)}' style='color:{C['link']};text-decoration:none'>"
            f"{label_html if label_html is not None else _h.escape(text)}</a>")


def _color(tt):
    for base, col in TOK:
        if tt in base:
            return col
    return None


def _lexer(lang, code):
    lang = ALIASES.get(lang.lower().strip(), lang.lower().strip())
    if lang:
        try:
            return get_lexer_by_name(lang)
        except ClassNotFound:
            return None
    return None


def highlight(code, lang):
    key = (lang, code)
    if key in _hl_cache:
        return _hl_cache[key]
    lx = _lexer(lang, code)
    out = []
    if lx is None or len(code) > 20000:
        out.append(_h.escape(code))
    else:
        for tt, val in lex(code, lx):
            esc = _h.escape(val)
            col = _color(tt)
            out.append(f"<span style='color:{col}'>{esc}</span>" if col and val.strip() else esc)
    res = "".join(out)
    _hl_cache[key] = res
    if len(_hl_cache) > 200:
        _hl_cache.popitem(last=False)
    return res


def _code_block(code, lang):
    code = code.rstrip("\n")
    cid = hashlib.sha1(code.encode("utf-8", "replace")).hexdigest()[:10]
    _codes[cid] = code
    if len(_codes) > 400:
        _codes.popitem(last=False)
    label = _h.escape(lang.strip().split()[0]) if lang.strip() else "code"
    return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin:8px 0 12px 0;"
            f"border:1px solid {C['code_border']};background-color:{C['code_bg']}'>"
            f"<tr><td width='85%' style='background-color:{C['code_head']};padding:4px 12px;"
            f"color:{C['dim']};font-size:11px'>{label}</td>"
            f"<td align='right' style='background-color:{C['code_head']};padding:4px 12px;font-size:11px'>"
            f"<a href='copy:{cid}' style='color:{C['link']};text-decoration:none'>Copy</a></td></tr>"
            f"<tr><td colspan='2' style='padding:10px 14px'>"
            f"<pre style='margin:0;white-space:pre-wrap;font-family:{MONO};font-size:13px;color:{C['text']}'>"
            f"{highlight(code, lang)}</pre></td></tr></table>")


def _attr(tok, name):
    return tok.attrGet(name) or ""


def _render_tokens(tokens):
    out, i, n = [], 0, len(tokens)
    list_stack = []                       # True = ordered
    while i < n:
        t = tokens[i]
        ty = t.type
        if ty == "fence" or ty == "code_block":
            out.append(_code_block(t.content, (t.info or "") if ty == "fence" else ""))
        elif ty == "heading_open":
            lvl = int(t.tag[1])
            size = {1: 24, 2: 20, 3: 17, 4: 15, 5: 15, 6: 15}[lvl]
            out.append(f"<div style='margin:{16 if lvl < 3 else 12}px 0 6px 0;font-size:{size}px;"
                       f"font-weight:700;color:{C['head']}'>")
        elif ty == "heading_close":
            out.append("</div>")
        elif ty == "paragraph_open":
            tight = t.hidden
            out.append("" if tight else "<p style='margin:0 0 10px 0'>")
        elif ty == "paragraph_close":
            out.append("" if t.hidden else "</p>")
        elif ty == "inline":
            out.append(_inline(t.children or []))
        elif ty == "bullet_list_open":
            list_stack.append(False); out.append("<ul style='margin:2px 0 10px 0'>")
        elif ty == "ordered_list_open":
            list_stack.append(True); out.append(f"<ol style='margin:2px 0 10px 0' start='{t.attrGet('start') or 1}'>")
        elif ty in ("bullet_list_close", "ordered_list_close"):
            out.append("</ol>" if list_stack.pop() else "</ul>")
        elif ty == "list_item_open":
            out.append("<li style='margin:2px 0'>")
        elif ty == "list_item_close":
            out.append("</li>")
        elif ty == "blockquote_open":
            out.append(f"<table width='100%' cellspacing='0' cellpadding='0' style='margin:6px 0 10px 0'>"
                       f"<tr><td width='3' bgcolor='{C['quote']}'></td><td style='padding:2px 0 2px 12px;"
                       f"color:{C['dim']}'>")
        elif ty == "blockquote_close":
            out.append("</td></tr></table>")
        elif ty == "hr":
            out.append(f"<hr style='margin:14px 0;color:{C['rule']}'>")
        elif ty == "table_open":
            out.append(f"<table cellspacing='0' cellpadding='7' style='margin:8px 0 12px 0;"
                       f"border:1px solid {C['code_border']}' border='1' bordercolor='{C['code_border']}'>")
        elif ty == "table_close":
            out.append("</table>")
        elif ty in ("thead_open", "tbody_open", "thead_close", "tbody_close"):
            pass
        elif ty == "tr_open":
            out.append("<tr>")
        elif ty == "tr_close":
            out.append("</tr>")
        elif ty in ("th_open", "td_open"):
            al = ""
            st = _attr(t, "style")
            m = re.search(r"text-align:(\w+)", st)
            if m:
                al = f" align='{m.group(1)}'"
            bg = f" bgcolor='{C['th_bg']}'" if ty == "th_open" else ""
            out.append(f"<{t.tag}{al}{bg} style='padding:6px 12px;{'font-weight:700;' if t.tag == 'th' else ''}'>")
        elif ty in ("th_close", "td_close"):
            out.append(f"</{t.tag}>")
        i += 1
    return "".join(out)


_TASK = re.compile(r"^\[( |x|X)\]\s")


def _inline(children):
    out, stack = [], []
    first = True
    for t in children:
        ty = t.type
        if ty == "text":
            txt = t.content
            if first:
                m = _TASK.match(txt)
                if m:
                    txt = ("☑ " if m.group(1) != " " else "☐ ") + txt[m.end():]
            out.append(_h.escape(txt))
        elif ty == "code_inline":
            code = (f"<code style='background-color:{C['inline_bg']};color:{C['inline_fg']};"
                    f"font-family:{MONO};font-size:13px'>&nbsp;{_h.escape(t.content)}&nbsp;</code>")
            in_link = any(s == "a" for s in stack)
            out.append(_path_link(t.content.strip(), code)
                       if not in_link and _PATH_FULL.match(t.content.strip()) else code)
        elif ty in ("softbreak", "hardbreak"):
            out.append("<br>")
        elif ty == "strong_open":
            out.append("<b>")
        elif ty == "strong_close":
            out.append("</b>")
        elif ty == "em_open":
            out.append("<i>")
        elif ty == "em_close":
            out.append("</i>")
        elif ty == "s_open":
            out.append("<s>")
        elif ty == "s_close":
            out.append("</s>")
        elif ty == "link_open":
            href = _attr(t, "href")
            if re.match(r"^[A-Za-z]:[\\/]", unquote(href)):          # [name](C:/path/file)
                href = path_url(href)
            out.append(f"<a href='{_h.escape(href, quote=True)}' style='color:{C['link']}'>")
            stack.append("a")
        elif ty == "link_close":
            out.append("</a>")
            if "a" in stack:
                stack.remove("a")
        elif ty == "image":
            out.append(_h.escape(t.content or "[image]"))
        elif ty == "html_inline":
            out.append(_h.escape(t.content))
        first = False
    return "".join(out)


_BARE_URL = re.compile(r"(?<![\"'>=(])(https?://[^\s<>()\]]+[^\s<>()\].,;:!?'\"])")


def render(text):
    """Markdown text -> HTML fragment (no outer margins)."""
    text = re.sub(r"\]\(\s*<?file:///", "](", text or "")     # markdown-it refuses file: links
    # [name](C:/My Folder/x.txt): spaces break a markdown link target unless it is <bracketed>
    text = re.sub(r"\]\(([A-Za-z]:[\\/][^()<>\n]*?\s[^()<>\n]*?)\)",
                  lambda m: "](<" + m.group(1).strip() + ">)", text)
    try:
        body = _render_tokens(_md.parse(text))
        body = _autolink(body)
    except Exception:
        body = _h.escape(text or "").replace("\n", "<br>")
    return body


def _autolink(body):
    """Make bare URLs clickable outside tags/anchors."""
    parts = re.split(r"(<[^>]*>)", body)
    out, in_a, in_pre = [], 0, 0
    for p in parts:
        if p.startswith("<"):
            low = p.lower()
            if low.startswith("<a "):
                in_a += 1
            elif low == "</a>":
                in_a -= 1
            elif low.startswith("<pre"):
                in_pre += 1
            elif low == "</pre>":
                in_pre -= 1
            out.append(p)
        elif in_a or in_pre:
            out.append(p)
        else:
            # URLs first; then paths, only in the text left outside the new URL anchors
            p = _BARE_URL.sub(lambda m: f"<a href='{m.group(1)}' style='color:{C['link']}'>"
                                        f"{m.group(1)}</a>\x00", p)
            out.append("".join(seg if seg.startswith("<a ") else
                               _BARE_PATH.sub(lambda m: _path_link(_h.unescape(m.group(0))), seg)
                               for seg in re.split(r"(<a [^>]*>.*?</a>)\x00", p)))
    return "".join(out)
