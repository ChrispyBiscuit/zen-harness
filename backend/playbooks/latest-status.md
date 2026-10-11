---
name: latest-status
title: Latest news or current status of something
triggers: latest, recent, news, current status, what happened, update on, this week, has it been released, is it still
examples:
  - "what's the latest on the new metro line opening"
  - "has version 3 of the library been released yet"
  - "recent news about the merger"
applies_when: the user wants what is true now or what happened recently
---
**Done looks like:** the current state with dates, the 2–3 most recent developments, and sources.
**Approach:** `web_search` with the subject + the current month/year as `queries`; read the newest pages from reputable outlets or the official source; order findings by date.
**Tools:** web_search (pages=4–6); web_fetch for the official announcement page.
**Pitfalls:** undated pages; old articles ranking high; "coming soon" treated as done; rumours reported as fact.
**Verify:** each key claim has a date and a source; the official source confirms anything consequential.
**Stop / ask:** two search rounds; if nothing newer than a few months appears, say so rather than filling the gap with older material.
