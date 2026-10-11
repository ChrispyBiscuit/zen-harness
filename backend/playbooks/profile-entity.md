---
name: profile-entity
title: Profile a company, product or public figure from public information
triggers: who are they, background on, tell me about, overview of, company profile, due diligence, what do they do
examples:
  - "give me a background brief on a startup before a meeting"
  - "what is this product and who makes it"
  - "overview of a public figure's career from public sources"
applies_when: the user wants an organised summary of what is publicly known about an entity
---
**Done looks like:** a structured brief (what it is, history, people, scale, recent developments) with a source per section.
**Approach:** `web_search` with 3 angles (official site, recent news, independent coverage); read the official "about" page and 2–3 independent pages; separate facts from marketing.
**Tools:** web_search; web_fetch(urls=[...], question=...); artifact_create for a brief longer than a screen.
**Pitfalls:** mixing up entities with similar names; treating the company's own claims as independent facts; stale leadership or figures.
**Verify:** names, dates and numbers come from at least one independent source or are marked "per the company".
**Stop / ask:** if public information is thin, say so; never fill with guesses. For private individuals, stick to what they published themselves.
