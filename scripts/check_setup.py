"""Checks that MySQL and MongoDB are set up correctly for this project.

    python scripts/check_setup.py            read-only checks
    python scripts/check_setup.py --smoke    also fires the fraud trigger on temporary data that is rolled
                                             back, and exercises the stored procedures' refusals
Nothing is saved by either mode.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config, load_env  # noqa: E402

load_env()
cfg = get_config()
SMOKE = "--smoke" in sys.argv
failures = 0
warnings = 0


def report(ok, label, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def note(label, detail=""):
    global warnings
    warnings += 1
    print(f"  [note] {label}" + (f"  ({detail})" if detail else ""))


def section(title):
    print(f"\n{title}")


# ------------------------------------------------------------------ environment
section("Environment")
report(len(cfg["SECRET_KEY"]) >= 16, "SECRET_KEY is set (16+ characters)",
       "set it in .env" if len(cfg["SECRET_KEY"]) < 16 else "")

# ------------------------------------------------------------------ MySQL
section(f"MySQL ({cfg['MYSQL_HOST']}:{cfg['MYSQL_PORT']}, database {cfg['MYSQL_DATABASE']})")
try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    report(False, "PyMySQL is installed", "run: pip install -r requirements.txt")

if pymysql:
    conn = None
    try:
        conn = pymysql.connect(host=cfg["MYSQL_HOST"], port=cfg["MYSQL_PORT"], user=cfg["MYSQL_USER"],
                               password=cfg["MYSQL_PASSWORD"], database=cfg["MYSQL_DATABASE"],
                               cursorclass=DictCursor, autocommit=True, connect_timeout=5,
                               init_command="SET time_zone = '+00:00'")
        report(True, "connected")
    except Exception as exc:  # noqa: BLE001
        report(False, "connected", str(exc))

    if conn:
        cur = conn.cursor()
        cur.execute("SELECT VERSION() AS v")
        version = cur.fetchone()["v"]
        major_minor = tuple(int(x) for x in version.split("-")[0].split(".")[:3])
        report("mariadb" not in version.lower() and major_minor >= (8, 0, 16),
               f"server version {version}", "this project needs MySQL 8.0.16 or newer")

        db = cfg["MYSQL_DATABASE"]
        cur.execute("SELECT table_name AS n FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (db,))
        have = {r["n"].lower() for r in cur.fetchall()}
        need = {"users", "customers", "branches", "accounts", "transactions", "fraud_rules", "fraud_flags"}
        report(need <= have, "7 tables exist", f"missing: {sorted(need - have)}" if need - have else "")

        cur.execute("SELECT trigger_name AS n FROM information_schema.triggers WHERE trigger_schema=%s", (db,))
        report("trg_check_fraud" in {r["n"] for r in cur.fetchall()}, "trigger trg_check_fraud exists")

        cur.execute("SELECT routine_name AS n FROM information_schema.routines WHERE routine_schema=%s", (db,))
        routines = {r["n"] for r in cur.fetchall()}
        report({"transfer_funds", "deposit_funds"} <= routines, "procedures transfer_funds and deposit_funds exist")

        cur.execute("SELECT table_name AS n FROM information_schema.views WHERE table_schema=%s", (db,))
        report({"v_suspicious_transactions", "v_account_summary"} <= {r["n"] for r in cur.fetchall()}, "2 views exist")

        if need <= have:
            cur.execute("SELECT COUNT(*) AS c FROM fraud_rules WHERE is_active = 1")
            report(cur.fetchone()["c"] == 3, "3 active fraud rules")
            cur.execute("SELECT COUNT(*) AS c FROM branches")
            branches = cur.fetchone()["c"]
            report(branches >= 1, "branches exist (needed for sign-up)", f"{branches} branches")
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'")
            if cur.fetchone()["c"] == 0:
                note("no analyst account yet", "create one with: python scripts/create_admin.py")
            else:
                report(True, "an analyst (admin) account exists")
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'customer'")
            print(f"  [info] {cur.fetchone()['c']} customer(s) signed up so far")

        if SMOKE and need <= have and {"transfer_funds", "deposit_funds"} <= routines:
            section("Smoke tests (nothing is saved)")

            # 1. procedures refuse bad requests with our custom error and change nothing
            for label, sql, args, expected in [
                ("transfer_funds refuses a transfer to the same account", "CALL transfer_funds(%s, %s, %s, 'online', NULL, NULL)", (0, 0, 100), "same account"),
                ("transfer_funds refuses accounts that do not exist", "CALL transfer_funds(%s, %s, %s, 'online', NULL, NULL)", (0, -1, 100), "not found"),
                ("deposit_funds refuses an account that does not exist", "CALL deposit_funds(%s, %s, 'online', NULL, NULL)", (0, 100), "not found"),
                ("deposit_funds refuses a zero amount", "CALL deposit_funds(%s, %s, 'online', NULL, NULL)", (0, 0), "greater than zero"),
            ]:
                try:
                    cur.execute(sql, args)
                    report(False, label, "no error was raised")
                except pymysql.MySQLError as exc:
                    msg = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
                    report(exc.args[0] == 1644 and expected in msg.lower(), label, msg)

            # 2. trigger: temporary customer, account and a big withdrawal in a far-away city
            try:
                conn.begin()
                tag = str(random.randrange(10**9, 10**10))
                cur.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, 'x', 'customer')",
                            (f"smoke{tag}@example.invalid",))
                uid = cur.lastrowid
                cur.execute("""INSERT INTO customers (user_id, full_name, phone, date_of_birth, home_city)
                               VALUES (%s, 'Smoke Test', %s, '1990-01-01', 'Mumbai')""", (uid, tag))
                cid = cur.lastrowid
                cur.execute("""INSERT INTO accounts (customer_id, branch_id, account_number, balance)
                               VALUES (%s, (SELECT MIN(branch_id) FROM branches), %s, 500000)""", (cid, "9" + tag + "00000"))
                aid = cur.lastrowid
                cur.execute("""INSERT INTO transactions (from_account_id, to_account_id, txn_type, amount, channel, location_city)
                               VALUES (%s, NULL, 'withdrawal', 60000, 'atm', 'Paris')""", (aid,))
                cur.execute("SELECT COUNT(*) AS c FROM fraud_flags WHERE txn_id = %s", (cur.lastrowid,))
                flagged = cur.fetchone()["c"]
                report(flagged == 2, "trigger flags a large withdrawal in a new city", f"{flagged} flags, expected 2")
                cur.execute("""INSERT INTO transactions (from_account_id, to_account_id, txn_type, amount, channel, location_city)
                               VALUES (NULL, %s, 'deposit', 90000, 'online', NULL)""", (aid,))
                cur.execute("SELECT COUNT(*) AS c FROM fraud_flags WHERE txn_id = %s", (cur.lastrowid,))
                report(cur.fetchone()["c"] == 0, "deposits are not scored as fraud")
            except Exception as exc:  # noqa: BLE001
                report(False, "trigger test", str(exc))
            finally:
                conn.rollback()
        conn.close()

# ------------------------------------------------------------------ MongoDB
section(f"MongoDB ({cfg['MONGODB_URI']}, database {cfg['MONGODB_DB']})")
try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None
    report(False, "pymongo is installed", "run: pip install -r requirements.txt")

if MongoClient:
    try:
        client = MongoClient(cfg["MONGODB_URI"], serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        report(True, "connected")
        mdb = client[cfg["MONGODB_DB"]]
        names = set(mdb.list_collection_names())
        have_both = {"fraud_alerts", "audit_logs"} <= names
        report(have_both, "collections fraud_alerts and audit_logs exist",
               "" if have_both else "run: python scripts/setup_mongo.py")
        if "fraud_alerts" in names:
            report(len(mdb.fraud_alerts.index_information()) >= 4, "indexes created on fraud_alerts")
            info = mdb.command("listCollections", filter={"name": "fraud_alerts"})["cursor"]["firstBatch"]
            report(bool(info and info[0].get("options", {}).get("validator")), "schema validation is on")
    except Exception as exc:  # noqa: BLE001
        report(False, "connected", f"{exc} (is the MongoDB service running?)")

print()
if failures:
    print(f"{failures} check(s) failed. Fix them in order from the top, then run this script again.")
    sys.exit(1)
print("Everything looks good. Start the app with:  python run.py")
if warnings:
    print("(See the [note] lines above for next steps.)")
