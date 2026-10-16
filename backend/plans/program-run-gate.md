# Run the program before "done"

Status: built 2026-10-16. Settings key: `program_run_gate` (true by default; false turns the gate off, the receipts
are still recorded).

## What went wrong

Run 71a2ec62 (27B, no thinking, the 3D FPS game): 20 of 20 unit tests passed, every module verified, 18 of 18
checklist items ticked, final answer "your game is done and ready to play". The page crashed while loading:
`engine.js:83` reads `ARENA.center`, which `config.js` never defines. Nothing in the harness had required the
program to run. Unit tests prove the parts; only a run proves they are wired together.

Three smaller things from the same run:

- The browser kept an old `ui.js` in its cache for about 50 steps. The server was sending the right file; the harness
  browser was the stale part.
- The model ticked 7 checklist items in one call with no check at all. In module mode, an item no module links to
  was ticked without any condition; in evidence mode the same tick would have needed the changed files covered by a
  passing run.
- `run_tests` picked `npm test` because the text `"test"` appeared in package.json. The key was at the top level,
  not under `scripts`, so npm reported "Missing script" and the model learned to call every red result spurious.

## The rule

Before the final answer, the project's entry point must have run since the last change to a source file, without
crashing. A run is:

| Project | Entry point | A clean run means |
|---|---|---|
| web page (`index.html`) | the page in the harness browser | loaded with no uncaught JavaScript error and no failed script, style or data request |
| server (`server.py`, `app.py`, `manage.py`, `npm start`, …) | the process | started and still running after the start window, or a request to it answered below 500 |
| command-line program (`main.py`, `cargo run`, `go run .`, …) | the process | exited with code 0, or ran until the time limit without an error |
| library, docs, tests only | none | nothing extra: the tests are the run |

A change to a test file or runtime data does not reset the gate; a change to any other code file does. The gate is
checked only at the end: a reminder with the exact call, once; a second answer goes out labelled, like the untested
changes label.

## Receipts come from what the model already does

No new tool and no declaration. The harness records a run when:

- `powershell` or `python_host` runs a program command (`python x.py`, `python -m x`, `node x.js`, `npm start`,
  `cargo run`, `go run`, `dotnet run`, `java -jar`, `uvicorn`, `flask run`, `http.server`, …) that is not a test
  command: exit code 0, "still running after Ns" or a blocked-and-adopted command counts as ran; a non-zero exit
  is a crash with the first error line kept.
- `bg_start` starts one: counts unless it "already exited" or is "not listening" on the asked port, or its first
  output has a traceback.
- `browser_open` or a reload lands on a local page (file:, localhost, 127.0.0.1): the console since the page opened
  decides, with the error and its file:line kept.
- `http_request` to a local server: a status below 500 counts (this never satisfies a page project: a 200 for
  index.html says nothing about its JavaScript).

The refusal names the call exactly, built from the project: `bg_start(command='python server.py', wait_port=8312)
then browser_open(url='http://127.0.0.1:8312/')`, or `browser_open(url='file:///…/index.html')` for a page without
ES modules, or `bg_start(command='python -m http.server 8000', wait_port=8000) then browser_open(…)` for one with
them, or `powershell(command='python main.py')`.

## Browser changes

- Local pages load with the HTTP cache disabled (CDP `Network.setCacheDisabled`), so an edited module is what the
  page runs. Websites keep their cache.
- A local page waits for the `load` event (module scripts included) before the console is read.
- The location on an uncaught error is the first frame in the project's own files (`engine.js:83`, not
  `three.module.js:3991`), and the line is as the browser reports it. The old code added 1 to a 1-based line.

## Checklist ticks

Both modes now track which files this chat changed since a passing run covered them (`verification_ledger`
`changed`). In module mode a passing `verify_module` covers the module's files, and a passing test run covers what
it ran. A done tick on a code item needs no uncovered changes, whether or not a module links to the item. The
refusal names the files and the run that clears them.

When the last open item is ticked, the result already says what the final answer will need: "Before you finish:
run the program: …". So the model hears it with the checklist, not after writing the answer.

## Not built, and why

- Per-item file attribution for ticks (knowing which files item 5.1 is about). It would need either the model
  naming files per item (ceremony) or guessing from the "current" item (wrong when the checklist is stale). The
  state-level rule above is weaker but automatic, and the final gate holds either way.
- A built-in static server for page projects. It would remove the model's own `server.py`, the port fights and the
  leftover processes. Worth doing; separate change.

## Limits

A clean load does not mean the program works: the player could still be stuck, enemies never spawn. This gate
catches the wiring errors (a missing export, an undefined config field, a wrong import path), which is what the
run above died of. Behaviour is still the model's job; `read_guide('software')` describes it. A stale server
answering an `http_request` after an edit still counts as a run: the harness cannot tell a dev server that
reloads from one that does not.

## To go back

`"program_run_gate": false` in settings.json under `agent`. Receipts keep being recorded for the logs.
