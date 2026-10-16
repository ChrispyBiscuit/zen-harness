# Compact prompt target for long-running work

This source-only change targets approximately 20,000 estimated tokens immediately after compaction on Zazen's current 61,440-token Bonsai profile. It is not a hard truncation rule. A complete active tool-call/result pair or a required task fact may make a checkpoint larger.

## What changes

- The recurring reminder shows the active tracker item, blockers, next action and settled decisions. The full checklist remains in the tracker and is available to the model through `update_tracker`.
- The recurring build workbook shows module states and the latest outcomes. Full contracts and receipts remain available through `module_status`.
- The stable initial tool set covers ordinary file, shell, test, project-tracker and retrieval work. Other tool groups remain discoverable through `available_tools` and can be enabled; a newly enabled group remains stable until another change. This avoids changing the schema on every step, which would break the local prompt-cache prefix.
- Compact tool schemas retain names, parameter types, required fields, enums and defaults. Longer descriptions point to `read_guide('tool:<name>')` for complete usage.
- Compaction budgets the recent verbatim slice after the current system, tools and reminder cost, reserving space for the evidence-linked checkpoint. Older calls and results remain in the chat archive with retrieval IDs. The retained slice always starts at a step boundary; no call is separated from its result.
- The token estimator uses the tools actually offered to the model and includes the recurring reminder. Target misses are logged with the estimated resulting size.

The new policy is staged **off** (`compact_prompt=false`). It appears under Agent settings as **Extreme Context Compression (experimental)**. Setting `compact_prompt=true` enables it after explicit approval. Switching it off restores the prior tool surface, reminder and recent-history allowance for comparison or rollback; it does not switch off the existing checkpoint format.

## Local evidence before deployment

Saved FPS snapshot `compaction_2.json` was replayed offline with the same 61,440-token profile, no model inference and invalid synthesis responses to exercise the deterministic checkpoint fallback. The compact source prompt measured 19,853 tokens without the live tracker and 21,043 with the 28-item tracker and build policy. The live app was not modified. These are Zazen's token estimates; the first deployed trial must compare them with llama.cpp's actual prompt-token counts.

Focused context, ledger and offline harness tests: 137 passed, 2 expected failures. The replay and tests establish prompt size, exact request retention, schema shape, tool discoverability and paired recent calls/results. They do not establish that Bonsai solves long tasks better.

## Deployment gate

Do not deploy while the current FPS inference is running. After authorization, enable on a saved run and compare against the previous policy using the same starting state. Inspect actual prompt tokens, cache reuse, compaction frequency, repeated reads, missed settled decisions, tool-call validity, verified task progress and elapsed time. Revert `compact_prompt` if completion, retention or speed regresses. Do not enforce 20K by dropping required state.
