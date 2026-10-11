---
name: explain-concept
title: Explain a concept or how something works
triggers: explain, how does, why does, what is the difference, teach me, help me understand, overview of, in simple terms
examples:
  - "explain how public-key encryption works"
  - "why does the moon have phases"
  - "what is the difference between a stock and a bond"
  - "help me understand how vaccines train the immune system"
applies_when: the user wants an explanation or understanding, not a lookup of one fact and not a document
---
**Done looks like:** a clear explanation in the chat, built from the reader's level up: the core idea in one or two sentences, then how it works, then an example; sources for any specific facts or numbers.
**Approach:** answer from solid knowledge for stable concepts; use `web_search` when the topic is recent, specialised, or the user asks for sources; use `calculate` for any numbers in an example.
**Tools:** usually none; web_search for current or specialised facts; make-diagram style artifacts (mermaid) when a flow or structure is easier to see than to read.
**Pitfalls:** walls of text; jargon without definitions; overclaiming certainty on contested topics; producing a file nobody asked for.
**Verify:** the explanation answers the question asked; specific facts are sourced.
**Stop / ask:** if the user's level is unclear and matters, pitch it at an intelligent non-specialist and offer to go deeper.
