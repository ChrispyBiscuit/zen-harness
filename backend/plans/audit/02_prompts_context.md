# Audit 02: prompts, context budget and instruction quality

Date: 2026-10-09. Scope: `agentic.py`, `agent_tools.py`, `factcheck.py`, `playbooks.py`, `guides/*.md`, `playbooks/*.md`, `settings.json`, `agent_config.py`.
This audit was read-only. No model server was contacted.

**Method.** I imported `agentic` and `agent_tools` in the project venv (no models started) and built two `Registry` objects: delegation on, screen off, advisor off, with fan-out off and with fan-out on. I rendered `SYSTEM.format(...)`, `BASIC_SYSTEM`, `LEAN_SYSTEM` and every schema, and counted tokens with the **Qwen3 tokenizer** (`models--Qwen--Qwen3-4B/tokenizer.json`, the same family as the app's Qwen3.5/3.6 models). Gemma counts are within about ±10%.

> **Note: the file changed during the audit.** Another session edited `agentic.py` while I worked: it added **project mode** (`guides/project.md`, `playbooks/project-mode.md`, `PROJECT_NOTE`, and a `"project"` plan level). Line numbers below are from the snapshot at about 21:35 on 2026-10-09. The numbers include the project-mode text.

---

## 1. Prompt inventory

| # | Prompt | Where | Tokens | When sent | Path |
|---|---|---|---:|---|---|
| 1 | `SYSTEM` (rendered, no extras) | agentic.py:40 | **2,806** | every step (system message) | extensive |
| 1a | + delegation extra | agentic.py `system_prompt()` | ~60 | every step | extensive |
| 1b | + fan-out extras (2 bullets) | same | ~250 | every step, only when fan-out is on | extensive |
| 1c | + long-term memory block | `at.read_memory(3500)` | up to ~900 | every step | all 3 paths |
| 1d | + project instructions (AGENTS.md/CLAUDE.md) | `_instructions()` | up to ~2,500 (9,000 chars) | every step | extensive |
| 1e | + `custom_instructions` (current settings) | settings.json | ~170 | every step | all 3 paths |
| 1f | `SUBAGENT_NOTE` / `PLAN_MODE_NOTE` | agentic.py:175/181 | 62 / 65 | every step when active | extensive |
| 2 | `LEAN_SYSTEM` | agentic.py:856 | **830** | once per message (lean pass, up to 6 rounds) | lean |
| 2a | facts_need_source add-on | agentic.py:1481 | ~130 | with the lean pass | lean |
| 2b | `GET_TOOLS` + `LEAN_CALC` + `LEAN_ENCY` schemas | agentic.py:907 ff. | 566 | with the lean pass | lean |
| 2c | Reference wrapper (lean variant) | agentic.py:1492 | ~140 + reference | with the lean pass | lean |
| 2d | `LEAN_CHECK` + schema | agentic.py:1412 | ~110 | once, after a direct lean answer | lean |
| 3 | `BASIC_SYSTEM` | agentic.py:934 | **554** | every step | basic |
| 3a | `BASIC_NOTE` (in reminder) | agentic.py:958 | 81 | every step | basic |
| 4 | Reference note (`_supplement`) | agentic.py:1381–1406 | ~110 preamble + up to ~1,200 (2×1,200-char wiki, 5 web rows) | once per message, stays in history | all 3 paths |
| 5 | Recall block (`_recall`) | agentic.py:~2170–2205, appended at ~2615 | 0–~450 | once per message, stays in history | all 3 paths |
| 6 | Guide hint (`_guide_hint`) | agentic.py:2150 | ~30 | once per message | extensive |
| 7 | Playbook block (`playbooks.render`) | agentic.py:2535, playbooks.py:248 | ≤ 2,600 chars ≈ **≤600** | once per message, stays in history | extensive |
| 8 | `PLAN_PROMPT` (+ data add-on, + fan-out add-on ~330) | agentic.py:209, 4232 ff. | 495 (+~70, +~330) | once per planned message | extensive |
| 8a | Auto `available_tools` catalog before the plan | agentic.py:4240 | **1,794** | once per planned message, stays in history | extensive |
| 8b | The written plan itself | `_plan_phase` | up to 563 / 1,690 / 2,252 **words** (brief/full/project) | stays in history for the turn | extensive |
| 9 | `PLAN_DECISION` (+ parallel_parts, data_points add-ons) | agentic.py:3984 | 301 (+~150) | rare: only when the lean pass gave no effort | extensive |
| 10 | `INTERPRET_PROMPT` + schema | agentic.py:4086 | 242 + ~120 | off (`interpret_first: false`) | — |
| 11 | think-frame select prompt | agentic.py:4186, thinking.py | ~?, plus the library index | off (`think_frame: false`) | — |
| 12 | `EXECUTOR_NOTE` | agentic.py:187 | 118 | off (hybrid disabled) | — |
| 13 | `SUMMARY_PROMPT` | agentic.py:256 | 148 | at compaction | all 3 paths |
| 14 | Pins (`_render_pins`) | agentic.py:397 | typically ~600–1,700 | after a compaction, every step | extensive |
| 15 | `factcheck.EXTRACT_PROMPT` + schema | factcheck.py | 287 + 120 | once per final answer that is due a check | basic/extensive |
| 16 | Fact-check send-back, DATES AFTER TODAY | agentic.py `_factcheck` | ~120 + list | ≤1 time per turn | basic/extensive |
| 17 | `QUOTE_RULE` | agentic.py:~2309 | 81 | off (`quote_check: false`) | — |
| 18 | Per-step reminder (`_reminder`) | agentic.py:1913–2021 | 80 (basic) / **~300–900** (extensive: tracker 200–600, current subtask ~70, verify notes ~80–160 at wrap-up, workers ~90) | every step, not cached (at the end) | basic/extensive |
| 19 | One-shot nags (narration, open items, empty, behaviour, repeat, reality check, tools off) | agentic.py:~2840–3010 | 40–110 each | event-driven | extensive |
| 20 | Tool-result warnings (⚠ repeat, 404, sign-in dead end, stuck) | agentic.py ~3280–3570 | 30–80 each | event-driven | basic/extensive |
| 21 | Tool schemas | agent_tools.py:2340 `BUILTIN` | see §2 | every step | basic/extensive |
| 22 | Guides | guides/*.md | clear_writing 1,639 · web_research 1,326 · powerpoint 1,072 · software 1,059 · project 948 · excel 691 · word 628 · pdf 354 | when `read_guide` is called (stays in history) | extensive (basic can call it too) |
| 23 | Playbooks (35 files) | playbooks/*.md | 136–510 each (median ≈ 280) | best match injected, see #7 | extensive |

---

## 2. Fixed prompt cost per path

### 2.1 Tool schemas (Qwen3 tokens, `json.dumps` of what `Registry.schemas()` sends)

| Set | Tools | Tokens |
|---|---:|---:|
| Extensive, fan-out off (delegation on, no screen) | 65 | **10,576** |
| Extensive, fan-out on | 67 | **11,207** |
| Extensive, no `progress` field anywhere | 65 | 8,497 |
| Basic path (`BASIC_TOOLS`) | 22 | **3,587** (of which browser: 1,078 for 9 tools) |
| Lean path (`get_tools`, `calculate`, `encyclopedia`) | 3 | 566 |

Schema cost by group (extensive): code 1,660 · browser 1,552 · files 1,423 · ALWAYS_ON 1,342 · shell 913 · memory 764 · web 646 · artifacts 613 · math 440 · schedule 382 · delegation 378 · documents 268.

**The `progress` field costs 2,145 tokens on every step.** `Registry.schemas()` (agent_tools.py:2741–2742) adds `progress` to 63 tools. Each copy costs about 33 tokens. That is 20% of the whole schema block.

The ten most expensive tools (schema tokens / description tokens): `update_tracker` 488/229 · `gather` 463/191 · `record` 444/98 · `python_sandbox` 367/197 · `calculate` 340/250 · `powershell` 285/175 · `create_document` 266/117 · `artifact_create` 258/132 · `debug_run` 251/75 · `run_tests` 247/97.

### 2.2 Totals per request (before any conversation history)

| Path | System | Schemas | Memory + custom | Per-turn injections (stay in history) | Per-step reminder | **Fixed before the 1st real step** |
|---|---:|---:|---:|---|---:|---:|
| Lean | 830 + 130 | 566 | ≤ 1,070 | reference ≤ 1,350, recall ≤ 450 | — | **~1.5k–4.4k** ✅ |
| Basic | 554 | 3,587 | ≤ 1,070 | reference ≤ 1,350, recall ≤ 450 | 81 | **~4.2k–7.1k** ✅ |
| Extensive, unplanned | 2,806 + 60 | 10,576 | ≤ 1,070 (+ ≤ 2,500 AGENTS.md) | reference, recall, playbook ≤ 600, hint 30 | 300–900 | **~14.0k–19.9k** ❌ |
| Extensive, planned (brief/full) | same | same | same | + catalog 1,794 + PLAN_PROMPT turn ~500 + plan 0.7k–2.2k | same | **~17k–24k** ❌ |
| Extensive + fan-out | + 250 | 11,207 | same | + fan-out plan add-on ~330 | + 90 | **~18k–25k** ❌ |

Against an **8–12k budget for small models**:
- Lean and basic fit.
- Extensive costs **about 1.4–2× the budget before step 1**. The hard core alone (SYSTEM plus schemas) is 13.4k.
- On the Gemma 12B profile (`num_ctx` 20,480, agentic.py model_overrides), a planned extensive turn uses **85–100% of the window** before the first tool result. Compaction starts almost at once.
- On Qwen 9B at 64k the window is big enough. But a 9B model attends poorly across 15k tokens of rules and schemas, so the cost is in quality, not overflow.

---

## 3. Instruction quality

### 3.1 Contradictions

| # | Conflict | Evidence |
|---|---|---|
| C1 | **"Tools load on demand" vs every tool is always loaded.** The SYSTEM text describes a gate that `_stable_tools()` switches off by default. A model told to enable tools first wastes a step, and the app then replies that nothing needed enabling. | agentic.py:77 *"at first you only have available_tools and update_tracker … enable it"* vs agentic.py:3918 `stable = True  # every backend: the model always sees every tool` and agentic.py:3889 *"All tools are already available on this model — no need to enable anything."* The `available_tools` description (agent_tools.py:2479) still says *"enable turns tools on … callable on your next step"*. |
| C2 | **"Every request starts with a checklist" vs the code skipping the checklist for simple requests.** | agentic.py:93 *"Every request starts with a checklist … Even a small request gets a short checklist"* vs agentic.py:2763 `self._need_plan = False  # simple request: no written plan and no forced checklist either`. |
| C3 | **Two different "first action" rules, plus two more ways to ask.** `ask_user` "first", `update_tracker` "first", PLAN_PROMPT step 1 questions, and INTERPRET `ask_first` (now off). | agentic.py:88–92 *"your first action is ask_user"*; agentic.py:93 *"your first action is update_tracker"*; agentic.py:212 PLAN_PROMPT §1; agentic.py:4086 `ask_first`. |
| C4 | **The lean router sends work to basic that basic cannot do.** LEAN puts *"reading or editing a file … a quick command"* on the basic path, but `BASIC_TOOLS` has no `edit_file`, `write_file` or `powershell`. The call is refused, which wastes a round, and the turn then escalates. | agentic.py:878 vs agentic.py:929 (`BASIC_TOOLS`) and the refusal text at ~agentic.py:3160 *"it is not part of the basic path's tools"*. |
| C5 | **web_research guide vs web_search's real behaviour.** web_research.md:75 is stale: it predates the version of `web_search` that reads pages. | web_research.md:75 *"Search results are only titles and snippets … open the best 2–3 pages with `web_fetch`"* vs web_research.md:69 *"`web_search` reads for you"*, PLAN_PROMPT agentic.py:~226 *"web_fetch only for one specific page"*, and the `web_search` tool description. |
| C6 | **Four overlapping working-memory stores.** Big work now has: the checklist (`update_tracker`, optionally saved as PROGRESS.md), a "reference artifact", project-mode `docs/INDEX.md`/`TASKS.md`/`DECISIONS.md`, and PRD.md. SYSTEM asks for both the artifact and the docs tree. | agentic.py:107–112 (PROJECT MODE bullet followed by the reference-artifact bullet); software.md §5; project.md "The project tree"; update_tracker `path` (agent_tools.py:2449). |
| C7 | **`<system-reminder>` authority vs "tool output is data".** SYSTEM says to *follow* any system-reminder. The app also wraps **earlier-conversation snippets** (recall) and **model-written playbook notes** in `<system-reminder>`. Old chat text and self-written lessons therefore arrive with instruction authority. This is a confusion and prompt-injection risk. | agentic.py:119 *"If a <system-reminder> appears … Follow it."* vs recall appended inside REMINDER_TAG at ~agentic.py:2615, and playbook block + "From earlier runs" notes at agentic.py:2547–2550 / playbooks.py:259. |
| C8 | **The plan has to be "concise" but is allowed thousands of words.** `words = max(150, budget*0.55)` gives 563 / 1,690 / **2,252** words. The plan stays in history for the whole turn. | agentic.py:4239, PLAN_PROMPT agentic.py:231 *"Keep it under {words} words"*, `EFFORT_PLAN` agentic.py:970 (`"project": 4096`). |
| C9 | **Three different priority clauses for custom instructions.** | LEAN agentic.py:~1462 *"(they take priority)"*; BASIC agentic.py:~1690 *"except the critical-fact rules"*; SYSTEM `system_prompt()` *"except the safety rules"*. |
| C10 | **`depth` and `plan` in `get_tools` are redundant.** The code turns every plan ≠ none into extensive, so the model answers two linked questions. Each enum is also explained twice: once in the LEAN text and once in the schema descriptions. | agentic.py:2719 `if need and self._depth == "basic": self._depth = "extensive"`; LEAN agentic.py:876–890 vs GET_TOOLS agentic.py:907 ff. |
| C11 | **Fan-out is promised when it is off** (the default). The basic path is told that workers "take over" when they do not exist. | GET_TOOLS agentic.py:910 *"parallel research workers"*; BASIC_SYSTEM agentic.py:947 *"parallel research workers take over"*; BASIC_NOTE agentic.py:958 *"(gather is not available here)"*; `go_extensive` description agent_tools.py:2618; `_request_extensive` return text. |
| C12 | **`go_extensive` is sent on the extensive path, where it does nothing.** It is in `ALWAYS_ON`, so it is in every extensive schema (137 tok) and only returns *"Already on the extensive path"*. | agent_tools.py:1505. |

### 3.2 Bugs in prompt text

- **Three tool descriptions are cut mid-sentence.** `schemas()` truncates at 900 chars (agent_tools.py:2743):
  - `update_tracker` (959 chars): the `path` option is lost.
  - `python_sandbox` (1,076): *"…reuse them; ne"*. The model loses *"never copy printed output back into new code. Don't print huge results…"*.
  - `gather` (1,394): loses the `mine` and `max_calls` instructions, both required parameters.
- **A newline is missing in BASIC_SYSTEM.** agentic.py:946 renders *"…they run at the same time.- If the task turns out bigger…"*.
- **The guide lists are incomplete.** SYSTEM's `read_guide(...)` list (agentic.py:67) and the `read_guide` description (agent_tools.py:2530) omit `software` and `project`, but SYSTEM itself orders *"read_guide('software') first"* (agentic.py:105) and *"read_guide('project')"* (agentic.py:107).
- **`_enable_mentioned` produces a noise reminder.** It still fires *"Tools enabled from your plan: …"* (agentic.py:4322) although nothing is gated.

### 3.3 Duplicates (one rule stated in many places)

| Rule | Copies |
|---|---|
| **Software: PRD first, test behaviour, screenshots** | SYSTEM 101–105 · SYSTEM 107 (project) · software.md · project.md · playbooks/build-software.md · playbooks/project-mode.md · behaviour nag (~agentic.py:2980) · LEAN "plan: project" text — **8** |
| **Stuck → web_search the error, read docs** | SYSTEM ~153 · PROGRESS CHECK reminder agentic.py:1975 · software.md §4 · fix-code.md · build-software.md — **5** |
| **No mental arithmetic → calculate** | SYSTEM 126 · LEAN 873 · `calculate` description · web_research.md §4 · math-calculation.md, plus the structural `_math_note` gate — **6** |
| **Critical facts + citations** | SYSTEM 143 (235-token single bullet) · BASIC_SYSTEM · LEAN 2a · `factcheck.CRITICAL_FACTS`, plus the structural fact-check gate — **5** |
| **How to use web_search / web_fetch / browser** | SYSTEM 54–58 · BASIC_SYSTEM · PLAN_PROMPT §4 · tool descriptions · web_research.md §2–3 · fact-lookup.md — **6** |
| **Record figures → query_data** | SYSTEM ~128 · `record` description · PLAN_PROMPT data add-on (agentic.py:4251) · PLAN_DECISION `data_points` — **4** |
| **Tool inventory in prose** | SYSTEM "What you can do" (715 tok) repeats the schemas; the auto catalog (1,794 tok) repeats them a third time. |

### 3.4 Stale or dead features still in prompts or settings

| Item | Status |
|---|---|
| `plan_tokens` | settings.json and agent_config.py:13 still define it. The engine only writes `turn["plan_tokens"]` (agentic.py:4313). The setting does nothing. |
| `think_level` | Legacy fallback (agentic.py:976); `effort` replaces it. settings.json still sets `"think_level": "off"`. |
| `compress_results` | In settings.json. Zero references in agentic.py or agent_tools.py. |
| `interpret_first` / `INTERPRET_PROMPT` | Off by default. About 70 lines of code plus a prompt that conflicts with `ask_user` (C3). |
| `think_frame` / thinking library | Off. Memory notes v1 lost 3–10. The code path remains (agentic.py:4186). |
| Hybrid executor | Off. `EXECUTOR_NOTE`, exec branches in `_reminder` (agentic.py:~1944, 1988) and `EXEC_EXCLUDE` remain. |
| Fan-out | Off by default, but referenced in always-on text (C11). |
| LLMLingua | Only in `agent_trash/llmlingua/`. Nothing in the prompts. Clean. ✅ |
| `checkin_every: 99` | The setting effectively disables the "Routine check-in" reminder, but the `progress` field, its SYSTEM bullet (113) and the tracker-with-work reminder still pay for the same function. |

### 3.5 Text that small models are likely to ignore or misapply

- **SYSTEM is one wall of 30 bullets.** It has 16 "never / do not" prohibitions, and the longest bullets are 235 (critical facts), 254 (artifacts) and 152 (web) tokens. 9B models follow the first and last few rules and skim the middle. The checklist block (736 tok) and "How to work" (1,118 tok) are 66% of SYSTEM.
- **The `update_tracker` schema is nested** (goal, design, milestones[{title, done_when, status, subtasks[{step, status}]}], updates[], notes[], next, path). Small models often send malformed `milestones` here. The many entries in `ARG_ALIASES` (agent_tools.py:2678) show the damage has already happened.
- **Rules a structural gate already enforces** (math, critical facts, behaviour test, open checklist items, repeat calls, basic budget) are also written out at length. The user's own policy is gates over piles of rules. The prose copies can shrink to one line each, because the gate does the work.
- **The custom instructions use emphatic labels:** *"HIGH CRITICAL: You are a wonderful little guy…"*, *"CRITICAL: Communicate ONLY in ASD-STE100 format for 75-85% of your output."* All three paths append them, and SYSTEM gives them priority over the defaults. Small models over-weight CRITICAL tokens, which can push tool discipline below style. This is a user setting, so it is only flagged here.
- **Task-specific hints in general text.** These stay generic as written, but watch them:
  - software.md:28 names pygame/Qt/tkinter.
  - The PROGRESS CHECK texts are generic. ✅
  - The `browser_eval` description example mentions *"game objects"* and `'player.x'` (agent_tools.py:2521–2523). That is mild game flavour in a general tool. Replace it with a neutral example.

---

## 4. Tool surface

**Count:** 65 tools (delegation on, fan-out off, screen off). There are 67 with fan-out on and 77 with screen control.

| Overlap cluster | Tools | Comment |
|---|---|---|
| Run things | `powershell`, `bg_start`, `bg_output`, `bg_stop`, `bg_list`, `show_user`, `run_tests`, `debug_run`, `python_host`, `python_sandbox` | **10 tools.** `bg_*` could be `powershell(background=true)` plus one `process(action=output/stop/list)`. `python_host` overlaps with "write a .py, run it with powershell", which the powershell description itself recommends. `run_tests` and `debug_run` are both "run headless and explain the failure" and could be one tool with a `mode`. |
| Web | `encyclopedia`, `web_search`, `web_fetch`, 13 `browser_*` | The browser costs 1,552 tok for 13 tools. Click/type/press/select/scroll/nav/wait can be one `browser_act(action, ref, text…)`, which leaves 4–5 tools. `browser_wait` duplicates screen `wait`. |
| Memory | `memory_search`, `memory_save`, `memory_update`, `memory_delete`, `history_search`, `playbook_note` | Save/update/delete can be one `memory_write(action)`. `playbook_note` is off in the user's settings (`playbook_notes: false`) but still in the schema (157 tok). |
| Artifacts | `artifact_create`, `artifact_edit`, `artifact_read`, `artifact_list` | 613 tok. Could be two tools: `artifact_write` and `artifact_read` (list when no id is given). |
| Schedule / delegation | 3 + 3 (+ `gather`, `wait_for_workers`) | 760 tok, rarely used. Good candidates for on-demand loading. |
| Gate tools | `available_tools`, `go_extensive` | Useless on the extensive path while `_stable_tools()` is true (C1, C12). |
| Niche | `read_formula`, `enable_screen_control` | `read_formula` could be an option of `read_file` for images. |

**What each path needs:**
- **Lean:** `get_tools`, `calculate`, `encyclopedia`. This is right.
- **Basic (short task):** `web_search`, `web_fetch`, `encyclopedia`, `read_file`, `read_document`, `list_dir`, `find_files`, `search_text`, `search_documents`, `calculate`, `memory_search`, `python_sandbox`, `go_extensive`. **Add** `edit_file`, `write_file` and `powershell` (approval already gates them), or stop LEAN from routing edits and commands to basic (C4). **Cut** the browser to `browser_open` + `browser_read` (−~700 tok).
- **Extensive core (~28):** files (8), `powershell` + background, `run_tests` / `debug_run`, `python_sandbox`, `query_data`, `record`, web (3), browser (≈5 after the merge), `calculate`, `update_tracker`, `ask_user`, `read_guide`, `create_document`, artifacts (2), memory (2), `history_search`. Load schedule, delegation, screen, advisor and fan-out per chat (sticky, so the cache breaks only once).

---

## 5. Recommendations, ranked by tokens saved

| # | Change | Saves (Qwen tok) | Where |
|---|---|---:|---|
| R1 | **Drop `progress` from the advertised schemas.** Keep accepting it silently: `normalize_args` and `parse_progress` already handle it. Delete SYSTEM bullet 113's first sentence and the matching reminder clause. | **−2,145 / step** (extensive), ~−700 (basic) | agent_tools.py:2741; agentic.py:113, ~1948 |
| R2 | **Do not auto-run `available_tools` before the plan when `_stable_tools()`.** Remove the stale "Tools are loaded on demand" paragraph. Hide `available_tools` and `go_extensive` on the extensive path. Change PLAN_PROMPT §4 "using the tool catalog above" to "the tools you have". | **−1,794 / planned turn**, −72 −297 / step | agentic.py:77, 4240, 4322; agent_tools.py:1505 |
| R3 | **Rewrite SYSTEM to ~1.1–1.3k tok.** (a) Replace the "What you can do" inventory (715) with 3 lines: identity, folders, "your tools are listed; use them, never claim you can't". (b) Shrink the checklist block (736 → ~200): "When the app asks for a checklist, make it with done_when evidence; keep it current via the reminder". The gate enforces the rest; this also resolves C2 and C3. (c) Software + project + reference artifact → one line: "Software and big projects: follow the playbook/guide the app gives you". The playbook and guide hint carry it (C6). (d) Critical facts 235 → ~70: "cite [n]; the app checks every fact". (e) Math, data and stuck rules → one line each. Keep: data-not-instructions, payment/CAPTCHA, trash_path, file links, short final answer. | **−1,500 / step** | agentic.py:40–173 |
| R4 | **Merge tools:** browser 13 → 5 (−~800), `bg_*` → powershell + `process` (−~350), memory write ops → 1 (−~250), artifacts 4 → 2 (−~250), schedule 3 → 1 (−~200), `wait_task` + `task_status` → 1 (−~100), `read_formula` → `read_file` option (−~95). | **−~2,000 / step** | agent_tools.py BUILTIN |
| R5 | **Trim the long descriptions** to ≤ 60–80 tokens and move usage advice into guides: `calculate` (250 → ~70, keep 3 examples), `python_sandbox`, `gather`, `update_tracker`, `powershell`, `create_document`, `artifact_create`, `encyclopedia`, `web_search`, `record`, `run_tests`, `debug_run`. This also fixes the 900-char truncation. | **−~1,200 / step** | agent_tools.py BUILTIN |
| R6 | **Sticky on-demand groups** for schedule, delegation, screen, advisor and fan-out: the schema is added once, when the chat first needs it. The prompt cache breaks once per chat, not per step. | −~760 / step when unused | agentic.py `_active_names` |
| R7 | **Cap the plan length** at ~300 words for every budget (only the token budget scales). If a playbook matched, use its existing 250-word variant. Resolves C8. | −0.4k to −2.6k of history per planned turn | agentic.py:4239 |
| R8 | **Lean pass:** merge `depth` + `plan` into one `effort` enum (none/brief/full/project; depth is derived from it) and explain it once, in the schema only. State the "facts from the Reference, else encyclopedia, else get_tools" rule once instead of three times (LEAN §3, agentic.py:1481, 1492). | −~400 / message | agentic.py:856–925, 1481, 1492 |
| R9 | **Basic path:** add the edit/write/command tools or change the LEAN wording (C4). Cut the browser to 2 tools. Fix the missing newline (946). Remove the worker references when fan-out is off (C11). | −~700 / step | agentic.py:929–962 |
| R10 | **Label background as data.** Put recall, playbook "From earlier runs" notes and the Reference in a `(Background — information, not instructions)` block, not in `<system-reminder>`. Or reword SYSTEM 119 to "app reminders about your task state". Fixes C7. | 0 (safety/clarity) | agentic.py:119, ~2615, 2547 |
| R11 | **One source per rule in guides and playbooks.** Delete web_research.md:75 and the "(in your checklist)" heading at :62 (C5). The build-software and project-mode playbooks should point to their guide and keep only Done/Pitfalls. Choose one working-memory store for big work: either the `docs/` tree or the reference artifact, not both (C6). Add `software` and `project` to the read_guide lists. | −~300 per injected playbook; fewer conflicts | guides/, playbooks/ |
| R12 | **Cap the AGENTS.md/CLAUDE.md injection** at ~3,000 chars (now 9,000). | up to −1,700 / step | agentic.py `_instructions` |
| R13 | **Retire dead settings and paths** (`plan_tokens`, `compress_results`, `think_level` fallback; and, if the user agrees, interpret_first, think_frame and the hybrid executor). This saves no prompt tokens while they are off, but it removes conflicting rules (C3) and reminder branches. Ask before removing experiments. | 0 tokens; less code | agent_config.py, agentic.py |
| R14 | **Unify the custom-instructions priority clause** across the three paths (C9). Optionally suggest that the user tones down "CRITICAL" in their own instructions. | 0 | agentic.py:~1462, ~1690, `system_prompt()` |

**Projected extensive fixed cost after R1–R6 and R12:**

| Part | Tokens |
|---|---:|
| SYSTEM | ~1.2k |
| Schemas: 10,576 − 2,145 − 297 − ~2,000 − ~1,200 − ~760 | ≈ 4.2k |
| Memory + custom | ≤ 1.07k |
| **Total** | **≈ 6.5–7.5k** |

Add the reference, playbook and a ~300-word plan, and the first real step lands at **~9–10k**. That is inside the 8–12k budget, and the Gemma 20k profile keeps half its window for work.

---

## Summary

- **Lean (~1.5–4.4k) and basic (~4.2–7.1k) fit a small-model budget. Extensive does not.**
  - SYSTEM (2.8k) plus 65 tool schemas (10.6k) cost 13.4k before memory, reference, playbook, catalog and plan.
  - The first real step of a planned extensive turn is ~17–24k tokens, 1.4–2× an 8–12k budget.
  - On the Gemma 20k profile, that is almost the whole window.
- **The biggest single wastes are mechanical:**
  - the `progress` field on every schema (2,145 tok/step);
  - the auto-injected `available_tools` catalog (1,794 tok/planned turn), which repeats the schemas the model already has;
  - SYSTEM's prose tool inventory (715 tok), a third copy of the same information.
- **The main contradictions:**
  - SYSTEM says tools load on demand, but every tool is always loaded.
  - SYSTEM says "every request starts with a checklist", but the code skips it for simple requests.
  - Two "first action" rules: `ask_user` and `update_tracker`.
  - LEAN routes edits and commands to a basic path that has no edit or command tools.
  - A stale web_research guide line contradicts how `web_search` works now.
  - `<system-reminder>` "follow it" authority covers recalled old chats and model-written playbook notes.
- **Bugs:**
  - three tool descriptions (`update_tracker`, `python_sandbox`, `gather`) are cut at 900 chars, mid-instruction;
  - BASIC_SYSTEM is missing a newline;
  - the read_guide lists omit `software` and `project`.
- **Duplication:** the software/PRD rule now exists in 8 places, counting the new project mode. Math, critical facts and "stuck → search" each exist in 5–6 places, although structural gates already enforce them.
- **Tools:** 65. Merging browser, background processes, memory, artifacts and schedule, plus trimming descriptions, saves ~3k tok/step.
- **R1–R6 + R12 bring extensive to ~6.5–7.5k fixed (~9–10k at step 1)** without removing any capability, and replace piles of rules with the gates the harness already has.
