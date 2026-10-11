# Web research — how to find, verify and report information

## 1. Plan before searching (in your checklist)
- Rewrite the request as 1–3 precise questions. What exactly must the answer contain (numbers, dates, names, links)?
- Decide what counts as a trustworthy source for this topic: official sites (gov, company, standards bodies), primary data, reputable news, documentation, peer-reviewed papers. Forums and SEO listicles are leads, not proof.
- Note the time sensitivity: prices, news, versions, job postings, "latest" anything → add the current year/month to queries and check page dates.
- **Find the thing, not a container.** Don't narrow beyond what the user asked: if they didn't ask for a PDF, a Word file or one specific site, don't restrict to one. Information about real people, products or examples lives in many forms — web pages, public profiles, personal sites, portfolios, GitHub, talks, articles, forum posts. Start broad, then narrow by what you actually find.

## 2. Search well
- **`web_search` reads for you.** One call searches your query plus 2–4 differently-worded `queries` in parallel, reads the top pages and returns the relevant passages with numbered sources. Follow up with another `web_search` on what's still missing (new angles, not the same words). `pages: 0` gives just the result list (fast) when you only need a URL; `web_fetch` reads one specific page in full, or several at once with `urls: [...]` (add `question` to get just the relevant passages of each); the browser is for interactive sites.
- Start with a short, specific query (3–7 words): the key entity + the attribute. `Tegucigalpa population 2025`, not `what is the population of the capital city of Honduras right now`.
- Run 2–4 DIFFERENT queries per question, not one query repeated: synonyms, the official name, quotes for exact phrases. Add `site:` or `filetype:` only when that source or format is what's needed — each filter throws most results away.
- Never repeat an identical search: the result will not change (and the app blocks it). If results are poor, change the words, add/remove a site filter, or try the browser.
- **Never guess URLs.** Open only URLs that came from search results or links on a page you read. A made-up deep link (`site.com/some/guessed/path`) is usually a 404 — search for the page instead.
- If results are mostly the wrong kind (templates, ads, SEO listicles when you want primary examples), change the query's words to describe what the real thing would contain, or exclude the wrong kind (`-template -sample -example`).
- Search results are only titles and snippets. Snippets get cut and can be stale or wrong — open the best 2–3 pages with `web_fetch` before relying on them.

## 3. Read sources
- `web_fetch` the most authoritative result first. If it returns `[blocked]`, a login wall or almost no text, don't retry it — use the in-app browser (`browser_open` then `browser_read`) or another source.
- Use the browser for: sites that need JavaScript, search boxes/filters on a site (job boards, shops, databases), multi-page navigation, and anything the user is signed into. After each action `browser_read` the page and use the numbered elements.
- Note for each fact: the value, the source URL, and the date of the page/data.

## 4. Verify
- Cross-check every important fact in at least 2 independent sources. If they disagree, report the range or the most authoritative one and say there's disagreement.
- Prefer the most recent authoritative figure; say which year it's from.
- Compute derived numbers with `calculate` (growth rates, conversions, totals) — never in your head.
- If you can't verify something after reasonable effort, say "I couldn't confirm X" — never fill gaps with guesses presented as fact.

## 5. Stop at the right time
- Stop when every question has a verified answer, or after ~8–12 searches/fetches with no progress — then report what you found and what's missing.
- Two failures in a row on the same approach (blocked site, empty results) → switch approach (different query style, different site, browser), not the same thing again.
- Sites that need sign-in, captchas or payment: tell the user what's needed; never enter payment details.

## 6. Report
- Lead with the direct answer. Then the key facts with sources as markdown links: `[Tegucigalpa – Wikipedia](https://en.wikipedia.org/wiki/Tegucigalpa)`.
- Give dates for anything time-sensitive ("as of the 2025 census").
- Separate what's verified from what's an estimate or a single-source claim.
- For long research, put the full findings in an artifact (`artifact_create`, markdown) and summarize in chat.

## Quick recipes
- **Fact lookup:** 1–2 searches → fetch the top authoritative page → confirm with a second source → answer with links.
- **Comparison (products, options):** list candidates from searches → fetch each official page for the same attributes → table (artifact or document) → recommendation with reasons.
- **Latest/news:** query with the month+year → open 2–3 recent articles from reputable outlets → note dates → summarize what's confirmed vs. reported.
- **Find files/documents (PDFs, reports) — only when the user wants a file:** `filetype:pdf` + topic + organization → fetch/`read_document` the PDF → quote page numbers.
- **Collect real-world examples (N of something):** search for words the real thing would contain, excluding the wrong kind (`-template -sample`) → take candidates from the results → `web_fetch` each public page → skip sign-in walls → keep collecting until you have enough, noting the source of each.
- **Listings on one site (shops, classifieds, databases):** open the site's home page in the browser and use its own search box and filters, rather than guessing deep links.
