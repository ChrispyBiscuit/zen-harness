---
name: math-calculation
title: Mathematics and calculations
triggers: calculate, compute, solve, derivative, integral, limit, equation, math problem, statistics, probability, how much is, convert units
examples:
  - "work through a few integration and series problems"
  - "solve this system of equations"
  - "what is the integral of x squared times e to the x"
  - "compute the compound interest on a loan"
  - "work out the probability of rolling two sixes"
  - "convert 65 miles per hour to metres per second"
applies_when: the user wants numbers or symbolic results worked out, shown in the chat — not a document unless they ask for one
---
**Done looks like:** the results in the chat, each with the expression that produced it, written in LaTeX ($...$, $$...$$), with brief steps when they help understanding. No file, deck or document unless the user asked for one.
**Approach:** restate each problem precisely; compute every result with `calculate` (or `python_sandbox` for numeric work over data); for "show me some" requests, pick problems that genuinely exercise the technique (e.g. indeterminate forms, chain/product rule, series), not textbook trivia.
**Tools:** calculate (limit, diff, integrate, solve, simplify, units, dates, statistics); python_sandbox for numerical methods or plots; artifact_create (markdown) only when the work is long.
**Pitfalls:** mental arithmetic; reporting a result the tool did not produce; rounding without saying so; turning a calculation request into a presentation.
**Verify:** check each answer a second way when cheap (substitute back, numeric spot-check, differentiate an integral).
**Stop / ask:** if the problem is ambiguous (domain, units, which variable), state the assumption or ask one question.
