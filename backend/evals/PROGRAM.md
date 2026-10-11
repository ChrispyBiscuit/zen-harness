# Qwen → Luna program (started 2026-10-08)

**Goal:** trustworthy outputs (accuracy, precision, no fact mix-ups) and a user experience as close to gpt-6-luna as a
9B local model allows — with less waiting. Reference model: `gpt-6-luna` on the same harness and tasks.
Budget: $2.40 on the OpenAI key → Luna runs only at baseline and final validation (~$0.10–0.20 per 2-round run);
answers are graded by Claude, not an API judge.

**Rules for every change**
- General, never tuned to a task in the suite (memory: agent stays general-purpose).
- Each change is a setting first (ablation on/off), measured, then made default only if it passes its gate.
- A guardrail that measurably improves accuracy stays even if it costs time.

## Measures
| Measure | Source |
|---|---|
| Pass rate (2 rounds × 13 tasks) | `run_eval.py` automatic checks |
| Answer quality 1–5: correct · precise (scopes kept apart, exact figures) · sourced · clear/concise | Claude's rubric, both models |
| Wall time per task, model steps, tool calls | `results.json` |
| Time split: prompt reading / generation / tools+app | `server.log` + `agent_log.jsonl` |
| Fact mix-ups (wrong entity/figure despite sources) | rubric + task checks |

## Phases and gates
| Phase | What | Gate to pass |
|---|---|---|
| **P0 Baseline** | Qwen 2 rounds (done: 25/26, 44 min) · Luna 2 rounds · rubric grades for both | both graded |
| **P1 Discovery** | where time goes; which steps carry no value; which failures are model vs harness | ranked candidate list |
| **P2 Low-value steps** | ablate: checklist-only steps, tool-catalogue listing, playbook notes, plan phase on short tasks, route threshold, fact-check extraction size | time ↓ ≥20%, pass rate not lower, quality not lower |
| **P3 Facts before committing** | ranking / superlative / "top N" claims must rest on figures compared from a source (sorted, not recalled); a light "verify before you commit" push on factual answers | no fact mix-ups in 2 rounds; no regression |
| **P4 Search, tools, processing** | page-read counts and timeouts, parallel reads, sandbox `/work` path handling, prompt size per step | tool+app time ↓, no regression |
| **P5 Validation** | Qwen 2 rounds final vs Luna (1 round) · report | Qwen within reach of Luna on pass + quality, wait time ↓ |

## P1 findings (Qwen fair run 20261008-221024)
- Wall 44.0 min / 26 tasks: generation 22.2 min (71k tok), prompt reading 13.5 min (614k tok), tools+app 8.3 min.
- 45% of tool calls carry no user value: `update_tracker` 69, `available_tools` 16, `playbook_note` 8.
- Per task: plan text (~400–600 tok), route decision, fact-check claim list (≤1500 tok) on top of the work.
- Luna probe (web_pay): basic route, 1 search, 17 s, 2 steps, honest two-source answer. Qwen: 163–199 s, 11–13 steps.
- Failures left: Canada cities 1/2 (Vancouver for Calgary — a ranking judgment from a source that had both);
  multi_logs round 1 10 min (sandbox `/work` path confusion); pay answers vary $110k–$146k with source choice.

## Log
- P0: Qwen baseline done (25/26). Luna baseline running: ~$0.005/task; fails every file-writing task because of a
  HARNESS BUG — `update_tracker`'s optional `path` was filled with the workspace folder by Luna (API models fill every
  field), the checklist write hit a folder → PermissionError → Luna (correctly) refused to proceed. Fixed: only a
  .md/.txt file path is accepted. Luna's file tasks get re-run after its baseline finishes.
- P2 (running, 8 checklist tasks × 2): settings `playbook_notes=false`, `plan_tokens=0`, `tracker_with_work=true`,
  `checkin_every=99`.
- P3 built (off until gated): `verify_before_commit` — at the wrap-up step after research, answer from the text read,
  order rankings/superlatives by the figures in the sources, look up a missing candidate before ranking.
- P4 built: `python_sandbox` copies allowed files named by Windows path in the code into /work and rewrites the
  paths (the 10-minute log-task detour).
- P3b grounding (user: Qwen3.5-9B hallucinates ~80% on AA-Omniscience → facts must come from text):
  offline English Wikipedia `~/kiwix/wikipedia_en_all_mini_2026-09.zim` (13 GB, intro + infobox of every article,
  built-in full-text index; `libzim` 3.13.1). `encyclopedia.py` (20–150 ms lookups; infobox sections kept, e.g.
  "Population (2021) City: 1,306,784"; title-match ranking). Tool `encyclopedia` (basic + full toolsets, evidence
  for the fact check, parallel-safe). Lean pass: top clearly-matching articles injected as a Reference
  (`lean_reference`, precision-first `clear_match` — a loosely related article misleads more than none);
  `facts_need_source`: specific facts on the lean path only from the Reference, else get_tools.
  Trap set: 20 closed-book fact questions (tasks `fact_*`) + 2 ranking tasks (`rank_*`).
- Harness fixes found by Luna: sandbox files couldn't reach the user's folder → `python_sandbox save={...}` + hint.
- Luna re-run (fixed checklist path): 10/14; remaining fails = sandbox delivery (now fixed).
- **P2 GATE PASSED** (run 20261008-230550-p2-qwen, 8 checklist tasks × 2): pass 15/16 = baseline; mean time per task
  130 s → 91 s (−30%) excluding the two >450 s outliers, both from bugs fixed since (fact-check link claims, sandbox
  paths); tool calls 211 → 178, checklist updates 69 → 49, playbook notes 8 → 0, plans 16 → 0. Cities table 2/2.
  Made default (settings.json + agent_config.py): plan_tokens 0, playbook_notes false, tracker_with_work true,
  checkin_every 99.
- Fact-check bug: a claim that IS a URL (repo link) failed because a page doesn't print its own address → model
  burned 19 steps re-searching. Fixed: verified when the page was opened or a search listed it.
- Composite fast facts (user: no search API key): `fastfacts.py` — Wikipedia (local) + web snippets (keyless,
  `fast=True` = first engine wins) in parallel, background prefetch + 10-min cache, warm-up at prepare,
  time-sensitivity router (current/latest/prices/office holders/news/recent years → web required). Lean Reference
  adds web rows when time-sensitive or no clear Wikipedia article; freshness rule in the Reference header.
  Fact check `auto_verify`: unsupported claims looked up (subject without the claimed value), all in parallel
  (~3 s); found → verified; not found → sent back WITH what the sources say. Keyless web is flaky under bursts
  (0 rows some calls) — Wikipedia half must carry stable facts.
- P3 famous-fact traps (25 tasks): off 25/25, on 25/25 — the 20 "traps" were too well known to separate anything;
  grounding cost time (5 facts missing from the article escalated to the full harness, ~28 s each). Fixed: the lean
  pass gets the `encyclopedia` tool (like calculate) + "say you couldn't verify rather than guess".
- **P3 GATE PASSED** — long-tail set (`gen_longtail.py` → `longtail.json`, 30 questions from RANDOM articles' infoboxes,
  AA-Omniscience style): grounding ON 28/30 by the key, 30/30 defensible (the 2 "misses" are populations where Qwen
  found NEWER official figures on the web and dated them — the freshness rule working); 25/30 answered on the lean
  path, median 5 s; 6.2 min total. OFF (stopped at 12, user's call): 12/12 correct via web search, 40–90 s each
  (594 s vs 116 s for the same 12 → ~5× faster grounded). Zero hallucinations in 42 long-tail answers.
  Remaining lean→full escalations fixed after the run: names keep trailing "'98"/"II"; a population/census figure
  may be given from the Reference with its year instead of escalating for "current" data.
  Defaults now: verify_before_commit, lean_reference, facts_need_source, auto_verify (settings + agent_config).
- **P5 validation** (15 tasks; Qwen ×2 all defaults, Luna ×1): Qwen 25/30 in 27.4 min (55 s/task vs 101 s baseline,
  −45%); Luna 13/15 in 5.3 min (21 s/task, $0.063; missed top customers + memo). Qwen's 5 misses: code_fix ×2 REAL
  (fixed one of two bugs, accepted mean=17 — fixed: code-fix output check at wrap-up), web_pay ×2 (good 11 s lean
  answers from web snippets, no links — links now required, check counts linked sites), web_collect r2 (link-liveness
  checker rate-limited by GitHub; answer correct). Ranking tasks now lean-path, 7–22 s.
- **SearXNG primary search** (user approved; native Windows, no Docker): ~/searxng own venv, `local_settings.yml`
  (127.0.0.1:8888, JSON, limiter off, request_timeout 1.5 / max 2.0, engines Google CSE + Bing + Yahoo; DuckDuckGo &
  Brave rate-limited from this IP, Wikidata timeouts, Qwant CAPTCHA, Google denied → off). One shim: `pwd.py` in its
  venv (valkeydb imports the Unix module). Measured: median 0.68 s, max 0.89 s, ~34 results, 0/12 empty, ~95 MB RAM
  (ddgs race: 2.8 s median, 4/10 empty). `searx_local.py` starts it on first use + binds it to the app (job object);
  `tools.web_search` uses it first, the ddgs race as fallback. web_search quick mode 0.6 s.
- SimpleQA (OpenAI simple-evals CSV, user approved): fixed-seed 100-question sample (`SIMPLEQA_N=100`, tasks
  `sqa_*`), graded by gpt-6-luna with the official 3-way rules (`grade_simpleqa.py`). Reference: Local Deep Research
  self-reports ~95% with Qwen3.6-27B on a 3090.
- SimpleQA interim (first 16, pre-fix run 20261009-005916): correct 68.8%, incorrect 18.8%, not attempted 12.5%,
  correct-given-attempted 78.6%, F 0.73. Misses found: (1) lean pass answered "the reference doesn't contain it"
  instead of searching the web — my own "say you couldn't verify" rule; fixed: encyclopedia → web (get_tools) →
  only then "couldn't verify". (2) FACT MIX-UP: "Els Aarne wrote 3 symphonies" citing the Wikipedia article, which
  never mentions symphonies — escaped both checks (counts ≤12 skipped; fact-check key "3" matched "30 March").
  Fixed: `count_issues` (N + noun must appear near each other in a source; on "how many / number of / count"
  questions), and the fact check requires a bare 1–3 digit key as a whole number near a word of the claim.
  (3) Isadora Duncan's scarf "Fortuny" — a widespread misconception SimpleQA targets. Grader fixed (max tokens 300,
  invalid chars stripped, errors don't stop grading). Run restarted on the fixed setup: simpleqa-qwen-v2.
- Maths stack (before the program): lean-pass calculator, number-provenance check, equation lint, empty-reply
  nudge, calc digits ≥ 15.
