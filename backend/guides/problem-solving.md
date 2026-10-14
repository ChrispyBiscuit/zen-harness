# Evidence-driven problem solving

Use this for a complex task or a blocker that is not yielding to ordinary work.
For a simple request, answer or act directly. This is the normal conversation,
not a recovery mode or another agent.

1. Keep the user's requested outcome and constraints visible. Use `problem_plan`
   to record your interpretation, observable acceptance conditions, and the
   unanswered questions preventing progress. The workbook retains actual user
   messages separately: your interpretation is not a replacement for them.
2. For the current question, state at least two plausible explanations. Include
   a faulty test expectation, environment, fixture or interface when applicable.
   Do not assume the implementation is wrong merely because a test fails.
3. Use `problem_experiment` to propose one useful normal tool call. Explain what
   different outcomes would mean. Declare the relevant input files, including
   tests, configuration and dependency files. The software verification adapter
   additionally binds evidence to its own tracked source/contract versions.
4. Execute the declared call through the normal tools. You retain ordinary file
   access and permissions; declaring an experiment grants no new access. Then
   use `problem_record` with the returned controller evidence ID. Separate the
   actual observation from your interpretation. An execution error, missing test
   or inconclusive observation is information, not a passing verification.
5. Refine the question, revise an assumption, or settle it conditionally. Record
   failed approaches and the next useful experiment. Rewording a plan does not
   create evidence. A ready experiment can be cancelled before execution with
   `problem_record(action="cancel", evidence="", ...)`; this does not clear a
   repetition trigger. An executed experiment must have its result recorded.
6. Test the pieces together against the original acceptance conditions. Add
   explicit integration questions referencing acceptance IDs. Link acceptance
   to their executed evidence. Passing a component test is not full task success.

Use `problem_status` to retrieve the current state. `question=ID` retrieves an
older question, `evidence=ID` retrieves exact recorded output (offset pages
characters), `experiment=true` retrieves the pending call's complete arguments,
and offset alone pages all questions. Prompt/compaction views carry a concise
index rather than repeating all outputs and code.

Settled questions reopen when declared inputs or prerequisites change. New
contradictory evidence can also reopen them. External source changes and omitted
dependencies are not automatically discoverable: declare relevant inputs and
inspect freshness when it matters. Recorded conclusions remain interpretations.

The controller detects identical actions with unchanged results, and repeated
structured module-test outcomes despite changing durations/receipt IDs. A
trigger requires a different experiment or changed tracked inputs. It does not
stop the task after a number of calls, limit reasoning time, or prove that a
different-looking action is intellectually useful. If no useful experiment is
available, report the concrete blocker through `ask_user`.

Choose the appropriate evidence for the domain: actual behavior for software,
primary documents for research, recalculation for numerical claims, and explicit
constraints/tradeoffs for planning. Not every conclusion is a Boolean test.
