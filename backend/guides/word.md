# Word (.docx) — how to write and edit documents

## Make a new document: `create_document`
`path` ending in `.docx`, `title`, optional `subtitle` and `theme`, and `content` as Markdown:

````markdown
# Executive summary
Revenue grew **12%** to $4.2M, driven by the new South region.

> **Bottom line:** the South launch paid off; freight costs are the risk to watch.

## Results by region

| Region | Revenue ($M) | Growth |
|---|---|---|
| North | 1.9 | 8% |
| South | 2.3 | 16% |
| Total | 4.2 | 12% |

```chart
type: bar
title: Revenue by region ($M)
```

## Next steps
1. Hire two account managers
2. Lock in freight rates

![The new office](C:/Users/me/Pictures/office.jpg)

---
# Appendix
````

- `#` `##` `###` headings, paragraphs, `-` bullets (2-space indent = sub-bullet), `1.` lists, `**bold**`, `*italic*`, `` `code` ``, code blocks, `---` page break, `![caption](C:/path/image.png)`.
- Tables: first row is the header. Number columns are right-aligned and a "Total" row is bolded automatically.
- `> ` makes a shaded callout box — use one for the key takeaway.
- Charts: a ` ```chart ` block right after a table charts that table (`type`: column, bar, line, pie, donut, area; optional `title`, `columns: [Revenue]` to pick columns). Or give the data: `{"type": "line", "labels": ["Q1", "Q2"], "series": [{"name": "Revenue", "values": [3.4, 3.9]}]}`.
- Themes: corporate (default), slate, modern, minimal, warm, forest. Documents always print on white.

## Writing rules
- Give it a `title`. Lead with the answer in a short summary, then detail under headings.
- Compare 3+ items in a table; show a trend or comparison with a chart under its table.
- Compute numbers with a tool (calculate, query_data); verify outside facts (web_search). The user's own material is the source.

## Check your work
`read_document` the file afterwards to confirm the structure and text.

## Edit an existing document
`python_host` (approval) with python-docx (installed — never `pip install`):
```python
from docx import Document
doc = Document(r"C:/path/report.docx")
for i, p in enumerate(doc.paragraphs):
    print(i, p.style.name, p.text[:80])
for p in doc.paragraphs:                                    # keep formatting: edit runs
    for r in p.runs:
        r.text = r.text.replace("2025", "2026")
doc.save(r"C:/path/report_edited.docx")                   # new name unless told to overwrite
```
Tables: `doc.tables[0].cell(row, col).text = "..."`.
