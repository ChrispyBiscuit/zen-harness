Build "Ledger", a small multi-user expense-tracking service, from scratch in {ws}. Python standard library only (no pip installs).

Layers:
1. Storage — `storage.py`: SQLite. The database file path comes from the environment variable LEDGER_DB (default `ledger.db` next to the code). Data must survive a server restart.
2. HTTP API — `server.py`: a JSON API using http.server, listening on 127.0.0.1 at the port in LEDGER_PORT (default 8750). Start it with `python server.py`.
   - `POST /users` with {{"name", "password"}} → 201 {{"id"}}; a name that already exists → 409.
   - `POST /login` with {{"name", "password"}} → 200 {{"token"}}; wrong name or password → 401.
   - Every `/expenses` and `/summary` request needs the header `Authorization: Bearer <token>`; missing or invalid → 401.
   - `POST /expenses` with {{"amount" (number > 0), "category" (non-empty text), "date" ("YYYY-MM-DD"), "note" (optional)}} → 201 with the stored expense including its "id". Invalid input → 400 with {{"error": "..."}}.
   - `GET /expenses` → 200, a JSON list of the user's own expenses sorted by date (oldest first); optional filters `?category=`, `?from=YYYY-MM-DD`, `?to=YYYY-MM-DD` (inclusive).
   - `GET /expenses/<id>` → 200 the expense; unknown id or another user's expense → 404.
   - `DELETE /expenses/<id>` → 204; unknown or another user's → 404.
   - `GET /summary?month=YYYY-MM` → 200 {{"total": number, "by_category": {{"<category>": number}}}}, amounts rounded to 2 decimals, the user's own expenses in that month only.
3. Command-line client — `cli.py`, which talks to the API over HTTP (not to the database):
   - `python cli.py --url URL register NAME PASSWORD` → prints the new user id
   - `python cli.py --url URL login NAME PASSWORD` → prints the token
   - `python cli.py --url URL --token TOKEN add AMOUNT CATEGORY DATE [NOTE]` → prints the new expense id
   - `python cli.py --url URL --token TOKEN list [--category C]` → one line per expense: `ID DATE CATEGORY AMOUNT` (amount with 2 decimals)
   - `python cli.py --url URL --token TOKEN summary YYYY-MM` → first line `total: 123.45`, then one line per category `CATEGORY: 12.34`
4. Tests — a `tests` folder with unittest tests that pass with `python -m unittest discover -s tests`.
5. A README.md saying how to run the server, the CLI and the tests.

Build it, run it, and make sure it all works before you say it is done.
