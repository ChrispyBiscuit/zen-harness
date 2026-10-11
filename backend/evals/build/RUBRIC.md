# Expense-tracker build — grading rubric (same for every model)

Request (SPEC.md): "I want an expense tracker my roommates and I can all use from our own laptops on the home Wi-Fi.
Everyone logs their own expenses, and each month we want to see totals by category. Using it from the terminal is
fine. Make sure it actually works. Put it in <folder>."

Graded hands-on: follow the build's own README, start it, use it as two different people. Each item pass / fail.

| # | Capability | Pass when |
|---|---|---|
| 1 | Starts from its README | The documented commands work without edits |
| 2 | Separate users | Two people can each have an account (or identity) |
| 3 | Log an expense | Amount, category and date (or automatic date) are stored |
| 4 | Own data | Each person sees their own expenses; one can't read or change the other's |
| 5 | Monthly totals by category | A month's totals per category are correct |
| 6 | Shared over the network | A server others can reach (not 127.0.0.1-only); the client takes the server's address |
| 7 | Terminal client | Everything above can be done from the terminal |
| 8 | Persistence | Data survives a server restart |
| 9 | Bad input | Invalid amounts/dates are rejected with a clear message, no crash |
| 10 | Own tests | Tests exist and pass |
| 11 | README | Accurate and complete enough to use it |

Process (from the transcript): planned first (PRD/design/checklist)? errors hit and fixed? time, tool calls, spend.
