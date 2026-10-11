---
name: reconcile-files
title: Merge, compare or reconcile files
triggers: merge, compare two files, diff, reconcile, what changed between, match records, which entries are missing
examples:
  - "compare these two exports and list what changed"
  - "merge two customer lists and flag conflicts"
  - "which rows in file A are missing from file B"
applies_when: the user has two or more files and wants them combined or their differences found
---
**Done looks like:** the merged file or the difference report, with counts (matched, only-in-A, only-in-B, conflicts) and the key used to match.
**Approach:** inspect both files; agree the matching key (ask if unclear); do the join/diff in `python_sandbox` with pandas (or `query_data` SQL); write the result and a short count summary.
**Tools:** query_data; python_sandbox (files=[...]); write_file / create_document for the output.
**Pitfalls:** keys that differ by case, whitespace or formatting; one-to-many matches silently multiplying rows; treating column order as meaning.
**Verify:** counts reconcile (A = matched + only-in-A, etc.).
**Stop / ask:** when no obvious key exists, propose one and ask before merging.
