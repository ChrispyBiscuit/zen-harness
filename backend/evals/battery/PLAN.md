# Harness improvement plan (from the 1000-query battery)

## Baseline (base2: original engine, the app's settings, 200 interleaved tasks = 10 per category)
- **Correct 189/200 (94%) — but handled as it should be only 132/200 (66%).**
- **182 redrafts in 124 of 200 tasks** (users watch answers appear and vanish):
  67 lean fact check · 52 math check · 27 lean text retracted when it switched to tools · 22 full claim check ·
  6 checklist-open nudges · 4 "looking it up".
- Slowest: comparisons (median 271 s, budget 240; 6/10 over; only 1/10 "ideal"), documents (median 243 s, 23 tool
  calls per task), research (404 s — within its budget).
- 91 failed tool calls, 44 blocked repeats, 32 loop warnings; 2 tasks hit the hard limit (one = the winver hang).
- Wrong answers: tables summed by eye (data 3/10 wrong), hallucinated "Done." actions on follow-ups (2), a copy error
  after a redraft spiral (math-100), clarifying asks hijacked by the background search (ambig 2).

## What was built (all covered by the offline suite, 69 tests) — evidence numbers refer to FINDINGS.md
| # | Fix | Evidence |
|---|---|---|
| F1 | Lean fact check policy: only specific, checkable facts a user relies on (claims about particular people/businesses/places, figures, dates of events, quotes, rankings, anything current) — never explanations, common knowledge, the user's numbers, calculator results, opinions, or what the assistant can do. Same policy in the lean system prompt. | #7, #8, #12 (67 redrafts) |
| F2 | A failed lean check gets one cheap repair round inside the lean pass (drop or look up the flagged details) before a full handover. | #1, #8 |
| F3 | The lean pass's calculator/encyclopedia calls and results join the history (the agent after a handover, the math check, later turns see them). | #2, #9, #11 (52 math redrafts, follow-ups) |
| F4 | A valid tool that isn't enabled is enabled and run in one call (only matters with stable_tools off). | #3 |
| F5 | The repeat gate never records a "did not run" refusal as a call that ran. | #4 |
| F6 | The background reference says it was searched from the message's words alone — vague messages get a question, not an answer about the search results. | #6, #29 |
| F7 | artifact_edit gets edit_file's forgiving match + closest-region error; the loose match ignores doubled spaces inside lines. | #14 |
| F8 | Artifact edits count as a change for the repeat gate (artifact_read after an edit was blocked). | #17 |
| F9 | Reading a table file adds "compute totals/counts/averages with query_data or python_sandbox — never by picking rows out by reading". | #18, #28, #29 |
| F10 | The checklist gate lets an update that closes a still-open item through. | #21 |
| F11 | The basic path gets create_document, query_data, make_dir, move_path, trash_path (+ one prompt line). | #23 |
| F12 | Sizing rubric: a short document and a 2-3 option comparison are "standard" (table in the answer); "deep" is a researched report. | #15, #16, #22 |
| F13 | Both background-reference blocks are <system-reminder> app notes ("not from the user… never mention it"); the fact check ingests that block as read evidence per URL. | #25 |
| F14 | The lean check also flags claims of having DONE something (nothing can be done in that step) → repair round → tools. | #26, #29 |
| F15 | t_powershell stops reading when the shell has exited even if a launched program holds the pipe (and 3 s after a timeout kill); prompts: never open GUI programs to get information. | #30 (44-minute hang, a window on the user's screen) |
| P1 | Playbook compare-options: the table goes in the answer (artifact only if asked / 5+ options); no re-opening research to polish cells. write-document: a short document is not a research project. | #16, #22 |

## Measurement
after1 = the improved engine on all 1000 tasks (same settings, same server). Compared with base2 on the 200 tasks
both ran (per category: correct, ideal, median/p90 time, first-answer time, redrafts, tool calls), and reported on the
full 1000 for coverage. A second round of fixes follows whatever after1 shows.
