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
- After the gpt-5-nano battery: add a Groq backend (OpenAI-compatible, key in C:\Users\user\.groq_key — the user
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
