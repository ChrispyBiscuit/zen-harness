# Audit 03: step-loop guards, bugs, dead code, complexity

Scope: `agentic.py`, `agent_tools.py`, `web_backend.py`, `llama_local.py`, `debug_tools.py`, `history_archive.py`,
`factcheck.py`, `browser_agent.py`, `bg_tasks.py`, `agent_config.py`. This was a read-only audit: no servers were
contacted and nothing was run on the GPU.

> **Line numbers** come from the `agentic.py` snapshot of 2026-10-09 21:34 (4,772 lines). The file was being edited
> during the audit: project mode (`_start_project`, `_project_gate`, `_project_unfinished`) appeared partway through,
> so re-grep the anchors before you patch anything.

Severity scale: **S1** breaks or corrupts a run · **S2** wastes time (extra model calls, cache re-reads) · **S3** maintainability.

---

## 0. Top 12 (fix in this order)

| # | Sev | Finding | Where |
|---|-----|---------|-------|
| 1 | S1 | The repeat gate records **refused** calls. The retry that a refusal message asks for ("run this exact call again") is then blocked as a repeat, and keeps being blocked until some write or exec happens | `_run_tool` 3318 → `_record_call` 3125, `_repeat_of` 3112 |
| 2 | S1 | Running out of steps **throws away a finished answer**. Each final-check send-back costs a step, so an answer drafted on step N-1 and sent back for a check becomes "(Paused after N steps…)", with the real answer folded away as a draft | loop `else:` 3047-3055 |
| 3 | S1 | `_fc_from` (the index where the turn starts) goes stale after a mid-turn compaction. The fact check, behaviour check, `_built_software`, `_used_research` and `_edited_code` then read the wrong slice, so they are silently skipped or read earlier turns | 2644, `_maybe_compact` 3740 |
| 4 | S1 | When an earlier checklist still has open items, **every** new message counts as "resuming": no lean pass, no plan, and the final answer is nagged about the old items or **auto-closes them as done** (`closed at the final answer`), even when the question has nothing to do with them | 2660, 2905-2925 |
| 5 | S1 | `_same_attempt` only notices a change when a file's **hash** changes, and only for files given as absolute `C:\…` paths. Successive edits through relative paths or commands look like "no change", which leads to STOP THIS APPROACH and, after 6, a **forced answer in the middle of a build** | `_file_effect` 3455, `_PATH_RX` |
| 6 | S1 | Turning tools on and off is meaningless while every tool is always offered (`_stable_tools()` is always True for main), yet `_enable_mentioned` still reports tools as "newly enabled". That produces the false nag "You described using X, which was not enabled, so nothing ran" (counts against `_intent_nags`), plus a junk "Tools enabled from your plan" reminder | 2886, 4320, 3867 |
| 7 | S2 | **The system prompt changes mid-chat**, so llama.cpp re-reads the whole conversation: `{cwd}` changes on every PowerShell `cd`, `{workspace}` changes when the folder is first created, saved memory and AGENTS.md (which depends on cwd) are inlined, and `{roots}` grows in `_keep_result` | SYSTEM 47/51, `system_prompt` 1862, `agent_tools.py:502, 2831` |
| 8 | S2 | `_INTENT_RX` matches "going to", "let's", "I'll" or "I need to" anywhere in the last 300 characters. Short real answers ("it's going to rain") are handed off from the lean pass to the full agent, or redone with `tool_choice=required` up to 3 times | 471, 1568, 2893 |
| 9 | S2 | `_prefetch_parallel` runs read calls **before** the gates: the repeat gate, `_is_repeat`, the fan-out gate, the basic-path budget, walled domains and `_offered`. A refused search has already cost its network time and saved a result file | 3032, 1610 |
| 10 | S2 | The basic route calls `_needs_plan` on the **full** harness prompt, then runs on the basic prompt. That is two different prompt prefixes, so two full prompt reads, on the path meant to be cheap | 2729 |
| 11 | S1 | The `"project:"` prefix is not part of the `if/elif` routing chain, so the lean pass still runs. It may answer directly (project mode never starts) or overwrite `_effort` and `_depth` | 2694-2711 |
| 12 | S3 | `_log` has no test isolation: 1,188 of the 4,257 rows are `model: "fake"`, plus conv `t`/`plan_t…`/`pbtest`/`sim`/`gate-test`/`test-conv`; 120 `lean_error` rows, about 112 of them from test fakes | 33, 426 |

---

## 1. Guard / nag / gate inventory

### 1a. Per-call gates (`_run_tool`, 3135). Order matters: the first match wins

| Order | Gate | Trigger | Effect | Limit | Notes |
|---|---|---|---|---|---|
| (pre) | `progress=` field | any call carrying `progress` | `_apply_progress` ticks checklist items **before** any gate (3144-3148) | none | **Bug:** items are ticked even when the call is then refused or denied. Apply progress only after `ok` |
| 1 | basic block (3157-3170) | basic path: worker tools, tools not in BASIC_TOOLS, budget used up | refusal | after 2 budget refusals sets `_upgrade` | `_basic_refusals` is **never reset per turn** (1060). In a later turn the first refusal upgrades straight away |
| 2 | repeat gate `_repeat_of` (3112) | same READ/EXEC/NET call, same `_change_seq` | refusal + the head of the earlier result | none | **S1 #1**: refused calls are recorded too (3318 only skips a call that was itself a repeat) |
| 3 | project gate (3175) | project mode, a code file written before docs/PRD.md, DESIGN.md and research | refusal | none | New (in progress). The `" did not run:"` wording is right for `_watch_progress` |
| 4 | fan-out gate | `_fan_due` and a research tool before `gather` | refusal | 2 per turn | Fan-out is off and hidden in the UI, so effectively dead code |
| 5 | gather_wait / gather_busy | fan-out | ok=True plus a note | none | Dead with fan-out off. `asked` shadows the tool-name variable (3191) |
| 6 | unknown tool / bad JSON | | refusal | none | |
| 7 | tool not enabled (tool gate) | `_gated()` and not active and `_offered is None` | enables the tool, refusal | none | Never fires for main (`_active_names` returns every tool while all tools are offered). Live only for the dead executor |
| 8 | `_is_repeat` (3406) | same web_search / web_fetch this turn | refusal | 1 retry after an error | Overlaps with #2: two repeat systems with different keys |
| 9 | `_offered` restriction | checklist first, check-in, or "answer now" | refusal ("run this exact call again") | none | Feeds S1 #1 |
| 10 | walled domain | a site known to need sign-in | refusal | per chat | |
| 11 | read_guide already read | | ok=True note | | |
| 12 | update_tracker "Not saved" / "Checklist unchanged" | tracker updated with nothing done since, or identical args | ok=True note | | `_tracker_bounces` ≥2 hides the tracker (non-llama only) and adds a reminder |
| 13 | safety block / approval (`_decision`) | | refusal / ask | | |

### 1b. Post-call detectors (`_watch_progress`, 3507)

| Detector | Trigger | Effect | Limit / reset | Notes |
|---|---|---|---|---|
| `_recent` "Same call" (3553) | the same `name+args[:400]` 3 times in the last 10 calls | appends ⚠ | **never reset per turn** (created in `__init__`, 1041) | Counts across turns. `[:400]` truncation makes different write_file calls whose content starts the same count as identical. Refused calls are counted |
| `_same_attempt` (3476) | ≥70% token overlap, same tool, no file effect | warns at 3, STOP at 4-5, `_force_answer` at 6 | per turn | Runs only when "Same call" did not fire. Refused calls count. See S1 #5 |
| fail streak | `failed` ≥3 → hint; ≥ `max_failures` (6) → `_force_answer` | | per turn | "failed" covers PowerShell output not starting with `exit code 0` (unless it is a test run) and web errors. Unknown tool, JSON error, denial and safety block also count as failures |
| `_no_progress` | tool calls since the last checklist item finished (heavy web_search counts 2) | read by PROGRESS CHECK in `_reminder` | per turn | |
| exec handoff on repeat | executor repeats a call | handoff | | Executor is dead (hybrid off) |
| tests progress | more tests passing → `_no_progress=0` | | | |

### 1c. Per-step state in `_reminder()` (1913), sent at the tail of every request

BASIC_NOTE · WORKERS IDLE · FAN-OUT RUNNING · FIRST create checklist (`_need_plan`) · checklist + "You are on X" ·
routine check-in (`checkin_every`, 99 in settings, so effectively dead) · tracker bounces · **PROGRESS CHECK**
(`progress_check` 12, doubled for software builds: 1 nag, and at 2× **sets `_force_answer` from inside the prompt builder**,
1970) · executor handoff · verify-before-commit (code / research) · playbook note · force-answer text · plan mode.

- **S3:** `_reminder()` has a side effect (`_force_answer=True`). It also runs inside `_needs_plan`, `_plan_phase`,
  `_factcheck` and `_think_frame`, because all of them call `_payload_messages()`. A step whose `tools` was already
  computed as True then gets a prompt saying "Stop calling tools now" while tools are still offered. Fix: compute the
  force-answer decision in the loop, before choosing `tools`, and keep `_reminder` pure.
- In basic mode an old (closed) checklist is still shown together with "add progress=…", which contradicts BASIC_NOTE ("No plan, no checklist").

### 1d. Final-answer acceptance chain (`run()`, roughly 2846-3030). Every `continue` costs one model call and one step

| Order | Check | Budget per turn | Skipped when forced | Retract shown |
|---|---|---|---|---|
| 1 | reality note (`_phantom_fixes`) when there are no calls | 3 (`_reality_checks`) | no | no |
| 2 | inbox / notes pending | none | | |
| 3 | gather running: nudge ×2, then block-wait | 2 | wait only | no |
| 4 | plan nag (no checklist yet) | 2 | yes | yes |
| 5 | intent nag "tool X not enabled" | shares 3 with #6 | yes | **no** |
| 6 | narration redo (pop + `tool_choice=required`) | shares 3 | yes | **no**: the narration stays visible |
| 7 | auto-close open items (≥2 tool calls, ≤2 open or ≤half) | | | |
| 8 | open-items nag | 1 | yes | yes |
| 9 | empty reply nag | shares 2 with #10/#11 | yes | no |
| 10 | forced answer was empty or a stripped text tool call | shares 2 | forced only | yes |
| 11 | forced answer "announced" a next step | shares 2 | forced only | yes |
| 12 | quote check (`quote_check`, default off) | 2 | yes | yes |
| 13 | project docs incomplete | 1 | yes | yes |
| 14 | behaviour not tested | 1 | yes | yes |
| 15 | math check | 1 | yes | yes |
| 16 | repeats an earlier answer | 1 | **no** | yes |
| 17 | fact check (+ future dates) | 1 send-back; round 2 adds a suffix | no | yes |
| (end) | ask_user → `_LeanDone` after the tool step | 1 per request | | |

Worst case is about 17 extra generations before an answer is accepted. Nothing keeps the last candidate answer, which is the cause of S1 #2.

**Conflicts and interactions found**

1. **Repeat gate vs. refusals** (S1 #1). Concretely: read_file is refused by `_offered` ("make the checklist first, then
   run this exact call again"), update_tracker runs (NOTE kind, so `_change_seq` does not move), read_file again is
   blocked by `_repeat_of`, whose message quotes the old refusal as "the result". The same happens after a basic-budget
   refusal followed by go_extensive, and after a fan-out gate refusal on read_document/search_text followed by gather.
   **Fix:** set `ran = True` only where `reg.call` or a prefetched result is used, and call `_record_call` only when
   `ran`. Apply the same rule to `_recent` and `_same_attempt` (pass `refused` into `_watch_progress` instead of
   sniffing the text).
2. **Four repeat detectors on one event.** `_repeat_of`, `_is_repeat`, `_recent` "Same call" and `_same_attempt` (plus
   the executor handoff). An identical third call gets the repeat-gate refusal **and** the "Same call ×3" ⚠ in the same
   result. The detectors also disagree on what "something changed" means: `_change_seq` moves on any successful
   WRITE/EXEC, while `_effect_seq` moves only on an observed hash change of an absolute path. **Fix:** one
   `CallLedger` per turn with a single key function (search keys from `_call_key`, exact args otherwise), one
   "world changed" counter (any successful WRITE/EXEC/INPUT, or an observed file change), and one escalation ladder.
3. **Refusal detection by string sniffing.** `_watch_progress` (3509) treats `"Not run again"`, `"Not saved"`,
   `" did not run:"` and `"was not enabled"` as refusals. "Checklist unchanged", "Skipped: … sign-in", "Unknown tool",
   "arguments were not valid JSON", "Blocked by the safety policy" and "The user denied" therefore count as **failures**
   (fail streak, which can force an answer) and as attempts. Return a structured `(ok, refused, text)` from the gate
   section instead.
4. **The narration redo can block a real answer** (S2 #8): the pop plus `tool_choice=required` makes the model call
   *some* tool after it has finished. On **Ollama**, `_call_ollama` never reads `_require_call`, so the redo replays
   the same prompt minus the narration and usually gets the same narration back, 3 times. `_require_call` is also never
   reset per turn: after Stopped, the next turn's first step is forced to make a call.
5. **The repetition cut writes into history from inside the stream** (`_should_cut` 4427-4441). The "your last reply
   started repeating" reminder is appended **before** the cut assistant message, so the order is wrong. If the cut reply
   has no tool call it goes on through the final-answer chain as a candidate answer. It also fires during side calls
   (lean pass, plan phase, in-place summary), which pushes a "take one concrete action with a tool call" note into the
   main history. **Fix:** have `_should_cut` return a flag and let `_complete` or `run()` add the note after the message.
6. **Guards that end runs early.** `_force_answer` is set from three places (same_attempt 6, fail streak 6, PROGRESS
   CHECK 2×12/2×24) and never cleared within a turn. In project mode `max_steps` doubles (2769) but the progress-check
   limit only doubles for `_built_software`, so research and design phases with few checklist ticks reach a forced answer.
7. **The `_empty_nags` budget is shared** by "empty reply" and the two forced-answer checks: two earlier empty replies
   mean a blank forced answer ends the turn with `""`.
8. **The open-items auto-close falsifies trackers** (S1 #4): it marks the *old task's* items done whenever the model
   made ≥2 tool calls for an unrelated question. **Fix:** decide `resuming` with the interpretation or decision call
   (or `_continues_work`) rather than "the tracker has open items", and auto-close only items created this turn.
9. **`_repeats_earlier_answer` can block a legitimate re-answer** when the user asks the same question again on
   purpose ("again?", "regenerate"). It is not skipped under force either, so it can fire after tools are off and demand
   "act on it with your tools". Skip it when `_force_answer` is set.

---

## 2. Bugs and edge cases

### S1
- **Steps run out → the answer is lost** (3047). Fix: keep `self._last_candidate` (the last `final` that reached the
  checks) and use it, marked "(checks unfinished)", in the `else:` branch and in `except Stopped`, which today recovers
  only quote drafts. Better still, set `_force_answer` when `step >= max_steps - 2` so the run ends with a report.
- **`_fc_from` stale after compaction** (2644 vs `self.history = head + self.history[cut:]` around 3740; the cut may
  fall inside the current turn by design). Fix: in `_maybe_compact`, after the splice,
  `self._fc_from = max(0, self._fc_from - cut + len(head))` if `cut <= _fc_from` else `len(head)`. Better: tag each
  turn's messages (`m["_turn"]=n`, stripped before sending) and filter on the tag.
- **The lean pass crashes after 6 tool rounds** (`for _round in range(6)`, 1517): if every round is a calculate or
  encyclopedia call, `text` is never assigned and `if text and…` (1568) raises UnboundLocalError. `run()` does not
  catch it, so the whole turn fails. Fix: `text = ""` before the loop.
- **Compaction removes the user's words with the image** (3677): `self.history[i]["content"] = "(older image removed)"`
  replaces a multimodal *user* message entirely, including the request text and the recall/Reference notes added to it.
  Fix: `m["content"] = _text(m) + " (image removed)"`.
- **"project:" routing** (2694): put it in the `if forced_deep … elif …` chain (`elif project_prefix:`) so the lean pass
  is skipped.
- **Prefetch ignores gates and the budget**: 6 parallel lookups run even when the basic budget has 1 call left or the
  calls repeat. The prefetched args are also **not** passed through `normalize_args` the way `_run_tool` does (although
  `Registry.call` normalises internally). Fix: run the gate section first (a pure `_gate(name, args)`), and prefetch only
  the calls that pass.
- **Stopped is swallowed by side calls**: `except Exception` in `_lean_pass` (1523), `_needs_plan`, `_interpret`,
  `_think_frame`, `_lean_facts_ok` and `_summarize_in_place` (3706), because `Stopped` subclasses `Exception` (839).
  The worst case is compaction: Stop pressed during the in-place summary → the fallback `_summarize` → `_plain` runs a
  **non-streaming 900 s request that never checks `should_stop`**. Fix: `class Stopped(BaseException)`, or add
  `except Stopped: raise` before every broad handler.

### Per-turn state (missing resets / getattr masking)
Not reset per turn: `_recent` (1041), `_basic_refusals` (1060), `_require_call`, `_retype_bucket` (the first retype
check of a new call is skipped when its size bucket equals the previous call's), `_prefetched` (left over after Stopped).
`_archived` is a set of `id(m)`; ids are reused after GC, so a new message can be wrongly treated as archived. Use a
flag on the message dict instead.
**Root fix:** move every per-turn field into a `TurnState` dataclass created at the top of `run()`
(`self.t = TurnState()`). This removes about 40 lines of resets and about 30 `getattr(self, "_x", default)` calls
(`_reality_checks`, `_calls_seen`, `_fetch_sizes`, `_tracker_bounces`, `_guides_read`, `_no_progress`, `_effect_seq`,
`_quote_drafts`, …) that today hide ordering bugs.

### Threading
- `browser_agent.BrowserManager.call` (206): when the caller times out, the job stays queued or running on the browser
  thread and still executes later, possibly a click or typing after the agent has moved on. Every later job queues
  behind a hung one. Fix: put a `cancelled` flag in `box` that `_loop` checks before running a job; restart the browser
  thread when a job exceeds 2× its timeout.
- `_prefetch_parallel` builds `Registry(wctx, None)` per call on a `copy.copy(ctx)`. Shared mutable containers
  (`ctx.roots`, `ctx.changed`, tracker) are mutated from worker threads. `_keep_result`'s check-then-append on
  `ctx.roots` can add duplicates. Low risk, but use a lock or pass immutable views.
- `web_backend.start_agent` checks `r.running()` without the host lock, and `run_finished`→`start_agent`
  (auto-continue) runs on an engine thread, so two runs can start on one engine. Two conversations' agent runs can also
  run at once against the single llama slot 0, evicting each other's prompt cache. Consider a global "one agent run on
  the main model" queue.
- `bg_tasks.start`: if `Popen` fails, a `BgProc` with `proc=None` stays in `procs`.

### Cache-breaking prompt changes (S2)
- `{cwd}` in SYSTEM (47) plus `_instructions()` (which depends on cwd) means every `cd` in PowerShell changes the system prompt.
- `_workspace_text()` "(created when first used)" (1803) flips once, on the first write.
- `read_memory()` is inlined in the system prompt (1901): a `memory_save` during a turn changes it.
- `_keep_result` appends to `ctx.roots` (`agent_tools.py:2831`), which is part of `{roots}`.
- Fix: keep the system prompt fixed per chat (roots, workspace path, date) and move cwd, workspace existence and memory
  deltas into `_reminder()` at the tail. Add a debug assertion that logs when `system_prompt()` differs from the last
  request's.
- Smaller mutations of the turn's user message after it was already sent (hint, playbook, think frame, project note
  appended after `_needs_plan` has read it) re-read only the tail. That is acceptable, but do all the appends **before**
  the first slot-0 call.
- The basic↔extensive switch and the `_needs_plan` call on the full harness (S2 #10) are full re-reads.
- Single slot: `parallel` is set only in the `qwen3.5:9b` override. For the current `qwen3.6-35b-a3b-reap48`,
  `_n_slots=1`, so `id_slot` is never sent and the lean pass and lean fact check share slot 0 with the conversation.
  Every lean→extensive turn evicts the conversation prompt, unless llama.cpp's `-cram` host cache restores it. **Verify**
  in the server log (`prompt cache hit`); if it does not, set `parallel: 2` with `kv_unified` in that override.
- `history_archive.add` (and `ha.entries`) re-parses the whole JSONL on every add. Compaction archives dozens of
  messages, so the cost is O(n²) over a file holding full tool texts. Keep a per-conv counter, or count lines without
  parsing.
- `_history_tokens()` runs `system_prompt()`, which does a disk read of memory and walks the folders for AGENTS.md,
  after **every tool result** (`emit_context`, 3324) and 3-4 times per compaction check. Cache the result per step.
- `_file_effect` md5-hashes up to 60 files on **every** tool call. Use `(mtime, size)` instead.

### Path vs. tier divergences
- The lean pass runs math and quote checks, the `_announces_action` handover and a model-based fact check (`_lean_facts_ok`).
  The basic and extensive paths run the full chain (repeat, behaviour, factcheck, open items, …). A lean answer is never
  checked for repeating an earlier answer and never gets the mechanical fact check.
- The basic path still runs `_think_frame` (2744 is not gated by `_basic`). Guide hints and playbooks are skipped on the
  basic path, and `_go_extensive_now` (1706) does not add them when it upgrades mid-turn either. `_go_extensive_now`
  sets `_need_plan` only `if not self.ctx.tracker`, so with an old closed tracker the upgraded turn gets no checklist.
- A `lean_error` (exception) leaves `_depth=""`, so the turn goes to the heavier extensive path, not basic.
- On the Ollama backend the lean pass is skipped (1502) after `_supplement` already spent its web search.
- `_supplement` (a web search, about 3 s) runs on "continue" and resuming turns, where a background Reference is noise.

### Smaller bugs
- `BASIC_SYSTEM` line 946: `"…at the same time.- If the task…"` is missing a newline.
- `_apply_playbook` appends to `self.history[-1]` (2548); everything else uses `self._turn_user_msg`.
  `m["content"] += note` raises if content is None.
- The narration redo and the "not enabled" intent nag emit no `answer_retract`, so the narration stays shown as an answer bubble.
- Several error rows are logged without `conv` (`recall_error`, `progress_error`, `factcheck_error`, `playbook_error`,
  `compact_error`, `compact_inplace_error`, `plan_decision_error`, `plan_retry_error`).
- The conv-folder sanitiser is duplicated three times (`agentic._saved_results`, `history_archive._dir`,
  `agent_tools._safe_name`). They agree today; make all three call one helper.
- `_keep_result` names files `r{count+1}`, so a deleted file leads to an overwrite.
- `debug_tools` failures are `print`ed to a windowless process's stdout, which nobody sees. Use `_log` instead.

---

## 3. Dead and stale code

| Item | State | Action |
|---|---|---|
| Thinking time-cut: `limit = 0.0` (4562, 4721), `cut`, `_cut_step` (4444), `_skip_think` (5 refs) | unreachable | delete; keep `_cut` only for the too-long path |
| `THINK_BUDGETS` (967) | unused | delete |
| `_progress_nagged`, `_singles` (2649-2651) | set, never read | delete |
| `plan_tokens` (config and settings) | not read; `turn["plan_tokens"]` is a stat | remove the key; budget comes from `EFFORT_PLAN` |
| `think_level` | **still load-bearing**: `effort` is missing from settings.json, so `think_max()` falls back to `think_level: "off"`, which means **no thinking at all**, while `think: true` is saved | save `effort` explicitly, migrate `think_level` → `effort`, and drop `think` (overwritten in `__init__` 1069 anyway) |
| `hybrid` executor: `_start_executor`, `_exec_history`, `_pick_role`, EXEC_EXCLUDE, EXECUTOR_NOTE, `_handoff`/`_exec_said` branches in `run`, `_reminder`, `_watch_progress` | disabled, hidden in the UI | move behind one `if self.exec_base` module, or delete (about 150 lines threaded through the hot path) |
| `fanout`: `_gopher`, `_collect_gather`, `_wait_workers`, fan-out gate, gather_wait/busy, WORKERS IDLE / RUNNING reminders, PLAN_PROMPT item 7, `_needs_plan` parallel_parts | disabled, hidden | same; it is the second-largest source of branches in `run()` and `_run_tool` |
| `interpret_first` / `_interpret` + INTERPRET_PROMPT/SCHEMA | off, hidden | delete or keep behind an experiment flag |
| `think_frame` / `_think_frame` + thinking.py | off, not in the UI | same |
| `tool_gate` | `_gated()` True but every tool always offered, so `_active_names` = every tool; only the side effects survive (S1 #6) | make `_gated()` return False when `_stable_tools()`; delete `_enable*` if the gate is never coming back |
| `quote_check` (QUOTE_RULE, `_quote_note`, `_best_quote_draft`, quote drafts in Stopped) | not in defaults or UI, default off | keep or delete deliberately |
| `checkin_every: 99` | check-in gate (198 calls) and reminder can never fire with max_steps 100 | remove the check-in machinery or set a real value |
| `progress_check`, `stable_tools`, `kv_unified` | read, not in defaults or UI | document in `DEFAULT_AGENT` |
| `compress_results` in settings.json | LLMLingua leftover (code is in `agent_trash/llmlingua/`) | delete the key |
| `_hide_tracker` | effective only when backend ≠ llama | fine; comment |
| `web_backend.serve_headless` | only reference is its own definition | check `app_web.py`, delete if unused |

No unused module-level functions were found in the audited files; the dead code is **dead branches behind settings**.

---

## 4. Complexity hotspots

| Function | Lines | Proposed split |
|---|---|---|
| `run()` 2598 | about 460 | `_begin_turn()` (creates `TurnState`, adds user-message notes) → `_route()` returning `Route(depth, effort, need_plan, project)` (lean/basic/extensive/continue/deep/project as one `if/elif` table) → `_prepare_extensive(route)` → `while: step = self._step()` → `self._accept_final(msg)` |
| final-answer chain inside `run()` | about 180 | a list of `FinalCheck(name, budget, skip_when_forced, predicate, note, retract_reason)` run in order; one place keeps `last_candidate`, one retract emitter, one budget table. This makes every conflict in §1d visible and testable |
| `_run_tool` 3135 | about 200 | `_gate(name, t, args) -> Refusal|None` (ordered list of gate callables, pure, also used by prefetch) → `_execute()` → `_postprocess()` (wall/404 notes, `_watch_progress`, logging, history) |
| `_call_openai` 4452 | about 240 | `_build_body()` → `_open_stream()` (Responses fallback, vision retry, tool-parse retry) → `_consume_stream(StreamState)` (deltas, drafts, too-long cut, repetition) → `_finish()` |
| `prepare()` 1076 | about 160 | `_llama_args(fit, draft, use_fan)` as a pure function (unit-testable against measured profiles) |
| `_lean_pass` 1453 | about 157 | `_lean_messages()` / `_lean_tool_round()` / `_lean_verdict()` |
| `_maybe_compact` 3636 | about 112 | `_stub_old_results()` / `_choose_cut()` / `_summarise()` / `_splice(head)`; the splice also fixes `_fc_from` |
| `_reminder` 1913 | about 110 | pure; one small function per part; no state writes |

Also extract the "append note to the turn's user message" snippet, repeated about 8 times (recall, resuming, hint,
`_supplement`, `_interpret`, `_think_frame`, `_apply_playbook`, `_start_project`), into `_annotate_turn_msg(text)`.

---

## 5. `_log` test isolation

`LOG = os.path.join(APP_DIR, "agent_log.jsonl")` (33) ignores `LWS_DATA_DIR`, which web_backend tests already use
for isolation, and `_log` (426) has no switch. Current log: 4,257 rows, of which 1,188 have `model: "fake"`, 81 have conv
`plan_t…`, 78 `pbtest`, 58 `sim`, plus `t` / `gate-test` / `test-conv`. About 112 of the 120 `lean_error` rows are
`fake() got an unexpected keyword argument 'raw_tools'`, i.e. stale fakes, not product errors. Fixes:
1. `LOG = os.environ.get("LWS_AGENT_LOG") or os.path.join(os.environ.get("LWS_DATA_DIR") or APP_DIR, "agent_log.jsonl")`,
   and make the test harnesses set `LWS_AGENT_LOG` to a temp file (or `os.devnull`).
2. Add `rec.setdefault("conv", None)` plus a `"src": "test"` marker when `os.environ.get("LWS_TEST")`.
3. A one-off cleanup script that keeps rows whose conv is a 32-hex id and whose model is not `"fake"`. Back up first.
4. Size-based rotation (for example at 20 MB, rename to `.1`).
5. Update the test fakes to accept `**kw`, so a signature change (`raw_tools`) fails loudly in the test instead of
   logging as a lean_error in production.

---

## Summary

The guards were each added for a real failure, but they share no common idea of a refusal, a repeat, "something
changed" or "this turn". Five of the findings come from that:
- the repeat gate blocks the retries that other gates ask for;
- refused calls feed the failure and stuck counters;
- `_fc_from` and other per-turn indexes break after compaction;
- per-turn fields leak across turns;
- tool-gate side effects survive after the gate itself stopped mattering.

The two fixes with the biggest effect on correctness:
1. **Only record and count calls that actually ran.**
2. **Keep the last candidate answer when steps or checks run out.**

The biggest time saving is keeping the system prompt fixed: move cwd, workspace existence and memory into the tail
reminder, and stop routing basic-path decisions through the full harness prompt.

For maintainability, the order is:
1. a `TurnState` dataclass;
2. a gate list shared by `_run_tool` and the parallel prefetch;
3. a `FinalCheck` table;
4. one call ledger replacing the four repeat detectors.

After that, delete the disabled hybrid, fan-out, interpret, think-frame and thinking-cut branches, about 500 lines
threaded through `run()` and `_run_tool`.
