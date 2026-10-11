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
**Done looks like:** a project folder with docs/ (INDEX.md, research/ notes with sources, PRD.md, DESIGN.md, TASKS.md, DECISIONS.md), the deliverable built from that plan, every acceptance criterion tested, README.md, and a short report.
**Approach:** read_guide('project'). Clarify (ask_user) if choices are open → research questions in INDEX.md → one research note per question (official docs first, exact names and versions verified, sources linked) → PRD.md → DESIGN.md → TASKS.md (milestones with done-when) mirrored in the checklist → build milestone by milestone with tests, updating TASKS.md and DECISIONS.md → verify everything → README.md → report.
**Tools:** read_guide; ask_user; web_search / web_fetch for research; write_file for docs; artifact_create for a short project overview if useful; run_tests / debug_run / browser tools to verify; show_user to present the result.
**Pitfalls:** writing code before research and design (the harness refuses it); research notes without sources or with names from memory; a PRD that is a feature wish-list without checkable criteria; letting TASKS.md and INDEX.md go stale; re-deriving facts that are already in a research note.
**Verify:** every acceptance criterion has a passing test or a looked-at screenshot; INDEX.md lists every document with its status.
**Stop / ask:** at the start if the goal, platform or scope is open; mid-way if research shows the request can't be met as asked — say what is possible instead.
