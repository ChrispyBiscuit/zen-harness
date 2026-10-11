"""Text extraction for documents the user sends (or the agent finds): PDF, Word, Excel, PowerPoint,
CSV and plain text/code. Legacy Office formats (.doc .ppt .xls .rtf .odt) are converted with the
installed Microsoft Office (COM, via PowerShell) when the Python readers can't open them.

extract(path, start, end, sheet, max_chars) -> dict(text, kind, unit, total, first, last, truncated, note)
"""
import csv
import hashlib
import io
import os
import subprocess
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ATTACH_DIR = os.path.join(APP_DIR, "agent_attachments")
CONVERT_DIR = os.path.join(tempfile.gettempdir(), "lws_doc_convert")

PDF = {".pdf"}
WORD = {".docx", ".docm", ".dotx"}
WORD_LEGACY = {".doc", ".rtf", ".odt", ".wpd"}
EXCEL = {".xlsx", ".xlsm", ".xltx"}
EXCEL_LEGACY = {".xls", ".ods"}
PPT = {".pptx", ".pptm", ".ppsx"}
PPT_LEGACY = {".ppt", ".pps", ".odp"}
TABLE = {".csv", ".tsv"}
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
TEXT = {".txt", ".md", ".markdown", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml",
        ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".sql", ".ps1", ".bat",
        ".sh", ".c", ".h", ".cpp", ".cs", ".java", ".go", ".rs", ".rb", ".php", ".r", ".tex", ".srt"}
SUPPORTED = PDF | WORD | WORD_LEGACY | EXCEL | EXCEL_LEGACY | PPT | PPT_LEGACY | TABLE | IMAGE | TEXT


class DocError(Exception):
    pass


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    for k, s in (("pdf", PDF), ("word", WORD | WORD_LEGACY), ("excel", EXCEL | EXCEL_LEGACY),
                 ("powerpoint", PPT | PPT_LEGACY), ("table", TABLE), ("image", IMAGE), ("text", TEXT)):
        if ext in s:
            return k
    return "unknown"


# ----------------------------------------------------------------------------- Office conversion
def _office_convert(path, target_ext):
    """Convert a legacy Office file with the installed Office apps. Cached per file version."""
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:16]
    os.makedirs(CONVERT_DIR, exist_ok=True)
    out = os.path.join(CONVERT_DIR, key + target_ext)
    if os.path.exists(out):
        return out
    src = os.path.abspath(path).replace("'", "''")
    dst = out.replace("'", "''")
    if target_ext == ".docx":
        script = (f"$a = New-Object -ComObject Word.Application; $a.Visible = $false; $a.DisplayAlerts = 0\n"
                  f"try {{ $d = $a.Documents.Open('{src}', $false, $true); $d.SaveAs2('{dst}', 16); "
                  f"$d.Close($false) }} finally {{ $a.Quit() }}")
    elif target_ext == ".pptx":
        script = (f"$a = New-Object -ComObject PowerPoint.Application\n"
                  f"try {{ $p = $a.Presentations.Open('{src}', -1, 0, 0); $p.SaveAs('{dst}', 24); "
                  f"$p.Close() }} finally {{ $a.Quit() }}")
    elif target_ext == ".xlsx":
        script = (f"$a = New-Object -ComObject Excel.Application; $a.Visible = $false; $a.DisplayAlerts = $false\n"
                  f"try {{ $w = $a.Workbooks.Open('{src}', 0, $true); $w.SaveAs('{dst}', 51); "
                  f"$w.Close($false) }} finally {{ $a.Quit() }}")
    elif target_ext == ".pdf":
        ext = os.path.splitext(path)[1].lower()
        if ext in (".doc", ".docx", ".rtf", ".odt"):
            script = (f"$a = New-Object -ComObject Word.Application; $a.Visible = $false; $a.DisplayAlerts = 0\n"
                      f"try {{ $d = $a.Documents.Open('{src}', $false, $true); $d.ExportAsFixedFormat('{dst}', 17); "
                      f"$d.Close($false) }} finally {{ $a.Quit() }}")
        elif ext in (".ppt", ".pptx", ".odp"):
            script = (f"$a = New-Object -ComObject PowerPoint.Application\n"
                      f"try {{ $p = $a.Presentations.Open('{src}', -1, 0, 0); $p.SaveAs('{dst}', 32); "
                      f"$p.Close() }} finally {{ $a.Quit() }}")
        elif ext in (".xls", ".xlsx", ".ods", ".csv"):
            script = (f"$a = New-Object -ComObject Excel.Application; $a.Visible = $false; $a.DisplayAlerts = $false\n"
                      f"try {{ $w = $a.Workbooks.Open('{src}', 0, $true); $w.ExportAsFixedFormat(0, '{dst}'); "
                      f"$w.Close($false) }} finally {{ $a.Quit() }}")
        else:
            raise DocError(f"no PDF preview for {ext} files")
    else:
        raise DocError(f"no converter to {target_ext}")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=180, creationflags=0x08000000)
    if not os.path.exists(out):
        raise DocError("Microsoft Office could not convert this file"
                       + (f": {r.stderr.strip()[:300]}" if r.stderr.strip() else ""))
    return out


# ----------------------------------------------------------------------------- readers
def _pdf(path):
    from pypdf import PdfReader
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise DocError("this PDF is password-protected")
        pages = []
        for pg in reader.pages:
            try:
                pages.append(pg.extract_text() or "")
            except Exception as e:
                pages.append(f"(could not read this page: {e})")
    except DocError:
        raise
    except Exception as e:
        raise DocError(f"could not open the PDF: {e}")
    note = ""
    if pages and sum(len(p.strip()) for p in pages) < 20 * len(pages):
        note = ("This PDF has little or no text layer (probably scanned images); the text below may be "
                "incomplete. Text recognition (OCR) is not available.")
    return "page", pages, note


def _iter_docx_blocks(doc):
    """Paragraphs and tables in document order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _docx(path):
    import docx
    try:
        d = docx.Document(path)
    except Exception as e:
        raise DocError(f"could not open the Word file: {e}")
    out = []
    for b in _iter_docx_blocks(d):
        if b.__class__.__name__ == "Paragraph":
            t = b.text.strip()
            if not t:
                continue
            style = (b.style.name or "").lower() if b.style is not None else ""
            if style.startswith("heading"):
                lvl = "".join(ch for ch in style if ch.isdigit()) or "1"
                t = "#" * min(int(lvl), 6) + " " + t
            elif style.startswith("list") or style.startswith("bullet"):
                t = "- " + t
            out.append(t)
        else:
            rows = []
            for r in b.rows:
                cells = [c.text.strip().replace("\n", " ") for c in r.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                ncol = rows[0].count("|") - 1
                rows.insert(1, "|" + " --- |" * ncol)
                out.append("\n".join(rows))
    # Word has no fixed pages: chunk by ~3000 characters so 'start/end' still works
    parts, cur = [], ""
    for block in out:
        if len(cur) + len(block) > 3000 and cur:
            parts.append(cur)
            cur = ""
        cur += block + "\n\n"
    if cur:
        parts.append(cur)
    return "part", parts or [""], ""


def _rows_text(rows, max_rows):
    lines = []
    for i, r in enumerate(rows):
        if i >= max_rows:
            lines.append(f"… (more rows; ask for a row range)")
            break
        vals = ["" if v is None else str(v).replace("\t", " ").replace("\n", " ") for v in r]
        while vals and vals[-1] == "":
            vals.pop()
        lines.append("\t".join(vals))
    return "\n".join(lines)


def _xlsx(path, sheet, start, end):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise DocError(f"could not open the workbook: {e}")
    names = wb.sheetnames
    chosen = [s for s in names if not sheet or s.lower() == str(sheet).lower()]
    if sheet and not chosen:
        raise DocError(f"no sheet '{sheet}'. Sheets: {', '.join(names)}")
    parts = []
    for name in chosen:
        ws = wb[name]
        lo = max(1, int(start or 1))
        hi = int(end) if end else None
        rows = ws.iter_rows(min_row=lo, max_row=hi, values_only=True)
        dims = f"{ws.max_row} rows × {ws.max_column} columns" if ws.max_row else "empty"
        body = _rows_text(rows, (hi - lo + 1) if hi else (400 if sheet else 120))
        parts.append(f"## Sheet: {name} ({dims}; rows {lo}–{hi or 'end'}, tab-separated)\n{body}")
    wb.close()
    note = f"Workbook sheets: {', '.join(names)}." + ("" if sheet else
                                                     " Pass sheet=<name> and start/end rows to see more.")
    return "sheet", parts, note


def _xls(path, sheet, start, end):
    import xlrd
    try:
        wb = xlrd.open_workbook(path)
    except Exception:
        return _xlsx(_office_convert(path, ".xlsx"), sheet, start, end)
    names = wb.sheet_names()
    parts = []
    for name in names:
        if sheet and name.lower() != str(sheet).lower():
            continue
        ws = wb.sheet_by_name(name)
        lo = max(1, int(start or 1)) - 1
        hi = min(ws.nrows, int(end)) if end else ws.nrows
        rows = (ws.row_values(i) for i in range(lo, hi))
        body = _rows_text(rows, (hi - lo) if end else (400 if sheet else 120))
        parts.append(f"## Sheet: {name} ({ws.nrows} rows × {ws.ncols} columns, tab-separated)\n{body}")
    return "sheet", parts, f"Workbook sheets: {', '.join(names)}."


def _pptx(path):
    from pptx import Presentation
    try:
        prs = Presentation(path)
    except Exception as e:
        raise DocError(f"could not open the presentation: {e}")
    slides = []
    for i, s in enumerate(prs.slides, 1):
        lines = []
        for shp in s.shapes:
            if getattr(shp, "has_text_frame", False) and shp.has_text_frame:
                t = "\n".join(p.text for p in shp.text_frame.paragraphs if p.text.strip())
                if t.strip():
                    lines.append(t)
            if getattr(shp, "has_table", False) and shp.has_table:
                for r in shp.table.rows:
                    lines.append("| " + " | ".join(c.text.strip() for c in r.cells) + " |")
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            notes = s.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"[speaker notes] {notes}")
        slides.append("\n".join(lines) or "(no text on this slide)")
    return "slide", slides, ""


def _table(path, start, end):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if path.lower().endswith(".tsv") else csv.excel
        rows = list(csv.reader(f, dialect))
    lo = max(1, int(start or 1))
    hi = int(end) if end else min(len(rows), lo + 299)
    body = _rows_text(rows[lo - 1:hi], hi - lo + 1)
    return "sheet", [f"## {os.path.basename(path)} ({len(rows)} rows; rows {lo}–{hi}, tab-separated)\n{body}"], \
        ("" if hi >= len(rows) else "Pass start/end to read more rows.")


def _text(path):
    with open(path, "rb") as f:
        raw = f.read(5_000_000)
    if b"\0" in raw[:4096]:
        raise DocError("this looks like a binary file")
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    parts = ["\n".join(lines[i:i + 200]) for i in range(0, len(lines), 200)] or [""]
    return "part", parts, ""


# ----------------------------------------------------------------------------- public
def extract(path, start=None, end=None, sheet=None, max_chars=40000):
    """Text of a document (one parse at a time: lxml isn't safe to run in several threads here)."""
    import tools as _wt
    with _wt.LXML_LOCK:
        return _extract(path, start, end, sheet, max_chars)


def _extract(path, start=None, end=None, sheet=None, max_chars=40000):
    if not os.path.isfile(path):
        raise DocError(f"{path} does not exist")
    ext = os.path.splitext(path)[1].lower()
    k = kind_of(path)
    if k == "image":
        raise DocError("this is an image; use read_file to look at it")
    if ext in PDF:
        unit, parts, note = _pdf(path)
    elif ext in WORD:
        unit, parts, note = _docx(path)
    elif ext in WORD_LEGACY:
        unit, parts, note = _docx(_office_convert(path, ".docx"))
    elif ext in EXCEL:
        unit, parts, note = _xlsx(path, sheet, start, end)
        start = end = None                       # rows were already applied inside the sheet
    elif ext in EXCEL_LEGACY:
        unit, parts, note = _xls(path, sheet, start, end) if ext == ".xls" else \
            _xlsx(_office_convert(path, ".xlsx"), sheet, start, end)
        start = end = None
    elif ext in PPT:
        unit, parts, note = _pptx(path)
    elif ext in PPT_LEGACY:
        unit, parts, note = _pptx(_office_convert(path, ".pptx"))
    elif ext in TABLE:
        unit, parts, note = _table(path, start, end)
        start = end = None
    else:
        unit, parts, note = _text(path)
    total = len(parts)
    first = max(1, int(start or 1))
    last = min(total, int(end)) if end else total
    if first > total:
        raise DocError(f"it only has {total} {unit}s")
    chunks, used, shown_last = [], 0, first - 1
    for i in range(first, last + 1):
        label = {"page": f"--- page {i} ---\n", "slide": f"--- slide {i} ---\n"}.get(unit, "")
        piece = label + parts[i - 1].strip() + "\n"
        if used + len(piece) > max_chars and chunks:
            break
        chunks.append(piece[:max_chars])
        used += len(piece)
        shown_last = i
    truncated = shown_last < last
    text = "\n".join(chunks).strip()
    if truncated:
        text += (f"\n\n…[showing {unit}s {first}–{shown_last} of {total}; ask for start={shown_last + 1} "
                 f"to continue]")
    return {"text": text, "kind": k, "unit": unit, "total": total, "first": first, "last": shown_last,
            "truncated": truncated, "note": note, "chars": len(text)}


def summary_line(path, info):
    unit = info["unit"]
    count = f"{info['total']} {unit}{'s' if info['total'] != 1 else ''}" if unit in ("page", "slide", "sheet") else \
        f"{info['chars']:,} characters"
    return f"{os.path.basename(path)} ({info['kind']}, {count})"
