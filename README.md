# SQL LLM

Ask questions about a database in plain English. The app turns them into safe, read-only SQL, runs it and shows the
result as a table or chart. It includes accounts, chat history, saved queries, daily usage limits, plans and payments,
an admin dashboard and an evaluation page that compares zero-shot and few-shot prompting.

**Stack:** FastAPI · mysql-connector-python · MySQL · plain HTML, CSS and JavaScript (no build step).

## Quick start

You need Python 3.10+ and a running MySQL 8 (or MariaDB 10.6+) server.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m backend.setup_db           # asks for your MySQL admin password and creates an app administrator
python run.py                        # open http://127.0.0.1:8000
```

`setup_db` creates `app_db` (the app's tables), `shop_db` (sample data the AI answers questions about), two MySQL
accounts, a `.env` file with generated passwords, and the starting data (plans, prompt template, examples). It is safe to
run again. Options: `--help`, `--reset` (wipes `app_db`), `--test-db` (also creates `app_db_test` for the tests).

**Without the script:** run `sql/001` to `sql/004` in order with the `mysql` client, change the two placeholder
passwords in them, copy `.env.example` to `.env` with the same passwords, then run
`python -c "from backend import seed; seed.seed_reference_data(); seed.create_admin('admin','you@example.com','a-strong-password')"`.

## Demo mode vs a real AI

With no `GEMINI_API_KEY` the app runs in **demo mode**: a small built-in rule set answers questions about the sample shop
(customers, products, orders). Good for trying the whole app. To use a real model, put your key in `.env`:

```
GEMINI_API_KEY=your-key
```

In demo mode zero-shot and few-shot score the same on the Evaluation page (it shows a banner saying so).

## Pages

| Page | Who | What |
|---|---|---|
| `/login`, `/signup`, `/forgot`, `/reset`, `/about` | everyone | accounts and password reset |
| `/chat` | users | ask questions, see SQL, table/chart, 👍/👎 feedback, save, CSV export (Pro) |
| `/history`, `/saved` | users | past questions (re-run is free) and saved queries |
| `/usage` | users | today's queries and tokens against the plan |
| `/billing` | users | plans, token packs, payments and invoices |
| `/settings` | users | preferences, password, delete account |
| `/admin` | admins | stats, users, plans, payments, errors, blocked queries, prompt templates, examples, audit log |
| `/evaluation` | admins | run test questions with zero-shot vs few-shot and compare accuracy |

The JSON API lives under `/api/` (see `app/routers/`). There is no `/docs` page on purpose.

## How it keeps generated SQL safe

1. Only a single `SELECT` / `WITH` statement passes `sql_guard` (no writes, no comments tricks, no system schemas, no app database).
2. The query runs as `llm_reader`, a MySQL account with `SELECT` on `shop_db` only.
3. The session is read-only, with a time limit and a row cap (`MAX_ROWS`).

Web safety: passwords are bcrypt-hashed; sessions are HttpOnly cookies (only a hash is stored); state-changing requests
need an `X-Requested-With` header; a strict Content-Security-Policy forbids inline scripts; login lockout after 5 failures.

## Configuration

All settings are environment variables or lines in `.env` (real environment variables win). See `.env.example`.
Set `COOKIE_SECURE=1` when serving over HTTPS. Run behind a reverse proxy (nginx, Caddy) with HTTPS in production.

- **Password reset emails:** there is no mail server. With `RESET_LINK_MODE=console` (default) the link is printed in the
  server log. `response` returns it in the API reply and is for local development only.
- **Payments:** without `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` a demo gateway upgrades the plan with no real payment.
  Set both to use Razorpay (signature verification is implemented; see Known limits).
- **Your own data:** point `TARGET_DB_*` at another database and give that account `SELECT` only.

## Tests

```bash
python -m backend.setup_db --test-db          # once: creates app_db_test
DB_NAME=app_db_test ALLOW_TEST_DB_WIPE=1 python -m pytest      # 116 tests
```

The tests **erase every table** in the database they use, so they refuse to run unless the name contains "test" and
`ALLOW_TEST_DB_WIPE=1` is set. A browser test is in `scripts/e2e_browser.py` (see the top of that file).

## Project layout

```
app/        FastAPI app, routes, security middleware
backend/    business logic: auth, SQL guard, LLM, usage, billing, admin, evaluation, setup_db
static/     HTML pages, css/style.css, js/ (one script per page + shared app.js)
sql/        schema and setup scripts
tests/      pytest suite
scripts/    browser end-to-end test
run.py      starts the server (HOST, PORT, RELOAD env vars)
```

## Known limits

- Tested on MariaDB 10.11; MySQL 8 should work but was not run here.
- The Gemini and Razorpay integrations have not been exercised against the live services.
- Plans have no expiry date: an upgraded user stays upgraded until an admin changes the plan.
- Refunds only update records; return the money in the gateway dashboard.
- No email sending (see above).
