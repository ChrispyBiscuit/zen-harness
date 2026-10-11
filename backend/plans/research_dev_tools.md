# Research: SWE workflow features in AI coding agents (2025-2026)

Input for the Zen Harness PRD. Zen Harness is a local desktop agent: Python backend, React UI, local LLMs 9B-35B on llama.cpp, 8 GB GPU, Win11. It already has file tools with diff/revert, PowerShell, background processes, a browser, run_tests, debug_run, lint on write, and a plan checklist. It has no git integration, no project concept, no file tree, and no code navigation.

Legend: **[V]** = checked against official docs during this research (2026-10-09). **[K]** = from prior knowledge or secondary sources; check before you build on it. **[?]** = uncertain or conflicting.

---

## 1. Repository connection

| Product | How a project is opened | Clone/auth | Multi-repo |
|---|---|---|---|
| Claude Code | The cwd is the project. `--add-dir` adds more roots. Trust prompt on first run [K] | Uses the user's own `git`/`gh` credentials. The desktop/web app connects to GitHub for cloud sessions [K] | `--add-dir`. Each worktree is its own session [K] |
| Cursor / Windsurf / Zed / Continue / Cline / Roo | The editor workspace folder is the project. VS Code multi-root workspaces [K] | "Clone repo" uses the editor's git and its credential manager. Cloud agents need a GitHub app install [K] | Multi-root workspaces. Indexing is often limited to one workspace (Kilo docs) [?] |
| Aider | Run inside a git repo. It offers `git init` if there is none [K] | User's git | One repo per session [K] |
| OpenHands | Mounts a local workspace into a container. The GUI lists GitHub/GitLab/Bitbucket repos via a token and clones them into the sandbox [K] | PAT stored in settings, injected as env var [K] | One repo per conversation [K] |
| Codex CLI / app | cwd. The app has a "projects" list of local folders. Codex cloud links GitHub repos plus an environment config [K] | User's git. Cloud uses the GitHub connector [K] | One project per thread [K] |
| Copilot coding agent | A GitHub repo. You assign an issue to Copilot [V] | Runs in GitHub Actions. `.github/workflows/copilot-setup-steps.yml` preinstalls tools. Strict firewall by default [V-ish] | Per repo |
| Devin | Repos connected through a GitHub/GitLab integration. "Machine snapshot" with repos pre-setup [K] | OAuth app | Many repos per workspace |

**Pattern:** a project is a local folder (ideally a git root) plus per-project settings: instruction file, allowed commands, test/lint commands, env setup. Cloning reuses the system git credential helper. On Windows that is Git Credential Manager. Nobody stores a password themselves. Recent-projects lists are standard in the desktop apps (Codex app, Claude desktop, Cursor).

## 2. Git integration

### Status, diff, commit

- **Aider** has the most opinionated git design [V]. It auto-commits every edit with a message written by the *weak model* from the diff plus chat (Conventional Commits). It commits pre-existing dirty changes *separately first*, so user and AI changes never mix. `/undo` reverts the last AI commit and `/diff` shows changes since the last message. Attribution appends "(aider)" to the author/committer, with an optional Co-authored-by trailer. https://aider.chat/docs/git.html
- **Claude Code** has no built-in git UI in the CLI. The model runs `git` via Bash, writes commit messages and adds a Co-Authored-By trailer. Commits only happen when asked. `gh pr create` handles PRs. The desktop app has a diff pane plus PR status/auto-merge tooling [K].
- **Cursor/Windsurf/Zed** use the editor's SCM panel. There is a "generate commit message" button (sparkle icon) [K]. Zed added separate Staged/Unstaged multibuffers (2026) [V-ish].
- **Codex app** shows a diff pane with stage/revert per file or hunk, commit, push and create-PR from the UI [K].
- **OpenCode** has `/undo` and `/redo` backed by git snapshots and `/share` [K].

### Checkpoints and undo (separate from user git history)

| Tool | Mechanism | Restore options |
|---|---|---|
| Cline | **Shadow git repo** outside the project. Commits after *every tool use*. Captures untracked files and never touches user commits [V] | Restore Files / Restore Task Only / Restore Files & Task, plus "Compare" diff per checkpoint [V] |
| Roo Code | Same shadow-git approach, inherited from Cline [K] | Same |
| Claude Code | File snapshots before each user prompt. Keeps the last 100 per session and persists with the session [V] | Restore code / conversation / both, "Summarize from here/up to here". **Does not track Bash-made changes** or most subagent edits [V] |
| Cursor | Checkpoint per agent request. "Restore checkpoint" on any message [K] | Files and chat |
| Zed | Per-hunk accept/reject. "Undo reject all" since 0.226 [V] | Review-based, not a timeline |

Docs: https://docs.cline.bot/features/checkpoints , https://code.claude.com/docs/en/checkpointing

**Key lesson:** Claude Code's checkpoints miss shell side effects. Cline's shadow-git snapshot of the *whole tree* catches them, at a storage/perf cost on big repos. Zen already has per-file revert, but PowerShell-made changes are a blind spot.

### Branches, worktrees, parallel agents

- **Git worktrees** are now the standard way to isolate parallel local agents. Claude Code (`--worktree`, desktop sessions each in their own worktree), Cursor 2.x "Worktrees / Local Parallel Agents" (up to 8 agents on one prompt, best-of-N, "Apply" to merge back), Codex app (worktree per thread) all use them [K/?]. Cursor runs a setup script in each new worktree [?]. Caveat: each worktree needs its own `node_modules`/venv.
- Cloud/background agents (Cursor cloud agents, Codex cloud, Copilot coding agent, Devin, Claude Code on the web) put each task on a branch in a VM. The output is a PR [K].

### PRs, review, CI

- Copilot coding agent: issue goes to a branch, then a PR that is updated as it works. You iterate by commenting on the PR. CI runs in Actions [V-ish].
- Review agents: Copilot code review, Cursor Bugbot, Claude Code `/code-review` and `/security-review`, Codex `/review` (reviews the working tree or against a base branch) [K]. CI status is usually read via `gh pr checks` or the PR API. Some tools auto-fix failing checks (Codex cloud, Claude desktop PR monitor) [K].

## 3. Code context

### Repo map (Aider) — the gold standard for small context
- Tree-sitter extracts definitions and references per file. A graph is built with files as nodes and edges from references to definitions. **PageRank, personalized** toward files in the chat and identifiers mentioned, ranks symbols [V for graph-rank, K for tree-sitter/personalization]. https://aider.chat/docs/repomap.html
- The map is a set of elided file outlines with key class/function signatures. Budget `--map-tokens` defaults to **1k**. It grows a lot when no files are in the chat. It is binary-searched to fit [V].
- It is cheap, deterministic, needs no embeddings or GPU, and works with any model. This is the best fit for Zen.

### Embedding indexes
- **Cursor**: a Merkle tree of file hashes for incremental sync, AST-aware chunking, embeddings cached by chunk content, and a remote vector DB (Turbopuffer). The server returns obfuscated paths and line ranges, and the client reads the code locally. Teammate index reuse via similarity hash [V for blog post]. https://cursor.com/blog/secure-codebase-indexing
- **Roo/Kilo**: tree-sitter chunks, embeddings from Ollama (`nomic-embed-text`) or an API, stored in Qdrant (docker). Content-hash cache. A `codebase_search` tool. Experimental [V]. https://docs.roocode.com/features/codebase-indexing
- **Claude Code, Codex, Cline, Aider: no embedding index.** They rely on agentic search (ripgrep/glob/read). Anthropic has said that agentic search beat RAG for them [K]. Industry sentiment in 2026 ("RAG is dead, right?" talks) favours grep plus a symbol map over vectors for code [K].

### LSP / symbols / diagnostics
- **OpenCode** starts language servers automatically and **feeds diagnostics back in the write/edit tool result** after each edit, plus an on-demand diagnostics tool [K/secondary]. This is the most-copied LSP pattern.
- Cursor iterates on lint errors from the editor's diagnostics. Zed/Copilot/Continue use editor LSP for `@symbol` context and diagnostics [K].
- Claude Code has LSP via plugins/IDE integration (diagnostics from the connected IDE) [K]. Codex has no native LSP; there are community MCP bridges [secondary].
- Useful agent-facing symbol tools: `find_definition(name)`, `find_references(name)`, `document_symbols(file)`, `workspace_symbols(query)`, `diagnostics(file)`.

### Project instruction files
- **AGENTS.md** is the cross-tool standard (Linux Foundation / Agentic AI Foundation). It is supported by Codex, Cursor, Copilot, Zed, OpenCode, Roo, Windsurf, Devin, Junie, Amp, Gemini CLI (config), and Aider (`read: AGENTS.md`). The **nearest file wins**, and the user prompt overrides it. Recommended sections: overview, build/test commands, style, testing, security, commit/PR rules, gotchas [V]. https://agents.md/
- CLAUDE.md (hierarchical: user, project, subdir; `@imports`; auto-memory), `.cursor/rules/*.mdc` (glob-scoped rules, replacing `.cursorrules`), `.clinerules/`, `.roo/rules-{mode}/`, `.windsurfrules`, `.github/copilot-instructions.md` [K]. Most tools now *also* read AGENTS.md.

## 4. File/project management UI

- **File tree:** standard in all IDE-based tools. CLI tools (Claude Code, Codex CLI, Aider, OpenCode) have none and rely on `@file` fuzzy mention instead. The Codex app and Claude desktop add a file/diff side pane [K].
- **Diff review:**
  - Zed: one multibuffer tab showing *all* agent changes, with accept/reject **per hunk or all**, also inline in the open file (`agent.single_file_review`) [V]. https://zed.dev/docs/ai/agent-panel
  - Cursor: inline green/red diffs with per-hunk Accept/Reject and "Review changes" across files; Keep/Undo all [K].
  - Cline/Roo: VS Code diff editor per write with an approve/reject gate *before* the write (in non-auto mode). The user can edit the proposed content inside the diff before accepting [K].
  - Windsurf Cascade: changes are written, then shown with accept/reject per file/hunk [K].
- **Change sets:** "N files changed, +X/-Y" summary bar above the composer (Zed, Cursor, Codex app) that expands to the multi-file review [V for Zed].
- **Project search/replace:** editor-native. Agents use ripgrep tools. Few agents expose a "replace across project" tool; models usually do multiple edits instead [K].

## 5. Execution and verification

- **Terminals:** Cline/Roo/Copilot run commands in the VS Code integrated terminal through shell integration, so output is captured and long-running processes can be detached ("Proceed while running") [K]. Claude Code has background Bash plus a Monitor/until-loop. Codex runs each command as a sandboxed exec [K].
- **Test/lint loops:** Aider `--lint-cmd`/`--test-cmd` with `--auto-test` feeds failures back automatically (lint on by default, tree-sitter based) [K]. Cursor iterates on lints. OpenCode feeds post-edit LSP diagnostics back. Claude Code uses hooks (PostToolUse runs formatter/typecheck and returns errors to the model) [K].
- **Sandboxing:**
  - Codex: there are two independent axes. Sandbox is `read-only` / `workspace-write` / `danger-full-access`; approval is `untrusted` / `on-request` / `never`. The default is workspace-write + on-request. **Network is off by default** in workspace-write. **`.git`, `.codex`, `.agents` stay read-only even inside writable roots.** The native Windows sandbox uses restricted tokens/SIDs and dedicated sandbox users in "elevated" mode [V-ish]. https://developers.openai.com/codex/concepts/sandboxing , https://openai.com/index/building-codex-windows-sandbox/
  - OpenHands runs every action in a Docker "agent-server" container with the workspace mounted [V-ish]. https://docs.openhands.dev/usage/llms/local-llms
  - Claude Code: permission rules (allow/ask/deny per tool and pattern, e.g. `Bash(npm test:*)`), modes (default, acceptEdits, plan, auto, bypassPermissions), and OS sandbox for Bash (Seatbelt/bubblewrap) [K].
  - Cline/Roo: per-category auto-approve toggles (read, write, execute, browser, MCP) plus a command allowlist and a max-requests cap [K].
- **Common pattern:** read-only actions are auto-approved, and writes inside the project are auto-approved in "auto-edit" mode. Shell commands go through an allowlist, otherwise the user is asked. Network and outside-project writes always ask.

## 6. Agent workflow

- **Plan/Act:** Cline Plan vs Act (you can use a different model per mode). Roo modes (Architect/Code/Ask/Debug/Orchestrator, custom modes with tool-group restrictions). Claude Code plan mode (read-only, then approve the plan). Cursor Plan mode. Aider `/architect` (architect model writes prose, editor model writes the edit format) [K].
- **Spec-driven:** Kiro (AWS) does requirements.md, then design.md, then tasks.md. GitHub Spec Kit. Cursor/Claude plan files saved to disk. Devin produces a plan before executing [K].
- **Subagents:** Claude Code subagents (own context, restricted tools, Explore/Plan built-ins). Roo Orchestrator "boomerang" tasks spawn mode-specific subtasks and return summaries. OpenCode subagents (`@general`), Codex subagents [K]. The main benefit is **context isolation**: the parent keeps only the summary.
- **Background/parallel:** cloud agents (above). Locally, worktree-per-agent.
- **Review agents:** see section 2. A second pass with fresh context catches more than self-review.

## 7. Small/local models: what actually works

- **Edit format matters most.** Aider: `whole` is the most robust for weak models but expensive. `diff` (SEARCH/REPLACE) is the efficient default. `diff-fenced` exists because some models misplace fences. `udiff` reduced "lazy" elision on GPT-4 Turbo. Architect/editor split lets a strong planner pair with a cheap editor [V]. https://aider.chat/docs/more/edit-formats.html. For 9B-35B local models, use search/replace with **fuzzy matching** (whitespace-insensitive, then nearest-match with a clear error message), and fall back to whole-file for small files.
- **Repo map budget:** 1k tokens default. Local users commonly run 1-2k. Keep it small and personalized [V/K].
- **Cline local:** "Use Compact Prompt" cuts the system prompt about 90%. Recommended model is Qwen3-Coder-30B-A3B. Context at least 32k is ideal (Ollama defaults to 2k/4k, which silently breaks agents). LM Studio guide says **KV-cache quantization off** for proper function [V, partly conflicting numbers]. https://docs.cline.bot/running-models-locally/overview
- **OpenHands local:** recommends Qwen3.6-35B-A3B (needs 24 GB VRAM), Qwen3-Coder-30B-A3B, and Devstral Small 2 (24B). Context above 32k. "If it behaves like a chatbot / refuses tools, it's the model" [V].
- **Practical limits** (community consensus) [K]:
  - Models under about 14B struggle with long multi-tool loops and large system prompts. Keep the tool count small (under 15), with short schemas.
  - Native tool-calling quality varies. XML or JSON-in-text fallbacks help (Cline/Roo use XML tool tags).
  - Compaction and fresh-context subtasks beat a huge context on small models.
  - Deterministic feedback (lint/test/LSP errors) gives the biggest reliability gain per token.
  - Planning is better with the larger model and execution with the smaller one (Aider architect, Cline plan/act split models).

---

## Top 12 patterns worth adopting for Zen Harness (ranked by value/effort)

| # | Pattern | Value | Effort | Notes for Zen |
|---|---|---|---|---|
| 1 | **Project = local folder** + recent-projects list + per-project config (`.zen/project.json`: test/lint/run cmds, allowlist) | Very high | Low | Everything else hangs off this. Scope file tools and the cwd to the root |
| 2 | **Read AGENTS.md** (nearest-file-wins), plus CLAUDE.md/.cursorrules as fallbacks; inject into the system prompt with a token cap | High | Very low | Free interop with existing repos |
| 3 | **Shadow-git checkpoints** per tool call (Cline-style, a git dir outside the project via `GIT_DIR`/`--work-tree`), with "restore files / chat / both" | Very high | Low-Med | Fixes the PowerShell blind spot in the current per-file revert. Never touches user history |
| 4 | **Git panel:** status, per-file/hunk diff, stage, commit with an *LLM-drafted message* the user edits; separate dirty user changes first (Aider) | High | Med | Use `git` CLI subprocess, not a library. Attribution trailer optional |
| 5 | **Post-edit diagnostics in the tool result** (extend the current lint-on-write to report *new* errors only, OpenCode-style) | High | Low | Already half-built. Diff the diagnostics before and after so the small model sees only what it broke |
| 6 | **Aider-style repo map** (tree-sitter tags, PageRank personalized to chat files and mentioned identifiers, 1-2k token budget) as a tool plus auto-injection | Very high for local models | Med | `grep-ast`/`tree-sitter-languages`, networkx pagerank. Cache by mtime |
| 7 | **File tree + multi-file change-set review** (summary bar "N files +X/-Y", per-hunk accept/reject, Zed-style) | High | Med | React tree of the project root. Reuse the existing diff component |
| 8 | **Robust edit format for small models:** search/replace with fuzzy fallback, actionable mismatch errors, whole-file for files under ~150 lines | High | Low-Med | Directly drives success rate on 9B-35B |
| 9 | **Approval tiers** (Codex two-axis style): read auto; in-project write auto in "auto-edit"; shell via allowlist or ask; network/outside-root always ask; `.git` protected from agent writes except via git tools | High | Med | Maps onto the existing PowerShell tool. Small models make destructive mistakes |
| 10 | **Symbol tools** (`find_definition`, `find_references`, `outline(file)`) built on the same tree-sitter index first, real LSP (pyright/tsserver) later | Med-High | Med (TS) / High (LSP) | Tree-sitter covers 80% with no server processes, which matters on 16 GB RAM |
| 11 | **Plan/Act split with model routing** (bigger planner, cheaper executor) plus a spec file (PRD/tasks.md) persisted in the repo | Med-High | Low (already have checklist plus hybrid executor) | Formalize it as a UI mode toggle |
| 12 | **Clone from GitHub/GitLab URL + worktree-per-task** for background/parallel runs, using the system Git Credential Manager; `gh pr create` for PRs | Medium | Med | Parallel *local* agents are GPU-bound on 8 GB (serial), so worktrees mainly isolate background tasks. Defer embeddings index (Roo-style Qdrant) since agentic grep plus repo map is enough |

**Skip or defer:** embedding index (heavy and low marginal value vs repo map plus ripgrep for local), cloud/background VMs, best-of-N parallel agents (VRAM-bound), Docker sandbox (heavy on Windows; prefer allowlist plus protected paths).

## Sources
- Aider repo map: https://aider.chat/docs/repomap.html ; edit formats: https://aider.chat/docs/more/edit-formats.html ; git: https://aider.chat/docs/git.html
- Cline checkpoints: https://docs.cline.bot/features/checkpoints ; local models: https://docs.cline.bot/running-models-locally/overview , https://docs.cline.bot/provider-config/ollama
- Claude Code checkpointing: https://code.claude.com/docs/en/checkpointing
- Cursor indexing: https://cursor.com/blog/secure-codebase-indexing ; parallel agents (forum): https://forum.cursor.com/t/cursor-2-0-x-what-are-parallel-agents-doing/138911
- Roo codebase indexing: https://docs.roocode.com/features/codebase-indexing
- AGENTS.md: https://agents.md/
- Codex sandboxing: https://developers.openai.com/codex/concepts/sandboxing ; Windows: https://openai.com/index/building-codex-windows-sandbox/ , https://developers.openai.com/codex/windows
- OpenHands local LLMs: https://docs.openhands.dev/usage/llms/local-llms
- Zed agent panel: https://zed.dev/docs/ai/agent-panel
- Copilot coding agent: https://github.blog/ai-and-ml/github-copilot/onboarding-your-ai-peer-programmer-setting-up-github-copilot-coding-agent-for-success/ , https://code.visualstudio.com/docs/copilot/copilot-coding-agent
- OpenCode LSP (secondary, older Go project): https://www.mintlify.com/opencode-ai/opencode/features/lsp-integration
