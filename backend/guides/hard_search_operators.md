# Hard-target search — operators and engine rotation (reference)

Part of the hard-target playbook (`read_guide('hard_search')` for the method). Status: October 2026. In this app
`web_search` races several engines (Bing, Yahoo, DuckDuckGo, Brave, local SearXNG), so an operator only one engine
understands may be ignored by the others — probe first (master guide §2).

## Rules for all engines

- No space between operator and value: `site:example.com`.
- Write `OR` and `AND` in capitals. Put each `OR` group in parentheses (Bing applies `AND` before `OR`).
- An operator filters an index; it cannot return a page the index does not hold. `site:` does not list all pages of a site.
- Result counts are estimates, not evidence.
- Add operators one at a time — each one can cause zero results.
- A query copied to another engine can fail silently: unknown operators are read as plain words.
- Only `site:`, exact quotes and `-` exclusion work on all major engines. On DuckDuckGo `-` only lowers a term's weight.

## Cross-engine table ("test" = documentation and reports disagree — probe first)

| Function | Google | Bing | DuckDuckGo | Brave | Yandex |
|---|---|---|---|---|---|
| Exact phrase | `"…"` | `"…"` | `"…"` | `"…"` | `"…"` |
| Exclude | `-term` | `-term`, `NOT` | `-term` (less weight) | `-term`, `NOT` | `-term` |
| Either term | `OR` | `OR` in parentheses | plain words (default) | `OR` | `a \| b` (spaces around the pipe) |
| One site | `site:` | `site:` | `site:` | `site:` | `site:`, `host:`, `rhost:` |
| File type | `filetype:`, `ext:` | `filetype:`, `ext:` | `filetype:` (pdf, doc, xls, ppt, html only) | `filetype:`, `ext:` | `mime:` |
| Word in title | `intitle:`, `allintitle:` | `intitle:` | `intitle:` | `intitle:` | `title:` (test) |
| Word in URL | `inurl:`, `allinurl:` | none (test `instreamset:url:term`) | `inurl:` | none | `url:` (full URL, or prefix ending in `*`) |
| Word in body | `intext:`, `allintext:` | `inbody:` | none | `inbody:`, `inpage:` | default |
| Proximity | `AROUND(n)` (unreliable) | `near:n` (test) | none | none | test |
| Date | `before:`, `after:` | interface filter only | interface filter only | interface filter only | `date:` |
| Language, region | URL parameters | `language:`, `loc:` | region setting | `lang:`, `loc:` | `lang:` |
| Unknown word | `*` inside quotes | test | none | none | `*` inside quotes |
| Number range | `n..m` | none | none | none | none |
| Force a term | quotes | `+`, `norelax:` (test) | `+` (more weight only) | `+` | `+` |
| Pages on an IP | none | `ip:` | none | none | none |
| Pages linking to a file type | none | `contains:` | none | none | none |
| Feeds | none | `feed:`, `hasfeed:` | none | none | none |

## Google

**Works:** quotes, `OR`, `-`, `*` inside quotes, parentheses, `site:`, `filetype:`/`ext:`, `intitle:`/`allintitle:`,
`inurl:`/`allinurl:`, `intext:`/`allintext:`, `before:`/`after:` (`YYYY-MM-DD` or `YYYY`), `define:`, `source:` (News),
`imagesize:` and `src:` (Images).
**Unreliable:** `AROUND(n)`, `n..m` (better with a currency sign or unit), `inanchor:`.
**Removed — don't use:** `cache:` (2024), `related:` (2023), `info:`, `id:`, `link:` (2017), `+term` (2011), `~term` (2013), `phonebook:` (2010).
Notes: Google documents only `site:`, `filetype:`, `imagesize:`, `src:`; the rest work by observation. Don't mix the
`allin…` operators with other operators. `before:`/`after:` use Google's assigned page date, which can be wrong.

| `site:` form | Scope |
|---|---|
| `site:example.com` | domain and subdomains |
| `site:docs.example.com` | one subdomain |
| `site:example.com/reports/` | one path |
| `site:.gov`, `site:.edu`, `site:.mil`, `site:.int` | a top-level domain |
| `site:*.example.com -site:www.example.com` | all subdomains but `www` |
| `-site:example.com` | everything but this site |

URL parameters (only when a browser tool builds the results URL; unofficial — test): `udm=14` plain web view, no AI
Overview · `tbs=li:1` verbatim · `tbs=qdr:d|w|m|y` last day/week/month/year · `tbs=cdr:1,cd_min:M/D/YYYY,cd_max:M/D/YYYY`
custom range · `nfpr=1` no spelling correction · `filter=0` show near-duplicates · `hl=` interface language · `gl=`
searcher country · `lr=lang_xx` page language · `cr=countryXX` page country · `start=N` offset · `num=100` removed (Sept 2025).
Automated queries to Google result pages are against Google's terms and hit a JavaScript challenge — use the app's tools;
never solve a CAPTCHA.

## Bing (also the main index behind DuckDuckGo and Yahoo)

Microsoft's list (read 11 Oct 2026): `contains:`, `ext:`, `filetype:`, `inanchor:`, `inbody:`, `intitle:`, `ip:`,
`language:`, `loc:`/`location:`, `prefer:`, `site:`, `feed:`, `hasfeed:`, `url:`; symbols `+`, quotes, parentheses,
`AND`/`&`, `NOT`/`-`, `OR`/pipe.

| Operator | Use |
|---|---|
| `ip:203.0.113.5` | indexed pages on a server at an IPv4 address |
| `contains:pdf` | pages that link to files of a type |
| `feed:term`, `hasfeed:term` | feeds about a term; pages that have a feed |
| `url:example.com/page` | is this URL in the index? |
| `inanchor:word` | words in link text pointing at a page |
| `prefer:term` | more weight for a term |
| `near:n` | terms ≤ n words apart (reported more reliable than Google's `AROUND`; not in the current list — test) |
| `norelax:term` | force a term Bing would drop (older docs — test) |
| `linkfromdomain:example.com` | sites a domain links to (works by report; not in the list) |

Notes: no `inurl:` (removed 2007; read as a plain word). `site:` reaches at most two directory levels deep. Only the
first 10 query terms are used. `NOT`/`OR` must be capitals or they're stop words; `OR` has lowest precedence. Cache link
and `cache:` removed December 2024.

## Yandex (strongest for Russian and Eastern European content)

Current help (11 Oct 2026): `site:`, `url:`, `host:`, `rhost:`, `domain:`, `mime:`, `lang:`, `date:`, `!`, `+`, `-`,
quotes, `*` inside quotes, the pipe.

| Operator | Use |
|---|---|
| `rhost:com.example.*` | host name reversed — all subdomains |
| `host:www.example.com` | one exact host |
| `url:example.com/docs/*` | URLs starting with a prefix |
| `domain:com` | one top-level domain |
| `mime:pdf` | file type (pdf, rtf, swf, doc, xls, ppt, odt, ods, odp, odg) |
| `lang:fr` | page language |
| `date:20231001`, `date:>20231001`, `date:20230101..20231001`, `date:202310*` | exact date, after, range, month |
| `!word` | exact word form |
| `+word` | required word |
| `"word * word"` | one unknown word per `*` |
| `a \| b` | either word (spaces around the pipe) |
| `-word` | exclude (put at the end) |

Reported, not in the help — test: `title:word`, `~~word` (exclude from page), `~word` (exclude from sentence).

## DuckDuckGo

Help (11 Oct 2026): quotes, `-term` (fewer results with it), `+term` (more), `filetype:` (pdf, doc, docx, xls, xlsx, ppt,
pptx, html), `site:`, `-site:`, `intitle:`, `inurl:`. Two plain words = either word. `-`/`+` change weight, not strict
filters. Some advanced syntax doesn't work on all queries. Web results come mainly from Bing.

## Brave

Help (11 Oct 2026): `ext:`, `filetype:`, `inbody:`, `intitle:`, `inpage:` (title or body), `lang:`/`language:`,
`loc:`/`location:`, `site:`, `+`, `-`, quotes, `AND`, `OR`, `NOT`. Operators are "experimental" and can be dropped when
too few results match. No `inurl:`. "Goggles" re-rank or filter results by a rule set.

## AI answer engines

Some accept `site:`, `filetype:` and date limits in a prompt; the operator applies to retrieval, not necessarily to the
written summary. Open and cite each source, never the summary.

## Engine rotation

Different indexes hold different pages and rank them differently. When one engine fails twice, change the **index** as
well as the words. Two engines on one index count as one.

| Index | Own crawler | Use it for |
|---|---|---|
| Google | yes | largest index; date operators; Books, Scholar, Patents, News |
| Bing | yes | second large Western index; `ip:`, `contains:`, feeds |
| Brave | yes (own index since April 2023) | independent ranking; forum discussions; public API |
| Mojeek | yes | independent, smaller; different long tail |
| Marginalia | yes | small, text-heavy, non-commercial sites; personal pages; hobby docs |
| Yandex | yes | Russian/Eastern European; strong operators; reverse image search |
| Baidu | yes | Chinese |
| Naver, Seznam | yes | Korean; Czech |
| DuckDuckGo, Yahoo, Ecosia | no (mainly Bing) | count as Bing |
| Startpage | no (Google) | count as Google |
| Semantic search APIs | varies | you can describe the page but have no keywords; "pages like this URL" |
