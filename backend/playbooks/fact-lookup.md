---
name: fact-lookup
title: Fact lookup with verification
triggers: what is, how many, when did, who is, how tall, population, definition, date, statistic, quick question
examples:
  - "how tall is the tallest building in Europe"
  - "when was the first commercial jet flight"
  - "what is the boiling point of ethanol"
  - "what is the population of a city"
  - "how far is the moon from the earth"
  - "who is considered the greatest actor of all time"
  - "what is a famous person's favorite food or show"
  - "is this city the capital of that state"
  - "who are the most famous people from a country"
applies_when: the user wants a specific fact or figure, possibly with a source
---
**Done looks like:** the fact, the date it applies to, and 1–2 source links; a note when sources disagree.
**Approach:** one `web_search` with the key entity + attribute (short query, 2–3 phrasings); the top pages are read for you — take the figure from an authoritative page (official site, reference work, standards body), not a snippet.
**Tools:** web_search (default page reading); calculate for any conversion or derived number.
**Pitfalls:** snippets are often stale or truncated; forum answers and SEO pages copy each other — prefer a primary source; time-sensitive facts (prices, office holders, records) need the year in the query.
**Verify:** a second independent source agrees, or say which year/source the figure is from.
**Stop / ask:** one search round normally answers it; if two rounds disagree, report both figures and their sources instead of searching again.
