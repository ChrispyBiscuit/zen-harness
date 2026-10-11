"""Create real, professionally styled Office documents for the agent.

  pptx  {"theme": "...", "slides": [{"layout": "title|bullets|two_column|stats|quote|section|image|closing", ...}]}
        or Markdown (one # heading per slide, - bullets)
  docx  Markdown (# headings, paragraphs, - / 1. lists, | tables |, **bold**, *italic*, `code`, ``` blocks, > quotes)
  xlsx  {"theme": "...", "sheets": [{"name", "rows", "formats": {"C": "$#,##0.00"}, "charts": [...]}]} or CSV text
  pdf   Markdown -> themed docx -> PDF through Microsoft Word
Themes: corporate (navy), modern (charcoal + teal), minimal (white + black), warm (cream + coral), forest (green).
See guides/*.md (document_guide tool) for the full formats.
"""
import csv
import io
import json
import os
import re

THEMES = {
    #           background  surface    title      text       accent     accent2    muted      font (headings, body)
    "corporate": {"bg": "FFFFFF", "band": "1F3864", "title": "1F3864", "text": "262626", "accent": "2E75B6",
                  "accent2": "F4B183", "muted": "7F7F7F", "on_band": "FFFFFF", "fonts": ("Segoe UI Semibold", "Segoe UI")},
    "modern": {"bg": "1E2329", "band": "13171C", "title": "FFFFFF", "text": "D9DEE4", "accent": "2EC4B6",
               "accent2": "FFB86B", "muted": "8A939E", "on_band": "FFFFFF", "fonts": ("Segoe UI Semibold", "Segoe UI")},
    "minimal": {"bg": "FFFFFF", "band": "111111", "title": "111111", "text": "333333", "accent": "111111",
                "accent2": "9E9E9E", "muted": "8C8C8C", "on_band": "FFFFFF", "fonts": ("Segoe UI Light", "Segoe UI")},
    "warm": {"bg": "FBF6EF", "band": "3D2C2E", "title": "3D2C2E", "text": "4A3F3F", "accent": "E76F51",
             "accent2": "F4A261", "muted": "9C8A84", "on_band": "FBF6EF", "fonts": ("Georgia", "Segoe UI")},
    "forest": {"bg": "F6F8F4", "band": "1B4332", "title": "1B4332", "text": "2D3A32", "accent": "40916C",
               "accent2": "D4A373", "muted": "7A8A80", "on_band": "F6F8F4", "fonts": ("Segoe UI Semibold", "Segoe UI")},
}


class DocError(Exception):
    pass


def theme(name):
    return THEMES.get(str(name or "corporate").lower(), THEMES["corporate"])


# ================================================================== Word
_INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`)")


def _rgb(hexs):
    from docx.shared import RGBColor
    return RGBColor.from_string(hexs)


def _runs(par, text, color=None):
    for part in _INLINE.split(text):
        if not part:
            continue
        if part[:2] in ("**", "__") and part[-2:] == part[:2] and len(part) > 4:
            r = par.add_run(part[2:-2])
            r.bold = True
        elif part[0] in "*_" and part[-1] == part[0] and len(part) > 2:
            r = par.add_run(part[1:-1])
            r.italic = True
        elif part[0] == "`" and part[-1] == "`":
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
        else:
            r = par.add_run(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", part))
        if color:
            r.font.color.rgb = _rgb(color)


def _shade(cell, hexs):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexs)
    tcPr.append(shd)


def make_docx(path, markdown, title=None, theme_name=None, subtitle=None):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    t = theme(theme_name if theme_name != "modern" else "corporate")      # dark theme -> light page for print
    hfont, bfont = t["fonts"]
    doc = Document()
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Pt(64)
    normal = doc.styles["Normal"]
    normal.font.name = bfont
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    for lvl, size in ((1, 18), (2, 14), (3, 12), (4, 11)):
        st = doc.styles[f"Heading {lvl}"]
        st.font.name = hfont
        st.font.size = Pt(size)
        st.font.bold = lvl > 1
        st.font.color.rgb = _rgb(t["title"] if lvl < 3 else t["accent"])
        st.paragraph_format.space_before = Pt(14 if lvl == 1 else 10)
        st.paragraph_format.space_after = Pt(4)
    if title:
        p = doc.add_paragraph()
        r = p.add_run(title)
        r.font.name, r.font.size, r.font.bold = hfont, Pt(26), True
        r.font.color.rgb = _rgb(t["title"])
        if subtitle:
            p2 = doc.add_paragraph()
            r2 = p2.add_run(subtitle)
            r2.font.size, r2.font.color.rgb = Pt(12), _rgb(t["muted"])
        bar = doc.add_paragraph()
        bar.paragraph_format.space_after = Pt(12)
        rb = bar.add_run("━" * 18)
        rb.font.color.rgb = _rgb(t["accent"])
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            p = doc.add_paragraph()
            r = p.add_run("\n".join(block))
            r.font.name, r.font.size = "Consolas", Pt(9)
        elif re.match(r"^#{1,6}\s", s):
            level = len(s) - len(s.lstrip("#"))
            doc.add_heading(s[level:].strip(), level=min(level, 4))
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?\s*:?-{2,}", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            ncol = max(len(r) for r in rows)
            tbl = doc.add_table(rows=len(rows), cols=ncol)
            tbl.style = "Table Grid"
            for ri, row in enumerate(rows):
                for ci in range(ncol):
                    cell = tbl.cell(ri, ci)
                    cell.text = ""
                    _runs(cell.paragraphs[0], row[ci] if ci < len(row) else "", t["on_band"] if ri == 0 else None)
                    if ri == 0:
                        _shade(cell, t["band"])
                        for r in cell.paragraphs[0].runs:
                            r.bold = True
                    elif ri % 2 == 0:
                        _shade(cell, "F2F2F2")
            doc.add_paragraph()
        elif re.match(r"^[-*+]\s", s):
            indent = (len(ln) - len(ln.lstrip())) // 2
            _runs(doc.add_paragraph(style="List Bullet 2" if indent else "List Bullet"), s[2:])
        elif re.match(r"^\d+[.)]\s", s):
            _runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s", "", s))
        elif s.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            _runs(p, s.lstrip("> "), t["muted"])
            for r in p.runs:
                r.italic = True
        elif re.match(r"^(-{3,}|\*{3,})$", s):
            doc.add_page_break()
        elif s:
            para = [s]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|[-*+]\s|\d+[.)]\s|\||>|```)", lines[i + 1].strip()):
                i += 1
                para.append(lines[i].strip())
            _runs(doc.add_paragraph(), " ".join(para))
        i += 1
    # page numbers in the footer
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    fp = doc.sections[0].footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = fp.add_run()
    for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.text = txt
        run._r.append(el)
    doc.save(path)
    return f"{sum(1 for p in doc.paragraphs if p.text.strip())} paragraphs, {len(doc.tables)} tables, theme {theme_name or 'corporate'}"


# ================================================================== Excel
def _num(v):
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        if re.fullmatch(r"-?\d*\.\d+(e-?\d+)?", t, re.I):
            return float(t)
        if re.fullmatch(r"-?\d+(\.\d+)?%", t):
            return float(t[:-1]) / 100
        m = re.fullmatch(r"-?\$(\d+(\.\d+)?)", t)
        if m:
            return float(t.replace("$", ""))
    return v


def make_xlsx(path, content, theme_name=None):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference, ScatterChart, Series
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    data = content
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except ValueError:
            data = {"sheets": [{"name": "Sheet1", "rows": list(csv.reader(io.StringIO(content)))}]}
    if isinstance(data, list):
        data = {"sheets": [{"name": "Sheet1", "rows": data}]}
    t = theme(data.get("theme") or theme_name)
    sheets = data.get("sheets") or []
    if not sheets:
        raise DocError('xlsx content needs {"sheets": [{"name": ..., "rows": [[header...], [row...]]}]} or CSV text')
    wb = Workbook()
    wb.remove(wb.active)
    total, charts = 0, 0
    thin = Side(style="thin", color="D9D9D9")
    for sh in sheets:
        ws = wb.create_sheet(str(sh.get("name") or f"Sheet{len(wb.sheetnames) + 1}")[:31])
        rows = sh.get("rows") or []
        if rows and isinstance(rows[0], dict):
            keys = list(rows[0].keys())
            rows = [keys] + [[r.get(k) for k in keys] for r in rows]
        raw_rows = rows
        for r in rows:
            ws.append([_num(v) for v in r])
        total += max(0, len(rows) - 1)
        ncols = max((len(r) for r in rows), default=0)
        if rows and sh.get("header", True):
            for c in ws[1]:
                c.font = Font(bold=True, color=t["on_band"], name="Segoe UI")
                c.fill = PatternFill("solid", fgColor=t["band"])
                c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            ws.row_dimensions[1].height = 22
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for ri in range(2, len(rows) + 1):
                for ci in range(1, ncols + 1):
                    cell = ws.cell(ri, ci)
                    cell.border = Border(bottom=thin)
                    if ri % 2 == 0:
                        cell.fill = PatternFill("solid", fgColor="F7F7F7")
        # number formats: explicit per column, else inferred from the source text ($ / %)
        fmts = dict(sh.get("formats") or {})
        for ci in range(1, ncols + 1):
            letter = get_column_letter(ci)
            if letter in fmts:
                continue
            col = [str(r[ci - 1]) for r in raw_rows[1:] if len(r) >= ci and r[ci - 1] not in (None, "")]
            if col and all(v.strip().endswith("%") for v in col):
                fmts[letter] = "0.0%"
            elif col and all(v.strip().startswith(("$", "-$")) for v in col):
                fmts[letter] = "$#,##0.00"
        for letter, f in fmts.items():
            for cell in ws[letter][1:]:
                cell.number_format = f
        for ci, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(ci)].width = min(60, max(9, width + 3))
        # native charts
        for ch in sh.get("charts") or []:
            kind = str(ch.get("type") or "bar").lower()
            n = len(rows)
            cat_col = int(ch.get("category_col") or 1)
            val_cols = [int(x) for x in (ch.get("value_cols") or [2])]
            chart = {"bar": BarChart, "column": BarChart, "line": LineChart, "pie": PieChart, "scatter": ScatterChart}.get(kind, BarChart)()
            if kind == "bar":
                chart.type = "bar"
            elif kind == "column":
                chart.type = "col"
            chart.title = ch.get("title") or ""
            chart.style = 10
            chart.height, chart.width = 8, 16
            cats = Reference(ws, min_col=cat_col, min_row=2, max_row=n)
            if kind == "scatter":
                for vc in val_cols:
                    s = Series(Reference(ws, min_col=vc, min_row=1, max_row=n), cats, title_from_data=True)
                    chart.series.append(s)
            else:
                for vc in val_cols:
                    chart.add_data(Reference(ws, min_col=vc, min_row=1, max_row=n), titles_from_data=True)
                chart.set_categories(cats)
            if hasattr(chart, "x_axis") and ch.get("x_title"):
                chart.x_axis.title = ch["x_title"]
            if hasattr(chart, "y_axis") and ch.get("y_title"):
                chart.y_axis.title = ch["y_title"]
            ws.add_chart(chart, ch.get("anchor") or f"{get_column_letter(ncols + 2)}2")
            charts += 1
    wb.save(path)
    return f"{len(sheets)} sheet(s), {total} data rows, {charts} chart(s)"


# ================================================================== PowerPoint
def _pp_color(hexs):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(hexs)


def _mix(hexs, other="FFFFFF", amount=0.1):
    a = [int(hexs[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(other[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b))


def _box(slide, x, y, w, h, fill=None):
    from pptx.enum.shapes import MSO_SHAPE
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.line.fill.background()
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = _pp_color(fill)
    else:
        shp.fill.background()
    shp.shadow.inherit = False
    return shp


def _text(slide, x, y, w, h, text, size, color, font, bold=False, align=None, anchor=None, italic=False):
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = {"middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}.get(anchor, MSO_ANCHOR.TOP)
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}.get(align, PP_ALIGN.LEFT)
        r = p.add_run()
        r.text = re.sub(r"\*\*([^*]+)\*\*", r"\1", str(line))
        r.font.size, r.font.bold, r.font.italic, r.font.name = Pt(size), bold, italic, font
        r.font.color.rgb = _pp_color(color)
    return tb


def _bullets(slide, x, y, w, h, items, t, size=20):
    from pptx.util import Pt
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    for i, b in enumerate(items or []):
        level = 0
        if isinstance(b, dict):
            level, b = int(b.get("level") or 0), b.get("text", "")
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(10)
        p.level = min(level, 3)
        dot = p.add_run()
        dot.text = ("▪  " if level == 0 else "–  ")
        dot.font.color.rgb = _pp_color(t["accent"])
        dot.font.size = Pt(size - (4 * level))
        r = p.add_run()
        r.text = re.sub(r"\*\*([^*]+)\*\*", r"\1", str(b))
        r.font.size = Pt(size - (4 * level))
        r.font.name = t["fonts"][1]
        r.font.color.rgb = _pp_color(t["text"])
    return tb


def _md_slides(md):
    slides, cur = [], None
    for ln in md.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if re.match(r"^#{1,3}\s", s):
            cur = {"layout": "bullets", "title": s.lstrip("#").strip(), "bullets": [], "notes": ""}
            slides.append(cur)
        elif cur is not None and re.match(r"^[-*+]\s|^\d+[.)]\s", s):
            cur["bullets"].append(re.sub(r"^([-*+]|\d+[.)])\s", "", s))
        elif cur is not None and s.lower().startswith("notes:"):
            cur["notes"] = s[6:].strip()
        elif cur is not None and s:
            cur["bullets"].append(s)
    return slides


def make_pptx(path, content, title=None, theme_name=None):
    from pptx import Presentation
    from pptx.util import Emu, Inches, Pt
    data = content
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except ValueError:
            data = {"slides": _md_slides(content)}
    if isinstance(data, list):
        data = {"slides": data}
    slides = list(data.get("slides") or [])
    if not slides:
        raise DocError('pptx content needs {"slides": [{"layout": "...", "title": ..., ...}]} or Markdown with # per slide '
                       "(document_guide('powerpoint') has the full format)")
    t = theme(data.get("theme") or theme_name)
    hfont, bfont = t["fonts"]
    title = title or data.get("title")
    if title and not any(s.get("layout") == "title" for s in slides):
        slides.insert(0, {"layout": "title", "title": title, "subtitle": data.get("subtitle", "")})
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    M = Inches(0.7)
    blank = prs.slide_layouts[6]
    total = len(slides)
    for idx, sd in enumerate(slides, 1):
        lay = str(sd.get("layout") or ("bullets" if sd.get("bullets") else "section")).lower()
        s = prs.slides.add_slide(blank)
        bg = t["band"] if lay in ("title", "section", "closing") else t["bg"]
        _box(s, 0, 0, W, H, bg)
        if lay in ("title", "closing"):
            _box(s, int(W * 0.66), 0, W - int(W * 0.66), H, _mix(t["band"], "FFFFFF", 0.07))      # side panel
            _box(s, int(W * 0.66), 0, Inches(0.08), H, t["accent"])
            _box(s, M, Inches(3.55), Inches(1.2), Emu(54864), t["accent"])
            _text(s, M, Inches(1.6), int(W * 0.66) - M - Inches(0.4), Inches(1.9), sd.get("title", ""), 44, t["on_band"], hfont, True, anchor="bottom")
            if sd.get("subtitle"):
                _text(s, M, Inches(3.8), int(W * 0.66) - M - Inches(0.4), Inches(1.2), sd["subtitle"], 20, t["accent2"], bfont)
            if sd.get("footer") or data.get("footer"):
                _text(s, M, H - Inches(0.9), W - 2 * M, Inches(0.5), sd.get("footer") or data.get("footer"), 12, t["muted"], bfont)
        elif lay == "section":
            _text(s, M, Inches(2.6), W - 2 * M, Inches(1.4), sd.get("title", ""), 40, t["on_band"], hfont, True, anchor="middle")
            _box(s, M, Inches(4.1), Inches(0.9), Emu(45720), t["accent"])
            if sd.get("subtitle"):
                _text(s, M, Inches(4.3), W - 2 * M, Inches(1.0), sd["subtitle"], 18, t["accent2"], bfont)
        else:
            _box(s, 0, 0, Inches(0.18), H, t["accent"])                       # accent rail
            _text(s, M, Inches(0.45), W - 2 * M, Inches(0.9), sd.get("title", ""), 30, t["title"], hfont, True)
            top, body_h = Inches(1.55), H - Inches(2.3)
            if lay == "two_column":
                colw = (W - 2 * M - Inches(0.5)) / 2
                for j, side in enumerate(("left", "right")):
                    x = M + j * (colw + Inches(0.5))
                    _box(s, x - Inches(0.15), top - Inches(0.15), colw + Inches(0.3), body_h + Inches(0.1),
                         _mix(t["bg"], "000000" if t["bg"].upper() in ("FFFFFF", "FBF6EF", "F6F8F4") else "FFFFFF", 0.05))
                    _box(s, x - Inches(0.15), top - Inches(0.15), colw + Inches(0.3), Emu(36576), t["accent"] if j == 0 else t["accent2"])
                    col = sd.get(side) or {}
                    if isinstance(col, list):
                        col = {"bullets": col}
                    if col.get("heading"):
                        _text(s, x, top, colw, Inches(0.5), col["heading"], 18, t["accent"], hfont, True)
                    _bullets(s, x, top + Inches(0.6 if col.get("heading") else 0), colw, body_h, col.get("bullets"), t, 18)
            elif lay == "stats":
                stats = sd.get("stats") or []
                n = max(1, len(stats))
                cw = (W - 2 * M) / n
                for j, st in enumerate(stats):
                    x = M + j * cw
                    _box(s, x + Inches(0.1), Inches(2.3), cw - Inches(0.2), Inches(2.6),
                         "F2F2F2" if t["bg"].upper() in ("FFFFFF", "FBF6EF", "F6F8F4") else "2A3038")
                    _text(s, x + Inches(0.1), Inches(2.5), cw - Inches(0.2), Inches(1.3), str(st.get("value", "")), 48,
                          t["accent"], hfont, True, align="center", anchor="middle")
                    _text(s, x + Inches(0.3), Inches(3.8), cw - Inches(0.6), Inches(1.0), str(st.get("label", "")), 16,
                          t["text"], bfont, align="center")
                if sd.get("caption"):
                    _text(s, M, Inches(5.4), W - 2 * M, Inches(0.8), sd["caption"], 16, t["muted"], bfont, align="center")
            elif lay == "quote":
                _text(s, M + Inches(0.6), Inches(2.0), W - 2 * M - Inches(1.2), Inches(2.6), f"“{sd.get('quote', '')}”", 30,
                      t["title"], hfont, italic=True, anchor="middle")
                if sd.get("author"):
                    _text(s, M + Inches(0.6), Inches(4.8), W - 2 * M - Inches(1.2), Inches(0.6), f"— {sd['author']}", 18,
                          t["accent"], bfont)
            elif lay == "image" and sd.get("image") and os.path.isfile(sd["image"]):
                has_text = bool(sd.get("bullets"))
                img_w = (W - 2 * M) * (0.55 if has_text else 1.0)
                pic = s.shapes.add_picture(sd["image"], M, top, width=img_w)
                if pic.height > body_h:
                    ratio = body_h / pic.height
                    pic.height, pic.width = int(pic.height * ratio), int(pic.width * ratio)
                if has_text:
                    _bullets(s, M + img_w + Inches(0.4), top, W - 2 * M - img_w - Inches(0.4), body_h, sd["bullets"], t, 18)
            else:
                _bullets(s, M, top, W - 2 * M, body_h, sd.get("bullets") or ([sd["body"]] if sd.get("body") else []), t,
                         int(sd.get("font_size") or 22))
            _text(s, W - M - Inches(1.5), H - Inches(0.55), Inches(1.5), Inches(0.4), f"{idx} / {total}", 11, t["muted"],
                  bfont, align="right")
        if sd.get("notes"):
            s.notes_slide.notes_text_frame.text = str(sd["notes"])
    prs.save(path)
    return f"{len(prs.slides)} slides, theme {data.get('theme') or theme_name or 'corporate'}, 16:9"


# ================================================================== PDF
def make_pdf(path, markdown, title=None, theme_name=None):
    tmp = os.path.splitext(path)[0] + ".__tmp.docx"
    make_docx(tmp, markdown, title, theme_name)
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        os.remove(tmp)
        raise DocError("PDF needs Microsoft Word (pywin32 not available); create a docx instead")
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        d = word.Documents.Open(os.path.abspath(tmp), ReadOnly=True)
        d.SaveAs2(os.path.abspath(path), FileFormat=17)          # wdFormatPDF
        d.Close(False)
    except Exception as e:
        raise DocError(f"PDF conversion through Word failed: {e}")
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
    return f"PDF via Word, theme {theme_name or 'corporate'}"


def create(path, content, kind=None, title=None, theme_name=None, subtitle=None):
    kind = (kind or os.path.splitext(path)[1].lstrip(".")).lower()
    kind = {"doc": "docx", "word": "docx", "excel": "xlsx", "xls": "xlsx", "powerpoint": "pptx", "ppt": "pptx",
            "slides": "pptx", "deck": "pptx", "presentation": "pptx"}.get(kind, kind)
    if kind not in ("docx", "xlsx", "pptx", "pdf"):
        raise DocError("type must be docx, xlsx, pptx or pdf")
    if theme_name and str(theme_name).lower() not in THEMES:
        raise DocError(f"unknown theme '{theme_name}'; use one of: {', '.join(THEMES)}")
    if not path.lower().endswith("." + kind):
        path = os.path.splitext(path)[0] + "." + kind
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    if kind == "docx":
        info = make_docx(path, text, title, theme_name, subtitle)
    elif kind == "xlsx":
        info = make_xlsx(path, content, theme_name)
    elif kind == "pptx":
        info = make_pptx(path, content, title, theme_name)
    else:
        info = make_pdf(path, text, title, theme_name)
    return path, kind, info
