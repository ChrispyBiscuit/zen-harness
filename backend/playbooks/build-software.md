---
name: build-software
title: Build a program, app or game
triggers: build, make, create, code, program, app, application, game, tool, website, prototype, project
examples:
  - "make a browser extension that saves tabs to a list"
  - "build a small to-do app with a web interface"
  - "create a command-line tool that renames files by date"
  - "write a desktop app that tracks my reading list"
  - "code a simple dashboard for this CSV"
applies_when: the user wants new software built, not a one-line fix or a single small script
---
**Done looks like:** PRD.md, then working code in a project folder, a test script that checks each acceptance criterion and passes, and — for anything with a screen — saved screenshots that you have looked at.
**Approach:** for a big build (a full application, site or game, unfamiliar technology) work in project mode: read_guide('project') — research, PRD, design and task plan in docs/ first. Otherwise: read_guide('software'); write PRD.md first (goal, users, features by priority, acceptance criteria as checkable behaviours, non-goals, tech, file layout); build the checklist FROM the PRD; build the smallest working version first and test it before adding features; keep logic separate from drawing/IO so it can be tested; split the code into small files by responsibility (logic/state, rendering or output, input/UI, data, entry point), each about 200 lines or less so it can be written or fixed in one call — plan that file layout in the PRD; one feature at a time, test after each; when stuck (a fix fails twice, an error you don't understand), web_search the error or technique and read the docs or a working example before trying again.
**Tools:** read_guide; write_file / edit_file (code-checked automatically); run_tests for the test suite; debug_run / browser_eval / browser_devtools (network, console, listeners, breakpoints) to inspect real values when something fails; powershell to run the program; read_file on saved .png screenshots to see the program; web_search / web_fetch when stuck.
**Pitfalls:** one monolithic file (a giant write that gets abandoned or truncated, and every fix rewrites everything); "it ran without an error" counted as done; launching a window you cannot see as the only test; logic tangled with drawing/IO so nothing can be tested; adding features before the smallest version works; states that never change back; code paths that are never called; a main object or entry point that is defined but never created/started; controls in the page with no handler wired to them.
**Verify:** run the test script — every acceptance criterion passes; for visual programs, save frames at key moments and look at them; play through the main path in a scripted run.
**Stop / ask:** if the request is broad and open choices would change the build (purpose, platform, scope, look and feel, must-haves), ask_user first with 2-5 questions and defaults; record the answers or defaults in the PRD.
