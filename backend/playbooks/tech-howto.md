---
name: tech-howto
title: Technical how-to or troubleshooting lookup
triggers: how do I, error message, not working, configure, set up, fix this error, command for, why does
examples:
  - "how do I rotate logs for a service on a server"
  - "what does this compiler error mean"
  - "how to set an environment variable permanently"
applies_when: the user wants working instructions or an explanation of a technical problem
---
**Done looks like:** the steps or command that solve it, with the version/platform they apply to, and the source (docs first).
**Approach:** `web_search` with the exact error text in quotes (or the tool name + task); prefer official documentation and the project's issue tracker; when it can be checked locally, run it (powershell / python_sandbox) before answering.
**Tools:** web_search; web_fetch for the docs page; powershell or python_sandbox to confirm.
**Pitfalls:** answers for a different version or OS; copying a forum fix without the reason; destructive commands presented as a quick fix.
**Verify:** tested locally when possible; otherwise the source is official and version-matched.
**Stop / ask:** if the fix depends on the user's environment you cannot see, ask for the one detail that decides it.
