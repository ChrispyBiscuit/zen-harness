# Excel (.xlsx) — how to build, analyze and edit workbooks

## Make a new workbook: `create_document`
`path` ending in `.xlsx`, `content` as JSON (or plain CSV text for a single sheet):

```json
{
  "theme": "corporate",
  "sheets": [
    {"name": "Sales",
     "title": "Revenue by region",
     "subtitle": "USD · source: finance export 2026-10-01",
     "rows": [["Region", "Q2", "Q3", "Growth"],
              ["North", "$1,820", "$1,900", "8%"],
              ["South", "$1,400", "$2,300", "16%"]],
     "totals": true,
     "charts": [{"type": "column", "title": "Revenue by region", "category_col": 1, "value_cols": [2, 3]}]},
    {"name": "Notes", "header": false, "rows": [["Source: finance export, 2026-10-01"]]}
  ]
}
```
- First row is the header: styled, frozen, filterable (an Excel table). `"header": false` for plain rows. Rows can also be a list of objects.
- `title` / `subtitle` go above the table. `"totals": true` adds a bold Total row with SUM formulas (a last row starting "Total" is styled the same).
- Numbers stay numbers: "1,900" → 1900, "8%" → 8%, "$2,300" → currency, "2026-10-01" → a date. Formats are inferred per column; override with `formats`, e.g. `{"B": "#,##0.0"}`.
- Formulas: any cell text starting with `=` (e.g. `"=B2*C2"`).
- Charts sit below the table: `type` = column, bar, line, pie, donut, area, scatter; `category_col` and `value_cols` are 1-based column numbers.
- Themes: corporate, slate, modern, minimal, warm, forest. Sheets print fitted to the page width with page numbers.

## Analyze data first
- Totals, averages, group-bys, filters over a CSV/Excel file: `query_data` (DuckDB SQL; each sheet is a table). Omit `sql` to see columns and sample rows.
- Heavier analysis: `python_sandbox` (pandas) with the file in `files`.
- Never do arithmetic yourself: `calculate` or SQL/pandas.

## Check your work
`read_document` the .xlsx afterwards (it prints each sheet as tab-separated rows).

## Edit an existing workbook
`python_host` (approval) with openpyxl (installed — never `pip install`):
```python
from openpyxl import load_workbook
wb = load_workbook(r"C:/path/book.xlsx")           # data_only=True to read computed values instead of formulas
ws = wb["Sales"]
print(ws.max_row, ws.max_column, [c.value for c in ws[1]])
ws["E1"] = "Total"
for r in range(2, ws.max_row + 1):
    ws[f"E{r}"] = f"=B{r}*(1+C{r})"
wb.save(r"C:/path/book_edited.xlsx")               # new name unless told to overwrite
```
