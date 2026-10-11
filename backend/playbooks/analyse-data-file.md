---
name: analyse-data-file
title: Analyse a data file: totals, trends, top items
triggers: analyse, total, average, top, group by, trend, breakdown, how many rows, summarise the data, which region
examples:
  - "what are the total sales per region in this spreadsheet"
  - "find the top 10 customers by spend in this export"
  - "what is the monthly trend in this CSV"
applies_when: the user has a CSV, Excel, JSON or similar file and wants numbers from it
---
**Done looks like:** the exact figures (from a tool, never mental arithmetic), a short reading of what they show, and the method in one line.
**Approach:** 1) look at the structure first (`query_data` without sql, or read_document) — columns, types, row count; 2) compute with `query_data` SQL for aggregates or `python_sandbox` with pandas for anything more; 3) sanity-check (totals add up, no all-zero columns, dates parsed).
**Tools:** query_data; python_sandbox (pass the file in files=[...]); calculate for small derived numbers; create_document or artifact for a table/chart deliverable.
**Pitfalls:** header rows or units inside cells; numbers stored as text; duplicated rows; retyping data into code instead of reading the file.
**Verify:** re-run one figure a second way (SQL vs pandas, or a subtotal that must match the total).
**Stop / ask:** if a column's meaning is ambiguous (two date columns, unclear units), ask before building on it.
