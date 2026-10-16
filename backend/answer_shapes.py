"""How an answer may be shaped: the rich blocks the UI draws inside an answer (ui/src/components/AnswerBlocks.tsx).

A block is a fenced code block in the answer text, with a fixed language name and a small JSON body; the UI renders
it (a chart, a map, a fact panel…) and shows the source with a note if it doesn't parse. The text below goes into
every prompt that writes answers (agentic's three, chat mode's), so small models know the shapes exist and when each
one helps. KEEP goes into the redraft and verify passes, which rewrite an answer and must not flatten its blocks.
See plans/answer-blocks.md."""

TEXT = """
Show, don't only tell: pick the shape that fits what you are answering (most answers need none of these):
- table: comparing 2+ items on the same attributes.
- chart: a trend, ranking or breakdown of 4+ numbers. ```chart
{"type":"bar","title":"Sales by year","unit":"$M","x":["2022","2023","2024"],"series":[{"name":"Sales","data":[4.1,5.3,7.2]}]}
``` type: bar, column, line, area, pie or scatter; several series share x; numbers only, never strings.
- map: 2+ places, or where something is. ```map
{"markers":[{"name":"Pequod's Pizza, Chicago","note":"deep dish"},{"name":"Lou Malnati's, Chicago"}]}
``` (add "lat"/"lon" when a source gives them; otherwise give the full place name and city).
- facts: one person, place or thing at a glance. ```facts
{"title":"Mount Kenya","subtitle":"Volcano, Kenya","items":[["Elevation","5,199 m"],["First ascent","1899"]]}
```
- timeline: dated events in order. ```timeline
[{"date":"1969","title":"Apollo 11 lands","text":"First crewed Moon landing"}]
```
- cards: 2-5 options to choose between. ```cards
[{"title":"RTX 4060 Ti","subtitle":"$429","badge":"best value","items":[["VRAM","16 GB"]]}]
```
- image: ![caption](image URL) for a picture of the subject from a page you read (the image's own URL), or a figure \
you made (its file path).
Put only figures you have from sources, tools or the user into a block, the same figures as in your text. One block \
per idea; keep the sentences around it short."""

KEEP = (" Keep the answer's tables, ```chart/```map/```facts/```timeline/```cards blocks and images, corrected where "
        "a figure in them was wrong.")
