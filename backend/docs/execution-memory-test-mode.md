# Execution Memory Test Mode (experimental)

This opt-in setting keeps a code-owned record of actual tool attempts and results. It complements either legacy structured compaction or Extreme Context Compression. It is off by default. The Legacy compaction checkbox is the UI inverse of `compact_prompt`; the two compaction choices cannot both be active. Test Mode is independent, so the three planned trial arms are legacy, extreme, and extreme plus Test Mode.

The harness writes a `started` event before dispatch, an exact arguments file, an exact result file and a `finished` event after the result. A small atomic projection stores the latest 40 completed actions and unresolved calls. On process restart it reconciles the projection from the append-only event log. The recurring reminder shows the active checklist item, next action (or an explicit missing-action notice), and up to eight recent observed actions. `history_search(entry='A…')` retrieves exact inputs and outputs. The ordinary compaction archive continues to work.

Tool execution success means the tool produced a result, not that the task is complete. Tracker claims remain claims; build/test receipts and actual project state remain the evidence for completion. A `write_file` or `edit_file` action records the resulting file hash when the path is accessible. Shell commands may mutate files outside a named path, so their scope is not inferred. User requests, checklist, module workbook, code, and journal retain their respective authority; Test Mode links them rather than replacing them with a model-written summary.

The extra state is a volatile tail message, outside the stable system/tool prefix. The full journal is never injected into the prompt. The view is capped at 3,600 characters and shares the existing prompt-token budget; Extreme mode may therefore retain a shorter raw suffix to stay near its 20K target. A large active step can still exceed that target rather than breaking a call/result pair. No additional model call is made for routine journal updates. Disk synchronization on every action is intentional for this experiment and its wall-time cost must be measured.

## Evaluation gate

Compare repeated identical tasks on one model, with the same context, tools, sampling, and environment:

1. Legacy structured compaction: `compact_prompt=false`, `execution_memory_test_mode=false`.
2. Extreme Context Compression: `compact_prompt=true`, `execution_memory_test_mode=false`.
3. Extreme plus Test Mode: `compact_prompt=true`, `execution_memory_test_mode=true`.

Measure task success with independent checks, facts and next-action retention after each compaction, repeated reads, failed tool calls, actual prompt tokens, cached versus newly read tokens, wall time, disk writes and peak memory. Record mode and settings per run. Do not attribute a difference to memory alone if model, task state, or cache warm-up differs. Stop or roll back a mode that degrades long-horizon completion. The current FPS run must be saved before deploying or switching models.
