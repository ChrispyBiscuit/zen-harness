# PDF and CSV files

## Make a PDF: `create_document`
`path` ending in `.pdf`, `content` as Markdown (same format as Word: headings, lists, tables, bold/italic, quotes), optional `title`, `subtitle`, `theme`. It is built as a themed Word document and saved as PDF through Microsoft Word (installed on this PC). Use this for reports, letters, one-pagers and printable handouts.

## Read a PDF
`read_document` extracts the text (use `start`/`end` for page ranges on long files). Scanned PDFs without a text layer come back empty — say so rather than guessing the content.

## Edit / merge / split PDFs
Use `python_host` (approval) with `pypdf` (already installed — never `pip install`):
```python
from pypdf import PdfReader, PdfWriter
r = PdfReader(r"C:/path/in.pdf"); w = PdfWriter()
for page in r.pages[0:3]:          # first three pages
    w.add_page(page)
w.write(r"C:/path/first3.pdf")
```
Merging: add pages from several readers into one writer. Rotating: `page.rotate(90)`.

## CSV files
- Write: `write_file` with CSV text (quote fields containing commas: `"Trains, passes",200`), or `create_document` to an `.xlsx` for a formatted spreadsheet.
- Read/analyze: `query_data` with `files: {"t": "C:/path/data.csv"}` (SQL), `read_document` for a quick look, `python_sandbox` + pandas for heavier work.
