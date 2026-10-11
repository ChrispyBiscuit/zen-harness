---
name: system-check
title: Run commands and check system state
triggers: check if, is running, disk space, processes, what version, system info, network, running services, ports
examples:
  - "is anything using port 8080"
  - "how much free disk space is there"
  - "which version of the tool is installed"
  - "what's using the most memory (RAM) on my PC right now"
  - "why is my computer running slow"
  - "how much CPU and GPU is that program using"
  - "is the app server still running"
applies_when: the user wants a fact about this PC that a command can answer
---
**Done looks like:** the command run, its real output (trimmed), and a one-line answer.
**Approach:** pick the read-only command; run it with powershell; read the output; answer from the output only.
**Tools:** powershell (read-only commands); bg_start for anything long-running.
**Pitfalls:** answering from memory instead of running; destructive commands; huge outputs (filter or head).
**Verify:** the answer quotes the output.
**Stop / ask:** before any command that changes state (install, kill, delete), confirm.
