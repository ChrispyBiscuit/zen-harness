# Hard-target search — dead, changed, deleted and blocked pages (reference)

Part of the hard-target playbook (`read_guide('hard_search')` for the method). In this app, fetch archive URLs with
`web_fetch`; if `web.archive.org` is blocked, try `archive.org/wayback/available`, another archive, or ask the user to
open the capture.

## Facts (October 2026)

- Google removed cached pages in 2024, Bing in December 2024: `cache:` is dead on both.
- The Internet Archive's Wayback Machine is the primary tool.
- **archive.today** (archive.ph/.is/…): in January 2026 it ran attack code in visitors' browsers and captures with changed
  content were found; English Wikipedia deprecated it (20 Feb 2026) and Wikimedia blacklisted it. Treat its captures as
  unverified, never as sole evidence, and never solve its CAPTCHA.

## Wayback Machine URL forms

| URL | Result |
|---|---|
| `https://web.archive.org/web/<URL>` | latest capture |
| `https://web.archive.org/web/2015/<URL>` | capture nearest that date (`YYYYMMDDhhmmss`; a prefix is enough) |
| `https://web.archive.org/web/<timestamp>id_/<URL>` | the raw original file — no toolbar, no rewritten links; best for parsing |
| `https://web.archive.org/web/*/<URL>` | calendar of captures (browser) |
| `https://web.archive.org/web/*/<prefix>*` | captured URLs below a prefix (browser) |
| `https://archive.org/wayback/available?url=<URL>&timestamp=<YYYYMMDD>` | JSON, one capture, different host. Tested: returned the latest capture and ignored `timestamp` — use CDX when the date matters |

## The CDX index — list what was captured

`https://web.archive.org/cdx/search/cdx?url=<target>&<parameters>`

| Parameter | Effect |
|---|---|
| `matchType=exact\|prefix\|host\|domain` | scope (`domain` includes subdomains); short forms `url=example.com/*`, `url=*.example.com` |
| `from=YYYY`, `to=YYYY` | date limits (1–14 digits) |
| `filter=statuscode:200` / `filter=!statuscode:200` | keep / remove matching rows (`field:regex`) |
| `filter=mimetype:application/pdf` | PDFs only |
| `filter=original:.*\.xlsx?$` | URL pattern |
| `collapse=urlkey` | one row per distinct URL |
| `collapse=digest` | skip adjacent identical captures |
| `collapse=timestamp:6` | one row per month |
| `fl=timestamp,original,statuscode,mimetype,digest,length` | columns |
| `output=json` | JSON |
| `limit=N` / `limit=-N` | first / last N rows |
| `showNumPages=true`, `page=N`; `showResumeKey=true`, `resumeKey=` | paging long results |

Uses: every PDF a site hosted (`url=example.com&matchType=domain&filter=mimetype:application/pdf&collapse=urlkey&fl=timestamp,original`);
a page whose exact URL you don't know (prefix match on its directory); when a page changed (`collapse=digest`, each row a
new version); a page's old URL after a redesign (list the early path structure). Then open
`https://web.archive.org/web/<timestamp>id_/<original>`.

## Wayback limits

Many pages were never captured (deep pages and files especially). Some "200" captures are error pages or redirects — read
the content. Script-built pages replay badly: use `id_` and look for data in the HTML (`hard_search_sites`). Owners can
remove captures: a missing capture doesn't prove a page never existed. Archived paywalls are still paywalls. The capture
date is the crawl date, not the publication date.

## Other archives

| Archive | Use |
|---|---|
| Common Crawl | monthly open crawl, 2008–now; URL index only. Crawls: `https://index.commoncrawl.org/collinfo.json` (tested; newest CC-MAIN-2026-39). Query `https://index.commoncrawl.org/<crawl-id>-index?url=<pattern>&output=json` (same wildcards as CDX); returns file, offset, length. Low request rate. |
| Arquivo.pt | Portuguese web archive with full-text search; holds many foreign sites |
| National web archives | UK Web Archive, Library of Congress… (some on-site only) |
| Archive-It | institutional collections, text search inside a collection |
| Memento aggregators | one query across many archives (check availability) |
| Perma.cc | captures cited by legal and academic works |
| Software Heritage | source code |
| urlscan.io | screenshots and content of scanned URLs; good for short-lived pages |

## Other traces of deleted content

Quotes of it in news, forums and replies (search a remembered phrase in quotes) · syndicated copies and reposts · search
snippets still showing old text (label "snippet only") · feed items in readers and archives · screenshots on other pages ·
earlier/later versions or other formats by the author.

## Preserve what you find

Record the capture URL and access date. If the host permits, request a capture at `https://web.archive.org/save/<URL>` —
this makes a public record, so ask the user first when the page is sensitive.

## Blocked or unreadable pages (never go around access controls)

| Error | Action |
|---|---|
| 403 / bot block | archive copy; alternative form (below); the browser tool; ask the user to open it and paste the text |
| 404 / domain gone | archives; the exact title in quotes to find a moved copy |
| 429 / rate limit | wait, slow down — never change identity to continue |
| Timeout / huge file | fetch a part; find a smaller format |
| Login required | stop; look for a public version; report the limit |
| CAPTCHA | stop — never solve or bypass it |
| Paywall | lawful routes below |

**Alternative forms of the same content:** print/plain view (`?print=1`, `/print/`, `?view=print`, `?format=txt`) · AMP/
mobile (`/amp/`, `?amp=1`, `m.`) · text-only editions · full-text feeds (`/feed/`, `/rss`, `/atom.xml`, `?format=rss`) ·
the site API or embedded JSON · the PDF of an HTML page or vice versa · another language edition (`/en/`, `/de/`) · the
author's site, an institutional repository, a government mirror.

**Copies elsewhere:** wire stories (AP, Reuters, AFP) on many outlets — search the headline or a distinctive sentence in
quotes · press releases (the newsroom, PR Newswire, Business Wire, GlobeNewswire, EurekAlert) · reports on the funder's or
client's site, legislative records, library catalogs · slides/talks on the conference site, the speaker's page, video ·
standards: drafts, public review copies, national adoptions, vendor guides quoting the text.

**Paywalled papers and news — lawful routes only:** 1) open-access location for the DOI (Unpaywall, OpenAlex);
2) PubMed Central, Europe PMC, arXiv, SSRN, institutional repository; 3) the author's home page, Google Scholar "All
versions"; 4) the accepted manuscript or preprint (say it may differ); 5) abstract, figures, supplementary data;
6) a library (the user's, a public library card, interlibrary loan); 7) a draft email to the author for the user. Never
use paywall-defeating or piracy services.

**Script-built pages:** the browser tool; embedded data in the raw HTML; the data API the page calls; the raw archive
capture; relay/reader services only if the host permits (obey blocks).

**PDFs and scans:** no text layer = an image → OCR, or find an HTML/text version. In a long PDF, search for your anchor
instead of reading front to back. Extracted tables lose structure — check each number against its row and column labels.
Read document properties for title, author, creation date.
