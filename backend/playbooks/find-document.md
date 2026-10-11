---
name: find-document
title: Find a specific document, report, dataset or file online
triggers: find the report, where can I download, dataset, PDF of, official document, specification, the paper on
examples:
  - "find the official annual report of a regulator for last year"
  - "where can I download the census dataset for a region"
  - "find the specification document for a file format"
applies_when: the user wants a particular file or publication, not a summary of a topic
---
**Done looks like:** the direct link to the document (and its publisher's page), what it contains, and its date/version.
**Approach:** `web_search` with the exact title or publisher + document type, `filetype:pdf` only when the user wants a file; prefer the publisher's own site; `web_fetch` the file to confirm it is the right one.
**Tools:** web_search; web_fetch (reads PDFs); read_document for a downloaded file.
**Pitfalls:** mirror sites and paywalled copies when a free official copy exists; the wrong year or edition; aggregator pages that only describe the document.
**Verify:** the fetched document's own title/date matches what was asked.
**Stop / ask:** if the document is behind a paywall or login, say so and offer the nearest public alternative.
