# Code graph for the coding harness — PRD

Status: P1–P3 built 2026-10-26 (code_graph.py, repomap.py); P4 not started. Setting: `agent.code_graph_check`
(on). Results:
- P1: regression set (tests/test_code_graph.py) catches 13/13 seeded bugs, both FPS-run bugs included, with 0 false
  alarms in 15 tricky clean cases; a warm check on a 200-file project is well under 200 ms. On this app's own code it
  reports 1 finding in 204 backend files (a real broken reference in legacy/bench.py) and 0 in 92 UI files.
- P2: `code_graph` tool (callers / callees / impact / path / explain). The tool-call reduction target needs model
  runs on recorded tasks: not measured yet.
- P3: tree-sitter definitions under the code map for JS/TS/JSX/TSX, C, C++, Go, Rust, C#, Java (regex fallback kept).
  On this app's UI it finds 804 definitions vs 786 (28 the regexes missed, none real lost); hand-checked fixtures in
  six languages pass. The context-pack benchmark can't measure P3 (it doesn't use the code map, and its JS corpus was
  deleted), so coverage is the measure.
- The PRD named tree-sitter-language-pack: it downloads grammars at run time, so the per-language packages (grammars
  inside the wheels, ~1.4 MB together, MIT) are used instead. Inspired by graphify (Apache 2.0, a code knowledge-graph tool for
coding agents); only its local, deterministic ideas are taken. **Constraint: nothing here calls a cloud model.**
Parsing, graph building, checks and queries are plain code; any wording is done by the local model.

## Problem

1. **Cross-file mistakes go unseen.** Both failures that sank the 3D game runs were names that resolve to nothing in
   another file: `engine.js` read `ARENA.center`, which `config.js` never defines (the page crashed on load after
   20/20 unit tests passed), and `main.js` imported `Screen` from `ui.js`, which exported `UIScreen` (about 50 steps
   lost). No check in the harness looks for this.
2. **Answering a structure question costs many steps.** "Who calls X?", "what breaks if I change X?" take the model
   several `search_text` / `read_file` calls, and each step costs real minutes on a 1-bit 27B at ~20 tok/s.
3. **The code map is weak outside Python.** `repomap.py` parses Python with `ast`; JavaScript, TypeScript, C, Go, Rust
   and C# use regex, and links are between files, not functions.

## Goals and non-goals

Goals: catch broken cross-file references the moment they are written; answer structure questions in one tool call;
a code map that is accurate in the main languages. All local, all deterministic.

Non-goals: concept graphs from documents, papers or images (graphify does these with a cloud model); an interactive
graph view; putting more graph into the prompt by default. The harness's own benchmark found that following
callers/callees to pack context did not help (`evals/context_pack/RESULTS.md`), so the graph is used to answer and to
check, not to stuff the prompt.

## Proposals

### P1. Broken-reference check after every code write (priority)

- A tree-sitter symbol table per project: for each file, what it defines and exports, and what it imports and uses
  (imported names, `module.member`, object fields read from imported constants).
- After each `write_file` / `edit_file` of code, resolve the names the changed file uses and the names other files use
  from it. Report those that resolve to nothing, with file:line and the nearest real names
  (`ARENA.center: config.js defines ARENA with size, wallHeight, … — no center`;
  `ui.js exports UIScreen, not Screen`).
- Shown in the write result, like the existing code check; findings stay open until fixed, and an unresolved one is
  listed in the run-before-done reminder.
- Conservative by design: only names the parser can resolve with certainty are reported; dynamic access
  (`obj[key]`, `getattr`, wildcard imports, globals from `<script>` tags) is skipped, not guessed.
- Languages first: Python, JavaScript/TypeScript (ES modules and CommonJS), then C/C++ headers, Go, Rust, C#.

Done when: on a regression set it catches the two FPS-run bugs and at least 80% of seeded ones (a misspelt export,
a missing config field, a renamed function with old callers, a wrong relative import path), with no more than one
false alarm per 20 clean writes, and adds under 200 ms per write on a 200-file project.

### P2. Graph questions in one tool call

A `code_graph` tool (or new `trace_symbol` actions):
- `callers X` / `callees X`: functions that call X / that X calls, with file:line.
- `impact X`: what breaks if X's signature or name changes (callers, overrides, re-exports, tests that use it).
- `path A B`: the shortest chain of calls/imports from A to B.
- `explain X`: definition, signature, docstring/first comment, callers and callees in one short block.

Results are short and capped (about 40 lines), with file:line so the model can `read_file(symbol=)` next.

Done when: on 10 recorded coding tasks the same results need fewer tool calls (target: 30% fewer `search_text` +
`read_file` calls before the first edit), with no drop in task success.

### P3. tree-sitter under the code map

Replace the regex extractors in `repomap.py` with tree-sitter for the languages it covers (keep `ast` for Python,
keep regex as the fallback), and add function-level edges (calls) beside the file-level ones. The existing
personalised PageRank, cache by mtime and the 550-token map stay as they are.

Done when: the context-pack benchmark (`evals/context_pack`, 50 questions) does not get worse, and the code map's
symbol coverage on the JS/TS/C test projects rises (count of definitions found vs a hand-checked list).

### P4. Architecture overview for large repos (later)

On projects above about 300 files: group related files (Leiden or Louvain clustering, local, no model) and list the
most-connected files per group, as a short overview the agent can ask for (`code_graph overview`). One-line group
names, if wanted, come from the local model. Not injected automatically.

Done when: on two large open-source repos the overview's groups match the projects' own top-level structure in a
hand review, and the agent's first-step orientation on them uses fewer tool calls.

## Dependencies and cost

- `tree-sitter` (MIT, 0.1 MB wheel) and `tree-sitter-language-pack` (MIT, 2.3 MB wheel); networkx is already used.
  Both ship in the installer; no runtime download, no model, no network.
- Index kept beside the repo map's cache (`<data>/repomap/`), updated incrementally by file mtime on each write.

## Evaluation plan

1. **Broken-reference regression set** (new, `evals/code_graph/`): small JS/TS/Python/C projects with seeded bugs,
   plus the two FPS-run cases reconstructed; scored for catches and false alarms. Runs offline in the test suite.
2. **Context-pack benchmark** (existing): before/after for P3.
3. **Tool-call count** on recorded tasks (existing battery code tasks and the roommate tracker): before/after for P2.

## Risks

- **False alarms** teach the model to ignore the check: hence certain-only resolution and the false-alarm target.
- **Stale index** after a command changes files (a generator, `git checkout`): re-index files whose mtime changed
  before each check.
- **Grammar gaps**: unparsable files fall back to today's regex path and are never reported on.

## Order of work

P1 (with its regression set) → P2 → P3 → P4. Each ships on its own and is measured before the next starts.
