# Context architecture — what is in context, what goes in and out, and when

Premise (2026-10-11): for small local models the frontier is context management, not raw capability. Nearly every
failure in a day of Bonsai 2 27B runs was a context failure — wrong things in (a copied placeholder, a half-file
presented as whole), right things missing (the end of a file, the contract, settled decisions), things leaving at
the wrong time (masking earlier pages, compaction at 75%), too much in up front (17K tokens of harness).

## Order of trust
1. **Reality**, rebuilt every step: the files, the last test run, running processes (the work-state reminder).
2. **Exact anchors**, pinned: the contract (DESIGN.md / CONTRACT.md), the checklist's Settled decisions, the file
   map with definitions. Code needs exact names; a conceptually right summary that says `--user` for `--person`
   breaks the tests.
3. **The concept and turning-point map**: what was tried, learned, ruled out — recorded by code as it happens
   (context_memory timeline), explained by the summary.
4. **The archive**: everything condensed, in full, by id (history_search), indexed by CONTEXT_MAP.md.

## Mechanisms (built)
- Timeline of turning points (`context_memory.add_event`): plan, decided, done, created, tests (state changes),
  error (first seen), reset, user, compacted. Pinned word for word at compaction; the summary is written from it on
  a template (goal / current understanding / turning points with exact names / current state).
- CONTEXT_MAP.md after each compaction: turning points + every archived item with its id; the summary head and the
  work state point at it.
- Ledger (`ledger.jsonl`): per step, the prompt by source (system, tools, pinned reminder, summary, user, assistant,
  tool results full vs stubbed, masked count) — to trace a bad step to its context.
- Snapshots: the conversation just before each compaction, for offline replay.
- `evals/context/compaction_quiz.py`: replays a snapshot through the old and the new compaction and scores how many
  recorded facts a model can still recall.

## Next
- Run the quiz on real snapshots (Bonsai as summarizer vs a stronger model vs code-only extraction).
- Phase-aware working set (plan / build / debug), driven by the checklist's current item.
- Point the model at the map at the right moments (before re-deriving, after an error seen before).
- A/B everything on the build benchmark, 3+ runs per variant (single runs mislead).
