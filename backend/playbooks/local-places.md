---
name: local-places
title: Find good local places (restaurants, shops, services nearby)
triggers: near me, nearby, in town, best place, good place, where to eat, restaurant, cafe, bar, hotel, shop, store, dentist, mechanic, things to do
examples:
  - "find me a great pizza place in a town"
  - "I'm looking for good restaurants near somewhere"
  - "best coffee shop in a neighborhood"
  - "recommend a hotel near the airport"
  - "where can I get my car fixed nearby"
  - "fun things to do this weekend in a city"
applies_when: the user wants a few good local options to choose from, not a researched comparison
---
**Done looks like:** 3 picks (up to 5 if asked), each with name, what it's known for in a few words, address or area, and a link (its own site or a listing); one line on why each made the list. Add a rating only if a source shows it.
**Approach:** 1) one `web_search` with the place and type plus 2-3 other phrasings in `queries` (e.g. "best X in Town", "X Town reviews", "X Town local favorite"); 2) pick the places that several sources mention or that rate well; 3) answer. A second search only when the first found nothing usable. No checklist, no comparison table, no evidence table.
**Tools:** web_search (one call, several phrasings); web_fetch on a restaurant's own page only if the user asked for hours, menu or prices.
**Pitfalls:** dozens of searches for a casual pick; Google Maps pages (they don't read well in a text browser); listicles as the only source; places in a different town with the same name; inventing hours, prices or ratings no source states.
**Verify:** each pick is in the right town and appeared in a source you read; addresses and phone numbers are copied from a source.
**Stop / ask:** if the place is ambiguous (several towns share the name) and context doesn't say which, ask.
