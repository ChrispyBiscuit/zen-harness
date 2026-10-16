"""create_document: one theme spec styles docx, pptx, xlsx and pdf; tables, charts and layouts come out native."""
import json
import os
import shutil
import sys
import tempfile
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import doc_create  # noqa: E402
import doc_pdf  # noqa: E402
import doc_themes as dt  # noqa: E402

REPORT = """# Summary
Revenue grew **12%** to $4.2M.

> **Bottom line:** the South launch paid off.

| Region | Revenue ($M) | Growth |
|---|---|---|
| North | 1.9 | 8% |
| South | 2.3 | 16% |
| Total | 4.2 | 12% |

```chart
type: bar
title: Revenue by region
```

- first point
  - sub point
1. step one
"""


@pytest.fixture()
def tmp():
    d = tempfile.mkdtemp(prefix="zz-docs-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _theme_xml(path, part):
    with zipfile.ZipFile(path) as z:
        return z.read(part).decode("utf-8")


# ------------------------------------------------------------------ shared pieces
def test_every_theme_has_the_full_spec():
    keys = {"label", "dark", "bg", "surface", "band", "on_band", "title", "text", "accent", "accent2", "muted", "line",
            "palette", "fonts"}
    assert {"corporate", "slate", "modern", "minimal", "warm", "forest"} <= set(dt.THEMES)
    for name, t in dt.THEMES.items():
        assert keys <= set(t), name
        assert len(t["palette"]) == 6 and len(t["fonts"]) == 2
        assert dt.paper(t)["bg"] == "FFFFFF"           # documents always print on white
    assert dt.paper(dt.THEMES["modern"])["title"] != "FFFFFF"


def test_markdown_blocks_tables_align_numbers_and_charts_use_the_table_above():
    blocks = dt.parse_blocks(REPORT)
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["heading", "para", "quote", "table", "chart", "bullets", "numbered"]
    table = blocks[3]
    assert table["align"] == ["left", "right", "right"]
    chart = blocks[4]["spec"]
    assert chart["type"] == "bar" and chart["labels"] == ["North", "South"]       # the Total row is not charted
    assert [s["name"] for s in chart["series"]] == ["Revenue ($M)", "Growth"]
    assert blocks[5]["items"] == [(0, "first point"), (1, "sub point")]


def test_chart_spec_json_and_numbers():
    spec = dt.parse_chart_spec('{"type": "line", "labels": ["a", "b"], "values": ["1,200", "$3k"]}')
    assert spec["series"][0]["values"] == [1200.0, 3000.0]
    assert dt.to_float("(5%)") == -5.0 and dt.is_number("-3%") and not dt.is_number("Q3")


def test_office_theme_part_gets_palette_and_fonts():
    from openpyxl.writer.theme import theme_xml
    xml = dt.office_theme_xml(theme_xml, dt.THEMES["warm"]).decode()
    assert '<a:accent1><a:srgbClr val="E76F51"/></a:accent1>' in xml
    assert 'typeface="Georgia"' in xml


# ------------------------------------------------------------------ Word
def test_docx_is_themed_with_native_table_chart_and_callout(tmp):
    from docx import Document
    path, kind, info = doc_create.create(os.path.join(tmp, "r.docx"), REPORT, None, "Q3 Review", "warm", "For the team")
    assert kind == "docx" and "1 tables, 1 charts" in info and "theme warm" in info
    doc = Document(path)
    assert doc.paragraphs[1].style.name == "Title" and doc.paragraphs[1].text == "Q3 Review"
    t = doc.tables[0]
    assert t.cell(0, 0)._tc.xml.count('w:fill="3D2C2E"') == 1                       # header in the theme band colour
    assert t.cell(1, 1).paragraphs[0].alignment == 2                                # numbers right-aligned
    assert all(r.bold for r in t.cell(3, 0).paragraphs[0].runs)                     # Total row bold
    assert len(doc.inline_shapes) == 1                                              # the chart picture
    assert any('w:left' in p._p.xml and "E76F51" in p._p.xml for p in doc.paragraphs)   # callout bar
    assert 'val="E76F51"' in _theme_xml(path, "word/theme/theme1.xml")
    assert doc.styles["Heading 1"].font.name == "Georgia"


def test_docx_missing_image_is_a_warning_not_a_crash(tmp):
    _, _, info = doc_create.create(os.path.join(tmp, "r.docx"), "# A\n![x](C:/nope/none.png)\ntext", None, "T")
    assert "image not found" in info


# ------------------------------------------------------------------ PowerPoint
DECK = {"theme": "slate", "title": "Q3", "slides": [
    {"layout": "stats", "title": "Numbers", "stats": [{"value": "$4.2M", "label": "revenue"}, {"value": "23%", "label": "m"}]},
    {"layout": "chart", "title": "Chart", "type": "column", "labels": ["A", "B"],
     "series": [{"name": "Q2", "values": [1, 2]}, {"name": "Q3", "values": [2, 3]}]},
    {"layout": "table", "title": "Table", "rows": [["Region", "Rev"], ["North", "1.9"], ["Total", "1.9"]]},
    {"layout": "cards", "title": "Cards", "cards": [{"title": "One", "text": "a"}, {"title": "Two", "text": "b"}]},
    {"layout": "timeline", "title": "Plan", "steps": [{"label": "Oct", "text": "x"}, {"label": "Nov", "text": "y"}]},
    {"layout": "bullets", "title": "Points", "bullets": ["Speed: twice as fast", "Cost: half"]},
    {"layout": "section", "title": "Next"},
    {"layout": "closing", "title": "Thanks"},
]}


def test_pptx_layouts_make_native_charts_and_tables(tmp):
    from pptx import Presentation
    path, _, info = doc_create.create(os.path.join(tmp, "d.pptx"), json.dumps(DECK))
    assert info.startswith("9 slides") and "theme slate" in info
    prs = Presentation(path)
    slides = list(prs.slides)
    assert any(sh.has_chart for sh in slides[2].shapes)
    chart = next(sh.chart for sh in slides[2].shapes if sh.has_chart)
    assert len(chart.plots[0].series) == 2 and chart.has_legend
    table = next(sh.table for sh in slides[3].shapes if sh.has_table)
    assert table.cell(0, 0).text == "Region" and table.cell(2, 0).text_frame.paragraphs[0].runs[0].font.bold
    texts = ["\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in slides]
    assert "$4.2M" in texts[1] and "Oct" in texts[5] and "One" in texts[4]
    assert "Speed" in texts[6]                                     # 'Label: text' bullets became cards
    assert "01" in texts[7]                                        # numbered section divider
    assert 'val="4F46E5"' in _theme_xml(path, "ppt/theme/theme1.xml")


def test_pptx_markdown_still_works_and_tables_become_table_slides(tmp):
    from pptx import Presentation
    path, _, _ = doc_create.create(os.path.join(tmp, "m.pptx"), "# One\n- a\n- b\n# Two\n| x | y |\n|---|---|\n| 1 | 2 |")
    slides = list(Presentation(path).slides)
    assert len(slides) == 2 and any(sh.has_table for sh in slides[1].shapes)


def test_long_titles_shrink_to_fit():
    big = doc_create._fit(["A short title"], 914400 * 11, 914400 * 0.95, 32)
    small = doc_create._fit(["A very long slide title that goes on and on " * 3], 914400 * 11, 914400 * 0.95, 32)
    assert big == 32 and small < big


def test_unknown_theme_is_refused(tmp):
    with pytest.raises(doc_create.DocError):
        doc_create.create(os.path.join(tmp, "x.pptx"), json.dumps({"theme": "neon", "slides": [{"title": "a"}]}))


# ------------------------------------------------------------------ Excel
BOOK = {"theme": "forest", "sheets": [{"name": "Sales", "title": "Revenue", "subtitle": "USD", "totals": True,
                                       "rows": [["Region", "Q2", "Q3", "Growth", "Year", "Date"],
                                                ["North", "$1,820", "$1,900", "8%", "2025", "2026-10-01"],
                                                ["South", "$1,400.50", "$2,300", "16%", "2026", "2026-10-02"]],
                                       "charts": [{"type": "column", "title": "Rev", "value_cols": [2, 3]}]}]}


def test_xlsx_has_title_table_formats_totals_chart_and_print_setup(tmp):
    from openpyxl import load_workbook
    path, _, info = doc_create.create(os.path.join(tmp, "b.xlsx"), json.dumps(BOOK))
    assert "2 data rows, 1 chart(s), theme forest" in info
    wb = load_workbook(path)
    ws = wb["Sales"]
    assert ws["A1"].value == "Revenue" and ws["A2"].value == "USD"
    assert ws["A4"].value == "Region" and ws["A4"].fill.fgColor.rgb.endswith("1B4332")
    assert ws.freeze_panes == "A5"
    assert list(ws.tables) == ["Sales"] and ws.tables["Sales"].ref == "A4:F6"
    assert ws["B5"].value == 1820 and ws["B5"].number_format.startswith("$#,##0.00")   # one decimal value -> cents
    assert ws["C5"].number_format.startswith("$#,##0;")
    assert ws["D5"].value == 0.08 and ws["D5"].number_format == "0%"
    assert ws["E5"].number_format == "0"                                               # years: no thousands comma
    assert ws["F5"].number_format == "yyyy-mm-dd"
    assert ws["A7"].value == "Total" and ws["B7"].value == "=SUM(B5:B6)" and ws["B7"].font.bold
    assert ws["D7"].value is None                                                      # percentages are not summed
    assert ws["B5"].alignment.horizontal == "right"
    assert len(ws._charts) == 1
    assert ws.page_setup.fitToWidth == 1 and ws.sheet_properties.pageSetUpPr.fitToPage
    assert 'val="40916C"' in _theme_xml(path, "xl/theme/theme1.xml")


def test_xlsx_csv_and_headerless_sheets(tmp):
    from openpyxl import load_workbook
    path, _, info = doc_create.create(os.path.join(tmp, "c.xlsx"), "name,score\nann,10\nbob,12")
    ws = load_workbook(path).active
    assert ws["B2"].value == 10 and ws["A1"].value == "name"
    path, _, _ = doc_create.create(os.path.join(tmp, "n.xlsx"), json.dumps(
        {"sheets": [{"name": "Notes", "header": False, "rows": [["just a note"]]}]}))
    assert load_workbook(path)["Notes"]["A1"].value == "just a note"


# ------------------------------------------------------------------ PDF
def test_pdf_html_has_cover_table_classes_and_inline_svg_chart():
    doc, counts, warnings = doc_pdf.to_html(REPORT, "Q3 Review", "Sub", dt.theme("corporate"))
    assert counts == {"tables": 1, "charts": 1} and not warnings
    assert '<div class="cover">' in doc and "Q3 Review" in doc
    assert '<td class="r">1.9</td>' in doc and '<tr class="total">' in doc
    assert "<svg" in doc and "<blockquote>" in doc
    assert "#1F3864" in doc                                            # theme band colour in the CSS


def _edge_available():
    return os.path.exists(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") or \
        os.path.exists(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


@pytest.mark.slow
@pytest.mark.skipif(not _edge_available(), reason="needs Microsoft Edge")
def test_pdf_prints_through_edge(tmp):
    from pypdf import PdfReader
    path, kind, info = doc_create.create(os.path.join(tmp, "r.pdf"), REPORT, None, "Q3 Review", "slate")
    assert kind == "pdf" and "printed by Edge" in info and "1 charts" in info
    text = PdfReader(path).pages[0].extract_text()
    assert "Q3 Review" in text and "North" in text and "Page 1 of" in text
