# Hard-target search — specialist indexes (reference)

Part of the hard-target playbook (`read_guide('hard_search')` for the method). Web engines don't index most database
records: when the target is a record, query the database. Entries are start points; access rules change. "Tested" = the
endpoint returned valid data on 11 October 2026. In this app, call JSON APIs with `web_fetch` (or `powershell`
`Invoke-RestMethod` / a python script — `python_sandbox` has no network).

## Scholarly work

| Need | Source | Note |
|---|---|---|
| Work from a title or citation | Crossref: `https://api.crossref.org/works?query.bibliographic=<text>&rows=5` | free; tested; confirm the top hit |
| Metadata, citations, authors, institutions | OpenAlex: `https://api.openalex.org/works?search=<text>&api_key=<key>` | free key required since 13 Feb 2026 |
| Lawful free copy of a paywalled paper | Unpaywall: `https://api.unpaywall.org/v2/<DOI>?email=<address>` | open-access locations |
| Body text of open biomedical papers | Europe PMC: `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=<q>&format=json` | free; tested |
| Biomedical citations | PubMed E-utilities | |
| Preprints | arXiv API, bioRxiv, medRxiv, SSRN, OSF Preprints, Zenodo | |
| Citations forward and backward | Semantic Scholar API, OpenAlex, Google Scholar "Cited by" | Scholar has no API and blocks bots |
| Open-access collections | CORE, BASE, DOAJ | |
| Theses | university repositories, OATD, NDLTD | long literature reviews, raw detail |
| Retractions and corrections | Crossref records, publisher page | |
| DOI → landing page | `https://doi.org/<DOI>` | |

## Law, government and public records

| Need | Source | Note |
|---|---|---|
| US company filings; contracts as exhibits | SEC EDGAR full-text search (2001+) | send a descriptive User-Agent |
| US federal court opinions and dockets | CourtListener, RECAP archive | PACER is paid |
| US laws, rules, notices | govinfo.gov, Federal Register API, eCFR, congress.gov API, regulations.gov | regulations.gov has public comments |
| US spending and contracts | USAspending.gov, SAM.gov | |
| US campaign finance, lobbying | FEC API, Senate lobbying disclosures, FARA | |
| US nonprofit tax filings | ProPublica Nonprofit Explorer, IRS Tax Exempt Organization Search | |
| Patents | Google Patents, Espacenet, USPTO Patent Public Search | describe mechanisms no manual does |
| Trademarks | USPTO trademark search | |
| Clinical trials, drugs, devices | ClinicalTrials.gov API, openFDA, FDA approval databases | |
| Research grants | NIH RePORTER, NSF Award Search, grants.gov | |
| Vehicle recalls, complaints, service bulletins | NHTSA | |
| Radio devices: internal photos, manuals, test reports | FCC equipment authorization search by FCC ID | |
| Released government records | agency FOIA reading rooms, FOIA.gov, MuckRock, DocumentCloud | |
| Property, permits, local courts, business licenses | county/city portals; state business search | no general index |
| United Kingdom | Companies House, legislation.gov.uk, Find Case Law, BAILII, WhatDoTheyKnow | |
| European Union | EUR-Lex, TED (tenders), Eurostat | |
| Companies in many countries | OpenCorporates, GLEIF (LEI), national registries | |

## Code and software

| Need | Source | Note |
|---|---|---|
| Exact code string | GitHub code search, grep.app | GitHub needs sign-in/token; qualifiers `repo:`, `org:`, `path:`, `language:`, `symbol:`, `content:`, regex `/…/` |
| Bugs and workarounds | the project's issue tracker, Stack Exchange API, mailing list | |
| Deleted or moved repositories | Software Heritage | |
| Packages and versions | npm, PyPI, crates.io, Maven Central, deps.dev | |
| Vulnerabilities | NVD, OSV.dev, GitHub Advisory Database | |
| Standards and protocols | RFC Editor, IETF Datatracker, W3C, WHATWG, ECMA, NIST | ISO/IEC are paid: look for previews, drafts, national adoptions |
| Mailing lists | lore.kernel.org, marc.info, mail-archive.com, the list's archive | |
| Old documentation | versioned docs on the project site; archives | |

## Discussion and community knowledge

| Place | Access | Note |
|---|---|---|
| Hacker News | `https://hn.algolia.com/api/v1/search?query=<q>&tags=comment` | free, no key, tested |
| Stack Exchange | `https://api.stackexchange.com/2.3/search/advanced?q=<q>&site=<site>` | |
| Reddit | Reddit search (`subreddit:`, `author:`, `title:`, `selftext:`) or `site:reddit.com` | limited without sign-in |
| Reddit archives | Arctic Shift (one subreddit/user); PullPush (outages reported) | Pushshift closed to the public |
| Usenet (1981+) | Google Groups archive | no new content since Feb 2024 |
| X, Bluesky, Mastodon | platform search; X: `from:`, `to:`, `since:`, `until:`, `filter:links`, `min_faves:` | most need sign-in — don't go around it |
| Telegram public channels | `https://t.me/s/<channel>` | |
| Specialist forums | `<topic> forum`, `inurl:viewtopic`, `inurl:showthread`, then the forum's search | engines index forums badly |
| Discord, Slack, private groups | not indexed, not reachable | tell the user |

## Data and statistics

- Series: national statistics agencies, FRED, World Bank, IMF, OECD, Eurostat, UN Data, UN Comtrade, FAOSTAT, WHO, Our World in Data.
- Open-data portals: data.gov, data.europa.eu, national and city portals.
- Research data: Zenodo, Dataverse, Figshare, Kaggle, Hugging Face datasets, Google Dataset Search.
- When a page gives a number, find the table it came from: search the exact number with its unit.

## Books, newspapers, pre-web material

| Need | Source | Note |
|---|---|---|
| Text inside books | Google Books, HathiTrust full-text, Internet Archive text search | snippet view confirms a phrase is on a page |
| Library holdings | WorldCat, Open Library, national catalogs | |
| Old newspapers, free | Chronicling America (US), Trove (AU), Papers Past (NZ), Gallica (FR), Delpher (NL), Europeana | OCR errors — short phrases, name variants |
| Old newspapers, paid | Newspapers.com, NewspaperArchive, ProQuest | name them to the user |
| Public-domain texts | Project Gutenberg, Wikisource, Internet Archive | |
| Natural history literature | Biodiversity Heritage Library | |
| Television news | Internet Archive TV News Archive (captions), GDELT | |
| Hearings and floor video | C-SPAN video library (transcripts) | |

## Images, video, audio, maps

| Need | Source | Note |
|---|---|---|
| Origin of an image | reverse image search on 2+ of Google Lens, Bing Visual Search, Yandex Images, TinEye | TinEye sorts by oldest; never identify a private person from a face |
| Words spoken in a video | its captions/transcript | |
| Free media | Wikimedia Commons, Flickr, Library of Congress, Europeana | |
| Map features by attribute | OpenStreetMap Overpass (`hard_search_sites`) | |
| Historical imagery | national map archives, USGS EarthExplorer, Google Earth history | |

## Products, parts, hardware

Manufacturer support site (and archived versions) · FCC ID database (radio devices) · ManualsLib and similar · distributor
datasheets (Digi-Key, Mouser, Octopart) · dealer parts diagrams · recall/safety databases (NHTSA, CPSC, EU Safety Gate) ·
certification listings (UL Product iQ, Energy Star, EPREL) · patents · teardowns (iFixit) · owner forums. Discontinued
product: Wayback copy of the product page and support downloads.

## Websites and infrastructure (public data only)

| Need | Source |
|---|---|
| Subdomains | certificate transparency: `https://crt.sh/?q=%25.example.com&output=json` |
| Past scans of a URL (screenshot, content, requests) | urlscan.io search |
| Domain registration | RDAP or WHOIS |
| Sites on one server | Bing `ip:` |
| Sites sharing an analytics/ad ID | source-code search engines (PublicWWW) — the same tag on two sites indicates one operator |
| Technology in use | response headers, page source, BuiltWith, job postings |

## Wikipedia

Use it to find sources, then cite the sources: references, external links, talk page, history, other-language versions,
"What links here", categories. Search syntax: `hard_search_sites`. In this app, `encyclopedia` searches an offline copy.
