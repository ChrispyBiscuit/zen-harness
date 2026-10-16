# Answer blocks: tables, charts, maps, figures and panels inside the answer

**Problem (2026-10-16).** Answers are plain text plus a carousel that shows the preview pictures of whatever pages the
agent read. There are no graphs, few tables, and no images or figures in the answer itself.

The causes, from the code:

1. **The carousel is a byproduct of search.** It shows each read page's `og:image` (`deep_search.py`,
   `agentic._answer_images`), not something the answer chose. Mostly logos and banners.
2. **The prompt says almost nothing about shaping an answer.** One paragraph (`agentic.py` "Format for a reader who
   scans"); nothing about charts, maps or images. Small models do what the prompt says.
3. **Rich output exists but sits elsewhere.** `artifact_create` has charts (Vega-Lite from a CDN, so it fails
   offline), mermaid and svg in the side pane; Python figures show only inside the tool card.
4. **Chat mode has two tools** (web_search, web_fetch) and no structure guidance.
5. **Nothing does maps.**

## Design: the answer is the canvas

A rich block is a fenced code block **inside the answer text**. That is the most reliable output form for a small
model (no extra tool call, streams with the text, survives redrafts), and the renderer already dispatches on the
fence language. A block that doesn't parse shows a quiet placeholder while the reply streams, then the source with a
note.

| Fence | What it draws | Data |
|---|---|---|
| ` ```chart ` | bar / column / line / area / pie / donut / scatter, SVG, offline | `{"type", "title", "unit", "x": [...], "series": [{"name", "data": [...]}]}` or `"data": [{"label", "value"}]` |
| ` ```map ` | an interactive map with markers (Leaflet, CARTO dark tiles); markers by coordinates or by name (geocoded by the backend) | `{"title", "markers": [{"name", "lat", "lon", "note"}]}` |
| ` ```facts ` | a fact panel: title, optional image, key/value rows | `{"title", "subtitle", "image", "items": [["k", "v"], ...]}` |
| ` ```timeline ` | dated events on a vertical line | `[{"date", "title", "text"}]` |
| ` ```cards ` | comparison cards: one per option, same attributes | `[{"title", "subtitle", "badge", "items": [["k", "v"]], "url"}]` |
| ` ```mermaid ` | diagrams (flow, sequence), inline | mermaid source |
| `![caption](url or file path)` | an inline image or the agent's own Python figure, captioned, click to zoom | Markdown image |

Tables stay plain Markdown tables, styled for scanning (sticky header, numeric columns right-aligned).

## Tier 1: prompt, images, carousel

- **Response shapes** in the agent and chat prompts: when to use each shape (table: comparing 2+ items on the same
  attributes; chart: a series or distribution of 4+ numbers; map: 2+ places; facts: one entity's key figures;
  timeline: dated events; cards: 2-5 options to choose between; image: a thing easier seen than described), with one
  compact example each. Redraft and verify passes keep tables, blocks and images as they are.
- **Inline images**: an `img` renderer with max height, caption from alt text, lazy loading, no referrer, click to
  zoom; local paths (Python figures) through `/api/file?raw=1`. The Python tool's result tells the model it can show
  a figure with `![caption](path)`.
- **Carousel demoted**: shown only when the answer cites at least 2 pages that have a picture of their own subject (a
  place or product: `name` set, not an article listing). Otherwise none.

## Tier 2: charts and maps

- **Charts**: a small hand-written SVG renderer, no dependency (well inside the +60 KB budget). Dataviz rules: thin
  marks, 4px rounded bar ends, 2px lines, hairline recessive grid, clean ticks, a legend for 2+ series with selective
  direct labels, hover tooltips, a table view toggle, text in ink tokens and never the series color. Categorical
  palette: the reference dark steps, validated against the card surface (`validate_palette.js`, all checks pass:
  worst adjacent CVD ΔE 8.4, normal-vision 19.3, all ≥ 3:1). No dual axes; a 9th series folds into "Other".
- **Maps**: Leaflet (~40 KB gz) lazy-loaded only when a map block appears; CARTO dark raster tiles (OpenStreetMap data,
  attributed). Markers without coordinates are geocoded by `/api/geocode` (Nominatim, cached, 1 request/s, a proper
  User-Agent; refused when the agent's web access is off). Offline or tiles failing: the same places as a list.
  Privacy note in docs: map tiles and geocoding send place names and areas to those services.

## Tier 3: panels and chat

- **facts / timeline / cards** renderers in the app's card style.
- **Source cards**: the Sources list as compact cards (favicon, site, title).
- **Chat mode** gets the same response shapes in its prompt, plus `calculate`, so quick questions get tables and
  charts too.

## Checks

- Replay screenshots of every block (streaming placeholder, rendered, parse error, offline map).
- Backend tests: carousel rule, geocode endpoint (cache, web-off refusal, no network in tests), prompt contains the
  shapes.
- `npm run build`; the full offline suite; the streaming perf replay (blocks must not re-render per frame: memoised
  by source text).
