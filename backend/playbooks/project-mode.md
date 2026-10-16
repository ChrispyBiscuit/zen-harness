---
name: project-mode
title: Big project — research, PRD, design, plan, then build
triggers: full application, complete app, whole website, full game, large project, research report, in-depth report, from scratch, end to end, production ready, multi-part, big build
examples:
  - "build me a full web app to track my workouts with charts and accounts"
  - "make a complete 3d game with levels, enemies and a menu"
  - "research the market for home batteries and write an in-depth report"
  - "create a desktop tool that manages my photo library end to end"
applies_when: the work is big and long — many parts, unfamiliar technology or a domain to learn — so research and design must come before building
---
**Done looks like:** a project folder with docs/PRD.md and docs/DESIGN.md (plus research/ notes only for choices that needed facts, and INDEX.md / TASKS.md / DECISIONS.md only for work a later chat will pick up), the deliverable built from that plan, every acceptance criterion tested, README.md, and a short report.
**Approach:** read_guide('project'). Clarify (ask_user) if choices are open → research only the questions whose answers you must look up (one note each: official docs first, exact names and versions verified, sources linked) → PRD.md → DESIGN.md → milestones in the checklist, each with a done-when your tests can show (never one that needs a feature nobody asked for) → build milestone by milestone with tests → once the suite passes, README.md → report. The paperwork scales with the work: a small app needs a PRD and a design, nothing more.
**Tools:** read_guide; ask_user; web_search / web_fetch for research; write_file for docs; artifact_create for a short project overview if useful; run_tests / debug_run / browser tools to verify; show_user to present the result.
**Pitfalls:** writing code before research and design (the harness refuses it); research notes without sources or with names from memory; a PRD that is a feature wish-list without checkable criteria; spending steps on status files within one chat (the checklist is the status); adding verification scripts on top of a green suite; re-deriving facts that are already in a research note.
**Verify:** every acceptance criterion has a passing test or a looked-at screenshot; the green suite run is the evidence.
**Stop / ask:** at the start if the goal, platform or scope is open; mid-way if research shows the request can't be met as asked — say what is possible instead.
