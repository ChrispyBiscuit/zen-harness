---
name: log-analysis
title: Analyse logs or error output
triggers: logs, errors in, what went wrong, crash, exceptions, most common error, when did it start failing, trace
examples:
  - "find out why the service failed last night from these logs"
  - "what are the most common errors in this log folder"
  - "when did the timeouts start"
applies_when: the user has log files or error output and wants causes, counts or timelines
---
**Done looks like:** the top errors with counts, the time pattern (first occurrence, peaks), the most likely cause, and the evidence lines.
**Approach:** list the files (list_dir/find_files); search_text for ERROR/WARN/exception patterns; aggregate with `python_sandbox` (counts per message, per hour) rather than reading everything; read around the first occurrence.
**Tools:** search_text; python_sandbox (files=[...]); read_file for context around key lines.
**Pitfalls:** large files loaded whole into the prompt; counting near-duplicate messages separately (normalise IDs/timestamps); mistaking the first symptom for the cause.
**Verify:** the cause explains the timeline; counts come from code, not estimation.
**Stop / ask:** if logs are rotated or incomplete, say what period is covered.
