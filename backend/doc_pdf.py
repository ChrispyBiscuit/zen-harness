"""PDF documents: the docx Markdown laid out as HTML + CSS in the theme, printed to PDF by Microsoft Edge
(headless, through Playwright — both already ship with the app, nothing to download). If Edge can't start,
Playwright's own Chromium is tried, then Microsoft Word (docx -> PDF) as the last resort.
"""
import base64
import html
import os
import re
import threading

import doc_themes as dt


class PdfError(Exception):
    pass


_INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def inline(text):
    out = []
    for part in _INLINE.split(str(text)):
        if not part:
            continue
        if part[:2] in ("**", "__") and part[-2:] == part[:2] and len(part) > 4:
            out.append(f"<strong>{html.escape(part[2:-2])}</strong>")
        elif part[0] in "*_" and part[-1] == part[0] and len(part) > 2:
            out.append(f"<em>{html.escape(part[1:-1])}</em>")
        elif part[0] == "`" and part[-1] == "`":
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
        elif part.startswith("[") and "](" in part:
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            out.append(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>')
        else:
            out.append(html.escape(part))
    return "".join(out)


def css(t):
    p = dt.paper(t)
    hfont, bfont = p["fonts"]
    hweight = 600 if "Semibold" in hfont else 700
    hfam = hfont.replace(" Semibold", "")
    c = {k: "#" + v for k, v in p.items() if isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", v)}
    return f"""
@page {{ size: Letter; margin: 0.85in 0.9in 0.9in 0.9in; }}
* {{ box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ font-family: "{bfont}", "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.5; color: {c['text']};
       margin: 0; font-variant-numeric: lining-nums; }}
h1, h2, h3, h4, .title {{ font-family: "{hfam}", "Segoe UI", sans-serif; font-weight: {hweight}; color: {c['title']};
       break-after: avoid; line-height: 1.25; }}
.cover {{ margin: 0 0 22pt 0; padding-bottom: 14pt; border-bottom: 1px solid {c['line']}; }}
.cover .bar {{ width: 44pt; height: 5pt; background: {c['accent']}; border-radius: 3pt; margin-bottom: 14pt; }}
.cover .title {{ font-size: 28pt; margin: 0; letter-spacing: -0.3pt; }}
.cover .subtitle {{ font-size: 13pt; color: {c['muted']}; margin-top: 6pt; }}
h1 {{ font-size: 16pt; margin: 22pt 0 8pt; padding-bottom: 4pt; border-bottom: 1px solid {c['line']}; }}
h2 {{ font-size: 13pt; margin: 16pt 0 5pt; }}
h3 {{ font-size: 10pt; margin: 12pt 0 4pt; color: {c['accent']}; text-transform: uppercase; letter-spacing: 0.8pt; }}
h4 {{ font-size: 10.5pt; margin: 10pt 0 3pt; color: {c['muted']}; }}
p {{ margin: 0 0 7pt; orphans: 3; widows: 3; }}
ul, ol {{ margin: 0 0 8pt; padding-left: 18pt; }}
li {{ margin: 0 0 3pt; }}
ul li::marker {{ color: {c['accent']}; }}
ol li::marker {{ color: {c['accent']}; font-weight: 600; }}
a {{ color: {c['accent']}; text-decoration: none; border-bottom: 1px solid {c['line']}; }}
code {{ font-family: Consolas, monospace; font-size: 9pt; background: {c['surface']}; padding: 1pt 3pt; border-radius: 2pt; }}
pre {{ font-family: Consolas, monospace; font-size: 8.8pt; background: {c['surface']}; padding: 9pt 11pt; border-radius: 4pt;
       white-space: pre-wrap; margin: 0 0 10pt; break-inside: avoid; }}
table {{ width: 100%; border-collapse: collapse; margin: 6pt 0 14pt; font-size: 9.5pt; break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
th {{ background: {c['band']}; color: {c['on_band']}; font-family: "{hfam}", sans-serif; font-weight: {hweight};
      text-align: left; padding: 6pt 9pt; }}
td {{ padding: 5pt 9pt; border-bottom: 1px solid {c['line']}; }}
tbody tr:nth-child(even) td {{ background: {c['surface']}; }}
tbody tr:last-child td {{ border-bottom: 1.5px solid {c['title']}; }}
tr.total td {{ font-weight: 700; color: {c['title']}; background: none !important; border-top: 1.5px solid {c['title']};
              border-bottom: 1.5px solid {c['title']}; }}
.r {{ text-align: right; font-variant-numeric: tabular-nums; }}
.c {{ text-align: center; }}
blockquote {{ margin: 8pt 0 12pt; padding: 9pt 13pt; background: #{dt.tint(p, 0.9)}; border-left: 4pt solid {c['accent']};
             border-radius: 0 4pt 4pt 0; break-inside: avoid; }}
figure {{ margin: 6pt 0 14pt; text-align: center; break-inside: avoid; }}
figure svg, figure img {{ max-width: 100%; height: auto; }}
figcaption {{ font-size: 9pt; color: {c['muted']}; font-style: italic; margin-top: 3pt; }}
.pb {{ break-after: page; }}
"""


def to_html(markdown, title=None, subtitle=None, t=None):
    t = t or dt.theme(None)
    out, warnings, counts = [], [], {"tables": 0, "charts": 0}
    if title:
        out.append(f'<div class="cover"><div class="bar"></div><h1 class="title" style="border:0;padding:0">'
                   f'{inline(title)}</h1>' + (f'<div class="subtitle">{inline(subtitle)}</div>' if subtitle else "") +
                   "</div>")
    for b in dt.parse_blocks(markdown):
        k = b["kind"]
        if k == "heading":
            out.append(f"<h{b['level']}>{inline(b['text'])}</h{b['level']}>")
        elif k == "para":
            out.append(f"<p>{inline(b['text'])}</p>")
        elif k in ("bullets", "numbered"):
            tag = "ol" if k == "numbered" else "ul"
            html_items, depth = [], 0
            for level, text in b["items"]:
                while depth < level:
                    html_items.append(f"<{tag}>")
                    depth += 1
                while depth > level:
                    html_items.append(f"</{tag}>")
                    depth -= 1
                html_items.append(f"<li>{inline(text)}</li>")
            html_items.extend([f"</{tag}>"] * depth)
            out.append(f"<{tag}>{''.join(html_items)}</{tag}>")
        elif k == "table":
            rows, align = b["rows"], b["align"]
            cls = {"right": ' class="r"', "center": ' class="c"', "left": ""}
            head = "".join(f"<th{cls[align[i]]}>{inline(c)}</th>" for i, c in enumerate(rows[0]))
            body = []
            for r in rows[1:]:
                tr = ' class="total"' if dt.is_total_row(r) else ""
                cells = "".join(f"<td{cls[align[i]]}>{inline(re.sub(r'^[*]{2}(.*)[*]{2}$', r'\1', c))}</td>"
                                for i, c in enumerate(r))
                body.append(f"<tr{tr}>{cells}</tr>")
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>")
            counts["tables"] += 1
        elif k == "quote":
            out.append(f"<blockquote>{inline(b['text'])}</blockquote>")
        elif k == "code":
            out.append(f"<pre>{html.escape(b['text'])}</pre>")
        elif k == "chart":
            try:
                svg = dt.chart_image(b["spec"], t, fmt="svg")
            except Exception as e:
                warnings.append(f"chart skipped ({e})")
                continue
            svg = svg[svg.find("<svg"):]
            out.append(f"<figure>{svg}</figure>")
            counts["charts"] += 1
        elif k == "image":
            path = b["path"]
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
                mime = {"jpg": "jpeg", "svg": "svg+xml"}.get(ext, ext)
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                cap = f"<figcaption>{inline(b['alt'])}</figcaption>" if b["alt"] else ""
                out.append(f'<figure><img src="data:image/{mime};base64,{data}" alt="{html.escape(b["alt"])}">{cap}</figure>')
            else:
                warnings.append(f"image not found: {path}")
        elif k == "pagebreak":
            out.append('<div class="pb"></div>')
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title or "")}</title>'
           f"<style>{css(t)}</style></head><body>{''.join(out)}</body></html>")
    return doc, counts, warnings


def _footer(t, title):
    p = dt.paper(t)
    left = html.escape(title or "")
    return (f'<div style="font-family:\'{p["fonts"][1]}\',sans-serif;font-size:7.5pt;color:#{p["muted"]};width:100%;'
            f'padding:0 0.9in;display:flex;justify-content:space-between"><span>{left}</span>'
            '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')


def _browser_pdf(doc, path, footer):
    """Print HTML to PDF with Edge (or Playwright's Chromium). Runs on its own thread: the sync Playwright API
    refuses to run on a thread that has an asyncio loop, which tool calls may have."""
    result = {}

    def run():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser, errors = None, []
                for opts in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                    try:
                        browser = pw.chromium.launch(headless=True, **opts)
                        break
                    except Exception as e:
                        errors.append(f"{opts.get('channel', 'chromium')}: {str(e).splitlines()[0][:120]}")
                if browser is None:
                    raise PdfError("no browser to print with (" + "; ".join(errors) + ")")
                try:
                    page = browser.new_page()
                    page.set_content(doc, wait_until="load", timeout=60000)
                    page.pdf(path=path, prefer_css_page_size=True, print_background=True, display_header_footer=True,
                             header_template="<div></div>", footer_template=footer)
                finally:
                    browser.close()
            result["ok"] = True
        except Exception as e:
            result["error"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(120)
    if th.is_alive():
        raise PdfError("printing the PDF timed out")
    if "error" in result:
        raise result["error"]


def _word_pdf(path, markdown, title, theme_name, subtitle):
    import doc_create
    tmp = os.path.splitext(path)[0] + ".__tmp.docx"
    doc_create.make_docx(tmp, markdown, title, theme_name, subtitle)
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        os.remove(tmp)
        raise PdfError("PDF needs Microsoft Edge or Word and neither could be used; create a docx instead")
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        d = word.Documents.Open(os.path.abspath(tmp), ReadOnly=True)
        d.SaveAs2(os.path.abspath(path), FileFormat=17)          # wdFormatPDF
        d.Close(False)
    except Exception as e:
        raise PdfError(f"PDF conversion through Word failed: {e}")
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
        try:
            os.remove(tmp)
        except OSError:
            pass


def make_pdf(path, markdown, title=None, theme_name=None, subtitle=None):
    t = dt.theme(theme_name)
    doc, counts, warnings = to_html(markdown, title, subtitle, t)
    engine = "Edge"
    try:
        _browser_pdf(doc, os.path.abspath(path), _footer(t, title))
    except Exception as e:
        engine = "Word"
        warnings.append(f"browser printing failed ({str(e)[:160]}), used Word")
        _word_pdf(path, markdown, title, theme_name, subtitle)
    pages = 0
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(path).pages)
    except Exception:
        pass
    info = (f"{pages} page(s), {counts['tables']} tables, {counts['charts']} charts, theme {dt.theme_name(theme_name)}, "
            f"printed by {engine}")
    return info + ("; " + "; ".join(warnings) if warnings else "")
