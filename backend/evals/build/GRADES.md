# Expense-tracker build — hand grades (RUBRIC.md)

Note on item 4: the request says "everyone logs their own expenses" — it does not ask for privacy between roommates,
so item 4 is graded as "expenses are attributed to the person who logged them"; privacy/auth is noted separately.
Scores: 1 = pass, 0.5 = partly, 0 = fail.

## Sonnet 4.6 + harness — 9.5 / 11 · 336 s · 35 tool calls · ~$1.82
Design: raw-socket JSON-lines server (0.0.0.0:5757, prints the LAN IP and the exact client command), SQLite,
interactive menu client (`python client.py <ip>`), .bat launchers, docs/PRD.md + docs/DESIGN.md written first.

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | `python server.py` / `python client.py <ip>` work as written |
| 2 | Separate users | 0.5 | identity = a typed name; no accounts or passwords |
| 3 | Log an expense | 1 | amount, category, note, date (defaults to today) |
| 4 | Attributed to the logger | 1 | every row stores the user; list can filter by person (no privacy — anyone sees all) |
| 5 | Monthly totals by category | 1 | October summary correct ($162.49: rent 100, transport 40, food 22.49) |
| 6 | Over the network | 1 | binds 0.0.0.0, prints LAN IP; client takes the IP |
| 7 | Terminal client | 1 | menu client drove add + summary end to end |
| 8 | Persistence | 1 | summary after a server restart still had the earlier rows |
| 9 | Bad input | 0.5 | negative amount rejected; bad date "10/01/2026" ACCEPTED and stored |
| 10 | Own tests | 0.5 | all test cases pass; both suites ERROR in tearDown (deletes the DB while its server holds it — Windows lock) |
| 11 | README | 1 | clear host/roommate instructions, menu walkthrough |

## Haiku 4.5 + harness — 10 / 11 · 524 s · 40 tool calls · ~$1.72
Design: Flask HTTP server (0.0.0.0, prints the LAN URL), SQLite model module, Click CLI with subcommands
(`add/list/totals`, `--server IP:PORT`, plus a local-only mode), requests-based client. Project mode: docs/research
(3 notes), PRD, DESIGN, INDEX before code. Needs `pip install flask click requests` (README says so).

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | server + CLI commands work as written (after the documented pip install) |
| 2 | Separate users | 0.5 | identity = `--user NAME`; no accounts or passwords |
| 3 | Log an expense | 1 | amount, category, date (default today), description |
| 4 | Attributed to the logger | 1 | user stored per row and shown in list (no privacy) |
| 5 | Monthly totals by category | 1 | October totals correct ($152.50) |
| 6 | Over the network | 1 | binds 0.0.0.0, README explains the 192.168.x.x address |
| 7 | Terminal client | 1 | add/list/totals subcommands |
| 8 | Persistence | 1 | identical totals after a server restart |
| 9 | Bad input | 0.5 | bad date: clear message; bad amount rejected but shown as a raw "400 Client Error: BAD REQUEST" |
| 10 | Own tests | 1 | 47 passed |
| 11 | README | 1 | setup, server, client, every command, local mode |

## Re-run after the SWE tooling fixes (finding 63: unbuffered output, listening ports, wait_port, http_request, stuck detector)

## Haiku 4.5 + harness (v2) — 10.5 / 11 · 227 s · 40 tool calls · ~$0.93  (v1: 10 / 11 · 524 s · ~$1.72)
Flask server (0.0.0.0, `--db-path`), CLI `add/list/report` with `--server`, PRD + DESIGN first; needs flask + requests.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | as written |
| 2 | Separate users | 0.5 | `--person NAME`, no accounts ("trust-based for roommates", stated in README) |
| 3 | Log an expense | 1 | |
| 4 | Attributed to the logger | 1 | list --person filter |
| 5 | Monthly totals by category | 1 | $152.50 correct |
| 6 | Over the network | 1 | 0.0.0.0 by default; README shows how to find the IP |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | identical report after restart |
| 9 | Bad input | 1 | "amount must be a positive number", "date must be in YYYY-MM-DD format" |
| 10 | Own tests | 1 | 23 passed |
| 11 | README | 1 | |

## Sonnet 4.6 + harness (v2) — 10.5 / 11 · 498 s · 40 tool calls · ~$3.51  (v1: 9.5 / 11 · 336 s · ~$1.82)
Flask server (0.0.0.0, LAN IP printed), single-file client `ledger.py` (`--server` or LEDGER_SERVER env, defaults to
localhost), add/list/summary with --user/--month filters, PRD first; needs flask + requests.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | as written |
| 2 | Separate users | 0.5 | `--user NAME`, no accounts |
| 3 | Log an expense | 1 | |
| 4 | Attributed to the logger | 1 | list/summary --user filters |
| 5 | Monthly totals by category | 1 | $152.50 correct (+ per-person summary) |
| 6 | Over the network | 1 | 0.0.0.0, prints LAN IP, env var for the server address |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | identical summary after restart |
| 9 | Bad input | 1 | "amount must be > 0", "date must be YYYY-MM-DD" (shown as the JSON error) |
| 10 | Own tests | 1 | 16 passed (v1's Windows tearDown bug gone) |
| 11 | README | 1 | client setup options, example session |

## Ornith 9B + harness (ornith5, in app) — ~1.5 / 11 · ~55 min · 177 tool calls · $0
Project mode: docs/research, PRD, DESIGN, TASKS before code; stdlib http.server + SQLite with password accounts.
Ended honestly: "checklist says 13/13 done, but that's not true" — correct-password login returns 401, /report and
/stats 404, so nothing past registration works; no README. Unit tests (6) pass but miss the live bugs (0.5); server
runs (0.5 for item 6/1 combined). Root cause of much of the time: Windows double-bind (finding 66), now guarded.

## gpt-5-nano BARE (no harness) — ~7 / 11 · 29 s · one reply · <$0.01
No server: a local argparse CLI on one SQLite file; README says to share that file over a network share / OneDrive
(fragile: SQLite over shares/sync folders risks corruption with several writers).
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | `python ledger.py add/report/list` work |
| 2 | Separate users | 0.5 | `--payer NAME` |
| 3 | Log an expense | 1 | |
| 4 | Attributed to the logger | 1 | payer column |
| 5 | Monthly totals by category | 1 | $152.50 correct |
| 6 | Over the network | 0 | no server — a shared database file |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | SQLite file |
| 9 | Bad input | 0.5 | rejected, but as raw Python tracebacks |
| 10 | Own tests | 0 | none |
| 11 | README | 0 | accurate for local use, but recommends the risky shared-file setup as the way to share |

## Haiku 4.5 + harness (v3, with today's changes) — ~7 / 11 · 235 s · 47 tool calls · ~$0.96  (v2: 10.5)
Flask on 0.0.0.0:5000, `client.py add/list/summary --server`. **The plan step broke:** Haiku wrote its plan as fake
`<function_calls>` XML in plain text and started writing the code inside it, hitting the 3072-token plan cap
("Continuing from where I was cut off"); its first write_file came with empty args. The checklist it made after
that had no PRD milestone, so it built straight from a guess and never planned users.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | `pip install flask requests`, `python server.py`, client works |
| 2 | Separate users | 0 | client takes `--user`, the server drops it (no user column at all) |
| 3 | Log an expense | 1 | |
| 4 | Own data | 0 | nothing attributed to anyone |
| 5 | Monthly totals by category | 1 | 2026-10: Food 30.50, Transport 40.00, matches what was stored |
| 6 | Over the network | 1 | 0.0.0.0, `--server IP` |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | same summary after restart |
| 9 | Bad input | 0.5 | `abc` rejected (argparse); `-5` and date `2026-13-45` accepted |
| 10 | Own tests | 0 | 12 errors: they need a server already running (not self-contained, not documented) |
| 11 | README | 0.5 | accurate, but no `--user`, no tests |

## Haiku 4.5 BARE (no harness) — ~5.5 / 11 · 39 s · one reply · ~$0.02  (graded from the code, not run)
A browser web UI server (0.0.0.0:8888, JSON file) + an interactive `cli.py` that edits the JSON file directly
(only works on the server machine), `run.py` menu. No README, no tests.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 0 | no README (run.py menu works) |
| 2 | Separate users | 0.5 | a name field |
| 3 | Log an expense | 1 | auto date |
| 4 | Own data | 1 | per-person in the summaries (same leniency as v2) |
| 5 | Monthly totals | 0.5 | current month only |
| 6 | Over the network | 0.5 | browser UI over the network; the terminal client is local-only |
| 7 | Terminal client | 0.5 | interactive, server machine only |
| 8 | Persistence | 1 | JSON file |
| 9 | Bad input | 0 | `float(input())` traceback in the CLI; raw exception text from the server |
| 10 | Own tests | 0 | none |
| 11 | README | 0 | none |

## Ornith 9B + harness (ornith7, in app) — ~8.5 / 11 (provisional, graded 22:35 while it ran its final checks) · ~2.5 h · $0
Stdlib http.server on 0.0.0.0:8899 + sqlite, salted-hash accounts, bearer tokens, `client.py register/login/logout/
add/list/month`. Found and fixed the deadlock itself at 22:27 (Lock -> RLock) after ~90 min, plus two month crashes.
Its FINAL_REPORT says every check passed, but its own tests.py still fails (expects key "name", the server now
returns "category"; the "no login" test still reads session.txt) — it verified with _e2e.ps1 instead.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | `python server.py`, client commands as written |
| 2 | Separate users | 1 | real accounts, salted hashes |
| 3 | Log an expense | 1 | |
| 4 | Own data | 1 | bob's list shows only his; no token -> "login first" |
| 5 | Monthly totals by category | 1 | per user, correct; same after restart |
| 6 | Over the network | 0.5 | 0.0.0.0, but how a client on another laptop sets the server address (ORNITH_BASE) is undocumented |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | |
| 9 | Bad input | 0.5 | `abc` rejected (raw message); `-5` and `2026-13-45` accepted |
| 10 | Own tests | 0 | tests.py: 2 FAIL then KeyError crash |
| 11 | README | 0.5 | accurate commands; missing the server-address setup |

## Haiku 4.5 + harness (v4, after the plan-step fix) — ~10 / 11 · 239 s · 40 tool calls · ~$0.83  (v3: ~7, v2: 10.5)
PRD.md + DESIGN.md first (docs/), Flask on 0.0.0.0:5000, JSON file, `client.py --server IP:5000 add-expense/list/totals`.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | |
| 2 | Separate users | 0.5 | `--person NAME`, no accounts (same as v2) |
| 3 | Log an expense | 1 | |
| 4 | Attributed / own data | 1 | `list --person Bob` shows only Bob's |
| 5 | Monthly totals by category | 1 | 2026-10: food 35.50, transport 40.00 — correct |
| 6 | Over the network | 1 | 0.0.0.0, `--server IP:5000` documented |
| 7 | Terminal client | 1 | (prints a ✓ that crashes only when output is piped to a cp1252 console; the data is saved) |
| 8 | Persistence | 1 | |
| 9 | Bad input | 1 | "Amount must be positive", "Invalid date format. Use YYYY-MM-DD", `abc` rejected |
| 10 | Own tests | 0.5 | 10 passed, but they need a running server and write into the live data file |
| 11 | README | 1 | |

## Qwen3.6-35B-A3B REAP-48-v2 + harness (reap1, bf16 KV, 64K) — ~6.5 / 11 · ~15 min of work (10 min, PC crash, 5 min resumed) · $0
One stdlib `server.py` (0.0.0.0:8080, SQLite, sha256 password hashes) with built-in browser pages — no terminal client,
no README, no tests; the turn ended telling the user to "try registering again". Fast compared with the 9B (~2.5 h).
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 0 | no README (`python server.py` prints the URLs) |
| 2 | Separate users | 1 | register/login accounts |
| 3 | Log an expense | 1 | month + category + amount |
| 4 | Own data | 0.5 | writes need the password, but `GET /api/expenses?user_id=1` returns anyone's expenses with no login |
| 5 | Monthly totals by category | 1 | household totals per category, survive restart |
| 6 | Over the network | 1 | 0.0.0.0, prints the network URL |
| 7 | Terminal client | 0 | browser only |
| 8 | Persistence | 1 | |
| 9 | Bad input | 0 | amount "abc", -5 and month 2026-13 all stored; a duplicate username leaves an open transaction -> every later write fails "database is locked" |
| 10 | Own tests | 0 | none |
| 11 | README | 0 | none |

## Ternary Bonsai 2 27B + harness (bonsai1, in app, q8/q4 KV, 60K) — ~10 / 11 provisional · ~1 h 53 min · 71 tool calls · $0
Graded from the transcript, README and its tests (10/10 re-run by hand), not yet hands-on as two users.
Stdlib only: `http.server` on 0.0.0.0:8799 + a JSON store (threading.Lock, atomic replace), `client.py --host/--port
add/list/totals [--user] [--month]`, PRD/DESIGN/TASKS first. **Not a clean run:** one process hint mid-run (04:47:
"store.py was overwritten by your own write of the history placeholder; read before editing") and chat recall
showed it an earlier attempt's request. Fixed bugs itself: `w+b` truncation, msvcrt locking, missing `global _cache`.
| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Starts from README | 1 | (provisional) |
| 2 | Separate users | 0.5 | `--user NAME`, no accounts |
| 3 | Log an expense | 1 | amount, category, automatic date |
| 4 | Attributed / own data | 1 | `totals --user` |
| 5 | Monthly totals by category | 1 | verified live by the agent with two client processes |
| 6 | Over the network | 1 | 0.0.0.0, `--host <IP>` documented with ipconfig |
| 7 | Terminal client | 1 | |
| 8 | Persistence | 1 | expenses.json (provisional) |
| 9 | Bad input | 1 | negative / missing category -> 400 with message; non-numeric -> usage error (no date input) |
| 10 | Own tests | 1 | 10 passed; they start their own server |
| 11 | README | 1 | 133 lines, host + roommate commands |
