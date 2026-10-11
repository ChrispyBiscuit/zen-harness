# Word (.docx) — how to write and edit documents

## Make a new document: `create_document`
`path` ending in `.docx`, optional `title`, `subtitle`, `theme`, and `content` as Markdown:

```markdown
# Executive summary
Revenue grew **12%** to $4.2M, driven by the new South region.

## Highlights
- New region launched in March
- Churn down to 2.1%

| Region | Revenue ($M) | Growth |
|---|---|---|
| North | 1.9 | 8% |
| South | 2.3 | 16% |

1. Hire two account managers
2. Expand to the West region

> Strong quarter; risks are concentrated in supply costs.

---
# Appendix
```
- `#`/`##`/`###` headings, paragraphs, `-` bullets (indent 2 spaces for a sub-bullet), `1.` numbered lists, `|` tables (first row = header, shaded in the theme color), `**bold**`, `*italic*`, `` `code` ``, fenced code blocks, `>` callout quotes, `---` page break.
- `title` + `subtitle` produce a styled title block; page numbers are added in the footer.
- Themes: `corporate` (default), `minimal`, `warm`, `forest` (`modern` prints as corporate — documents stay light).

## Writing rules
- Lead with the answer/summary, then detail. Use headings every few paragraphs.
- Tables for anything compared across 3+ items; keep cells short.
- Compute numbers with a tool (calculate, query_data); verify outside facts (web_search) before writing them. Material the user gave you is the source — don't second-guess it.

## Check your work
`read_document` the file afterwards to confirm the structure and text.

## Edit an existing document
Use `python_host` (approval) with python-docx (already installed — never `pip install`):
```python
from docx import Document
doc = Document(r"C:/path/report.docx")
for i, p in enumerate(doc.paragraphs):
    print(i, p.style.name, p.text[:80])
doc.paragraphs[3].text = "Replacement paragraph"          # replaces text (loses run formatting)
for p in doc.paragraphs:                                    # keep formatting: edit runs
    for r in p.runs:
        r.text = r.text.replace("2025", "2026")
doc.add_heading("New section", level=2); doc.add_paragraph("Body text")
doc.save(r"C:/path/report_edited.docx")                   # new name unless told to overwrite
```
Tables: `doc.tables[0].cell(row, col).text = "..."`. For a PDF of an existing docx, create a PDF with `create_document` from the same Markdown, or ask before automating Word.
