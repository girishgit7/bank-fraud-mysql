# Ledgerwatch: Bank Transaction & Fraud Detection System

A DBMS Level 3 project. Customers sign up, add funds and send money; MySQL enforces the banking rules and a database trigger flags suspicious transactions the moment they happen; fraud analysts review the flags. MongoDB stores flexible alert context, analyst notes and the audit trail.

**The system starts completely empty**: no preset users, accounts or transactions. Customers create their own accounts on the sign-up page, and the analyst account is created once with a script.

| Layer | Technology |
|---|---|
| Backend and API | Python 3.10+, Flask |
| Relational database | MySQL 8.0.16+ (accounts, transactions, fraud rules, flags; trigger, stored procedures, views) |
| NoSQL database | MongoDB 6+ (alert details, analyst notes, audit log, aggregation) |
| Frontend | Server-rendered pages with vanilla JavaScript (no build step, no external libraries or fonts) |
| Auth | Sign-up and sign-in with salted PBKDF2-SHA256 password hashes, signed session cookie, CSRF tokens, throttling |

## Prerequisites

- **Python 3.10 or newer** (`python --version`)
- **MySQL Server 8.0.16 or newer** with the `mysql` command-line client on your PATH (CHECK constraints are only enforced from 8.0.16; MariaDB and MySQL 5.7 will not work)
- **MongoDB Community Server 6 or newer** running as a service

## Setup

Run everything from inside this project folder.

**1. Create a virtual environment and install the Python packages**

```
python -m venv .venv
.venv\Scripts\activate          (Windows)       source .venv/bin/activate   (macOS / Linux)
pip install -r requirements.txt
```

**2. Create the MySQL database**

```
mysql -u root -p -e "source db/schema.sql"
```

This creates the tables, trigger, procedures and views, plus reference data only: 4 branches and the 3 fraud rules. **There are no users.** (Add `-P 3307` if your MySQL uses another port. **Re-running this file erases all data.**)

**3. Prepare MongoDB**

```
python scripts/setup_mongo.py
```

Creates the two collections with validation and indexes. It adds no data. (Use `--reset` to wipe existing alerts and audit events.)

**4. Configure**

```
copy .env.example .env          (Windows)       cp .env.example .env        (macOS / Linux)
```

Open `.env` and set `MYSQL_PASSWORD`, and a long random `SECRET_KEY` (generate one with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Change `MYSQL_PORT` or `MONGODB_URI` if yours differ.

**5. Create the analyst (admin) account**

```
python scripts/create_admin.py
```

It asks for an email and a password (typing is hidden). Analysts cannot sign up on the website; this is deliberate, so nobody can make themselves an administrator.

**6. Check that everything is wired up**

```
python scripts/check_setup.py --smoke
```

Every line should say `[ok]`. If one says `[FAIL]` the message tells you what to fix. `--smoke` also fires the fraud trigger on temporary data and exercises the stored procedures' refusals; everything is rolled back, so nothing is saved.

**7. Run**

```
python run.py
```

Open http://localhost:5001. (Change the port with `PORT=5002` in `.env`.)

## Using the app

- **New customers:** click *Create an account* on the sign-in page. You enter your name, email, phone, date of birth (you must be 18+), home city, a branch and a password (8+ characters with a letter and a number). You are signed in straight away with a savings account at zero balance.
- **Returning users:** sign in with email and password.
- **Add funds** (on the dashboard) puts simulated money into an account, so you can try transfers. Deposits are not scored as fraud.
- **Open account** adds up to 5 accounts per customer; you can send money between your own accounts.
- **Analysts** sign in with the account made in step 5 and land on the review queue.

## Demo script (two browsers, or one normal and one private window)

1. **Browser A:** create an account as Alice (home city Mumbai). Click **Add funds** and add 100000.
2. **Browser B:** create an account as Bob. Copy his account number with the **Copy** link.
3. **Browser A:** send Bob 2,000 with *Your city* = Mumbai. It completes with no flag.
4. Send Bob 60,000 with *Your city* = Dubai. The trigger raises two flags (large amount, unusual location) and the toast says it was flagged.
5. Try sending more than Alice's balance. The stored procedure refuses and the balance does not change.
6. **Browser C (or sign out):** sign in as the analyst. The queue shows Alice's two flags ranked by risk. Click one to open the detail drawer: the transaction (MySQL), the alert context (MongoDB) and the notes.
7. Add a note, tick *Freeze the sender's account* and press **Confirm fraud**. Alice's account is frozen, the Recent activity feed records it (MongoDB audit log), and Alice can no longer send from it.
8. Use **Unfreeze account** in the drawer to restore it.

To trigger the *rapid transactions* rule, send 5 transfers within 10 minutes.

## Testing

```
python -m unittest discover -s tests -v        40 backend tests: sign-up, sign-in, CSRF, roles, validation, deposits,
                                               transfers, fraud review, and an empty-system end-to-end scenario
python -m tests.serve_fake                     run the whole UI on in-memory demo data, no databases needed (port 5057)
FRESH=1 python -m tests.serve_fake             the same, but starting empty with one analyst: admin@bank.com / Password@123
python scripts/check_setup.py --smoke          verify your real MySQL and MongoDB, trigger and procedures
```

`serve_fake` and the unit tests use in-memory stand-ins for the two databases (`tests/fakes.py`). They check the application logic, not the SQL, and are for development only: the real app never contains preset users. The SQL is checked by `check_setup.py --smoke` and by the test cases in `db/queries.sql` (section D).

## Project structure

```
app/
  __init__.py      app factory (config, security headers, error handlers)
  api.py           JSON endpoints: sign-up, sign-in, accounts, deposit, transfer, admin flags/review/stats
  pages.py         page routes (login, signup, dashboard, admin)
  validation.py    server-side sign-up rules (name, email, phone, age 18+, password policy)
  repo.py          ALL MySQL access (queries, stored procedure calls, sign-up and review transactions)
  store.py         ALL MongoDB access (alerts, notes, audit log, aggregation)
  security.py      CSRF, role checks, login/sign-up throttling, security headers
  templates/       login, signup, customer dashboard, admin review
  static/          app.css, common.js, login.js, signup.js, customer.js, admin.js
db/                schema.sql, queries.sql, database_design_notes.md
scripts/           setup_mongo.py, create_admin.py, check_setup.py
tests/             unit tests, in-memory fakes, fake-backed demo server
run.py             start the app
```

## How the two databases work together

MySQL is the source of truth. A transfer calls the `transfer_funds` procedure, which debits, credits and logs atomically; the `trg_check_fraud` trigger then flags the transaction inside the same transaction. The app copies each new flag into MongoDB with context (device, IP, place) and keeps analyst notes and the audit trail there. If MongoDB is down, transfers and reviews still work and the admin page shows flags without the extra context.

## Where each requirement is met

- **SQL + NoSQL:** MySQL for core data, MongoDB for alert context, notes and audit log (justification in `db/database_design_notes.md`).
- **Advanced database features:** transactions, two stored procedures, a trigger, views, indexes, window function, MongoDB aggregation pipeline, geospatial and text indexes.
- **Not just CRUD:** sign-up with validation, automatic fraud detection, atomic transfers, an analyst review workflow with account freezing.
- **Roles:** `customer` (self sign-up) and `admin` (created by script), enforced in every API route and page.
- **Security:** hashed passwords, password policy, CSRF tokens on every write, login and sign-up throttling, secrets in `.env`, parameterised SQL, HTML escaping of all user text, Content-Security-Policy and other headers, ownership checks on every account action.
- **Validation at the database level:** CHECK, UNIQUE and FOREIGN KEY constraints in `db/schema.sql`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `mysql` is not recognized | Add MySQL's `bin` folder to PATH, or run the SQL file from MySQL Workbench / MySQL Command Line Client with `source C:/path/to/db/schema.sql` |
| `Access denied for user` | `MYSQL_USER` / `MYSQL_PASSWORD` in `.env` do not match your MySQL login |
| `Can't connect to MySQL server` | MySQL is not running, or `MYSQL_PORT` is wrong |
| `RuntimeError: SECRET_KEY is missing` | Create `.env` from `.env.example` and set `SECRET_KEY` |
| `No module named flask` (or pymysql, pymongo) | Activate the virtual environment (`.venv\Scripts\activate`) and run `pip install -r requirements.txt` |
| `cryptography` error when connecting to MySQL 8 | `pip install -r requirements.txt` installs it; re-run it inside the virtual environment |
| Sign-up page says branches could not be loaded | `schema.sql` has not been run, or the MySQL settings in `.env` are wrong |
| Sign-up says "Too many sign-up attempts" | Wait an hour or restart the app (the throttle is in memory) |
| Admin page loads but alert details are empty | MongoDB is down or not prepared: run `python scripts/setup_mongo.py` |
| `CHECK` constraints seem ignored | You are on MySQL older than 8.0.16 (or MariaDB): upgrade |

**To start over completely:** re-run `mysql -u root -p -e "source db/schema.sql"` (erases everything), then `python scripts/setup_mongo.py --reset`, then `python scripts/create_admin.py`.

## Contributions

Girish Jayan S
