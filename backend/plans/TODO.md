# Zen Harness — to-do

Items the user has queued for later. Newest first.

## Document and media generation (queued 2026-10-10; after the harness tests and improvements)
The harness templates for slides/PowerPoint, spreadsheets, PDFs and other documents are weak, and media generation
needs work too. Plan:
- Audit what exists: create_document, the playbooks/guides for each format, the output quality (layout, styling,
  charts, formulas, typography), and how artifacts preview in the app.
- Research how strong harnesses do it (design systems/themes, python-pptx / openpyxl / reportlab or HTML→PDF
  pipelines, render-and-look-at-it checks with the vision path).
- Build a document battery (a slide deck, a financial spreadsheet with formulas, a report PDF, a one-pager…) with
  render-based checks, measure, then improve templates and playbooks. Keep it generic (no tailoring to the tests).
- Media generation: scope with the user (images, charts, diagrams, audio/video?) before building.

## Copy-by-reference (queued 2026-10-10; evaluated: good, build after the nano analysis)
Stop retype/copy-paste churn: the harness copies source data, the model only references it.
SOURCES = ANYTHING READ (user 2026-10-10): files, web pages, PDFs/documents, search results, tool output. Every read
is kept verbatim in a per-chat source store with a short id (S4) + origin (path/URL/page); excerpts reference
id + anchor/rows/pages; web pages are cached at read time (the copy the model saw; URL travels as the citation).
Builds on the text factcheck.build_evidence already collects; the near-copy detector checks against all sources.
- artifact_create `from_path` (+ line range / sheet / page): exact copy, no retyping.
- Excerpt placeholders in write_file / artifact_create / artifact_edit, e.g. {{excerpt: file rows 10-20}} or an
  anchor range; expanded mechanically on save; a bad reference fails loudly, never saved literally.
- Near-copy detector: written text that is ≥95% similar to something read but not identical → flag it
  ("use an excerpt or fix it"). This, not prompt rules, is what makes models use it.
- Prompts: one rule + a line in the document guides. No new standalone tools (small models neglect rare tools).
- Measure first from battery logs (edit misses, quote/figure redrafts, repeated artifact edits); add copy-heavy tasks.

## Queued in the harness work (2026-10-10)
- After the gpt-5-nano battery: add a Groq backend (OpenAI-compatible, key in %USERPROFILE%\.groq_key — the user
  creates it) and run the same 200 tasks on Llama 3.1 8B with rate-limit waits; three-way comparison 9B / nano / 8B.
- Then: test against Claude Haiku 4.5 (decide: the harness with an Anthropic backend, or a bare Claude session in
  the browser as an unharnessed reference).

## Software-engineering features (queued 2026-10-09)
The agent's engineering features are sparse. Research how other harnesses do it, then plan:
- **Repositories:** connect to / clone git repos, branches, commits, diffs, pull requests (GitHub/GitLab).
- **Project and file management:** a project workspace concept, file tree view, multi-file edits, search/replace
  across a project, undo per change set.
- **Look at:** Claude Code, Cursor / Windsurf, Cline / Roo Code, Aider (repo map, git-native commits), OpenHands,
  Codex CLI, OpenCode — how each handles repo context (repo maps, indexing), git integration, worktrees,
  sandboxes, test loops and review/approval of changes.
- Deliverable first: a short comparison + a proposed feature set for Zen Harness, before building anything.
- **Done 2026-10-09:** research in plans/research_dev_tools.md; PRD in plans/engineering-workspace-prd.md
  (M1 projects → M8 remote/workflow). Waiting for the user's review before building.

## Also pending (offered, not yet approved)
- Compaction trigger based on a free-space reserve (~12K tokens) instead of a fixed 75%; optional "focus cap".
- A hint when the agent runs its own test runner through PowerShell: point it at run_tests.
- Graphical polish v2 phases 2–6 (plan: plans/graphical-polish-v2.md; phases 0–1 done).

## Hierarchical harness loading (queued 2026-10-11 — investigate/plan first, don't build yet)
The full path sends ~17K tokens before any conversation (measured offline, chars/3.6; the model's tokenizer likely
~11K): system prompt ~4.2K + **71 tool schemas ~12.7K on every step** (stable_tools overrides tool_gate so the
llama.cpp prompt cache never breaks — any change to the tool list makes the hybrid model re-read everything).
Biggest groups: code ~2.5K, browser ~2.1K, files ~1.8K, shell ~1.4K; biggest tools browser_devtools ~500,
update_tracker ~490, record ~460, python_sandbox ~400. Prompt sections: How to work ~1.8K, Checklist ~1.0K,
What you can do ~1.0K. Existing levels: lean pass ~1.8K → basic path ~1K + core tools → full ~17K.
Proposal (cache-safe: decide the toolset once, early; only ever append):
1. Per-conversation toolset profiles (research / code / documents / full) chosen by the lean pass; one-time,
   logged upgrade to full.
2. Trim tool descriptions to 1-2 lines; usage detail moves to read_guide('tool:<name>') (appended = cache-safe).
3. Rare/heavy tools (browser devtools, schedules, artifacts, screen, record/query_data) as a one-line catalog
   behind use_tool(name, args) with on-demand schema fetch.
4. Layered system prompt: core ~1.5K; checklist rules appended when the first plan is made; detail stays in guides.
Target ~5-7K for a typical code/research task (estimate). Before building: real counts via llama.cpp /tokenize;
tool-usage stats per task type from agent_log.jsonl to define the profiles; A/B the expense-tracker build and a
10-task battery sample (score, time, tool-call errors) with and without.

## Bonsai 2 27B: more context by trimming GPU overhead (queued 2026-10-11 — after the bonsai1 expense-tracker benchmark)
Profile ternary-bonsai-2-27b (PrismML llama.cpp, %USERPROFILE%\llama.cpp-prism) runs q8_0 K / q4_0 V at 60K in ~7.8 GB.
Same-type quantized KV (q8/q8, q4/q4) outputs "////" on long prompts in this build; mixed types work.
Try: smaller compute buffers (-ub 128 / 256, -b), fewer ctx checkpoints, check CUDA context size; measure the largest
-c that loads with VRAM < ~7.9 GB and no WDDM shared spill, then a 17K-token prompt for correctness and tok/s.
Expected maybe ~80-90K (unmeasured). 128K needs CPU offload of layers or KV (big slowdown) — separate decision.
