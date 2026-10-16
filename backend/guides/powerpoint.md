# PowerPoint (.pptx) — how to build and edit decks

## Make a new deck: `create_document`
`path` ending in `.pptx`, `theme`, and `content` as JSON. Pick the layout that fits each slide's content:

```json
{
  "theme": "corporate",
  "title": "Q3 2026 Results",
  "subtitle": "Quarterly business review",
  "footer": "Leadership team · October 2026",
  "slides": [
    {"layout": "stats", "title": "Revenue grew 12% on the South launch",
     "stats": [{"value": "$4.2M", "label": "revenue, up 12%"}, {"value": "23%", "label": "margin"}],
     "caption": "Source: finance export, 2026-10-01"},
    {"layout": "chart", "title": "South is now the largest region", "type": "column",
     "labels": ["North", "South", "West"],
     "series": [{"name": "Q2", "values": [1.8, 1.4, 0.6]}, {"name": "Q3", "values": [1.9, 2.3, 0.6]}],
     "bullets": ["South grew 64% in one quarter"]},
    {"layout": "table", "title": "Results by region",
     "rows": [["Region", "Revenue", "Growth"], ["North", "$1.9M", "8%"], ["South", "$2.3M", "16%"], ["Total", "$4.2M", "12%"]]},
    {"layout": "bullets", "title": "What changed", "bullets": ["Churn fell to 2.1%", "Two enterprise contracts"],
     "notes": "Speaker notes go here."},
    {"layout": "two_column", "title": "Strengths and risks",
     "left": {"heading": "Strengths", "bullets": ["South growth", "Lower churn"]},
     "right": {"heading": "Risks", "bullets": ["Freight costs up 9%", "West shrinking"]}},
    {"layout": "section", "title": "What we recommend"},
    {"layout": "cards", "title": "Three moves for Q4",
     "cards": [{"title": "Hire", "text": "Two account managers in the South"},
               {"title": "Lock in", "text": "Freight rates before January"},
               {"title": "Reprice", "text": "Review West pricing"}]},
    {"layout": "timeline", "title": "Q4 plan",
     "steps": [{"label": "Oct", "text": "Open roles"}, {"label": "Nov", "text": "Freight tender"}, {"label": "Jan", "text": "New contract"}]},
    {"layout": "quote", "quote": "Best quarter since launch.", "author": "CFO"},
    {"layout": "image", "title": "The new office", "image": "C:/Users/me/Pictures/office.jpg", "bullets": ["Opened in May"]},
    {"layout": "closing", "title": "Thank you", "subtitle": "Questions?"}
  ]
}
```

| Content | Layout |
|---|---|
| 2–4 key numbers | `stats` |
| numbers over categories or time | `chart` (`type`: column, bar, line, pie, donut, area, stacked) |
| rows × columns of facts | `table` (first row = header; a "Total" row is bolded) |
| 2–4 ideas, each with a short label | `cards` |
| steps, phases, dates | `timeline` (2–6 steps) |
| comparison | `two_column` |
| 2–5 short points | `bullets` (`"Label: text"` bullets get a bold label) |
| new part of a long deck | `section` |

- A top-level `title` adds the title slide. `chart` and `table` slides take optional `bullets` (shown beside) and `caption`.
- Themes: corporate (navy, light), slate (grey + indigo), modern (dark slides, teal), minimal (black + red), warm (cream, serif), forest (green). One theme per deck.

## Design rules
- One idea per slide; the title states the takeaway ("South is now the largest region"), not a topic word ("Regions").
- At most 5 bullets of under ~12 words. No paragraphs; detail goes in `notes`.
- Show numbers as `stats`, `chart` or `table`, not as bullets.
- Facts not from the user's material: verify them (web_search) first and cite in a `caption` or the last slide.
- Most requests: title slide + 4–8 content slides.

## Check your work
`read_document` the .pptx afterwards: it lists every slide's text and notes. Fix and regenerate with `overwrite: true`.

## Edit an existing deck
`create_document` only makes new files. To change an existing .pptx, use `python_host` (approval needed) with python-pptx (installed — never `pip install`):

```python
from pptx import Presentation
prs = Presentation(r"C:/path/deck.pptx")
for i, slide in enumerate(prs.slides, 1):
    for shp in slide.shapes:
        if shp.has_text_frame:
            print(i, shp.shape_id, shp.text_frame.text[:80])
for shp in prs.slides[1].shapes:
    if shp.has_text_frame and "old text" in shp.text_frame.text:
        shp.text_frame.paragraphs[0].runs[0].text = "new text"
prs.save(r"C:/path/deck_edited.pptx")      # a NEW name unless the user asked to overwrite
```
`python_sandbox` cannot make Office files.
