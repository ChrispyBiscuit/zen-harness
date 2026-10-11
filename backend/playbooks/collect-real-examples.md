---
name: collect-real-examples
title: Collect N real-world examples of something
triggers: find real examples, collect, gather, show me N actual, instances of, examples in the wild, samples of
examples:
  - "find 10 real open-source project README files that use a changelog section"
  - "collect 5 real pricing pages of small SaaS companies"
  - "show me 8 actual company mission statements"
applies_when: the user wants a set of genuine artifacts, pages, organisations or people — not templates, advice or made-up samples
---
**Done looks like:** N distinct, genuine items, each with its source URL and one line on why it qualifies; a note on what you could not get.
**Approach:** 1) decide what "genuine" means here and where such items are public (official sites, repositories, archives, directories, public profiles); 2) `web_search` with 3 phrasings of what the real thing would *contain*, excluding "template", "sample", "example"; 3) shortlist from results; 4) `web_fetch(urls=[...], question=...)` the candidates in one call and keep the ones that qualify; 5) repeat with new angles for what is still missing.
**Tools:** web_search (pages=0 to scout a new angle, then pages=4–6); web_fetch with urls=[...]; artifact_create for the list.
**Pitfalls:** template and "examples" sites dominate the first results; sign-in-walled sites — use the summary shown in the search result, don't open them; don't guess URLs; a site's own search page is usually blocked; the same site counted several times.
**Verify:** each kept item was actually read, not inferred from a snippet; no duplicates; the count is right.
**Stop / ask:** after ~8 searches with fewer than half the target, report what is realistically public and ask whether a substitute kind of source is acceptable.
