# Debug toolkit — gap analysis and plan (2026-10-10)

Evidence: an inventory of every build/run/test/debug tool, aid and guide, plus an episode-by-episode analysis of
four 9B build runs (ornith2/3/5/6, ~370 tool calls). The 9B was rarely blocked by not knowing how to code; it was
blocked by not SEEING: which process answered, what values the code computed, what changed since it last worked.
~250 of the ~370 calls went to problems one capability below would have solved in 1–2 steps.

## Ranked by calls lost

| # | Blocker (calls lost) | Build / integrate |
|---|---|---|
| 1 | Requests reached the WRONG PROCESS — leftover servers, two servers on one port (Windows reuse), the app's own port (~95) | **`port_info(port)`** tool: every process listening on a port (pid, start time, cwd, command line, "yours / this app / other"). bg_start(wait_port) refuses/warns on ANY foreign holder, not just the agent's own. Per-turn process cleanup ✅ (done). |
| 2 | Client and server never actually met — wrong port/host, invisible (~60) | **Request log by default**: bg_output shows the server's access lines; guide: servers log each request, clients print the URL they call. **In-process HTTP test template** (server on a free port inside the test) in the software guide. |
| 3 | Bugs it introduced and couldn't find by reading (salt hashed twice, commit reorder, `global`, fetchone after INSERT) (~75) | **"Tests last passed" diff**: after an edit to code that has tests, the harness says "re-run the tests" and offers **`changes_since_green`** — the diff of every file since the last passing test run (the backups already exist). **`trace_call`**: run one function call and print each line with its locals (extends debug_run's probe from one line to a whole function). |
| 4 | Tests that can't fail or don't test the real path (`check(True)`, "login" test that never logs in, bare assert with no message) (hidden; caused #3) | Guide: one round-trip test per feature (register→login, insert→fetch, authed GET **with a query string**), in-process over HTTP. Code check flags `assert True` / `check(..., True)`. run_tests prints full assertion details. |
| 5 | Wrong belief about where code runs ("the sandbox can't reach the host"; python_sandbox used to call localhost) (~20) | python_sandbox refuses localhost/LAN URLs with "runs isolated, not on this PC — use powershell / http_request"; one line in the guide. |
| 6 | Edit loops — add/remove cycles with no net change; "old text not found" retries (~25) | Detect edits that cancel out (net diff empty since N steps) and say so; after 2 failed matches suggest replace-by-line-range. |
| 7 | Harness side effects | Fix the done-check: **run_tests, debug_run, http_request, browser_devtools count as testing** (today they don't — testers get "you never tested"). Log which guard fires. A checklist with no build/test items can't read "done". Find the reminder leaking another project's checklist path (ornith6: "references a different project's checklist (OneDrive path)"). |
| 8 | Windows quirks (~10) | Software guide cheat sheet: `curl.exe` not `curl`, LAN IP from Get-NetIPConfiguration (not 169.254.x), close a DB before deleting it, always start servers with bg_start. |

## Also worth fixing (inventory)
- Tool results are cut to the first 8,000 chars (start only) — tracebacks and later output are lost; use head+tail for code/run tools.
- http_request: show response headers, keep a cookie session (login flows), longer body.
- bg_output: `grep` filter and "only new lines since last read".
- git_tools / checkpoints exist but the agent can't use them; edit backups exist but can't be listed or diffed → expose as `changes_since_green` + `undo_file`.
- No Node REPL / no type or ESLint checks (lower priority for now).

## Order of work
1. Done-check fix + port_info + foreign-holder guard + python_sandbox localhost refusal (small, high value).
2. changes_since_green + "re-run tests" reminder + undo_file (uses existing backups).
3. trace_call (debug_tools) + request-log visibility in bg_output (grep / new-since-last).
4. Guide: in-process HTTP test template, round-trip tests, Windows cheat sheet; code check for tests that can't fail.
5. Edit-loop detector, head+tail clipping, http_request headers/cookies, guard logging, leaked-reminder fix.
Validate each step on the offline suite, then a 9B build re-run graded on RUBRIC.md.
