# Excel (.xlsx) — how to build, analyze and edit workbooks

## Make a new workbook: `create_document`
`path` ending in `.xlsx`, `content` as JSON (or plain CSV text for a single sheet):

```json
{
  "theme": "corporate",
  "sheets": [
    {"name": "Sales",
     "rows": [["Region", "Revenue", "Growth", "Margin"],
              ["North", "$1,900", "8%", "21%"],
              ["South", "$2,300.50", "16%", "24%"]],
     "formats": {"B": "$#,##0.00"},
     "charts": [{"type": "column", "title": "Revenue by region", "category_col": 1, "value_cols": [2],
                 "y_title": "USD"}]},
    {"name": "Notes", "header": false, "rows": [["Source: finance export, 2026-10-01"]]}
  ]
}
```
- First row is the header (styled, frozen, filterable) unless `"header": false`. Rows can also be a list of objects (keys become the header).
- Numbers stay numbers: "1,900" → 1900, "8%" → 0.08 shown as 8.0%, "$2,300.50" → 2300.5 shown as currency (inferred automatically per column; override with `formats`, e.g. `"0.0%"`, `"#,##0"`, `"yyyy-mm-dd"`).
- Charts: `type` = `column`, `bar`, `line`, `pie`, `scatter`; `category_col` and `value_cols` are 1-based column numbers; optional `anchor` cell (default: right of the data).

## Analyze data first
- For totals, averages, group-bys or filtering over a CSV/Excel file use `query_data` (DuckDB SQL; each Excel sheet is a table) — exact and fast. Omit `sql` to see columns and sample rows.
- For heavier analysis or charts as images use `python_sandbox` (pandas) with the file passed in `files`.
- Never do arithmetic yourself: `calculate` or SQL/pandas.

## Check your work
`read_document` the .xlsx afterwards (it prints each sheet as tab-separated rows).

## Edit an existing workbook
`python_host` (approval) with openpyxl (already installed — never `pip install`):
```python
from openpyxl import load_workbook
wb = load_workbook(r"C:/path/book.xlsx")           # data_only=True to read computed values instead of formulas
ws = wb["Sales"]
print(ws.max_row, ws.max_column, [c.value for c in ws[1]])
ws["E1"] = "Total"
for r in range(2, ws.max_row + 1):
    ws[f"E{r}"] = f"=B{r}*(1+C{r})"                # formulas are fine; Excel computes them on open
wb.save(r"C:/path/book_edited.xlsx")               # new name unless told to overwrite
```
