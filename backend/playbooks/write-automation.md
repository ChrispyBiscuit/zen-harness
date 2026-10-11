---
name: write-automation
title: Write code or a script to automate a task
triggers: write a script, automate, program that, generate a tool, batch process, cron job, scrape, parse these files
examples:
  - "write a script that renames photos by their date taken"
  - "automate converting all docs in a folder to PDF"
  - "a small tool that checks a site daily and emails changes"
  - "write a script that archives files older than a given number of days"
applies_when: the user wants new code that does a job, not a one-off answer
---
**Done looks like:** a working script saved at a sensible path, run once on real input with the output shown, and a two-line usage note.
**Approach:** confirm inputs/outputs; write the smallest version that works; run it (python_sandbox for pure processing, powershell/python_host when it must touch the PC); handle the obvious failure cases; then extend.
**Tools:** write_file; python_sandbox / python_host; powershell; read_file to check outputs.
**Pitfalls:** pasting data into the code instead of reading files; silent overwrites; no dry-run for destructive file operations; dependencies that are not installed (say what is needed).
**Verify:** ran on a real sample; outputs inspected.
**Stop / ask:** before anything that deletes or moves many files, show the plan and ask.
