# Audit 01: Pipeline, Latency and Waste (agent_log.jsonl, 2026-10-08 to 2026-10-09)

Read-only audit. Sources: `agent_log.jsonl` (4,252 lines), `conversations.json` (36 chats, 65 user messages),
`app_server.log` (process restarts), `C:\Users\user\llama.cpp\swarm_server.log` (llama-server timings for the
last server session, about 21:21 to 21:35 on 10-09). Helper scripts are in
`C:\Users\user\AppData\Local\Temp\claude\audit\` (`main.py`, `sup*.py`, `fc.py`, `conv_sum.py`, `rx.py`).

## 0. Method and caveats

- **Excluded as test pollution:** conv `null` (2,132 lines, all from offline unit runs; every `turn`/`tool` among them has
  `model: "fake"`), plus `t`, `gate-test`, `sim`, `test-conv`, `plan_t`, `pbtest` and `interp-smoke-*`.
  What remains is **67 real user messages in 36 chats, about 16,800 s (4.7 h) of wall-clock time**. Of that, 5
  messages are from 10-08 and the rest from 10-09.
- **How messages were split:** log events are bucketed by the user-message timestamp `t` in `conversations.json`.
  The end of a turn is `recall.t + stats.seconds`. `turn.t0` is set right after `_recall()`, so `stats.seconds`
  does not include engine prepare or recall time. That gap is measured separately below.
- **How durations were assigned:** almost every `_log` call fires when its phase ends. So the time between two
  consecutive events is the duration of the later phase: `turn` = one model call (prefill, thinking and generation,
  including tool-call arguments such as a whole `write_file` body), `tool` = tool execution, `compacted` =
  compaction, `factcheck`/`auto_verify` = claim extraction plus lookups. Timestamps have 1-second resolution.
- **Confounders:**
  - The app was **restarted 59 times** in these two days (`app_server.log`). Each restart builds a new engine and
    makes the server re-read the whole prompt.
  - `agentic.py` changed during the day (mtime 21:34), and `agent_tools.py` too (20:39). Some guards in today's code
    (`narration_redo`, `phantom_fix`, the `edit_file` old==new refusal) **never appear in the log**. They were most
    likely added after these runs, so some findings below may already be fixed. Each one is marked.
  - No two real messages overlapped in time, so GPU contention between chats is not a factor.
- **Inferred, not measured:** thinking time is estimated from thinking characters saved in `conversations.json`
  (about 3.6 characters per token) and from completion tokens and model time per model.

## 1. Pipeline, as reconstructed from `run()` (agentic.py ~L2512-2960)

```
user msg -> [engine prepare if new engine] -> _recall (log "recall") -> turn.t0
  -> _supplement/_lean_reference (log "lean_reference", ms)          ~1 s
  -> _interpret (off: interpret_first=False; 0 real events)
  -> _lean_pass (log "lean": answered | tools, lean_calc/lean_ency rounds, lean_check)
       answered -> done
       tools    -> [effort given? skip decision] else _needs_plan (log "plan_decision"; "route" if basic->extensive)
  -> _think_frame (off; 0 events) -> _apply_playbook (log "playbook", ~0 s)
  -> _plan_phase (available_tools tool + log "plan")
  -> loop: _maybe_compact ("compacted") -> _complete ("turn", prompt_tokens) -> tools ("tool", "parallel")
       no-call reply -> nags (plan / open items / behaviour / math / repeat) -> factcheck ("auto_verify","factcheck")
```

## 2. Latency by path

| Path | n | total median | total p90 | before first step, median | time to first step, median / p90 | time to first tool, median / p90 | steps, median / p90 |
|---|---|---|---|---|---|---|---|
| lean-answered | 16 | **21.2 s** | 57.9 s | (entire turn) | n/a | n/a | 0 |
| basic | 22 | **159 s** | 349 s | 0 s* | 40.2 / 114.5 s | 46.9 / 97.8 s | 6.5 / 22.6 |
| extensive | 19 | **524 s** | 1,270 s | 50.0 s (p90 113) | **68.8 / 140.5 s** | 66.9 / 140.5 s | 19 / 47 |
| stopped in lean pass | 6 | 50 s | 256 s | n/a | n/a | n/a | 0 |
| other (lean said tools, then nothing ran) | 4 | 41 s | 112 s | n/a | n/a | n/a | 0 |

\*Most basic-path messages are follow-ups that resume an open checklist, so the lean pass and plan decision are skipped.

**Engine prepare + recall gap** (user `t` to the `recall` log line). It is not included in `stats.seconds`:

| Case | n | median | p90 |
|---|---|---|---|
| first message of a chat (new engine) | 36 | **15.0 s** | 20.2 s |
| follow-up after an app restart | 4 | 18.5 s | 20.5 s |
| follow-up, same process | 8 | 0 s | 0 s |

For lean-answered messages, the lean model call itself has a median of 5.0 s. The 21 s median total is mostly this
**15 s cold engine start**.

### 2.1 Phase totals (all 67 messages)

| Phase | n | median | p90 | total s | share |
|---|---|---|---|---|---|
| model step (`turn`) | 649 | 8.0 | 38.0 | **12,011** | 71% |
| tool execution | 642 | 0.0 | 4.0 | 1,149 | 7% |
| lean pass that was stopped (no cap) | 7 | 35 | 216 | 569 | 3.4% |
| prepare + recall gap | 48 | 13.2 | 20.0 | 464 | 2.8% |
| plan_decision | 20 | 15.0 | 29.9 | 418 | 2.5% |
| lean pass -> tools | 26 | 8.3 | 35.4 | 372 | 2.2% |
| plan (written plan + catalog) | 9 | 26.0 | 66.0 | 310 | 1.8% |
| factcheck / auto_verify | 34 | 3.0 | 25.5 | 287 | 1.7% |
| compaction | 9 | 27.0 | 49.7 | 278 | 1.7% |
| lean pass -> answered | 16 | 5.0 | 38.1 | 255 | 1.5% |
| lean_calc / lean_ency rounds | 3 | 24-37 | | 87 | 0.5% |
| lean_reference (background search) | 27 | 1.0 | 1.4 | 27 | 0.2% |
| playbook, route | 33 | 0 | 0 | 3 | 0% |

Tools are cheap. **Model calls are 71% of all wall-clock time**, so the main levers are fewer model calls, fewer
thinking tokens and fewer full prompt re-reads.

### 2.2 Model step time by the tool the step produced (generation of arguments included)

| Produced call | n | median s | p90 s | total s |
|---|---|---|---|---|
| write_file | 46 | 33.5 | 134 | 2,373 |
| edit_file | 86 | 12.5 | 38.5 | 1,869 |
| powershell | 160 | 6.5 | 24.1 | 1,663 |
| read_file | 86 | 10.0 | 29.0 | 1,472 |
| **update_tracker** | 72 | 10.0 | 34.6 | **1,108** |
| (no call) | 63 | 8.0 | 35.6 | 972 |
| python_sandbox | 11 | 82.0 | 136 | 854 |
| browser_open | 21 | 13.0 | 70.0 | 729 |

### 2.3 Context growth

The first step of a message prefills **12-17k tokens** on the extensive path (system prompt and tool list), and
about 5k on the basic path with the core tool set (`ptok0` 4,750-5,151).

Median prompt growth per step: qwen3.5:9b +350, ornith +350, reap48 +280. The p90 is 1-2.4k tokens per step (whole
file reads and writes). Twelve single steps added more than 3k tokens; the largest was +5.4k at 14:35:42 in
`1e1c5019`.

Long build chats reach 44-69k prompt tokens (`915475fb` 69,158, `c5ba3575` 49,721, `7c310563` 48,981).
`915475fb` message 1 consumed 1.14M prompt tokens in 37 steps.

## 3. Waste inventory

| Kind | Count | Wall-clock (step + exec) | Evidence |
|---|---|---|---|
| Narration replies with no call (not final) | 23 | 260 s | reap48 accounts for 19. `34792e9e` 21:05:47 "Let me use Python to download…", then a call 7 s later. `narration_redo` was logged 0 times |
| Final answers that were rejected or nagged | 10 | 144 s | asked a question instead of acting (`1e1c5019` 14:34:33/38), answered before the checklist (`c5ba3575` 18:56:42) |
| Answer sent back by the fact check | 6 | 57 s (plus the extra steps that followed) | see #7 |
| Blocked repeat calls ("did not run") | 19 | 136 s of model time spent producing them | `c5ba3575` 19:41:10-19:42:34: five blocked identical `read_file` calls in a row |
| Identical repeat, nothing changed in between | 44 | 513 s | powershell 21, read_file 12, browser_open 7 (353 s; `e179aedb` re-opened the same `file://…/index.html` 6 times between 17:35 and 17:48) |
| Re-read of an unchanged file | 20 | 203 s | |
| Whole-file overwrite of an existing file | 6 | 596 s | `915475fb` game.py at 19:59:19 (133 s) and 20:01:35; `7c310563` player.py at 16:27:28 (155 s) |
| Failed calls (log `ok:false`) | 35 / 642 | ~480 s | edit_file 7 (155 s), browser_click 2/3 (92 s), write_file 2 (91 s). `stats.failed_calls` counts more broadly: 96 / 614 = 15.6% |
| No-op edits (`old == new`) | 7 | ~100 s | 6 in `915475fb` between 20:00 and 20:04, each returned "replaced 1 occurrence". **Fixed since:** `agent_tools.py` L263 now refuses them |
| update_tracker bookkeeping | 77 calls (12% of steps) | **1,113 s** | median 9 s per check-in step |
| Compactions that freed less than 10% | 5 of 9 | ~110 s | `e179aedb` 17:47:30 freed 25 tokens (24,738 to 24,713) |
| repetition_cut | 312 | (stopped by the user) | `811ee03e` 16:57:26. **Bug**, see #11 |
| empty_forced_answer / announced_forced_answer | 3 / 1 | small | `c5ba3575` |
| Messages that ended "(stopped)" | 13 of 41 with steps, plus 6 in the lean pass | | the user lost patience. "is chicago the capital of illinois" was asked 5 times between 18:13 and 18:26 |

## 4. Model comparison

All rows are real messages whose majority `turn` model is the one shown. Thinking tokens are estimated as described
in section 0.

| Model | msgs | steps | tool calls | failed (`stats`) | narration / non-final no-call | steps that think | thinking share of completion tokens | completion tokens per model-second | median step s | seconds per step |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.5:9b | 13 | 134 | 125 | 20 (16.0%) | 1 / 8 | 29% | ~9% | 12.2 | 10.0 | 20.9 |
| ornith-1.5:9b | 12 | 284 | 295 | 54 (18.3%) | 2 / 6 | 47% | **~63%** (~105k tokens) | 35.7 | 6.0 | 16.6 |
| qwen3.6-35b-a3b-reap48 | 13 | 188 | 158 | 20 (12.7%) | **19 / 24** | 45% | ~42% (~29k tokens) | 17.5 | 12.5 | 22.0 |
| reap48-v2 gguf path (`e179aedb` msgs 1-2) | 2 | 32 | 31 | 1 | 1 / 1 | 64% | n/a (stats incomplete) | | 7.0 | 17.1 |
| MiniCPM5-2B executor (`e1191f92`) | 1 | 25 | 26 | 1 | 0 | 20% | 66% | 47.9 | 2.0 | 4.2 |

### The same build task ("Mario-style 2D game for Windows") on each model

| Chat | Model | Wall time | Steps | Tools | Failed | Identical repeats | Compactions | Peak prompt tokens | Outcome |
|---|---|---|---|---|---|---|---|---|---|
| 42b946cd | qwen3.5:9b | 2,010 s / 5 msgs | 67 | 64 | 5 | 15 | 0 | 44k | "complete and ready to play" |
| 7c310563 | ornith-1.5:9b | 2,571 s / 3 msgs | 85 | 103 | 1 | 18 | 2 | 49k | (stopped); msg 2 alone: 2,125 s, 48 of 58 steps thinking, 74.6k completion tokens |
| e1191f92 | reap48-v2 planner + MiniCPM executor | 132 s | 25 | 26 | 2 | 2 | 0 | 18k | (stopped) |
| e179aedb | reap48 v2 / reap48 at 32k ctx | 2,256 s / 3 msgs | 65 | 65 | 2 | 15 | **6** | 25k | "working" |
| 3be98ece | ornith-1.5:9b | 742 s | 21 | 28 | 1 | 2 | 0 | 27k | (stopped) |
| c5ba3575 | ornith-1.5:9b | 1,974 s / 6 msgs | 162 | 158 | 13 | 30 | 1 | 50k | (stopped) |
| 915475fb | reap48 | 2,278 s / 4 msgs | 89 | 85 | 5 | 20 | 0 | 69k | "complete"; **27 phantom-typo steps** |
| 34792e9e (3D FPS) | reap48 | 1,545 s / 12 msgs | 58 | 36 | 2 | 1 | 0 | 53k | **21 non-final no-call replies**, Three.js class-name loop |

What the comparison shows:
- **ornith** has the cheapest steps (6 s median) but thinks the most: about two thirds of its output tokens, on half
  of its steps. It also makes the most calls per task and repeats itself the most (50 repeats).
- **reap48** narrates the most (10% of steps are no-call "Let me…" replies) and has the slowest steps (12.5 s
  median), because MoE layers are offloaded to the CPU (`n_cpu_moe` 16, eval speed 11-23 tok/s in the server log).
- **qwen3.5:9b** thinks least, finished its build, and has the slowest median step on the 9B class (10 s). It
  generates large bodies without thinking: one step at 15:35:30 took 311 s.
- No model clearly wins on steps per task. The variance comes from loops (phantom typos, repeated opens) more than
  from the model.

## 5. Top 10 inefficiencies, ranked by wall-clock cost

Total sample: about 16,800 s. Where a figure is an estimate it is marked "≈".

### 1. Thinking tokens on routine steps: ≈ 3,500-4,000 s (about 22% of all time)
- **Evidence:**
  - ornith: about 105k thinking tokens out of 165.6k completion tokens. It thinks on 133 of 284 steps (47%).
  - reap48: about 29k of 70.6k completion tokens; 85 of 188 steps (45%) at 11-23 tok/s, so about 1,700-1,900 s.
  - `7c310563` message 2 (16:01:51): 2,125 s, thinking on 48 of 58 steps, ended "(stopped)".
  - Converted at the observed decode rates (ornith about 50 tok/s, reap48 about 15 tok/s), that is roughly 2,000 s
    (ornith) plus 1,800 s (reap48).
- **Recommendation:**
  - Think only on step 1, after a failure or ⚠ result, and on the final answer. That logic is already in
    `_wants_thinking`, but `effort: high` (L3849) makes every step think.
  - Cap `high` to 2× medium for non-final steps.
  - Put a separate small budget on steps that write or edit code, where the thinking mostly drafts the code twice.
  - Log `think_tokens` per `turn` entry so this can be measured instead of estimated.

### 2. Full prompt re-reads after side calls and restarts (single-slot server): ≈ 1,000-1,300 s
- **Evidence:**
  - The first step of a follow-up message takes **24.7 s median (p90 76) in the same process** and **49 s after a
    restart**, against 8 s for an ordinary step. Over 24 follow-ups that is about 960 s of excess.
  - `swarm_server.log` shows `n_slots = 1`. After a lean-pass side call took the only slot (task 1041, 2,007
    tokens), the next agent step re-read **45,077 tokens in 98.7 s**. After task 1267 (1,310 tokens) it re-read
    **56,898 tokens in 121 s**. A post-restart re-read took 47,106 tokens in 69.6 s.
  - In that 14-minute window, these three re-reads were **289 of 418 s of all prompt evaluation (69%)**. The host
    prompt cache (`-cram 1536`) holds only one 887 MiB state ("removing oldest entry").
  - In agent_log the same pattern appears at `34792e9e` 21:32:47 (110 s step), `915475fb` 20:09:42 (70 s, after the
    20:07:31 restart) and `42b946cd` 15:28:49 (59 s).
- **Recommendation:**
  - Set `parallel: 2` (with `kv_unified`) in the model profiles. The code already routes lean, plan and side calls to
    `id_slot 1` (L1216, L4479), but settings have `parallel` unset, and the qwen3.5 profile sets it to 1.
  - If VRAM forbids a second slot, raise `cache_ram_mib` so that at least two states fit.
  - Skip the lean pass for follow-ups in a chat whose previous turn used tools.
  - Avoid restarting the app in the middle of a task.

### 3. Phantom-typo / identical-name loops: ≈ 650 s of agent time, plus about 40 min of the user re-prompting
- **Evidence:**
  - `915475fb` 19:59-20:06: **27 steps, about 468 s**, on "`BLUE_OVERALLS` should be `BLUE_OVERALLS`". Six of them
    were no-op `edit_file` calls (old == new) that returned "replaced 1 occurrence".
  - `34792e9e` 20:4x-21:24: a "PlaneGeometry" class-name loop: 19 steps (about 181 s) across 6 user messages, plus
    artifacts written to "remember" the names.
  - Today's `_phantom_fixes` regex would catch only **9 of the 34** typo-claim turns. Examples it misses: "has a typo.
    It's missing the `U`", "`X` was written as `X`".
  - `phantom_fix` was logged 0 times.
- **Recommendation:**
  - Keep the new exact guard in `agent_tools.py` L263. It is generic and exact.
  - Also add `"... was written as ..."` and `"has a typo"` to the regex. When a reply claims a typo in backticked
    `X`, the app can run an exact check itself: grep the file for X and report "X appears N times, spelled
    identically everywhere".

### 4. Up-front pipeline before the first action on the extensive path: ≈ 1,150 s (median 50 s per message)
- **Evidence:**
  - Extensive: time to first step 68.8 s median, p90 140.5 s.
  - Components (medians): prepare+recall 14.7 s, lean→tools 8.6 s, **plan_decision 16.5 s**, **plan 26 s**
    (p90 66 s). Example: `34792e9e` 21:22:22 → 21:23:36 took 74 s for a 44-token plan.
  - The lean pass said "basic" and was overruled to extensive **10 of 22 times** (`route` events), so two serial
    routing calls are made.
  - Only 4 of 26 lean→tools events carried `effort`. When effort was present, plan_decision was skipped every time
    (0 violations).
- **Recommendation:**
  - Make `plan` and `think` required in the GET_TOOLS schema, so the lean pass always decides effort and
    plan_decision is never a separate call.
  - For playbook tasks whose first step already writes a PRD or checklist, fold the written plan into that step
    instead of a separate no-tools call.

### 5. Lean pass with no wall-clock cap and runaway thinking: 569 s stopped, plus retries
- **Evidence:**
  - `86b2d681`, `c1e73cbf`, `881611f0` (18:13-18:24): "is chicago the capital of illinois" was stopped after
    **145 s, 325 s and 36 s**. The loaded model did not fit the GPU (`context_fit`: 8.2 GB weights > 8 GB, "runs on
    the CPU (slow)"), and its thinking drifted into an unrelated topic (ASD-STE100 alphabets).
  - LEAN_THINK (384 tokens) did not bound the time.
  - The user asked the same question 5 times. A 4th attempt (`5221b449`) took 70 s, including an encyclopedia round.
  - Related: `4871e940` 18:04 — a **build request was answered inline by the lean pass** (4,804 characters of code,
    102 s).
- **Recommendation:**
  - Give the lean pass a hard wall-clock limit (for example 30 s). When it expires, fall through to the agent path.
  - Enforce the thinking cap in tokens on the stream.
  - Generic rule: a lean answer that contains a code block longer than N lines for a "make/build" request is a
    hand-off, not an answer.
  - Warn in the UI when `context_fit.partial` is true.

### 6. Cold engine start on every new chat or restart: ≈ 460 s (15 s median per new chat)
- **Evidence:**
  - First message of a chat: gap 15.0 s median (p90 20.2) across 36 chats. After a restart: 18.5 s. Same-process
    follow-ups: 0 s.
  - This gap is the bulk of the lean-answered latency (21 s total versus 5 s of model time).
- **Recommendation:**
  - Time each part of `prepare()` (Registry, MCP start, `fit_context`, `llama_local.ensure`, `memory_store` load).
  - Share the Registry, MCP manager and fit result between engines with the same config key.
  - Create the engine when the user opens a new chat, not when the first message arrives.

### 7. Fact check false positives on build and negative-statement answers: ≈ 450 s
- **Evidence:**
  - Code and UI literals flagged as unverified "facts":
    - `915475fb` flagged "random.randint(20, 60)" (20:29:12), leading to 6 extra steps until round 2 at 20:33:06
      (**about 234 s**).
    - `34792e9e` flagged a three.js CDN URL in two rounds (21:26:34 and 21:27:27).
    - `42b946cd` flagged its own `python main.py` command (15:39, about 60 s).
  - Negative statements: `b7587cef`/`8f3bf758` "no reliable record" / "no specific brand reported", leading to
    web_fetch rounds that look for an absence.
  - Direct check time is 287 s, plus 6 answer→check redo turns.
- **Recommendation (generic):**
  - Count a claim as verified when it appears verbatim in this turn's tool results or in files written or read this
    turn.
  - Do not extract claims of absence or negation, or code/command/keybinding literals.

### 8. Compaction thrash and re-reads after compaction: ≈ 280 s direct, plus about 500 s of re-read
- **Evidence:**
  - 9 compactions. `e179aedb` msg 3 (num_ctx forced to **32,768**, so the budget is 24.6k) compacted **6 times in
    22 minutes**. After compaction the history was still 21.5-24.7k tokens, which is 88-100% of the budget, so it
    triggered again within 1-3 steps.
  - Space freed per compaction: 12.5k, 3.2k, 1.5k, 1.3k, **25**, and 1.8k tokens.
  - The first step after a compaction took **212 s (17:32:08) and 201 s (17:44:37)**, because the whole prompt had
    to be re-read.
  - `c5ba3575` 19:36-19:37:54: a 105 s compaction of 57.9k tokens.
- **Recommendation:**
  - Add hysteresis: compact down to ≤ 55% of the budget, or do not compact at all.
  - Skip the summary call when the stub pass alone gives more than 10% (the code already returns early at 0.85).
  - Refuse a compaction whose estimated gain is below 15%.
  - When `fit_context` shrinks num_ctx, scale `keep_tokens` (`room * 0.35`) down with it.

### 9. Repeated, blocked and narrated steps: ≈ 1,100 s combined
- **Evidence:**
  - 44 identical repeats with nothing changed in between (513 s; 7 `browser_open` of the same file cost 353 s).
  - 19 blocked repeats (136 s of generation for calls that were refused); after being blocked the model asks again
    (`c5ba3575` 19:41-19:42, 5 in a row).
  - 23 narration replies with no call (260 s; reap48 accounts for 19).
  - 20 re-reads of unchanged files (203 s).
- **Recommendation:**
  - After the second blocked repeat in a turn, add the cached result plus a "use it or change approach" note. On the
    third, force a different tool or a report. The `same_attempt` ladder exists but was never logged.
  - Make sure `narration_redo` (tool_choice required) actually fires and is logged. It was never logged during these
    runs. With an ending ":" heuristic, 34792e9e's narrations would have matched.

### 10. Checklist bookkeeping steps: ≈ 1,100 s
- **Evidence:** 77 `update_tracker` calls (12% of steps); the steps that produce them cost 1,108 s (median 10 s,
  p90 35 s). Small models tick items one at a time, and there are forced check-ins every 10 calls.
- **Recommendation:**
  - Push progress through the `progress` field on normal calls (`_apply_progress`, L2228), which costs no extra
    step.
  - Lengthen `checkin_every` for basic tasks, and close trailing items automatically at the end (already done when
    50% or fewer are open).

### 11. Correctness bug with latency side effects (not ranked by time): `repetition_cut` never stops generation
- **Where:** `_call_openai` in the llama/openai streaming path (L4519-4520). `if self._should_cut(content, tools):
  break` only leaves the inner `for ch in ev["choices"]` loop. The outer `for raw in resp` keeps streaming, and
  every later check (every ~40 characters) fires again.
- **Effect:** each firing appends another "Your last reply started repeating…" reminder to `self.history`.
- **Evidence:** `811ee03e` 16:57:26: **312 repetition_cut events in 3 s**. The saved `agent_state.history` for that
  chat has **313 messages, 312 of them this reminder**. They would be about 12k tokens of junk in the context if the
  chat were continued.
- **Same flaw elsewhere:** the made-up-tool-call cut in the same function also does not stop generation, so text
  keeps streaming after a complete pseudo-call. The Ollama path (L4644) correctly calls `stream.close()`.
- **Fix:** set a flag and break the outer loop (as `too_long` does), close `resp`, and append the reminder at most
  once per step.

## 6. Other observations

- **Speculative decoding:** in the server sample, MTP/ngram draft acceptance was ≤ 15% on 12 of 20 requests (tool
  calls and short replies) and 90% only on a long code write. Check whether the drafter slows down short steps.
- **Prompt eval sometimes very slow:** task 663 read 1,527 tokens at 52 tok/s, and task 873 read 3,028 at 93 tok/s
  (normal is 250-680). This points to RAM pressure or swapping with the 35B MoE on a 16 GB machine.
- **Saving conversations failed:** `app_server.log` shows `PermissionError` on `os.replace(conversations.json.tmp)`
  (OneDrive lock). Chat saves can silently fail.
- **Repeated questions:** the user restarted the same Mario request 8 times across chats. Recall surfaced the earlier
  chats (sim 0.9-1.0), but no earlier artifact was reused.

## 7. Key numbers

- **67 real messages, about 4.7 h.** Model calls take 71% of the time, tools 7%.
- **Time to first agent step:**
  - extensive: **69 s median / 141 s p90** (50 s of routing, decision and plan first)
  - basic: 40 s / 115 s
  - lean-answered total: 21 s median, of which **15 s is cold engine start**
- **Total turn time:** lean 21 s, basic 159 s, extensive **524 s median / 1,270 s p90**.
- **Thinking:** about 63% of ornith's and 42% of reap48's output tokens (≈ 3.5-4k s), the largest controllable cost.
- **Single-slot server:** full 45-57k-token re-reads (70-121 s each) after every side call or restart. Follow-up first
  steps take 25-49 s against 8 s for an ordinary step.
- **Waste items:** 44 identical repeats (513 s), 19 blocked calls, 23 narration steps, 77 checklist steps (1.1k s),
  27-step phantom-typo loop (468 s), 6 compactions in 22 min that freed as little as 25 tokens, a 312× repetition_cut
  bug.
- **Failure rate (`stats.failed_calls`):** qwen3.5 16%, ornith 18%, reap48 13%. 19 of 67 messages ended "(stopped)".
