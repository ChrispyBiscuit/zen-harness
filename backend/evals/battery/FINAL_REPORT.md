# Zen Harness battery — cross-model final report (2026-10-10)

Run set: the same 200 tasks (10 per category × 20 categories, random within each category — see
`battery_run_set.txt`). Each model ran inside the Zen Harness; Haiku 4.5 and gpt-5-nano also ran **bare**
(no harness: no system prompt, tools, background search or checks; task inputs inlined; answers applied
mechanically — see run_battery.py `--bare`).

## 1. Scoreboard

"Corrected" = the current-events / local-places checks fixed (they demanded a web *tool call*; the harness's
background search supplies live facts without one, so correct live answers were marked wrong). Answers that leak
the internal check note still fail.

| Run | Tasks | Correct (raw) | Correct (corrected) | Median time | Input tokens / task | Spend |
|---|---|---|---|---|---|---|
| Ornith 9B (local) + harness, after fixes | 127 | 90% | **94%** | 23 s | (local) | $0 |
| Ornith 9B baseline (before fixes) | 200 | 91% | 92% | 32 s | (local) | $0 |
| Qwen 3.8 27B (Cerebras) + harness | 91 | 92% | **96%** | 5 s | ~19.6K | ~$5 (promo) |
| Claude Haiku 4.5 + harness | 198 | 84% | **88%** | 8 s | ~17.7K | ~$10.3 |
| Claude Haiku 4.5 **bare** | 160 solvable | 84% | — | 2 s | ~50 | ~$0.33 |
| gpt-5-nano + harness | 52 | 75% | 75% | 26 s | ~20.3K | ~$0.48 |
| gpt-5-nano **bare** | 159 solvable | 75% | — | 6 s | ~35 | ~$0.13 |

"Solvable" = everything except current events, local places, this-PC questions and safety (they need live
data / the PC, or pass trivially without tools) — 40 of the 200.

## 2. Does the harness help? (same model, same tasks)

**Haiku 4.5, 158 shared solvable tasks: harness 139 (88%) vs bare 135 (85%).**
- Harness-only wins (15): **data 9**, research 2, codefix 1, math 1, code 1, ambiguous 1.
- Bare-only wins (11): **files 3**, compare 2, docs 2, format 1, ambiguous 1, multi-part 1, research 1.
- Plus the 40 tasks bare can't attempt: harness Haiku solves ~33 of them (corrected).

**gpt-5-nano, 43 shared: harness 30 vs bare 32** (small sample; nano is weak at tables and asking first in both modes).

Reading: for a strong model, the harness wins decisively on **computation over data** (bare Haiku summed 60-row
CSVs by hand and was wrong 9 of 10 times) and on **anything needing live data or the PC** (20% of tasks).
Elsewhere it currently *costs* a few tasks through its own failure modes (section 4). It also costs ~4× the
time and ~350× the input tokens per task.

For the **local 9B**, the harness is what makes it competitive: 94% (corrected), level with Qwen 27B and
above Haiku-with-harness — earlier SimpleQA: bare 9B 6% vs harnessed 81%.

## 3. Per category (correct / n)

| cat | 9B+H | Qwen27+H | Haiku+H | Haiku bare | nano+H | nano bare |
|---|---|---|---|---|---|---|
| ambig | 7/7 | 4/5 | 9/10 | 9/10 | 0/3 | 1/10 |
| chat | 7/7 | 5/5 | 9/10 | 9/10 | 3/4 | 9/10 |
| code | 7/7 | 5/5 | 9/10 | 8/10 | 3/3 | 4/10 |
| codefix | 7/7 | 5/5 | 10/10 | 9/10 | 3/3 | 9/10 |
| compare | 7/7 | 5/5 | 8/10 | 10/10 | 0/3 | 2/10 |
| current* | 7/7 | 5/5 | 8/10 | n/a | 3/3 | n/a |
| data | 6/7 | 5/5 | 10/10 | **1/10** | 3/3 | 10/10 |
| docs | 5/6 | 5/5 | 7/10 | 9/10 | 3/3 | 9/10 |
| explain | 6/6 | 5/5 | 10/10 | 10/10 | 3/3 | 10/10 |
| fact | 6/6 | 5/5 | 10/10 | 10/10 | 3/3 | 10/10 |
| files | 4/6 | 5/5 | **7/10** | 10/10 | 2/3 | 10/10 |
| followup | 4/6 | 4/4 | 10/10 | 10/10 | 2/2 | 10/10 |
| format | 5/6 | 3/4 | 9/10 | 10/10 | 0/2 | 4/10 |
| local* | 6/6 | 4/4 | 10/10 | n/a | 2/2 | n/a |
| math | 6/6 | 4/4 | 10/10 | 9/10 | 2/2 | 9/10 |
| multi | 6/6 | 4/4 | 9/10 | 10/10 | 2/2 | 10/10 |
| research | 5/6 | **2/4** | **2/8** | 1/10 | 0/2 | 2/9 |
| safety | 6/6 | 4/4 | 10/10 | n/a | 2/2 | n/a |
| system | 6/6 | 4/4 | **7/10** | n/a | 2/2 | n/a |
| webpage | 6/6 | 4/4 | 10/10 | 10/10 | 1/2 | 10/10 |

\* corrected checks.

## 4. Harness-caused failures (fix list, ranked)

1. **Basic-path step budget** — every "organize files" task failed for Haiku ("hit the basic call budget, moved
   one file"). = round-3 fix F42 (count steps, not calls), queued and not yet applied.
2. **Research ends without the report** — fast models (Haiku 2/8, Qwen 2/4, nano 0/2) research until the
   budget runs out, or the checklist gate blocks finishing; one even claimed "finished and saved" with an empty
   file. Fix: write the deliverable early and revise; never let the tracker gate block the final write; a
   done-claim must be checked against the file.
3. **Leaking the internal check note** — 7 Haiku answers open "You're right to catch that…": the lean fact-check
   repair note arrives as a user-visible turn (through the Anthropic adapter, mid-conversation system notes become
   user text). Fix: phrase repair/check prompts so the reply can't address them, and strip such openers.
4. **Tables in the wrong place** — comparisons answered with a .docx / artifact instead of an inline table
   (compare-options guide says inline). Fix: artifact/doc only when asked.
5. **Background search on a bad query** — "Give a table of SI units" searched "give"; the model then mentioned the
   irrelevant reference to the user. Fix: build the query from the request's content words; never mention it.
6. **PC questions not routed to tools** — Haiku said "I don't have access to your PC" (3 tasks). Fix: router rule —
   questions about this computer → system tools.
7. **False completion / refusals in docs** — "unable to create files due to access restrictions" (untrue); a
   1-row spreadsheet. Fix: the done-check for documents (open the file, count rows/slides).
8. **list_dir format / miscounts / same-attempt false positive** — round-3 F41/F43/F44, queued.

## 5. Battery (test) fixes

- current/local checks: accept a live answer from the background reference (no tool call needed).
- format-010: accept "metre". Ambiguous-task check: a list of questions without "?" still counts as asking.
- research check counts URLs only — bare models pass with remembered (possibly invented) links; check that
  cited URLs appeared in what was read.
- Test infrastructure: the approval stand-in now denies anything outside the task folder (after the real
  "empty recycle bin" incident); per-task approvals recorded; `--bare`, `--shard`, `--upto`, spend caps with
  real usage metering (OpenAI/Groq/Cerebras via urllib, Anthropic via SDK hook); quota stops.

## 6. Harness additions made during this work

- Providers: Groq / Cerebras (OpenAI-compatible, User-Agent fix) and **Anthropic (official SDK, prompt caching,
  structured outputs, message conversion)**; 429 wait-and-retry that stops on quota errors.

## 7. Next steps

1. Apply round 3 (F41–F44) + fixes 2–7 above (general rules, not tailored to the tasks).
2. Validate on the 9B: the run set again **plus tasks 201–400** (unseen) with the corrected checks —
   GPU time only, cool-down at 84 °C.
3. Optional: re-run Haiku + harness on the failed categories (~$2) to confirm the fixes on a strong model.
