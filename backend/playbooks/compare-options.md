---
name: compare-options
title: Compare options and recommend one
triggers: compare, versus, which is better, pros and cons, recommend, should I choose, alternatives, best option for
examples:
  - "compare three note-taking apps for a small team"
  - "which budget mirrorless camera should I buy"
  - "pros and cons of two web frameworks for a small site"
  - "compare two storage plans for a family and tell me which to pick"
  - "which of these two tools is better for my use case"
  - "which laptop should I buy for under $1000"
applies_when: the user wants a decision between named or researched alternatives, with reasons (a few good local places to go: local-places)
---
**Done looks like:** a short comparison table IN THE ANSWER (the options against the criteria that matter to the user), then a clear recommendation with the trade-off stated. An artifact or document only when the user asks for one or there are 5+ options.
**Approach:** 1) fix the criteria first (ask if the request gives none; otherwise infer 3–5 from the user's situation); 2) one `web_search` per option, or one search with the options as `queries`, reading pages; 3) collect the same attributes for every option from official pages; 4) decide.
**Tools:** web_search (one call, the options as `queries`); web_fetch(urls=[official pages], question=the criteria) only for a figure the search didn't give; calculate for cost comparisons.
**Pitfalls:** the table only in an artifact (the user reads the chat); re-opening research after the answer is written to polish cells; "best X" listicles are affiliate marketing; comparing attributes gathered from different years; recommending without saying what the user would give up.
**Verify:** every cell in the table has a source; the recommendation follows from the criteria, not from popularity.
**Stop / ask:** if the criteria depend on unknowns (budget, scale, platform), ask one question before researching.
