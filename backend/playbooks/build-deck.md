---
name: build-deck
title: Build a presentation
triggers: presentation, slides, deck, PowerPoint, pitch, slideshow
examples:
  - "make a 6-slide overview of our quarterly results"
  - "create a short training deck on password hygiene"
  - "build a pitch deck outline from this document"
  - "a short deck introducing a new process to the team"
  - "slides that explain our new onboarding steps"
applies_when: the user wants slides
---
**Done looks like:** a .pptx at the requested path, one idea per slide, titles that state the takeaway, speaker notes for detail.
**Approach:** read_guide('powerpoint'); collect the content (files, or a short research pass); plan slides as JSON (layouts: title, bullets, two_column, stats, quote, section, closing); create_document; read_document to check.
**Tools:** read_guide; create_document (pptx JSON); read_document.
**Pitfalls:** paragraphs on slides; made-up numbers; more than ~8 content slides without sections; facts from the user's own material "verified" against the web unnecessarily.
**Verify:** slide count and order match the brief; numbers match the source.
**Stop / ask:** audience and length if unstated.
