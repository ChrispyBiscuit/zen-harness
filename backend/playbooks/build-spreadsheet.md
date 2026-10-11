---
name: build-spreadsheet
title: Build a spreadsheet or workbook
triggers: spreadsheet, Excel, workbook, xlsx, with a chart, tracker, budget sheet, table with formulas
examples:
  - "build a monthly budget workbook with a chart"
  - "turn this CSV into a formatted Excel file with totals"
  - "create an issue tracker spreadsheet"
applies_when: the user wants an .xlsx deliverable
---
**Done looks like:** the workbook at the requested path: typed numbers (not text), a frozen header, formats, and any requested chart.
**Approach:** read_guide('excel'); compute or load the data first (query_data / python_sandbox); build with create_document (sheets JSON: rows, formats, charts); read_document the result.
**Tools:** read_guide; query_data; python_sandbox; create_document (xlsx); read_document.
**Pitfalls:** numbers as strings; charts pointing at the wrong columns; doing the arithmetic yourself instead of in code or formulas.
**Verify:** totals in the sheet equal the computed totals; the chart references the right range.
**Stop / ask:** which fields matter when the source has many columns.
