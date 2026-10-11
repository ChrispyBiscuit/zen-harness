---
name: write-document
title: Write a document from notes or sources
triggers: write a report, memo, draft a document, write up, turn these notes into, create a brief, proposal, Word document
examples:
  - "turn these meeting notes into a memo with action items"
  - "write a two-page project brief from this material"
  - "draft a policy document from these bullet points"
applies_when: the user wants a finished written piece, usually as a file
---
**Done looks like:** the document at the requested path (or an artifact), structured for its reader, with a one-line note of what it contains.
**Approach:** read_guide('word') or 'pdf' first, and read_guide('clear_writing') for the writing style; gather the inputs (read_document / read_file); outline; write with create_document (markdown content); read_document the result to check structure.
**Tools:** read_guide; read_document; create_document; artifact_create for drafts the user will iterate on.
**Pitfalls:** turning a short document (a page, a few slides) into a research project — no web research unless the user asks for sources or current figures; inventing facts to fill gaps (mark unknowns instead); walls of prose where a table fits; formatting by hand instead of the document tool.
**Verify:** every fact traces to the inputs; headings match the requested structure; the file opens.
**Stop / ask:** if audience, length or tone are unstated and matter, ask once; otherwise choose and say what you chose.
