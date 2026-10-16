"""How an answer may be shaped: the rich blocks the UI draws inside an answer (ui/src/components/blocks/).

A block is a fenced code block in the answer text: the fence line is ``` plus the block's name, then a small JSON
body, then ```. The UI renders it (a chart, a map, a fact panel…) and shows the source with a note if it doesn't
parse. TEXT goes into every prompt that writes answers (agentic's three, chat mode's), so models know the shapes
exist. hint() is the nudge for one question: small models skip a general menu but follow "this question wants a
map" next to the question (the battery in plans/answer-blocks.md: 0 of 8 shape questions used a block before it).
KEEP goes into the redraft and verify passes, which rewrite an answer and must not flatten its blocks."""
import re

TEXT = """
Show, don't only tell. Some answers read best as a block the app draws: a fenced code block whose fence line is \
``` followed by the block's name, then the data, then ``` on its own line. Use one when it fits (most answers need \
none); the numbers and names in it come from your sources or tools, as in your text.
- table (plain Markdown): 2+ items compared on the same attributes.
- chart: a trend, ranking or breakdown of 4+ numbers. type: bar, line, area, pie or scatter. Numbers only.
```chart
{"type":"line","title":"Sales by year","unit":"$M","x":["2021","2022","2023","2024"],"series":[{"name":"Sales","data":[4.1,5.3,7.2,8.0]}]}
```
- map: 2+ real places, or where one place is. Give "lat"/"lon" if a source has them, else the full name and city.
```map
{"title":"Deep dish in Chicago","markers":[{"name":"Pequod's Pizza, Chicago","note":"caramelized crust"},{"name":"Lou Malnati's, River North, Chicago"}]}
```
- facts: one person, place or thing at a glance.
```facts
{"title":"Mount Kenya","subtitle":"Volcano, Kenya","items":[["Elevation","5,199 m"],["First ascent","1899"]]}
```
- timeline: dated events in order.
```timeline
[{"date":"1969","title":"Apollo 11","text":"First crewed Moon landing"},{"date":"1972","title":"Apollo 17","text":"Last crewed landing"}]
```
- cards: 2-5 options to choose between, the same attributes on each.
```cards
[{"title":"Kobo Clara BW","subtitle":"$130","badge":"best value","items":[["Screen","6 in"]]},{"title":"Kindle","subtitle":"$110","items":[["Screen","6 in"]]}]
```
- mermaid: a process, flow or structure, as a mermaid diagram.
```mermaid
flowchart LR
  A[Idea] --> B[Draft] --> C[Review] --> D[Publish]
```
- image: ![caption](image URL) for a picture of the subject from a page you read (the "Pictures on these pages" \
lines in search results give the URLs), or ![caption](path) for a figure you made.
One block per idea, after a one-line answer; keep the sentences around it short."""

KEEP = (" Keep the answer's tables, ```chart/```map/```facts/```timeline/```cards/```mermaid blocks and images, "
        "corrected where a figure in them was wrong.")

# question patterns → the block that answers them best (first match wins; one hint per question)
_RULES = [
    ("mermaid", r"\b(flow ?chart|diagram|flow of|process (of|for)|how (does|do) .{3,40} work step|steps? (of|in) "
                r"(the|a) process|org(anization)? chart|architecture)\b"),
    ("timeline", r"\b(timeline|chronolog|history of|over the (years|decades|centuries)|key (dates|events|moments)"
                 r"|milestones)\b"),
    ("map", r"\b(where (is|are)|near(by| me)?|located|locations? of|map of|on a map|in (the )?(city|town|area) of|"
            r"places? to|restaurants?|cafes?|bars?|hotels?|shops?|stadiums?|museums?|parks?|beaches|attractions|"
            r"neighbou?rhoods?)\b"),
    ("chart", r"\b(chart|graph|plot|trend|over time|growth|grew|growing|change[sd]? (from|since|over)|market share|"
              r"share of|breakdown|by year|per year|annual|monthly|rank(ing|ed)?|largest|biggest|top \d+|"
              r"population|statistics|stats)\b"),
    ("cards", r"\b(which (one |should i|to)|should i (buy|get|choose|pick)|vs\.?|versus|or the|best .{0,30} for|"
              r"options? for|recommend)\b"),
    ("table", r"\b(compare|comparison|differences? between|side by side|pros and cons)\b"),
    ("facts", r"\b(facts? about|at a glance|tell me about|who (is|was)|what is the .{0,20} of|quick facts|overview "
              r"of|profile of)\b"),
    ("image", r"\b(look(s)? like|picture|photo|image|show me)\b"),
]
_RX = [(k, re.compile(p, re.I)) for k, p in _RULES]

_NUDGE = {
    "chart": "a ```chart block (the figures from your sources) after a one-line answer, if there are 4+ numbers",
    "map": "a ```map block with each place (name and city, or lat/lon) after a one-line answer",
    "facts": "a ```facts block (the key figures from your sources) after a one-line answer",
    "timeline": "a ```timeline block of the dated events",
    "table": "a Markdown table, one row per item and one column per attribute",
    "cards": "a ```cards block, one card per option with the same attributes, then your pick in one line",
    "mermaid": "a ```mermaid diagram of the steps",
    "image": "a picture of it: ![caption](image URL) from a page you read (search for it first)",
}


def kind(question):
    """The block that suits this question best, or ''."""
    q = str(question or "")
    if len(q) > 600 or "```" in q:          # long tasks and pasted code: not a lookup to shape
        return ""
    for k, rx in _RX:
        if rx.search(q):
            return k
    return ""


def hint(question):
    """One line for the app's note beside the question, or ''."""
    k = kind(question)
    return f"This question reads best with {_NUDGE[k]} (block format in your instructions)." if k else ""
