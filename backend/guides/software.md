# Building software — PRD first, test behaviour, look at what you built

For a big build (a full application, site or game, or technology you don't know well) work in project mode:
read_guide('project') — research notes, PRD, design and a task plan in docs/ before any code. This guide covers the
engineering practice inside that.

## 1. PRD.md before any code
Write `PRD.md` in the project folder. Keep it to one page:
- **Goal** — one sentence: what it is and who it is for.
- **Core features, in priority order** — the first one is the "smallest working version".
- **Acceptance criteria** — each a behaviour you can CHECK in a test, e.g. "submitting the form with an empty name shows an error", "the totals row equals the sum
  of the column", "an unknown command prints usage and exits with code 2".
- **Non-goals** — what you will not build now.
- **Tech and file layout** — language, libraries, files and what each holds.
Then make the checklist FROM the PRD: milestone 1 PRD, 2 smallest working version + its tests, then one milestone
per feature (each with its tests), last milestone: run every acceptance test and review screenshots.

## 2. Make it testable — and modular
- Split the program into small files by responsibility (core logic / state, rendering or output, input or UI,
  data and config, entry point) — each about 200 lines or less, so it can be written or fixed in ONE tool call
  and read back cheaply. A single huge file means one giant write you are likely to abandon or truncate, and
  every fix rewrites everything. Web apps: ES modules (`<script type="module">` + `import`) or one small
  script per part; Python: a package or a few modules. Put the planned file layout in the PRD/design.
- Separate **logic** from **drawing / input / IO**. An update step changes state and a separate draw/render step only
  displays it; a web app's handlers call plain functions. Then tests can drive the logic without a window.
- **Unsure how a library behaves? Probe it.** A few lines in python_sandbox (or debug_run to see real values)
  answer "does BaseHTTPRequestHandler log to stderr?" in seconds; reasoning it out from memory takes minutes and is
  often wrong.
- **Parts that talk to each other share a written contract.** In the design, list every endpoint or command:
  method, path, inputs, outputs, error codes — plus ports, environment variables and file locations. Client,
  server and tests all follow that table; most "it doesn't work" bugs between layers (POST vs GET, the wrong
  port, a query string the router doesn't expect, a dropped base URL) are departures from it. Put it under a
  "Contract" heading in DESIGN.md (or in CONTRACT.md), including the command-line usage: the app pins it into every
  step, so it stays in view however long the work gets — update it when an interface changes.
- **Build a thin end-to-end slice first:** one real request through every layer (client → server → storage and
  back), tested, before any other feature. Then add features one at a time and re-run the tests after each.
- **Comment the why.** Each file opens with a docstring or header comment saying what it is responsible for;
  each class and non-trivial function says what it does, its inputs/outputs and errors. Add a short comment wherever
  a reader would ask "why?": a workaround, a platform quirk (file locking on Windows, encodings), an invariant, a
  choice between approaches, a bug a line prevents. Don't narrate what each line does.
- Anything time-based gets a fixed step in tests so scripted runs are repeatable.
- Every state that changes must have a path that changes it back (flags cleared, timers reset).

## 3. Test behaviour, not "it ran"
A program that starts without a traceback can still be completely wrong. Write `tests/test_behaviour.py` (plain
asserts are fine) with one test per acceptance criterion (functions named `test_*`), run it with **run_tests** (pytest: it never
hides a traceback), and fix until it passes. **Write the tests a group at a time** — one feature or acceptance
criterion per write (`append=true` for the next group), running them after each: a 500-line test file written in
one go comes back with duplicate functions and syntax errors, and its failures are hard to tell apart. Tests call the
program exactly as the contract says (the same command-line arguments, endpoints and file names) — check the
contract, don't recall it. If you write your own
runner, print the full traceback for every error (`traceback.print_exc()`), not just the message: an error without
its file and line sends you looking in the wrong file.

**Programs with a window or screen (desktop apps, games, visualisations):** drive them without a visible window and
script the input. Most toolkits can run headless (pygame: `SDL_VIDEODRIVER=dummy`; Qt: `QT_QPA_PLATFORM=offscreen`;
tkinter: call `update()` yourself and use `event_generate`). Call the logic step with scripted inputs, assert on the
resulting state, then render and save a frame as .png at key moments and **look at it with read_file** (.png files come
back as images). A blank window, elements off-screen or overlapping are obvious in one frame.

**Web apps:** start the server in the background (bg_start), open it with browser_open, use browser_screenshot and
browser_read, click through the main flow.
**Servers and APIs:** start it with `bg_start(command, wait_port=PORT)` — the result says whether it is listening and
on which port (bg_list / bg_output say the same later). Call it with **http_request** (method, url, JSON body): one
call per check, no curl/PowerShell scaffolding. Pick a free port that is not the app's (8765, 8099-8101); a busy port
means pick another — never stop a process you didn't start. Not listening? Read bg_output for the real error, or run
the server in the foreground for a few seconds to see it. Test scripts that start the server themselves must stop it
at the end (otherwise the run hangs until the timeout).
**Command-line tools:** run with sample inputs, compare the output with what you expect (worked out independently).

**Tests that can fail:** one round-trip test per feature, checking a real result — register → log in, add → fetch
it back, a filtered/authed request **with a query string** (`/report?month=2026-10`). `assert True` or a "login"
test that never logs in proves nothing (the code check flags lines that can never fail).
**A server test without ports or processes** — start the server inside the test on a free port:
```python
import threading, http.server, json, urllib.request
from server import Handler                      # your handler class
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)   # port 0 = any free port
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"
# ... urllib.request calls against URL, assert on status and JSON ...
srv.shutdown()
```

## 4. When stuck, look it up
If a fix fails twice, or you don't understand an error or a wrong behaviour, stop guessing.
**Your debugging tools — use them before guessing:**
- **changes_since_green** — every change since the tests last passed: the edit that broke it is usually there.
  Re-run the tests after each change so a break shows up where it happened. **undo_file** undoes an experiment.
- **trace_call** — run one call (`verify_password('ann', 'pw1')`) and see every line with the values that changed:
  where a value goes wrong is visible at once (two different salts, a None, a wrong key).
- **stack_dump** — a server or command that HANGS: shows the exact line every thread is waiting on (yours marked).
  A command that goes silent and idle for 10s now comes back by itself with these stacks (for it and your servers).
- **trace_symbol(name, root)** — a function/class/variable and everything connected to it across files: definition,
  every caller shown inside its whole function, what it calls. Same-named functions elsewhere are kept apart. Use it
  before changing a function and when a bug may span two places.
- **search_text(pattern, root, context='function')** — every use of a name shown inside its whole function, so
  related code (a lock taken in two places, a caller and its callee) is on screen together.
  Commands and test runs that time out show it automatically. A hang is invisible otherwise — don't read code
  looking for it, ask where it is waiting.
- **port_info** — who listens on a port (yours / this app / another program). Two processes can hold one port on
  Windows; a 404 or response you didn't write means another process answered.
- **bg_output** with `grep` ('Traceback|Error|GET|POST') and `new_only` — the server's log since your last look;
  log each request in the server (method, path, status) so you can see what actually arrived.
- **http_request** keeps cookies like a browser: log in once, then call protected routes.
- python_sandbox is isolated (not on this PC): use http_request / python_host / powershell for anything local.
**Windows:** `curl.exe` (plain `curl` is a PowerShell alias), the LAN IP from `Get-NetIPConfiguration` (not
169.254.x), close a SQLite connection before deleting its file, start servers with bg_start (stopped for you when
you finish).
Then:
- First find WHERE it is raised: get the full traceback and read its last frame — that file and line, not the file
  you expected. Command output marks it as **[Error located]**.
- See the real data instead of guessing: **debug_run** shows a script's local variables where it crashed, or at
  any line you name (`probe='src/parser.py:42'`, with `watch` expressions); in a web page, **browser_eval**
  evaluates expressions in the page (`state.items.length`, `form.elements.length`), and **browser_devtools** is the
  page's DevTools: every network request, the full console, an element's event listeners and whether something
  covers it, breakpoints with local variables, and pause-on-exception. Every file you write is code-checked
  automatically (syntax errors, undefined names) — fix what it reports first.
- Something does nothing (a button, a key, a command) and there is no error: trace it in the code. Find where the
  program starts (is the main object actually created and started?), find the element or command by its id/name,
  confirm a handler is attached to it (in a web page: browser_devtools action='element'), and confirm the handler
  runs (a log line, or a breakpoint on it). Silence usually
  means nothing is listening.
- Never conclude "the environment can't do this" (the browser, the sandbox, the OS) without proof: make the smallest
  test of just that feature (a tiny page or script) and show it fails there too. Until then, assume the bug is in
  the code.
- web_search the exact error message (in quotes) with the language or library name.
- Read the official docs for the API or technique you are using, and find a known working example of it; web_fetch
  the best result and compare it with your code line by line.
- Write what you learned in the checklist notes, change one thing at a time, and re-run the tests.
- If the approach itself can't work, revise the design (and the PRD) instead of patching around it.

## 5. Keep a reference artifact
For anything bigger than a small script, make one artifact (artifact_create) as your working memory: the design in
a few lines, decisions and why, the exact names and APIs you depend on (with where you verified them), and open
problems. Update it when something changes (artifact_edit); read it (artifact_read) when you resume or after the
conversation was compressed, instead of re-deriving or guessing. PRD.md stays a file in the project.

## 6. Finish
Run the full test script once more, look at the latest frames, then report: what was built, how to run it, which
acceptance criteria pass, and anything not done.
