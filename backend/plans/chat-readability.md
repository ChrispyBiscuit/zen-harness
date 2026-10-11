# Chat readability: one run per question (plan, 2026-10-10)

## What past chats look like now (inspected via `?replay=history`, 71 saved chats)

| Problem | Example |
|---|---|
| **Drafts posted as answers** | "Draft answer — revised after checks" bars, often several, sometimes *before* the work starts ("Top 5 whales": 4 drafts). |
| **Narration reads like answers ("double posts")** | "The folder exists… I'll create…", "I'll use the host Python…" render as full replies with their own speaker button, between tool rows. |
| **Duplicated media and checks** | The same image carousel twice ("2 places", then "5 places" with the same photos); an intermediate fact check ("sent back to fix") plus the final one. |
| **Every step equally loud** | 10–20 tool rows, red failures, and a "Reasoning" toggle between most steps; the "Approach" card mid-run. |
| **The answer doesn't stand out** | The final answer looks like any narration line and comes last, after a wall of steps; its stats line sits under it. |
| **Bug** | A tool row shows `record [object Object],[object Object]…`. |

## Plan

1. **Group each question into a turn**: your message → **run summary** → **answer** → **after** (files-changed card, the
   *last* fact check, delegated-task results).
2. **Run summary line** (one row): status icon, "Worked 4m 37s · 15 steps · 19 tool calls", a red "1 failed" pill
   when something failed, "2 drafts revised" when drafts exist. Folded by default once the run is done; open while it
   runs (live), and it folds itself when the answer lands. Click to open.
3. **Inside the fold, in order**: understanding, playbook, approach, tool steps, reasoning, earlier fact checks,
   discarded drafts, and narration rendered as quiet small text (no speaker button, no stats, no carousel).
4. **The answer**: rendered as today (markdown, carousel, citations, read-aloud), without its stats line (the run
   summary carries it). Only the answer shows images, so carousels no longer repeat.
5. **Short replies** (no tool steps): no run summary; just the answer (reasoning toggle kept).
6. **Fix** the `[object Object]` tool summary (show list/object arguments readably).

Done when: the five inspected chats read as message → one line → answer, with nothing duplicated; a live run still
shows its steps while it works; replay checks pass (fixture, build-run, history).
