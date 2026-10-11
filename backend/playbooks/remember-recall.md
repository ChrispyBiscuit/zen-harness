---
name: remember-recall
title: Remember or recall something
triggers: remember that, don't forget, what did I say about, my preference, from now on, recall, what was the
examples:
  - "remember that I prefer short answers"
  - "what did I tell you about my setup last week"
  - "from now on use British spelling"
applies_when: the user is storing a preference or fact, or asking for one stored earlier
---
**Done looks like:** the fact saved (one memory, written to stand alone) or the recalled fact quoted with when it was saved.
**Approach:** to store: memory_save one clean sentence (update an existing one with memory_update instead of adding a contradiction); to recall: memory_search, then answer from what it returns.
**Tools:** memory_save; memory_update; memory_search; memory_delete.
**Pitfalls:** saving task progress as memory; several facts in one memory; answering a recall question from general knowledge instead of the store.
**Verify:** the saved text makes sense without this conversation.
**Stop / ask:** nothing to ask — confirm what was saved in one line.
