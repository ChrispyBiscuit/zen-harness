---
name: summarise-document
title: Summarise or extract from a document
triggers: summarise, summary of, key points, extract the, what does the document say about, TL;DR, read this and tell me
examples:
  - "summarise this contract's termination terms"
  - "what are the key findings of this report"
  - "extract all deadlines from this PDF"
  - "summarise the key obligations in this agreement"
applies_when: the user has a document (or a URL) and wants its content condensed or specific items pulled out
---
**Done looks like:** a structured summary or the extracted items, each with a page/section reference, at the length the user wants.
**Approach:** read_document (page ranges for long files; the whole thing for short ones) or web_fetch for a URL; outline the sections first, then summarise against the user's question, not generically.
**Tools:** read_document (start/end for pages); web_fetch; artifact_create for a long summary.
**Pitfalls:** summarising from the first pages only; scanned PDFs with no text layer (say so); mixing your own knowledge into what the document says.
**Verify:** every claim in the summary is in the document; quotes are exact.
**Stop / ask:** if the document is very long, confirm the focus (what matters to the user) before reading all of it.
