# PRD: Engineering workspace for Zen Harness

**Status:** draft for review (2026-10-09). **Owner:** the user. **Research:** [research_dev_tools.md](research_dev_tools.md)
(survey of Claude Code, Cursor, Windsurf, Cline/Roo, Aider, OpenHands, Codex, OpenCode, Copilot, Zed, Devin).

## 1. Problem

Zen Harness can write and run code, but it has almost none of the structure software work needs. Today's build runs
showed what that costs:

- **No project.** Each chat works in a loose set of "allowed folders". There is no project root, no per-project
  commands (how to run, test or lint), no project instructions, and no list of recent projects.
- **No safety net for commands.** File edits can be reverted one by one, but anything changed through PowerShell
  (a `python -c` rewrite, `npm install`, a moved folder) cannot be undone. Neither lws-web nor web-agent is a git
  repo, so there is no history at all.
- **No git.** No status, diff, commit, branch, pull request or CI view, for the user or the agent.
- **No code map.** The agent finds code by reading files top to bottom. Small models lose the thread in big files
  and re-read the same regions (seen repeatedly today).
- **No review surface.** Changes appear as a stream of diff cards in the chat. There is no single "what changed in
  this task" view with accept/reject per hunk.
- **Weak feedback loop for small models.** The agent has run_tests, debug_run and lint-on-write. But edits fail on
  whitespace mismatches, and diagnostics report everything, not just what the last edit broke.

## 2. Goals

1. Opening a folder makes it a **project**: the agent works inside it, knows its commands and instructions, and the
   UI shows its files and changes.
2. **Nothing the agent does is unrecoverable.** Every step can be rolled back, including changes made by commands.
3. **Git-native workflow** for the user and the agent: status, review, commit (message drafted by the model),
   branches, and later pull requests and CI status.
4. **Small-model reliability:** a compact repo map, symbol lookups, forgiving edits, and feedback that shows only
   what the last change broke.
5. **One review surface** for a task's changes, with accept/reject per file and per hunk.

### Non-goals (this version)
- An embedding / vector index of the code (grep plus a repo map is enough for local models; it costs RAM and GPU).
- Docker or VM sandboxes, cloud or background VMs, best-of-N parallel agents (the 8 GB GPU serves one model job at a
  time).
- A full IDE. Zen links out to VS Code (installed) for heavy editing.
- Language servers running by default (16 GB RAM). Tree-sitter covers navigation; LSP can come later.

## 3. Users and key scenarios

The user is a technical program manager who builds tools, scripts, sites and games with the agent, mostly on local
models, and reviews the results.

| # | Scenario | Today | With this PRD |
|---|---|---|---|
| S1 | "Open my project C:\dev\lws-web and add a settings search box." | Agent wanders the folder, guesses the build command | Project opens, AGENTS.md and `npm run build` known, repo map shows where settings live |
| S2 | The agent breaks the app with a command. | Manual repair | Restore the checkpoint before that step (files, chat, or both) |
| S3 | "Review what you changed and commit it." | Scroll through diff cards; no git | Changes panel: per-hunk accept/reject, model-drafted commit message, commit |
| S4 | "Clone github.com/x/y and get the tests passing." | Manual clone in PowerShell | Clone from URL into a project; run the tests via project commands; branch + PR at the end |
| S5 | Agent stuck on an error in a 900-line file. | Re-reads chunks, guesses | `outline` / `find_definition` / `find_references` jump straight to the code; the edit result shows only new errors |

## 4. Requirements

Priorities: **P0** = this version must have it, **P1** = should have, **P2** = later. Each has a check that proves
it works.

### A. Projects (P0)
| ID | Requirement | Acceptance check |
|---|---|---|
| A1 | Open a folder as a project (picker, drag-and-drop, or "Open project…" in the command palette). A recent-projects list shows in the sidebar. | Opening C:\dev\lws-web lists it under Recent; reopening restores it |
| A2 | A chat can belong to a project. The agent's working folder and file permissions are scoped to the project root (plus attachments). | In a project chat, `list_dir` with no path lists the project root; writes outside it ask for approval |
| A3 | Per-project config `.zen/project.json`: run, test, lint, build and setup commands, extra allowed commands, ignored paths. The agent proposes it on first open (detected from package.json, pyproject, Makefile…) and the user confirms. | First open of lws-web proposes `npm run build`/`npm run dev`; after confirming, the agent uses them unprompted |
| A4 | The project header shows the git branch, dirty-file count, and run/test buttons from the config. | Clicking Test runs the test command in a terminal card |

### B. Project instructions (P0)
| ID | Requirement | Acceptance check |
|---|---|---|
| B1 | Read `AGENTS.md` (nearest to the files being worked on wins), with `CLAUDE.md`, `.cursor/rules`, `.github/copilot-instructions.md` as fallbacks. Inject into the agent prompt with a token cap (~1.5k), and say which files were used. | A repo with AGENTS.md saying "use pnpm" gets pnpm commands; the turn shows "Project instructions: AGENTS.md" |
| B2 | "Create AGENTS.md" action: the agent drafts one from the repo (commands, layout, conventions); the user edits and saves it. | Draft appears in the file viewer, not written until saved |

### C. Checkpoints (P0)
| ID | Requirement | Acceptance check |
|---|---|---|
| C1 | Shadow-git checkpoints (Cline-style): a separate git directory kept in the app's data folder, never inside the project, never touching the user's own history. A snapshot of the project tree is taken after every tool call that can change files (write, edit, move, trash, PowerShell, python_host). Respects .gitignore, skips large/binary files over a size cap. | After `python -c` rewrites a file, a checkpoint for that step exists and its diff shows the change |
| C2 | Restore to any checkpoint: **files**, **chat**, or **both**, from a timeline in the turn (and from any message's menu). | Restoring "files" to before step 7 reverts both an edit_file change and a PowerShell change made in step 8 |
| C3 | Compare: show the diff between any checkpoint and now. | The diff view opens with per-file changes |
| C4 | Works for projects that are not git repos. Storage is pruned (e.g. keep the last 50 turns or 2 GB). | Checkpoints work in a plain folder; storage stays under the cap |

### D. Git (P0 core, P1 remote)
| ID | Pri | Requirement | Acceptance check |
|---|---|---|---|
| D1 | P0 | Git panel: branch, ahead/behind, changed files (staged/unstaged/untracked), per-file and per-hunk diff, stage/unstage, discard (to trash/backup). | Matches `git status` exactly on a test repo |
| D2 | P0 | Commit with a message the model drafts from the staged diff (Conventional Commits style); the user edits before committing. Optional attribution trailer. | "Draft message" fills the box in under 10 s on the 9B; commit appears in `git log` |
| D3 | P0 | User changes stay separate: if the tree was dirty before the agent started, those changes are offered as their own commit first (Aider rule). | A pre-existing edit and an agent edit end up in different commits |
| D4 | P0 | Agent git tools: `git_status`, `git_diff`, `git_log`, `git_commit` (asks for approval), `git_branch` (create/switch). Raw writes into `.git` are blocked. | The agent commits only after approval; `write_file` into `.git` is refused |
| D5 | P0 | `git init` offered when a project is not a repo. | One click turns lws-web into a repo with a first commit |
| D6 | P1 | Clone from URL into a new project (system Git Credential Manager; no passwords stored by Zen). | Cloning a public repo creates and opens the project |
| D7 | P1 | Push/pull; create a pull request with `gh pr create` (title/body drafted by the model); show PR and CI status (`gh pr checks`). | PR link and check status appear in the project header |
| D8 | P2 | Worktree per background task (isolates delegated/background work on its own branch); "apply" merges it back. | A delegated task's changes land on its own branch |

### E. Code context for small models (P1)
| ID | Requirement | Acceptance check |
|---|---|---|
| E1 | Repo map (Aider-style): tree-sitter definitions and references, ranked with PageRank personalised to the files in play and identifiers in the request; 1–2k token budget; cached by file mtime. Injected into project chats and available as a `repo_map` tool. | On lws-web the map names SettingsDialog/Composer/store within budget; rebuild after one edit takes under 1 s |
| E2 | Symbol tools on the same index: `outline(file)`, `find_definition(name)`, `find_references(name)`. Languages: Python, JS/TS/TSX, HTML/CSS first. | `find_definition("useApp")` returns src/lib/store.ts with the line |
| E3 | Project search tool upgraded to ripgrep-style (regex, glob filters, context lines, respects .gitignore). | Search across lws-web returns in under 1 s |

### F. Edit reliability (P0)
| ID | Requirement | Acceptance check |
|---|---|---|
| F1 | `edit_file` falls back when the exact `old` text is not found: whitespace/indent-insensitive match, then the closest match returned in the error with a diff of what differed. | An edit with wrong indentation succeeds; a near-miss error shows the closest lines |
| F2 | Small files (under ~150 lines) may be rewritten whole; the result still shows a diff. | Already true; kept |
| F3 | Diagnostics delta: after each write/edit, report only problems that are **new** compared with before the edit (lint now, typecheck optional per project: `tsc --noEmit`, `pyright`). | Editing a file that already had 3 warnings reports only the 1 new error |

### G. UI (P0/P1)
| ID | Pri | Requirement | Acceptance check |
|---|---|---|---|
| G1 | P0 | **Files panel:** tree of the project root (gitignore-aware), git status badges (M/A/?/D), open in the file viewer, reveal in Explorer, open in VS Code. | Tree shows lws-web with M badges after an agent edit |
| G2 | P0 | **Changes panel:** all changes of the current task as one change set ("4 files +120 −35"), per-file and per-hunk accept/reject (reject reverts the hunk), "Commit…" button. A summary bar above the message box links to it. | Rejecting one hunk reverts only that hunk on disk |
| G3 | P0 | **Checkpoint timeline** inside each run (ties into the polish-v2 run block): restore/compare per step. | See C2/C3 |
| G4 | P1 | **Project search** UI (results grouped by file, click to open at line). | Same engine as E3 |
| G5 | P1 | **Run bar:** run/test/build buttons from project.json, each opening a terminal card; long-running ones (dev servers) as background processes with a stop button. | Dev server starts in the background and appears in Tasks |
| G6 | P1 | **Git panel** (D1–D3, D7) as a dock panel next to Files/Changes. | — |

### H. Permissions (P1)
| ID | Requirement | Acceptance check |
|---|---|---|
| H1 | Two separate settings (Codex model): **what it may touch** (read-only / project / full) and **when it asks** (always / when outside the rules / never). Defaults: project + ask outside rules. | Writes inside the project run without asking; writes outside ask |
| H2 | Protected paths: `.git`, `.zen`, `.env*`, keys/credentials files — never written by file tools; git changes only through git tools. | `write_file .env` is refused |
| H3 | Project allowlist for commands from project.json (e.g. `npm run *`, `pytest*`). | `npm run build` runs without a prompt in that project only |

### I. Workflow (P1/P2)
| ID | Pri | Requirement | Acceptance check |
|---|---|---|---|
| I1 | P1 | PRD and task list saved in the repo (`docs/PRD.md`, `docs/tasks.md`) and linked from the checklist, so they survive across chats. | A second chat in the project picks up the PRD |
| I2 | P1 | Plan/act as a visible toggle: plan mode = read-only investigation producing the plan; act = execution. (Builds on the existing read-only mode and checklist.) | — |
| I3 | P2 | Review pass: "Review changes" runs a fresh-context review of the task's diff (bugs, missing tests) before commit. | Review comments appear on the Changes panel |

## 5. Design notes

**Backend (web-agent):**
- `projects.py`: project registry (recent list, root, id), `.zen/project.json` load/detect/save, AGENTS.md discovery.
- `checkpoints.py`: shadow repo per project at `<data>/checkpoints/<project-id>.git`, using `git --git-dir … --work-tree <root>`
  (git 2.49 is installed). Snapshot hook in `Registry.call` after change-capable tools (same place as the existing
  debug aids). Restore = checkout of the snapshot's tree into the root, plus history truncation for "chat".
- `git_tools.py`: subprocess `git` / `gh` (gh 2.92 installed), never a git library. Parsed status/diff for the UI;
  agent tools registered only when a project is open (keeps the tool list short for small models).
- `repomap.py`: tree-sitter (tree-sitter-language-pack) + networkx PageRank; cache in `<data>/repomap/<project-id>`.
  Pure CPU, no GPU.
- `edit_file`: fuzzy fallback + diagnostics delta (extends `debug_tools.lint_file`).
- New API routes: projects (list/open/detect/config), checkpoints (list/diff/restore), git (status/diff/stage/commit/
  draft-message/branch/clone/pr), files (tree/search), repomap.

**UI (lws-web):** new dock panels Files, Changes, Git; project switcher in the sidebar header; summary bar above the
composer; checkpoint timeline in the run block (polish v2 phase 3). Follows the polish-v2 design tokens.

**Prompt budget (small models):** project tools are added only in project chats; repo map capped at 1–2k tokens;
AGENTS.md capped at ~1.5k. Target: the agent's fixed prompt grows by at most ~2.5k tokens.

**Must stay general:** nothing tailored to any one kind of project (game, site, script).

## 6. Milestones

| # | Milestone | Contents | Done when |
|---|---|---|---|
| M1 | Projects + instructions | A1–A4, B1–B2 | S1's setup half works on lws-web |
| M2 | Checkpoints | C1–C4, G3 | S2 works, including a PowerShell-made change |
| M3 | Edit reliability | F1–F3 | Fewer failed edits on the task-class eval (target: −50% "not found" errors) |
| M4 | Git core | D1–D5, G6 | S3 works end to end on a test repo |
| M5 | Files + Changes UI | G1, G2, G4 | Per-hunk reject works on disk |
| M6 | Code context | E1–E3 | S5: the agent reaches the right function without reading whole files |
| M7 | Permissions + run bar | H1–H3, G5 | Defaults ask only outside the project rules |
| M8 | Remote + workflow | D6–D8, I1–I3 | S4 end to end with a PR |

Suggested order: M1 → M2 → M3 first (biggest safety and reliability gain for the least UI), then M4 → M5, then
M6–M8.

## 7. Success measures

- **No lost work:** every agent change (including commands) restorable — 100% in the checkpoint tests.
- **Fewer wasted calls:** in build runs, re-reads of the same file region and failed edits down by half (measured
  from the agent log, before vs after).
- **Task completion:** build tasks on the task-class eval complete more often with the 35B profile.
- **Review time:** a typical task's changes reviewed and committed in under 2 minutes from the Changes panel.

## 8. Risks

- **Checkpoint cost on big folders:** snapshotting node_modules-sized trees is slow — mitigated by .gitignore,
  size caps and incremental `git add -A` (only changed files are hashed).
- **Restore surprises:** restoring files while a dev server runs, or after the user edited by hand — show exactly
  what will change, keep a backup checkpoint of "now" before every restore.
- **Prompt growth hurting small models:** strict caps; project tools only in project chats.
- **Windows paths and line endings:** normalise in diffs; respect `core.autocrlf`.
- **Tree-sitter packages offline:** bundle wheels in the venv; fall back to ctags-style regex outlines if a grammar
  is missing.

## 9. Open questions (defaults in italics)

1. Commits: agent commits only when asked or approved, while checkpoints are automatic. *(default: yes — no
   auto-commit like Aider)*
2. Attribution trailer on agent-drafted commits. *(default: off)*
3. Where `.zen/project.json` lives: in the repo (shareable) or in app data (private). *(default: in the repo,
   with a .gitignore entry offered)*
4. Repo-map languages beyond Python/JS/TS/HTML/CSS. *(default: add C# and Go next)*
5. Checkpoint retention. *(default: last 50 turns or 2 GB per project)*
6. Should existing loose "allowed folders" chats keep working unchanged? *(default: yes — projects are opt-in)*
