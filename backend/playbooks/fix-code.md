---
name: fix-code
title: Fix a bug or failing script
triggers: fix, bug, broken, fails, exception, doesn't work, wrong output, failing test, debug
examples:
  - "this script should print the mean but prints the wrong number"
  - "the build fails with this error"
  - "the tests fail after my change"
applies_when: the user has code that misbehaves and wants it corrected
---
**Done looks like:** the minimal fix applied, the command that proves it (output shown), and one line on the cause.
**Approach:** reproduce first (run it, read the error and the [Error located] file:line); read the relevant code (read_file around the failure); form a hypothesis — and when it depends on how a library or the code behaves, check it with a few-line probe (python_sandbox / debug_run) instead of reasoning it out from memory; change one thing with edit_file; re-run; repeat. When the fix is not obvious from the code, add a one-line comment at it saying what it prevents (so nobody "simplifies" it back). If a fix fails twice or the error is unclear, web_search the exact error message (with the language or library) and the docs or a working example before the next attempt. Keep the user's style.
**Tools:** run_tests for test suites; debug_run (crash post-mortem, probe a line) and browser_eval / browser_devtools (web pages: network, console, event listeners, breakpoints) to see real values; powershell / python_sandbox to run; read_file; search_text; edit_file (exact old → new) — writes are code-checked automatically; web_search / web_fetch for errors, docs and working examples.
**Pitfalls:** guessing fix after fix instead of looking the error up; rewriting the file instead of a targeted edit; fixing the symptom (e.g. a special case) rather than the cause; declaring it fixed without re-running; changing behaviour the user didn't ask about; blaming the environment (browser, sandbox, OS) without a minimal test proving it; when something silently does nothing, not checking that a handler is attached and the program's entry point actually runs.
**Verify:** the original failing command now passes; existing tests still pass.
**Stop / ask:** if the "correct" output is ambiguous, ask what it should be before guessing.
