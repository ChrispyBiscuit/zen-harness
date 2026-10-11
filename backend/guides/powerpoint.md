# PowerPoint (.pptx) — how to build and edit decks

## Make a new deck: `create_document`
Call `create_document` with `path` ending in `.pptx` (or `type: "pptx"`), an optional `theme`, and `content` as JSON:

```json
{
  "theme": "corporate",
  "title": "Tegucigalpa",
  "subtitle": "Capital of Honduras · 2026 briefing",
  "slides": [
    {"layout": "bullets", "title": "At a glance",
     "bullets": ["Capital since 1880", "Population ≈ 1.3 million", {"text": "Twin city: Comayagüela", "level": 1}],
     "notes": "Speaker notes go here."},
    {"layout": "stats", "title": "By the numbers",
     "stats": [{"value": "1.3M", "label": "residents"}, {"value": "990 m", "label": "elevation"}, {"value": "1578", "label": "founded"}],
     "caption": "Sources: national statistics office, 2025"},
    {"layout": "two_column", "title": "Strengths vs. challenges",
     "left": {"heading": "Strengths", "bullets": ["Government seat", "Universities"]},
     "right": {"heading": "Challenges", "bullets": ["Water supply", "Traffic"]}},
    {"layout": "quote", "quote": "A city of hills and history.", "author": "Travel guide"},
    {"layout": "section", "title": "Next steps", "subtitle": "What we recommend"},
    {"layout": "image", "title": "The cathedral", "image": "C:/Users/me/Pictures/cathedral.jpg", "bullets": ["Built 1765–1782"]},
    {"layout": "closing", "title": "Thank you", "subtitle": "Questions?"}
  ]
}
```

- A `title` at the top level adds a title slide automatically (unless the slides already start with `layout: "title"`).
- Layouts: `title`, `bullets` (default), `two_column`, `stats` (2–4 big numbers), `quote`, `section` (divider), `image` (picture, optional bullets beside it), `closing`.
- Themes: `corporate` (navy, light), `modern` (dark charcoal + teal), `minimal` (black on white), `warm` (cream + coral), `forest` (green). Pick one that fits the audience; don't mix.
- Output is 16:9 with slide numbers and accent styling. Markdown also works (one `#` heading per slide, `-` bullets, `notes:` line), but JSON gives the real layouts.

## Design rules (what "professional" means)
- One idea per slide; the title states the takeaway ("Water is the city's biggest constraint"), not a topic word ("Water").
- 3–5 bullets, each under ~12 words. No paragraphs. Put detail in `notes`.
- Use `stats` for numbers, `two_column` for comparisons, `section` to break up decks over ~8 slides.
- Facts that don't come from the user's own material: verify them (web_search) before putting them on a slide, and cite sources in a caption or the last slide.
- 1 title slide + 3–7 content slides is right for most requests; a single "slide" request → title slide + 1 content slide unless told otherwise.

## Check your work
`read_document` the .pptx afterwards: it lists every slide's text and notes. Fix typos or overfull slides by regenerating with `overwrite: true`.

## Edit an existing deck
`create_document` only makes new files. To change an existing .pptx, use `python_host` (approval needed) with python-pptx, which is installed in the app's own Python:

```python
from pptx import Presentation
prs = Presentation(r"C:/path/deck.pptx")
for i, slide in enumerate(prs.slides, 1):
    for shp in slide.shapes:
        if shp.has_text_frame:
            print(i, shp.shape_id, shp.text_frame.text[:80])
# change text on slide 2
for shp in prs.slides[1].shapes:
    if shp.has_text_frame and "old text" in shp.text_frame.text:
        shp.text_frame.paragraphs[0].runs[0].text = "new text"
prs.save(r"C:/path/deck_edited.pptx")      # save to a NEW name unless the user asked to overwrite
```
Never install packages for this (no `pip install`); python-pptx, python-docx and openpyxl are already available to `python_host`. `python_sandbox` cannot make Office files.
