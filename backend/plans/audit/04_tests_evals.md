# Audit 04: Testing and evaluation

Date: 2026-10-09 · Scope: `web-agent/` (engine `agentic.py`, tools `agent_tools.py`, `debug_tools.py`, `history_archive.py`,
`artifacts.py`, `evals/`). Nothing contacted 127.0.0.1:8765/8099, the GPU wasn't used, and no project file was changed.
The new suite is at `%USERPROFILE%/AppData/Local/Temp/claude/audit/offline_suite.py`.

## TL;DR
- **All the evals are live-model runs.** The only offline check the project has is `selftest_classes.py`, which tests the
  *graders* and not the harness. It passes: 47 tasks, 0 problems. Before this audit, none of the harness guards from
  2026-10-08/09 had an automated test.
- **The published numbers come from an older harness.** The newest eval run started at 09:30 on 10-09. Of the guards in
  the gap list, these fired **zero times** in any eval log: narration redo, phantom-fix, continuation routing, lean
  check (and same-attempt never fired in an eval either). Several of them didn't exist when the evals ran.
- **The task-class eval was only partly run:** 14 of 47 tasks (files, code, data, part of docs), 12/14 passed. The other 33
  tasks (math, format, grounded, web, browser, multi-turn, safety, projects) have never run live.
- **I built and ran an offline suite of 56 tests in about 16 s: 52 PASS, 1 FAIL (a real bug), 3 WARN (known-risk probes).**
- **Real bug (data loss):** `update_tracker(path=<the user's .txt file>)` overwrites that file with the checklist. This is what
  actually happened in `tc_data_stats`, where the input `response_times.txt` was replaced by the checklist markdown. The
  run_eval report called it a "check error", which hid the cause.
- **Eval isolation gaps:** `run_eval.py` patches `at.TRASH` but not `at.BACKUPS` (fixed at import time) or `at.TRACKERS`.
  Eval write/edit backups land in the real `agent_trash/backups`; the task-class run left 9 there (e.g.
  `20261009_093336_1_app.ini`, `..._textutils.py`, `..._calc.py`).

---

## 1. Inventory

| What | File(s) | Measures | Run | Live model? | Last run / latest result |
|---|---|---|---|---|---|
| Grader self-test | `evals/agent_eval/selftest_classes.py` | each task-class check PASSES on an ideal outcome and FAILS on a blank one | `python -u selftest_classes.py` | **No** (tc_web_current fetches endoflife.date; browser tasks start a local site on a random port) | Run today: **47 tasks, 0 problems**. Note: without `-u` the output is lost when piped, because `os._exit(0)` skips the stdout flush |
| Agent eval runner | `evals/agent_eval/run_eval.py` + `tasks.py` | pass/fail from each task's automatic check, optional OpenAI judge 1-5, steps, tool calls, failed calls, ⚠ warnings, blocked repeats, nudges, seconds, tokens, cost | `run_eval.py --backend llama --no-judge [--only/--cats/--set k=v/--repeat]` | **Yes** (llama.cpp, or OpenAI) | See the runs below. It writes `runs/<stamp>-<label>/{results.json,report.md,agent_log.jsonl}` and diffs against the previous run |
| Core tasks | `tasks.py` (~30 tasks: data, code, math, documents, web, ranking, facts_*, rank_*) | P0–P5 program gates | as above | Yes | P5 qwen **25/30, 27.4 min** (`20261009-002258-p5-qwen`); Luna 13/15 (`-p5-luna`) |
| Long-tail facts | `gen_longtail.py` → `longtail.json` (30 random-article infobox facts) | AA-Omniscience-style hallucination | `run_eval --cats longtail` | Yes | `20261009-001529-lt-on`: **28/30** (30/30 defensible), 6.2 min |
| SimpleQA | `tasks.py` (SIMPLEQA_N=100, seed 20261009) + `grade_simpleqa.py` (gpt-6-luna grader) + `vanilla_simpleqa.py` | correct / incorrect / not-attempted, F | `set SIMPLEQA_N=100 && run_eval ...; grade_simpleqa.py runs\<run>` | Yes + OpenAI grader | quote run `20261009-032549`: 83/10/7 (F 0.86, 145 min). The 17 misses were re-run in `-090405` (4 correct, 10 incorrect, 3 n/a). **Combined: 87 / 10 / 3.** Earlier v2: 80.6/17.3/2.0. Vanilla (no harness): 6/92/2 |
| Task-class suite | `tasks_classes.py` (**47 tasks, 12 classes** × ~4) | how the agent handles kinds of work | `run_eval --backend llama --no-judge --cats tc_files,...` | Yes | `20261009-093016-task-classes`: **12/14, only 14 of 47 run**, 19.4 min (~83 s/task, 10.9 steps). Fails: `tc_code_cli` (model: default word-count wrong), `tc_data_stats` (**harness bug**, see §3) |
| A/B local | `ab_local.py` | same query under different settings: wall time, tok/s, GPU spill | manual | Yes | `20261008-144028-ab` |
| Math eval | `evals/math_eval.py` | calculator tool vs direct answer | manual | Yes | no `math_results.json` on disk (never kept, or deleted) |
| Perf | `evals/perf/slowdown_matrix.py`, `fit_test.py` | tok/s under GPU/CPU workers and low RAM; model fit at the 128k profile | manual | Yes (GPU) | `perf/runs/20261008-201934`: GPU-assisted workers cut generation to 0.13× |
| Thinking library | `evals/thinking/judgment.py`, `controls.py` | reasoning frames vs plain answers (blind Luna judge) | manual | Yes | `judgment_v1.json`: frame wins 3, plain wins 10, ties 7 (v1 lost) |
| Phrase memory / steering | `evals/phrasemem/*.py`, `evals/steer/condvec.py` | research experiments (concept vectors, CAA) | manual | Yes (HF models) | `phrasemem/results/*.json` 10-09 midday: behaviour unchanged |
| Legacy root scripts | `test_history.py`, `test_isolate.py`, `test_load.py`, `test_nothink.py`, `test_stream.py`, `test_think.py`, `test_ui.py`, `smoke_gui.py`, `bench.py` | Ollama/Qt-era checks (Aug 2026) | manual | Yes (Ollama qwen3:30b) / Qt | **Stale.** They target `agent.py`, Ollama and `gui.py`, not the current engine. Candidates for deletion or moving to `legacy/` |

No pytest is installed in the venv. The system `python` (C:\Python313) has ruff 0.15.13 and pyflakes, and the harness
uses that interpreter for lint and run_tests.

## 2. Coverage gaps (before this audit) → what the suite now covers

| Feature (added 10-08/09) | Automated test before | Live eval exercised it? | Now covered offline |
|---|---|---|---|
| Restorable compaction (stubs + archive + pins) | none | no (`compacted` never in eval logs) | yes: fake-engine forced compaction, full text retrievable, outcome lines kept in stubs, user's words pinned verbatim |
| `history_search` tool / `history_archive` BM25 | none | no | yes: add/get/search/tool, id normalisation, paths under LWS_DATA_DIR |
| Search-first gate (`_supplement`) | none | indirectly (SimpleQA) | yes: Reference appended (str and list content), no card on first message, skipped for acks and when disabled |
| Effort triage (lean `get_tools` plan/think) | none | no (`effort` absent from eval logs) | yes: valid plan/think set `_effort` + depth; invalid values ignored; a narrating lean reply hands over with none/medium |
| Continuation routing (`_continues_work`) | none | no | yes |
| Narration redo (`tool_choice=required`) | none | no | yes: **full fake-model turn** checks that step 2 gets require=True and the narration is dropped from history |
| Repeat gate (`_repeat_of`/`_record_call`) | none | no hits in eval logs | yes: blocked, reset only by a *successful* write, exempt tools, arg order |
| Same-attempt detector | none | no | yes: 3 → warn, 4-5 → STOP, 6 → force answer; reset by a real file change; exempt tools; executor exempt |
| Phantom-fix reality check | none | no | yes (regex pairs). The engine-side injection isn't covered yet |
| No-op edit guards (write/edit/artifact) | none | no | yes, including the CRLF-equivalent rewrite and LF sent for a CRLF file |
| Timeout wording ("STILL RUNNING") | none | no | yes: powershell + debug_run, real subprocesses (~10 s) |
| `show_user` | none | no | argument and blocked-command guards only (launching is left untested on purpose) |
| `run_tests` / `debug_run` / `browser_eval` | none | no | yes: pytest summary, post-mortem locals, line probe, local-only URL rule (fake browser) |
| Lint-on-write | none | no | yes: Registry.call path for write and edit; py syntax, undefined name, JSON, skips |
| Per-chat workspace folders | none | no | yes: naming, made on first use, stable per chat, outside-root/subagent/non-ASCII cases |
| `ask_user` | none | partly (tc_safe_ambiguous, never run) | yes: format, 6-question cap, second-round refusal |
| Artifact pins in compaction | none | no | yes (`_collect_pins` artifacts + render) |
| Lean fact check (`_lean_facts_ok`) | none | no (`lean_check` 0 in eval logs) | yes: verdicts, schema→plain fallback, no-Reference short-circuit |
| `_test_passes`, `think_max`/`_step_budget`/`_wants_thinking` | none | n/a | yes |

**Still not covered** (they need a fake-model loop, or they are model behaviour): the quote check and fact check
(`factcheck.py` and `quotecheck.py` are pure modules and good next targets), `_math_note`, checklist auto-close at the
final answer, the behaviour-test nag, the fan-out/gather flow, the hybrid executor, tool-gate enabling, and the
browser tools in general.

## 3. Offline suite: results

`venv\Scripts\python.exe %USERPROFILE%\AppData\Local\Temp\claude\audit\offline_suite.py` gives **56 tests, 15.7 s:
52 PASS, 1 FAIL, 3 WARN, 0 ERROR** (exit code 1 because of the FAIL). Per-test JSON is in
`.../audit/offline_suite_results.json`. Isolation: LWS_DATA_DIR, `at.TRACKERS`, `at.TRASH`, `at.BACKUPS` and `agentic.LOG`
all point to a temp dir, and `agentic._log` is a no-op. Afterwards I checked that nothing new appeared in the real
backups, trackers, attachments or artifacts.

| Area | Tests | Result |
|---|---|---|
| phantom fixes | identical_pair, arrow_and_instead_of, real_fix_not_flagged, case_difference_is_real | 4 PASS |
| narration detector | intent_phrases, final_answers_not_flagged, length_limits | 3 PASS · `probe_polite_closing_offer` **WARN** |
| continuation | after_tool_turn, needs_previous_tool_use, not_for_new_requests_or_first_message, ignores_reminder_messages | 4 PASS |
| repeat gate | blocks_identical_read, reset_by_successful_write_only, exempt_tools, arg_order_insensitive | 4 PASS |
| same attempt | escalates_3_4_6, reset_when_file_changes, exempt_tools_and_executor | 3 PASS |
| test parser | formats, debug_run_and_python_host | 2 PASS |
| thinking budget | think_max_effort_mapping, step_budget_levels_and_caps, wants_thinking_routing | 3 PASS |
| compaction | collect_and_render_pins, history_archive_add_get_search, history_search_tool, compaction_stubs_archive_and_pins_fake_engine | 4 PASS |
| debug tools | clip_ht, enrich_locates_project_frame, enrich_pytest_and_node_formats, lint_file_python_json_and_skips, lint_on_write_via_registry, run_tests_pytest, debug_run_post_mortem_and_probe, debug_run_timeout_wording, powershell_timeout_says_still_running | 9 PASS · `probe_lint_undefined_name_without_ruff_or_pyflakes` **WARN** |
| file tools | write_file_noop_refused_and_crlf_equivalent, edit_file_noop_and_not_found, resolve_refuses_outside_roots, update_tracker_folder_path_ignored | 4 PASS · `update_tracker_must_not_clobber_user_data_file` **FAIL** |
| workspace | named_by_date_topic_chat, edge_cases | 2 PASS |
| ask/show/browser_eval | ask_user_formats_and_guards, show_user_guards_without_launching, browser_eval_only_local_pages | 3 PASS |
| search-first + lean check | supplement_appends_reference_and_card_rules, supplement_skips_acks_and_disabled, lean_facts_ok_verdicts | 3 PASS · `probe_lean_facts_ok_fails_open` **WARN** |
| effort triage | lean_pass_effort_triage, lean_pass_answers_directly | 2 PASS |
| artifacts | create_edit_read_isolated (versions, no-op edit refused) | 1 PASS |
| fake turn | narration_redo_requires_tool_call_fake_turn | 1 PASS |

Two failures on the first run were **wrong test assumptions**, which I fixed: `_effort` is only initialised inside `run()`,
and `artifacts.create` returns `(meta, version)`.

### FAIL: real bug, `update_tracker` clobbers user files (data loss)
`agent_tools.t_update_tracker` accepts any existing `.md/.txt/.markdown` path as the checklist file
(`re.search(r"\.(md|txt|markdown)$", p)`), then `open(path, "w")` overwrites it. In the eval
(`runs/20261009-093016-task-classes`, agent_log turn 21), Qwen sent
`update_tracker(path=...\tc_data_stats\response_times.txt, updates=[...])` and the 200-line data file became
"# Checklist: tc_data_stats". The grader then crashed parsing it (`invalid literal for int() ... '#'`), and the report
showed a check error instead of the data loss. The `.txt` allowance came from the Luna-folder fix in PROGRAM.md.
**Fix:** accept the path only if the file doesn't exist yet, or already starts with `# Checklist`. Otherwise keep the
default tracker path and say so in the result. Also make `check_latency` compute its expected values from the setup seed
rather than re-reading the agent-modifiable file.

### WARN probes (design risks, for you to decide)
1. **Narration false positive:** a real final answer ending in an offer ("…If you want, I'll compare it with London.")
   matches `_INTENT_RX` in its last 300 characters. If it's ≤600 characters, the engine drops it and redoes the step with a
   **required** tool call, up to 3 times. Consider ignoring an intent phrase inside an "if you want / would you like"
   clause, or requiring that the reply contains no answer-shaped content (numbers or citations) before it counts as
   narration.
2. **Lint degrades silently:** `lint_file` runs ruff/pyflakes with `shutil.which("python")`, whatever is first on PATH. If
   neither tool is installed for that interpreter (the venv has neither), an undefined name reports *"no problems (Python
   syntax + undefined names)"*. That's false confidence, and the phantom-fix guard tells the model to trust this check.
   Use the venv interpreter with ruff installed, or say "undefined-name check unavailable".
3. **Lean fact check fails open:** an unparsable checker reply or two server errors return True, so the answer is shown
   unchecked. That's defensible for latency, but it should at least be counted (log `lean_check_error`, which already
   exists) and tracked as a metric.

### Other findings
- `run_eval.py` isolation leaks: `at.BACKUPS` (confirmed 9 eval backups in the real `agent_trash/backups`) and
  `at.TRACKERS`. Set both next to `at.TRASH`. Better: make `agent_tools` derive TRASH, BACKUPS and TRACKERS, and
  `agentic.LOG`, from `LWS_DATA_DIR` the way `memory_store`, `artifacts` and `history_archive` already do.
- `selftest_classes.py` loses its output when piped (`os._exit` without a flush). Add `sys.stdout.flush()`.
- `agentic.py` changed while the suite was being written (functions moved about 100 lines). The suite imports by name,
  so this didn't matter, but it shows why line-number-based notes in plans go stale.

## 4. Recommended test strategy

### 4.1 Committed offline suite (every change, about 20 s)
- Move the suite to **`web-agent/tests/test_harness_offline.py`**. It's already plain asserts with one test per function,
  so it runs under pytest unchanged: `pip install pytest` into the venv, then
  `venv\Scripts\python.exe -m pytest tests -q`. Keep the isolation preamble in `tests/conftest.py` (env vars and patches
  applied *before* importing `agentic`/`agent_tools`).
- Mark the `probe_*` tests `xfail(strict=False)` until each one is decided. Then turn it into a real assertion or delete it.
- Rule: **every new guard lands with one positive and one negative test** (fires on X, stays quiet on a look-alike). The
  look-alike should come from a real transcript, so the test doesn't encode the eval task (consistent with "agent stays
  general-purpose").
- Next targets (pure functions, cheap): `factcheck.math_issues/count_issues`, `quotecheck`, `_stub_result`,
  `_slim_call_args`, `_strip_apology`, `partial_json_field`, `_parse_attrs`/XML tool-call parsing, `tracker_*` helpers,
  `is_safe_command`/`blocked_reason`, `rule_matches`.
- Run `selftest_classes.py -u` in the same command, since it's also offline.

### 4.2 Recorded-replay tests (harness logic against real model behaviour, no GPU)
- Add a recorder to the engine (env `LWS_RECORD=<dir>`) that saves each `_call_openai` and `_call_ollama` response
  (`msg`, `ntok`, `ptok`), plus the tool name and args, as JSONL per conversation.
- The replayer feeds responses **by sequence** (not by prompt hash, so prompt edits don't break it) into a fake
  `_call_openai`, the same technique as `narration_redo_requires_tool_call_fake_turn`. Tools run for real in a temp
  workspace, or are stubbed from the recording for web and browser.
- Assertions are on harness behaviour: which guards fired, what the history looks like, that there's no crash, and the
  final answer passes the task check. Keep 10–20 cassettes, picked from real failures (the narration-heavy FPS build, the
  `tc_data_stats` tracker clobber, a compaction-heavy chat, a fact-check send-back). If the code diverges from a cassette
  (the engine asks for a call the recording doesn't have), mark that cassette stale; don't fail.

### 4.3 Small fixed live smoke eval (before making a setting default, or after a model switch; about 15–20 min on AC)
Use 15 tasks, one or two per class, chosen to be fast and to discriminate. The times are from the 10-09 run (~83 s/task
average):

| Class | Task | Why |
|---|---|---|
| files | `tc_files_replace` (47 s), `tc_files_largest` (33 s) | edits without collateral damage; a simple read |
| code | `tc_code_failing_test` (48 s), `tc_code_cli` (95 s, failed last time) | run_tests / lint path; a known discriminator |
| data | `tc_data_join` (64 s), `tc_data_stats` (94 s) | sandbox maths; **tracker-clobber regression** |
| documents | `tc_doc_pdf` (44 s) | read_document |
| math | `tc_math_tz` | calculator + DST reasoning |
| format | `tc_fmt_json` | strict output |
| grounded | `tc_ground_absent` | says "not in the document" (anti-hallucination) |
| browser | `tc_br_table` | local site, deterministic |
| multi-turn | `tc_mt_correction` | follow-up + continuation routing |
| safety | `tc_safe_web_inject` | prompt-injection resistance |
| projects | `tc_plan_scaffold` | checklist / extensive path |
| facts | 1 long-tail item (lean path, ~5 s) | search-first + lean check |

Leave out live web tasks (SearXNG/ddgs are flaky) and SimpleQA (2.5 h) from the smoke set. Run those nightly or weekly as
the "accuracy" eval. Run the full 47-task class suite once per model or harness milestone (~65 min); it has never been
fully run.

Check AC power and warm the model up first: battery is ~3.5× slower (memory: laptop power throttle).

### 4.4 Metrics to track per model (add them to `run_eval.py` results by parsing the run's `agent_log.jsonl` per conv)
| Metric | Source | Why |
|---|---|---|
| pass rate, judge/rubric score | existing | outcome |
| wall s/task, **time to first action** (turn start → first tool_call event) | emit timestamps | perceived latency |
| model steps and tool calls per task, failed calls | existing | efficiency |
| **narration rate** = narration_redo / model steps | log `narration_redo` | small-model weakness the redo targets |
| guard firings per task: repeat_blocked, same_attempt (3/4/6), phantom_fix, continue_work, empty/announced forced answer | log | guards working, or loops growing |
| lean path share (answered vs tools) and effort distribution (plan/think) | log `lean`, `effort` | triage behaviour |
| **fact-check pass rate**: lean_check unsupported, fact-check redo rounds, quote_check rounds, math_check send-backs | log | trustworthiness |
| compactions per task; history_search calls after compaction | log `compacted`, tool names | whether restorable compaction gets used |
| prompt vs generation tokens; prefill/generation tok/s | `turn`, server.log | where time goes (PROGRAM P1) |
| harness errors (engine exceptions, check crashes, 400s from the server) | results `error` | e.g. `sqa_011` "model server error 400" |

Write a per-model `metrics.json` next to `report.md`, and make the run diff report metric deltas as well as pass/fail
flips, e.g. "narration rate 0.18 → 0.06".

## 5. Summary
The project measures model quality well: a gated program with SimpleQA (87/10/3 after the miss re-run), the long tail
(28/30) and P5 (25/30). It had **no regression protection for the harness itself**, and its latest numbers predate the
guards being audited. The new offline suite covers 18 of the listed features in about 16 s without a model. It found one
real data-loss bug (the `update_tracker` path clobber, seen live in the eval) and three design risks (narration false
positive, lint silently degrading, the lean check failing open), and the audit also turned up two eval-isolation leaks.
Next steps: commit the suite under `web-agent/tests/` with pytest; fix the tracker path and the isolation leaks; add the
recorder/replayer; adopt the 15-task smoke eval with the per-model metrics above.
