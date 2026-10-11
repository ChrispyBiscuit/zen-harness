---
name: transform-data
title: Clean, reshape or convert a dataset
triggers: clean up, convert to, reformat, deduplicate, normalise, split column, merge columns, pivot, export as
examples:
  - "convert this JSON export to a clean CSV"
  - "remove duplicate rows and standardise the dates"
  - "pivot this table so months are columns"
  - "deduplicate these contacts and normalise the phone numbers"
  - "reformat the export so each order is one row"
applies_when: the user wants the data changed into another shape or format, saved as a file
---
**Done looks like:** the output file at the requested path, a before/after row count, and the transformations listed.
**Approach:** inspect first (query_data / read_document), write the transformation in `python_sandbox` with pandas reading from the file, write the result, then re-open the result to confirm.
**Tools:** python_sandbox (files=[...]); query_data for a quick check; write_file for small text outputs; create_document for a formatted Excel.
**Pitfalls:** encoding and delimiter surprises; silently dropped rows; dates reinterpreted; overwriting the source file (write to a new name unless told).
**Verify:** row counts and a few spot rows match expectations; the output opens.
**Stop / ask:** if a rule is unclear (which duplicate to keep, how to treat blanks), state the assumption in the answer or ask.
