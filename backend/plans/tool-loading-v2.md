# Tool loading v2: a smaller always-on set, measured

Status: design for review. Nothing here is built yet.

## What the numbers say (2026-10-16)

**Usage:** 5,201 logged tool calls across 119 chats, 65 distinct tools.

| Tools, by use | Share of all calls |
|---|---|
| Top 5 (read_file, powershell, edit_file, write_file, update_tracker) | 62% |
| Top 12 | 77% |
| Top 20 | 91% |

A typical chat uses 6 distinct tools. At the 90th percentile it uses 15, and the most any chat used is 30.

**Per-step fixed cost on a build turn** (live code, compact prompts, which is the default):

| Part | Tokens |
|---|---|
| System prompt, after build layering | about 3.4K |
| Tool schemas: the 24-tool core | about 4.3K |
| `available_tools` catalog, each time it is called (91 calls in the logs) | about 2.1K |

The largest schemas in the core: update_tracker (479 tokens), module_contract (346), powershell (328), problem_plan (298), run_tests (216), edit_file (196), problem_record (188), problem_experiment (179).

**Correction to an earlier estimate:** I said a restructure would save 5 to 7K tokens a step. Measured, the whole tool block is 4.3K, so the realistic saving is about 2 to 3K tokens a step, plus about 2K each time the catalog would have been dumped.

## Hierarchical loading already exists

- **A core set:** 15 tools, plus the module, workbook and focus_pack tools when the turn calls for them.
- **Groups by request words:** groups the request's words call for are turned on at the start of the turn (`_profile_tools`).
- **On demand:** everything else is listed by `available_tools` and loads when enabled or first called.
- **A stable list:** the list stays fixed for the turn. Changing it forces a full re-read on a hybrid model, about 60 to 90 s at 50K tokens on this machine.

So v2 tunes what's always on and how much each tool costs. It doesn't need a new mechanism.

## Proposal

1. **Core by evidence.** Keep these always on:
   - read_file, edit_file, write_file, powershell, update_tracker, run_tests
   - list_dir, search_text, find_files, make_dir
   - ask_user, available_tools

   Move these to on-demand:
   - **problem_plan, problem_experiment, problem_record, problem_status:** 15 calls ever, about 840 tokens a step. They load when the workbook is started, or on first call.
   - **module_contract, begin_module, verify_module, module_status:** these load only when the ledger is actually in module mode, meaning a from-scratch folder. A build request alone isn't enough.
   - **history_search and go_extensive:** rarely called.
   - **read_guide:** 61 calls, so it stays if its schema is cut to one line.
2. **Brief schemas.** Cap each always-on description at about 200 characters and one example. Detail moves to a `read_guide` page per tool, and the error message points there when an argument is wrong. update_tracker and powershell are the main targets, together worth about 400 tokens.
3. **A compact catalog.** `available_tools` returns one line per group by default, with the group's tools by name only, at about 300 tokens. `available_tools(group="web")` shows that group's tools in full.
4. **Load decisions at turn start, not mid-run.** Keep the turn-start profile, and add the code-work and module-mode decisions to it. A tool first called mid-run still loads, but it's logged as a "late load", so the core can be adjusted from data.
5. **No `use_tool` indirection.** The earlier idea, one generic `use_tool(name, args)` instead of real schemas, is dropped. Small models make many more argument errors without the real schema, and every call becomes two hops.

## Expected effect

| | Now | v2 |
|---|---|---|
| Tool schemas per step, build turn | about 4.3K | about 2.0 to 2.5K |
| Catalog dump | about 2.1K per call | about 0.3K |
| Share of a 60K window that is fixed | about 13% | about 9% |

At 15 to 30 tokens per second of prompt re-reading after a cache miss, that's about 5 to 15 seconds saved per full re-read. On top of that there's more room before compaction. The saving is real but modest: the attention cost of a long history is still the bigger problem.

## How to measure it

Run an A/B with the same model, the 27B with thinking off, on:
- one build: the roommate tracker, in a fresh folder for each arm
- the battery's code and file categories

Compare:
- tokens per step and prompt size at step 10
- full re-reads per run, and late tool loads
- task success: passing tests at the end, and battery pass rate

Ship only if success doesn't drop.

## Risks

- **A late load costs a full re-read** on a hybrid model. Mitigation: log late loads and adjust the core from them. A late load is still cheaper than carrying the tool on every step of every chat.
- **A model may not find a tool it needs.** Mitigation: `available_tools` stays in the core, and the catalog line for each group says what the group is for.
- **Shorter descriptions may cost argument errors.** Mitigation: errors point to the tool's guide page, and the A/B tracks argument errors.
