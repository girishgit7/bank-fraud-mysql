"""All MySQL access lives here. Everything else in the app talks to this small interface,
so the SQL is easy to find, review and (in tests) replace with an in-memory fake."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import secrets

from .errors import RegistrationError, TransferError

CUSTOM_SQLSTATE_ERRNO = 1644  # MySQL error number raised by SIGNAL SQLSTATE '45000'
DUPLICATE_KEY_ERRNO = 1062
MAX_ACCOUNTS_PER_CUSTOMER = 5


def new_account_number():
    """16 digits, starting with 10 (the bank prefix)."""
    return "10" + "".join(str(secrets.randbelow(10)) for _ in range(14))


class MySQLRepo:
    def __init__(self, cfg):
        self.cfg = cfg

    @contextmanager
    def _conn(self):
        import pymysql  # imported lazily so tests can run without the driver
        from pymysql.cursors import DictCursor

        conn = pymysql.connect(
            host=self.cfg["MYSQL_HOST"],
            port=self.cfg["MYSQL_PORT"],
            user=self.cfg["MYSQL_USER"],
            password=self.cfg["MYSQL_PASSWORD"],
            database=self.cfg["MYSQL_DATABASE"],
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
            init_command="SET time_zone = '+00:00'",
        )
        try:
            yield conn
        finally:
            conn.close()

    def _all(self, sql, params=()):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _one(self, sql, params=()):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    # ------------------------------------------------------------ auth
    def get_user_for_login(self, email):
        return self._one(
            """SELECT u.user_id, u.email, u.password_hash, u.role, c.customer_id, c.full_name
                 FROM users u
                 LEFT JOIN customers c ON c.user_id = u.user_id
                WHERE u.email = %s""",
            (email,),
        )

    # ------------------------------------------------------------ customer
    def list_accounts(self, customer_id):
        return self._all(
            """SELECT a.account_id, a.account_number, a.account_type, a.balance, a.status,
                      b.branch_name
                 FROM accounts a
                 JOIN branches b ON b.branch_id = a.branch_id
                WHERE a.customer_id = %s
                ORDER BY a.account_id""",
            (customer_id,),
        )

    def list_transactions(self, customer_id, limit=60):
        rows = self._all(
            """SELECT t.txn_id, t.txn_type, t.amount, t.channel, t.location_city, t.description,
                      t.status, t.created_at,
                      fa.account_number AS from_account, ta.account_number AS to_account,
                      (fa.customer_id = %s) AS outgoing
                 FROM transactions t
                 LEFT JOIN accounts fa ON fa.account_id = t.from_account_id
                 LEFT JOIN accounts ta ON ta.account_id = t.to_account_id
                WHERE fa.customer_id = %s OR ta.customer_id = %s
                ORDER BY t.created_at DESC, t.txn_id DESC
                LIMIT %s""",
            (customer_id, customer_id, customer_id, limit),
        )
        for r in rows:
            r["outgoing"] = bool(r["outgoing"])
        return rows

    def account_owned_by(self, account_id, customer_id):
        return (
            self._one(
                "SELECT account_id FROM accounts WHERE account_id = %s AND customer_id = %s",
                (account_id, customer_id),
            )
            is not None
        )

    def find_account_by_number(self, number):
        return self._one(
            """SELECT a.account_id, a.account_number, a.status, c.full_name AS holder,
                      b.branch_name
                 FROM accounts a
                 JOIN customers c ON c.customer_id = a.customer_id
                 JOIN branches b ON b.branch_id = a.branch_id
                WHERE a.account_number = %s""",
            (number,),
        )

    def _call_for_txn_id(self, sql, params):
        """Runs a stored procedure that ends with SELECT <txn_id>; turns its SIGNAL errors into TransferError."""
        import pymysql

        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(sql, params)
                row = cur.fetchone()
                while cur.nextset():
                    pass
            except pymysql.MySQLError as err:
                if err.args and err.args[0] == CUSTOM_SQLSTATE_ERRNO:
                    raise TransferError(str(err.args[1])) from err
                raise
        return int(row["txn_id"])

    def transfer(self, from_id, to_id, amount, channel, city, description):
        """Calls the transfer_funds stored procedure (atomic debit + credit + log)."""
        return self._call_for_txn_id(
            "CALL transfer_funds(%s, %s, %s, %s, %s, %s)",
            (from_id, to_id, amount, channel, city, description),
        )

    def deposit(self, account_id, amount, channel, city, description):
        """Calls the deposit_funds stored procedure (simulated deposit for the demo)."""
        return self._call_for_txn_id(
            "CALL deposit_funds(%s, %s, %s, %s, %s)",
            (account_id, amount, channel, city, description),
        )

    # ------------------------------------------------------------ sign-up and accounts
    def list_branches(self):
        return self._all("SELECT branch_id, branch_name, city FROM branches ORDER BY branch_id")

    def register_customer(self, email, password_hash, full_name, phone, dob, home_city, branch_id):
        """Creates the user, the customer profile and a first savings account in ONE transaction."""
        import pymysql

        with self._conn() as conn:
            conn.begin()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'customer')",
                        (email, password_hash),
                    )
                    user_id = cur.lastrowid
                    cur.execute(
                        """INSERT INTO customers (user_id, full_name, phone, date_of_birth, home_city, kyc_status)
                           VALUES (%s, %s, %s, %s, %s, 'pending')""",
                        (user_id, full_name, phone, dob, home_city),
                    )
                    customer_id = cur.lastrowid
                    account = self._insert_account(cur, customer_id, branch_id, "savings")
                conn.commit()
            except pymysql.err.IntegrityError as err:
                conn.rollback()
                if err.args[0] == DUPLICATE_KEY_ERRNO:
                    text = str(err.args[1])
                    if "uq_users_email" in text:
                        raise RegistrationError("An account with this email already exists. Sign in instead.", "email") from err
                    if "uq_customers_phone" in text:
                        raise RegistrationError("That phone number is already registered.", "phone") from err
                raise
            except Exception:
                conn.rollback()
                raise
        return {"user_id": user_id, "customer_id": customer_id, "full_name": full_name,
                "email": email, "account": account}

    def _insert_account(self, cur, customer_id, branch_id, account_type):
        import pymysql

        for _ in range(8):  # retry on the (very unlikely) clash of random account numbers
            number = new_account_number()
            try:
                cur.execute(
                    """INSERT INTO accounts (customer_id, branch_id, account_number, account_type, balance)
                       VALUES (%s, %s, %s, %s, 0.00)""",
                    (customer_id, branch_id, number, account_type),
                )
                return {"account_id": cur.lastrowid, "account_number": number, "account_type": account_type}
            except pymysql.err.IntegrityError as err:
                if err.args[0] == DUPLICATE_KEY_ERRNO and "uq_accounts_number" in str(err.args[1]):
                    continue
                raise
        raise RuntimeError("Could not generate a unique account number")

    def open_account(self, customer_id, branch_id, account_type):
        with self._conn() as conn:
            conn.begin()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s FOR UPDATE", (customer_id,))
                    cur.execute("SELECT COUNT(*) AS c FROM accounts WHERE customer_id = %s", (customer_id,))
                    if cur.fetchone()["c"] >= MAX_ACCOUNTS_PER_CUSTOMER:
                        conn.rollback()
                        raise RegistrationError(f"You can have at most {MAX_ACCOUNTS_PER_CUSTOMER} accounts.")
                    account = self._insert_account(cur, customer_id, branch_id, account_type)
                conn.commit()
            except RegistrationError:
                raise
            except Exception:
                conn.rollback()
                raise
        return account

    def create_admin(self, email, password_hash):
        """Used by scripts/create_admin.py: analysts cannot sign up through the website."""
        import pymysql

        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'admin')",
                    (email, password_hash),
                )
            except pymysql.err.IntegrityError as err:
                if err.args[0] == DUPLICATE_KEY_ERRNO:
                    raise RegistrationError("A user with this email already exists.", "email") from err
                raise
            return cur.lastrowid

    def flags_for_txn(self, txn_id):
        return self._all(
            """SELECT f.flag_id, f.risk_score, fr.rule_name
                 FROM fraud_flags f JOIN fraud_rules fr ON fr.rule_id = f.rule_id
                WHERE f.txn_id = %s
                ORDER BY f.flag_id""",
            (txn_id,),
        )

    # ------------------------------------------------------------ admin
    def list_flags(self, status=None, limit=200):
        return self._all(
            """SELECT f.flag_id, f.status, f.risk_score, f.flagged_at, f.reviewed_at,
                      fr.rule_name,
                      t.txn_id, t.txn_type, t.amount, t.channel, t.location_city,
                      t.description, t.created_at AS txn_at,
                      a.account_id, a.account_number, a.status AS account_status,
                      c.full_name AS customer_name, c.home_city
                 FROM fraud_flags f
                 JOIN fraud_rules fr ON fr.rule_id = f.rule_id
                 JOIN transactions t ON t.txn_id = f.txn_id
                 LEFT JOIN accounts a ON a.account_id = t.from_account_id
                 LEFT JOIN customers c ON c.customer_id = a.customer_id
                WHERE (%s IS NULL OR f.status = %s)
                ORDER BY (f.status = 'open') DESC, f.risk_score DESC, f.flagged_at DESC
                LIMIT %s""",
            (status, status, limit),
        )

    def review_flag(self, flag_id, status, reviewer_id, freeze):
        """Marks an OPEN flag as confirmed/dismissed (and optionally freezes the sender's account)
        in one transaction. Returns None if the flag is missing or already reviewed."""
        with self._conn() as conn:
            conn.begin()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT f.txn_id, t.from_account_id
                             FROM fraud_flags f JOIN transactions t ON t.txn_id = f.txn_id
                            WHERE f.flag_id = %s AND f.status = 'open'
                              FOR UPDATE""",
                        (flag_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        conn.rollback()
                        return None
                    cur.execute(
                        """UPDATE fraud_flags
                              SET status = %s, reviewed_by = %s, reviewed_at = NOW()
                            WHERE flag_id = %s""",
                        (status, reviewer_id, flag_id),
                    )
                    frozen_account_id = None
                    if freeze and status == "confirmed" and row["from_account_id"]:
                        cur.execute(
                            "UPDATE accounts SET status = 'frozen' WHERE account_id = %s AND status = 'active'",
                            (row["from_account_id"],),
                        )
                        if cur.rowcount == 1:
                            frozen_account_id = row["from_account_id"]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"txn_id": row["txn_id"], "frozen_account_id": frozen_account_id}

    def set_account_status(self, account_id, status):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET status = %s WHERE account_id = %s AND status <> %s AND status <> 'closed'",
                (status, account_id, status),
            )
            return cur.rowcount == 1

    def stats(self):
        totals = self._one(
            """SELECT
                 (SELECT COUNT(*) FROM transactions)                                       AS total_txns,
                 (SELECT COUNT(*) FROM transactions
                   WHERE created_at >= NOW() - INTERVAL 24 HOUR)                           AS txns_24h,
                 (SELECT COALESCE(SUM(amount), 0) FROM transactions
                   WHERE created_at >= NOW() - INTERVAL 24 HOUR)                           AS volume_24h,
                 (SELECT COUNT(*) FROM fraud_flags WHERE status = 'open')                  AS open_flags,
                 (SELECT COUNT(*) FROM fraud_flags WHERE status = 'confirmed')             AS confirmed_flags,
                 (SELECT COUNT(*) FROM fraud_flags WHERE status = 'dismissed')             AS dismissed_flags"""
        )
        by_rule = self._all(
            """SELECT fr.rule_name,
                      COUNT(f.flag_id)                         AS total,
                      COALESCE(SUM(f.status = 'confirmed'), 0) AS confirmed,
                      COALESCE(SUM(f.status = 'dismissed'), 0) AS dismissed,
                      COALESCE(SUM(f.status = 'open'), 0)      AS open
                 FROM fraud_rules fr
                 LEFT JOIN fraud_flags f ON f.rule_id = fr.rule_id
                GROUP BY fr.rule_id, fr.rule_name
                ORDER BY total DESC, fr.rule_id"""
        )
        for r in by_rule:
            for k in ("total", "confirmed", "dismissed", "open"):
                r[k] = int(r[k])

        raw_daily = self._all(
            """SELECT DATE(t.created_at) AS day, COUNT(*) AS txns, SUM(t.amount) AS volume,
                      SUM(EXISTS (SELECT 1 FROM fraud_flags f WHERE f.txn_id = t.txn_id)) AS flagged
                 FROM transactions t
                WHERE t.created_at >= UTC_DATE() - INTERVAL 13 DAY
                GROUP BY DATE(t.created_at)
                ORDER BY day"""
        )
        by_day = {r["day"]: r for r in raw_daily}
        today = datetime.now(timezone.utc).date()
        daily = []
        for i in range(13, -1, -1):
            d = today - timedelta(days=i)
            r = by_day.get(d)
            daily.append(
                {
                    "day": d,
                    "txns": int(r["txns"]) if r else 0,
                    "volume": float(r["volume"]) if r else 0.0,
                    "flagged": int(r["flagged"]) if r else 0,
                }
            )
        return {"totals": totals, "by_rule": by_rule, "daily": daily}
