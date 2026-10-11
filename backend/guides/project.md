# Project mode — research, plan and build big work properly

Project mode is for big, long work: a full application, a site or a game, a multi-part research report, anything
where a wrong start wastes hours. The work is done in phases, and every phase leaves a document behind. Together they
are the project's memory: you (and a later chat) read them instead of re-deriving anything.

## The project tree
Make one project folder inside this chat's own folder (or where the user said), and keep everything in `docs/`:

```
<project>/docs/INDEX.md          the map: one line per document below, what it holds, its status
<project>/docs/research/01-<topic>.md   one note per research question
<project>/docs/PRD.md            what is being made and how success is checked
<project>/docs/DESIGN.md         how it will be made
<project>/docs/TASKS.md          the build plan, kept current
<project>/docs/DECISIONS.md      dated decisions and why
```
Code and other deliverables go next to `docs/` (e.g. `src/`, `tests/`). Create INDEX.md first and update it whenever
a document is added or changes status.

## Phase 1 — Clarify
If the request leaves choices open that change the result, ask_user first (2-5 questions with defaults). Record the
answers in PRD.md.

## Phase 2 — Research (before any design)
1. Write the research questions into INDEX.md: what must be known to do this well — the domain, how others solve it,
   the libraries/APIs/formats to use and their exact current usage, constraints, risks. Usually 3-6 questions.
2. For each question: web_search (several phrasings), web_fetch the best sources (official docs first), and write
   `docs/research/NN-<topic>.md`:
   - **Question**
   - **Findings** — specific facts, versions, names, code patterns, each with its source link
   - **Options** compared, if there is a choice
   - **Implications for this project** — what it means for the design
3. Verify the exact names and versions you will rely on (APIs, class names, CDN URLs, commands) against official docs
   and note where you verified them. Do not rely on memory for these.

## Phase 3 — PRD (docs/PRD.md)
Goal, who uses it, features in priority order (the first is the smallest working version), acceptance criteria as
checkable behaviours, non-goals, constraints. Cite research notes where they shaped a choice.

## Phase 4 — Design (docs/DESIGN.md)
Architecture and components (what each part does) and the **file layout** — small modules by responsibility, each
small enough to write or fix in one call (about 200 lines or less), never one monolithic file; data and state, the main flows, technology choices with the
reason (link the research note), how it will be tested (one test per acceptance criterion; headless runs for
anything with a window), risks and how to reduce them.

## Phase 5 — Task plan (docs/TASKS.md)
Milestones in build order, each with subtasks and **done when** (the evidence that it works), mapped to the PRD's
acceptance criteria. The first milestone is the smallest version that runs end to end. Mirror the milestones in the
checklist (update_tracker).

## Phase 6 — Build loop
For each milestone: build, test (run_tests / headless run / browser check), fix, then update TASKS.md (status) and
DECISIONS.md (anything decided or changed, with the date and why). If the design proves wrong, update DESIGN.md
first, then the code.

## Phase 7 — Verify and report
Run every acceptance test, look at screenshots for anything visual, update INDEX.md and TASKS.md to their final
state, write README.md (what it is, how to run it), and report: what was built, what passes, what is not done.

## Rules of thumb
- Research and design are not optional in project mode: code files are refused until docs/ has a PRD, a design and
  at least one research note.
- Keep documents short and factual; update them instead of writing new ones.
- When you resume (a new message, a new chat, after the conversation was compressed): read docs/INDEX.md first, then
  TASKS.md, then only the notes you need.
