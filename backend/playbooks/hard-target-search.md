---
name: hard-target-search
title: Hard-to-find information (hard-target web search)
triggers: hard to find, can't find, couldn't find anywhere, hard search, deep search, dig up, track down, obscure, rare, niche, original source of, where did this come from, dead link, deleted page, old version of a page, archived copy, not indexed, buried on a site, many constraints, needle in a haystack, exhaustive search
examples:
  - "track down the original source of a statistic everyone quotes"
  - "find the old version of a product page that was taken down"
  - "dig up a hard-to-find technical specification for discontinued hardware"
  - "which person matches these four clues"
applies_when: the information is hard to find with ordinary search — normal queries return noise, templates or nothing, the page is dead or buried, the answer sits in a database, archive, filing or another language, or many constraints must hold at once
---
**Done looks like:** the target found and verified (primary source, or two independent sources), or a clear negative result that says where you looked, with which terms, and what would change the conclusion.
**Approach:** read the full method first: `read_guide('hard_search')`. In short: 1) define the target and how you'll recognize it; ask who would publish it and where they'd keep it; 2) go to the source type directly (database, registry, archive, forum, repository — `read_guide('hard_search_sources')`); 3) short, broad queries in the words of the answer page, 2–3 different routes per cycle in parallel (`web_search` `pages: 0` to triage); 4) find an anchor (unique ID, phrase, name) and search it in quotes; search for the container (list, directory, registry) that holds the items; 5) after two failures on a route, change the route: index, source type, language, time window, format; dead or changed pages → `read_guide('hard_search_archives')`; 6) open the pages, follow leads, log each query and closed route in the checklist notes.
**Tools:** web_search (several `queries`, `pages: 0` to triage, then read), web_fetch (one page, or `urls` + `question`; Wayback copies), browser for site search/filters, powershell for JSON APIs, encyclopedia for names and other-language terms, the `hard_search_*` reference guides.
**Pitfalls:** rewording the same failed query; long question-shaped queries; trusting snippets; counting copies of one source as several; SEO pages crowding out primary sources; going around logins, paywalls or CAPTCHAs (never); collecting private data on private persons (never).
**Verify:** each key fact traced to its origin; confidence labelled (Confirmed / Probable / Unverified / Conflicting / Not found); routes tried listed in the report.
