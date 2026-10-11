# Hard-target search — inside one site, and structured queries (reference)

Part of the hard-target playbook (`read_guide('hard_search')` for the method). Use the site section when you know which
site holds the target but its navigation or the search engines don't show it. Use the structured-query section for
"all X that have property Y".

## Read the site's maps

- `/robots.txt` gives sitemap locations; its disallowed paths show the structure (read it; obey it when fetching many pages).
- `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap.xml.gz`: URL lists, often dated (news sites split them by month).
- `/feed/`, `/rss.xml`, `/atom.xml`, `/index.xml`: recent items (some accept `?paged=2`).
- Also: HTML site map, A–Z index, date archives, tag and category pages.

## Use the site's own search

Common forms: `/?s=<q>` (WordPress), `/search?q=<q>`, `/search/?query=<q>`, `/search/node/<q>` (Drupal). Fetch the URL
directly (or use the browser tool for search boxes). Site search reaches pages engines never indexed; look for advanced
search, date and type filters.

## Known platform endpoints (public content only)

| Platform | Endpoint |
|---|---|
| WordPress | `/wp-json/wp/v2/posts?search=<q>&per_page=100`, `/wp-json/wp/v2/pages`, `/wp-json/wp/v2/media` |
| MediaWiki | `/w/api.php?action=query&list=search&srsearch=<q>&format=json`, `Special:AllPages` |
| Discourse | `/search.json?q=<q>`; add `.json` to a topic URL |
| GitHub, GitLab | repository search, `/releases`, `/wiki`, raw file URLs |
| CKAN data portals | `/api/3/action/package_search?q=<q>` |
| DSpace | OAI-PMH `/oai/request?verb=ListRecords&metadataPrefix=oai_dc` |
| EPrints | OAI-PMH `/cgi/oai2?verb=ListRecords&metadataPrefix=oai_dc` |

## URL surgery

Go up one directory at a time (a parent often shows an index); change the year, issue number or ID for sibling documents;
change the language code or extension (`.html`→`.pdf`); drop the query string or add `?page=2`/`?limit=100`; compare with
archived URL lists (`hard_search_archives`, CDX) to learn the pattern. A guessed URL is a test — cite it only after a
successful fetch. Never use this to reach admin areas, private files or other users' records.

## Find the data API behind a page

Pages showing tables, maps or results often load JSON. Look in the HTML and scripts for `/api/`, `.json`, `graphql`,
`ajax`, `fetch(`, `endpoint`; with the browser tool, read network requests. Fetch the endpoint directly and vary `page`,
`limit`, `offset` and filters. Public endpoints only, low request rate; never use keys or tokens found in page code.

## Data embedded in the HTML

| Element | Content |
|---|---|
| `<script type="application/ld+json">` | structured data: dates, authors, prices, identifiers |
| `__NEXT_DATA__`, `window.__INITIAL_STATE__`, `window.__NUXT__` | full page data on script-built sites |
| `<meta>` `og:…`, `article:published_time` | title, description, publication date |
| `<meta>` `citation_title`, `citation_doi`, `citation_pdf_url` | academic pages put the PDF link here |
| `<link rel="alternate">` | feeds, other languages, AMP |
| `<link rel="canonical">` | the page's primary URL |
| HTML comments, hidden rows | occasional leftover data |

## Structured queries

### Wikidata (SPARQL) — lists by property

- Endpoint: `https://query.wikidata.org/sparql?query=<SPARQL>&format=json` (also `https://query-main.wikidata.org/sparql`).
- Since 9 May 2025 scholarly-article items live in `https://query-scholarly.wikidata.org/sparql`; joining papers with
  other items needs federation.
- Find IDs first: `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=<name>&language=en&format=json`.
- Wikidata is incomplete: an empty result doesn't prove absence. Verify each result at its reference. The example IDs
  below weren't tested — check them.

```sparql
SELECT ?item ?itemLabel WHERE {
  ?item wdt:P31 wd:Q12280 ;     # instance of: bridge
        wdt:P177 wd:Q1471 .     # crosses: Seine
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
```

### Wikipedia search

API: `https://<lang>.wikipedia.org/w/api.php?action=query&list=search&srsearch=<query>&format=json`

| Operator | Use |
|---|---|
| `insource:"exact text"` | search the wikitext incl. references and URLs |
| `insource:/regex/` | regex over wikitext (slow; add a plain term) |
| `intitle:` | word in the title |
| `incategory:`, `deepcat:` | category members; with subcategories |
| `hastemplate:` | pages using a template (e.g. an infobox type) |
| `linksto:` | pages linking to a page |
| `prefix:` | titles starting with a string |

Uses: every article citing a URL or DOI (`insource:"example.com/report"`); category members meeting a second condition;
titles in other languages (`action=query&prop=langlinks&titles=<title>&lllimit=500`).

### OpenStreetMap (Overpass)

`https://overpass-api.de/api/interpreter?data=<query>` — "all features of type T in area A", local-script names, addresses.

```
[out:json][timeout:25];
area["name"="Lyon"]["admin_level"="8"]->.a;
node["amenity"="drinking_water"](area.a);
out body;
```

### Scholarly graph (OpenAlex)

Parameters `search=`, `filter=`, `group_by=`; key required. Works from 2019 citing a work:
`https://api.openalex.org/works?filter=publication_year:2019,cites:<work ID>&api_key=<key>`

### Other

GDELT (news events, TV captions); CKAN and Socrata APIs on government portals; SEC EDGAR, CourtListener and
ClinicalTrials.gov APIs for records by field.
