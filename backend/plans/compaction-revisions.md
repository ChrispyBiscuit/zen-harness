# Compaction revisions

A record of changes to how the agent compacts its context, and how to go back to each earlier behavior if a later
test shows a regression. Every change below can be switched off in settings, under `agent` in settings.json. No code
revert is needed.

## 2026-10-16

### 1. Smaller working context for slow models (commit 785cb06)

**What changed:**
- A model generating under 20 tok/s compacts at 35K tokens instead of near the window's end, about 53K on a 61K
  window.
- After compacting it goes down to 22K, instead of 72% of the compaction point (about 38K).
- The working set, meaning the current files re-added after compaction, gets its full allowance (9K) on slow
  models, not a share of the smaller budget.

**Why:** a 27B fell from 32 tok/s at 7K tokens to 15 tok/s at 50K. After compacting 55.7K to 37.9K it regrew in
12 minutes, and it re-read files it could already see.

**To restore the old behavior:** `"slow_compaction": false`.

### 2. Every older write/edit drops its file text (commit 785cb06)

**What changed:** write_file and edit_file calls older than the last 6 assistant messages drop the file text they
carried, whether or not the file was touched again. The text is on disk, and the working set re-adds the current
version. Before this, only calls whose file was later written, edited or read again were stripped. The trimming
still happens in masking batches, so the prompt prefix stays stable between batches.

**Why:** old call arguments were about a third of a 27B's history.

**To restore the old behavior:** `"trim_old_writes": "superseded"`.

### 3. Speed measured from llama.cpp's own timing (commit 34d234d)

**What changed:** the speed that decides slow-model compaction (and the thinking time cap) comes from the server's
`timings.predicted_per_second`. The streamed-chunk estimate is no longer used for either decision on llama.cpp.

**Why:** the estimate counted 17 tok/s for a 27B generating at 30, and wrongly switched it to the slow budget.

**To restore the old behavior:** none. This was a measurement bug. Turn off item 1 if the slow budget itself is
the problem.

### 4. A terse fixed-template summary (commit 150acc6)

**What changed:** the summary the model writes when the context compacts.

| | Before | After |
|---|---|---|
| Form | Four sections: goal and constraints, current understanding, turning points, current state | Five slots: GOAL, ESTABLISHED (with the root cause, file and line), RULED OUT (one line each), DECISIONS, NOW (the exact next action) |
| Length | At most 500 words | About 300 words, with "none" for an empty slot |
| Token cap | 1,600 (cached path) / 1,200 (fallback) | 700 |
| Narrative | Yes ("turning points", "current understanding") | No: only what is still true and useful |

**Unchanged:** the app keeps pinning its own facts beside the summary: the checklist, test results, changed files
and gate state.

**Why:** a 27B rediscovered the same root cause at least four times after compacting. Every summary word also
costs a slow model generation time.

**To restore the old behavior:** `"compact_summary": "narrative"`. The old prompt is kept in full as
`SUMMARY_PROMPT_NARRATIVE` in agentic.py.

### 5. Slow-model compaction 20% later (2026-10-16, after the first no-thinking run)

**What changed:** the slow-model tier now compacts at 42K tokens instead of 35K, down to 26.4K instead of 22K.
Models that aren't slow keep the normal point, near the end of the window (about 53K of 61K), which is already as
high as it can go while leaving room for a reply.

**Why:** run 2405352c was put on the slow tier by the old speed estimate, before the speed fix. It compacted five
times in 30 minutes, every 6 to 7 minutes, and kept losing what it had found.

**To restore the old behavior:** set `SLOW_COMPACT_AT` and `SLOW_COMPACT_TARGET` back to 35000 / 22000 in
agentic.py, or turn the tier off with `"slow_compaction": false`.

## How to check for a regression

- **Quick check:** `backend/evals/context/compaction_quiz.py` asks questions about a conversation after it is
  compacted. Run it with each setting and compare the scores.
- **Real check:** the roommate expense tracker build on the 27B, with thinking off, in a fresh folder for each
  setting. Compare:
  - compactions per run
  - steps between a compaction and the next successful edit
  - root causes rediscovered
  - passing tests at the end
  - wall time
- **Baseline:** the first run with all four changes is chat 2405352c in folder `bonsai25` (2026-10-16). It had no
  thinking, and its logs are in agent_log.jsonl.
