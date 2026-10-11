# Reference index ("playbooks"): build plan

*2026-10-08. Goal: give local models the task-level judgment they lack — framing, sources, dead ends, when to stop —
from a local library of short playbooks matched to each new request. Free, private, instant; measured before it
becomes the default.*

## Design in one paragraph

A `playbooks/` folder of ~30 markdown files, one per *task pattern* (never per task). Each is indexed in the existing
memory store (new kind `playbook`) by its title, trigger phrases and example requests — matching on intent, not on
the body. At the start of a **new** task (the same gate as the plan decision: not "continue", not small talk, not an
open checklist), the request is matched with the store's hybrid search; the best playbook above a similarity
threshold is appended to the user message as a `<system-reminder>` block, exactly how memory recall is injected today
(end of the prompt → the llama.cpp cache stays valid). The planner is told to follow it and write only the
task-specific details. Successful runs can leave a one-line **experience note** under the playbook ("source X worked",
"site Y is a dead end"), shown with its date and kept editable. No match → nothing injected (later: the API briefing).

## What already exists and is reused

| Piece | Where | Reuse |
|---|---|---|
| Hybrid search (FTS5 + bge-small, RRF, `min_sim`) | `memory_store.py` `search()` | new kind `playbook`, `playbook_note` |
| Recall injection, cache-safe | `agentic.py` `_recall()` → note appended to the user message | same path, one more section |
| New-task gate | `_needs_plan()` / `_NO_PLAN_RX` / open-checklist skip | retrieve only when a plan decision would run |
| Guides + `read_guide` | `guides/*.md`, `_guide_hint()` keyword triggers | guides become playbooks' "how-to" references; keyword triggers replaced by retrieval |
| Memory panel | `MemoryPanel.tsx`, `/api/memory` | a Playbooks tab (list, on/off, edit, "fired in this chat") |
| Eval suite | `evals/agent_eval` (22 tasks) | A/B with the feature flag |

## Playbook format

```markdown
---
name: collect-real-examples
title: Collect N real-world examples of something
triggers: find real examples, collect samples, show me N actual, gather instances
examples:
  - "find me 10 real resumes of senior engineers"
  - "collect 5 examples of company privacy policies"
  - "show me real landing pages for SaaS pricing"
applies_when: the user wants a set of genuine artifacts/people/pages, not templates or advice
---
**Done looks like:** N distinct, genuine items, each with its source URL and 1–2 lines of why it qualifies.
**Approach:** 1) decide what "genuine" means and where such items are public; 2) one broad web_search with 3 phrasings
(what the real thing would contain, excluding "template", "sample", "example"); 3) shortlist; 4) web_fetch the
candidates in one multi-page call and keep the ones that qualify; 5) repeat with new angles for what's missing.
**Tools:** web_search (pages=0 to scout, then pages=4–6), web_fetch(urls=[...], question=...), artifact_create for the list.
**Pitfalls / dead ends:** SEO template sites dominate results; sign-in walls (LinkedIn-type) — use the summary in the
search result, don't open; don't guess URLs; a site's own search page is usually blocked.
**Verify:** each item was read, not inferred from a snippet; no duplicates; counts match.
**Stop / ask:** after ~8 searches with < half the target, report what is realistically available and ask whether
a substitute (public profiles, anonymised databases, portfolios) is acceptable.
```

Rules: 200–400 tokens of body; strategy, not tool syntax (syntax lives in tool descriptions); placeholder examples
only; **no test-task nouns** (checked by a grep in the eval step); every playbook has a *Stop / ask* section.

## Initial library (~30, pattern-level)

Research: fact lookup with verification · compare options and recommend · collect N real examples · latest news /
status of X · profile a company or person from public info · find a document/report/dataset · technical how-to /
troubleshooting lookup · price / availability check.
Files & data: analyse a data file (totals, trends) · clean / transform a dataset · find something in a folder or
project · summarise a document · merge / reconcile files · log / error analysis.
Build & write: write a document from notes (memo, report) · build a deck · build a spreadsheet with charts · write or
fix a script · write code to automate a task · create a diagram/flowchart · draft an email or message.
Operate: run and interpret a command / check system state · schedule or monitor something · organise / rename files
· install or configure a tool.
Judgment / meta: ambiguous request (clarify first) · infeasible or blocked request (report and offer substitutes)
· long multi-part request (split, checklist, deliver incrementally) · follow-up on earlier work (resume the
checklist) · "remember / recall" requests (memory tools).

## Retrieval and injection

1. **Index:** at app start and when a file under `playbooks/` changes (mtime), upsert each playbook as kind
   `playbook`: `text` = title + triggers + examples + applies_when (the matching surface), `source` = path,
   `title` = name. Body is read from the file at injection time (always current, no re-index on edits to the body).
2. **Match:** `search(request, ("playbook",), k=3, min_sim=T)`. Start T = 0.62; tune on the phrasing test below.
   Take the top hit; add a second only if its score is within 15% of the first and it is a different pattern (e.g.
   "collect examples" + "infeasible request"). Hard cap: 600 tokens injected.
3. **Inject:** append to the user message, after recall: `<system-reminder>Playbook "…" (edit in Settings →
   Memory → Playbooks): …body… From earlier runs: • 2026-10-08 …note… </system-reminder>`. Emit an event so the UI
   shows a "Playbook: …" chip on the Approach card.
4. **Planning with a playbook:** the plan prompt adds "A playbook for this kind of task is above: follow it; write
   only what is specific to this request (sources, files, names), ≤ 250 tokens." For small models this both improves
   the plan and shortens it (ties into the speed plan's plan cap).
5. **Experience notes:** at wrap-up of a turn that finished its checklist (not forced), the final-answer step gets one
   extra line in the reminder: "If you learned something reusable for this kind of task (a source that worked, a dead
   end), call playbook_note(pattern, note) — one short sentence, no task-specific data." Stored as kind
   `playbook_note`, source = playbook name, dated; the 5 most recent per playbook are injected. Dead-end detections
   the harness already makes (sign-in walls, 404s) are added automatically. Notes are visible/editable/deletable in
   the Playbooks tab. Guard: notes never change the playbook body; a note containing a user's own names/paths is
   rejected (simple check) — notes are *where to look*, not *what was found*.
6. **No match:** nothing injected. Hook point for the optional API briefing (separate feature, off by default).
7. **Setting:** `playbooks: on/off` (default off until the eval says otherwise); per-playbook enable in the UI.

## Measurement (before it becomes default)

- **Matching precision:** 40 held-out request phrasings → expected playbook (or "none"). Report hit rate and
  wrong-match rate at thresholds 0.55 / 0.62 / 0.70; choose the threshold with ≤ 5% wrong matches.
- **Overfitting check:** grep playbooks and notes for the eval tasks' nouns; must be empty.
- **A/B on the eval suite:** flag off vs on, same model (Qwen3.5-9B first, then the 35B), 22 tasks: success rate,
  steps, generated tokens, wall time, time-to-first-real-source on research tasks. Keep if success ≥ baseline and
  steps/time drop; otherwise fix the playbooks that fired on failed tasks and re-run.
- **Guides regression:** the Office/document tasks must still read their guides when a playbook replaces the keyword
  trigger (the build playbooks point at `read_guide(...)`).

## Build order and effort

| # | Step | Effort | Depends on |
|---|---|---|---|
| 1 | Loader + index (kind `playbook`), retrieval, injection, flag, Approach chip | ½ day | – |
| 2 | Author 30 playbooks (drafted with a strong model, then reviewed by you), phrasing test set | ½–1 day | – |
| 3 | Threshold tuning + overfitting grep + eval A/B on the 9B | ½ day (+ GPU time) | 1, 2 |
| 4 | Experience notes (`playbook_note`, auto dead ends), Playbooks tab in the Memory panel | 1 day | 1 |
| 5 | Retire `_guide_hint` keyword triggers; build playbooks reference the guides | ¼ day | 2, 3 |
| 6 | Optional: API-briefing fallback when nothing matches | ½ day | 3 |

Total ≈ 3–3½ days. Steps 1 and 2 can run in parallel; nothing is enabled by default until step 3 passes.

## Risks and mitigations

- *Wrong playbook fires* → threshold + second-opinion rule + chip in the UI so you see what fired; off-switch per playbook.
- *Advice followed too literally by small models* → every playbook has alternatives and a Stop/ask section.
- *Notes drift into task-specific memory* → notes capped, dated, reviewed in the UI, rejected when they contain names/paths.
- *Context cost* → ≤ 600 tokens, injected once per task, counted in the eval.
- *Staleness after harness changes* → playbooks are strategy-only; re-run the phrasing test and the A/B after tool changes.
