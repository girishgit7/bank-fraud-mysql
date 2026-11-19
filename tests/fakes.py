"""In-memory stand-ins for MySQLRepo and MongoStore.

They mirror the real interfaces and the sample data in db/seed.sql, so the Flask app (routes,
auth, CSRF, validation, JSON, UI) can be tested and demoed without MySQL or MongoDB running.
They do NOT test the SQL itself: run scripts/check_setup.py against the real databases for that.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from werkzeug.security import generate_password_hash

import itertools

from app.errors import RegistrationError, TransferError

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
FAST_HASH = lambda pw: generate_password_hash(pw, method="pbkdf2:sha256:1000")  # noqa: E731


class FakeRepo:
    def __init__(self, seeded=True):
        n = NOW()
        h = FAST_HASH("Password@123")
        self.users = {
            "admin@bank.com": dict(user_id=1, email="admin@bank.com", password_hash=h, role="admin", customer_id=None, full_name=None),
            "aarav@mail.com": dict(user_id=2, email="aarav@mail.com", password_hash=h, role="customer", customer_id=1, full_name="Aarav Sharma"),
            "priya@mail.com": dict(user_id=3, email="priya@mail.com", password_hash=h, role="customer", customer_id=2, full_name="Priya Verma"),
            "rohan@mail.com": dict(user_id=4, email="rohan@mail.com", password_hash=h, role="customer", customer_id=3, full_name="Rohan Iyer"),
            "sneha@mail.com": dict(user_id=5, email="sneha@mail.com", password_hash=h, role="customer", customer_id=4, full_name="Sneha Nair"),
            "karan@mail.com": dict(user_id=6, email="karan@mail.com", password_hash=h, role="customer", customer_id=5, full_name="Karan Mehta"),
        }
        self.customers = {
            1: ("Aarav Sharma", "Mumbai"), 2: ("Priya Verma", "Delhi"), 3: ("Rohan Iyer", "Bengaluru"),
            4: ("Sneha Nair", "Chennai"), 5: ("Karan Mehta", "Mumbai"),
        }
        self.accounts = {
            1: dict(account_id=1, customer_id=1, account_number="1000000000000001", account_type="savings", balance=Decimal("250000"), status="active", branch_name="Mumbai Main"),
            2: dict(account_id=2, customer_id=2, account_number="1000000000000002", account_type="savings", balance=Decimal("80000"), status="active", branch_name="Delhi Central"),
            3: dict(account_id=3, customer_id=3, account_number="1000000000000003", account_type="savings", balance=Decimal("50000"), status="active", branch_name="Bengaluru Tech Park"),
            4: dict(account_id=4, customer_id=4, account_number="1000000000000004", account_type="savings", balance=Decimal("120000"), status="active", branch_name="Chennai Marina"),
            5: dict(account_id=5, customer_id=5, account_number="1000000000000005", account_type="savings", balance=Decimal("300000"), status="frozen", branch_name="Mumbai Main"),
            6: dict(account_id=6, customer_id=1, account_number="1000000000000006", account_type="current", balance=Decimal("40000"), status="active", branch_name="Mumbai Main"),
        }
        self.rules = {
            1: ("Large transaction (>= 50,000)", 70), 2: ("Rapid transactions (5 in 10 min)", 80),
            3: ("Transaction outside home city", 50),
        }
        self.txns, self.flags = [], []
        self.branches = [dict(branch_id=1, branch_name="Mumbai Main", city="Mumbai"),
                         dict(branch_id=2, branch_name="Delhi Central", city="Delhi"),
                         dict(branch_id=3, branch_name="Bengaluru Tech Park", city="Bengaluru"),
                         dict(branch_id=4, branch_name="Chennai Marina", city="Chennai")]
        self._next_user = itertools.count(100)
        self._next_account = itertools.count(100)
        if not seeded:
            # a brand-new system: no users, customers, accounts or transactions
            self.users, self.customers, self.accounts = {}, {}, {}
            return
        d = lambda **kw: n - timedelta(**kw)  # noqa: E731
        for args in [
            (None, 1, "deposit", 20000, "branch", "Mumbai", "Salary credit", d(days=10)),
            (None, 2, "deposit", 15000, "branch", "Delhi", "Cash deposit", d(days=9)),
            (1, 2, "transfer", 2500, "online", "Mumbai", "Shared dinner", d(days=8)),
            (2, 3, "transfer", 1200, "upi", "Delhi", "Book purchase", d(days=7)),
            (3, None, "withdrawal", 3000, "atm", "Bengaluru", "ATM cash", d(days=6)),
            (4, 5, "transfer", 800, "upi", "Chennai", "Gift", d(days=5)),
            (None, 4, "deposit", 10000, "branch", "Chennai", "Cheque deposit", d(days=4)),
            (5, None, "withdrawal", 2000, "atm", "Mumbai", "ATM cash", d(days=3)),
            (5, 1, "transfer", 1500, "upi", "Mumbai", "Rent share", d(days=2)),
            (6, 2, "transfer", 4000, "online", "Mumbai", "Invoice payment", d(days=2)),
            (1, 3, "transfer", 75000, "online", "Mumbai", "Large transfer", d(days=1)),
        ] + [(3, None, "withdrawal", 4000, "atm", "Bengaluru", f"ATM burst {i}", d(hours=3) + timedelta(minutes=i - 1)) for i in range(1, 6)] + [
            (4, None, "withdrawal", 15000, "atm", "Dubai", "Overseas ATM", d(hours=5)),
            (5, None, "withdrawal", 90000, "atm", "London", "Overseas large withdrawal", d(hours=2)),
        ]:
            self._insert_txn(*args)
        # analyst decisions (as in seed.sql)
        self.flags[0].update(status="dismissed", reviewed_at=n)
        self.flags[3].update(status="confirmed", reviewed_at=n)

    # ---- emulation of the trigger trg_check_fraud ----
    def _insert_txn(self, frm, to, kind, amount, channel, city, desc, created):
        txn = dict(txn_id=len(self.txns) + 1, from_account_id=frm, to_account_id=to, txn_type=kind,
                   amount=Decimal(str(amount)), channel=channel, location_city=city, description=desc,
                   status="completed", created_at=created)
        self.txns.append(txn)
        new = []
        if frm and txn["amount"] >= 50000:
            new.append(1)
        if frm:
            recent = [t for t in self.txns if t["from_account_id"] == frm
                      and created - timedelta(minutes=10) <= t["created_at"] <= created]
            if len(recent) >= 5:
                new.append(2)
            home = self.customers[self.accounts[frm]["customer_id"]][1]
            if city and home.lower() != city.lower():
                new.append(3)
        for rid in new:
            self.flags.append(dict(flag_id=len(self.flags) + 1, txn_id=txn["txn_id"], rule_id=rid,
                                   status="open", flagged_at=created, reviewed_at=None))
        return txn["txn_id"]

    # ---- sign-up, accounts, deposits, admin creation ----
    def list_branches(self):
        return [dict(b) for b in self.branches]

    def register_customer(self, email, password_hash, full_name, phone, dob, home_city, branch_id):
        if email in self.users:
            raise RegistrationError("An account with this email already exists. Sign in instead.", "email")
        if any(c.get("phone") == phone for c in self._profiles()):
            raise RegistrationError("That phone number is already registered.", "phone")
        user_id = next(self._next_user)
        customer_id = user_id
        self.users[email] = dict(user_id=user_id, email=email, password_hash=password_hash, role="customer",
                                 customer_id=customer_id, full_name=full_name)
        self.customers[customer_id] = (full_name, home_city)
        self._phones = getattr(self, "_phones", {})
        self._phones[customer_id] = phone
        account = self._new_account(customer_id, branch_id, "savings")
        return dict(user_id=user_id, customer_id=customer_id, full_name=full_name, email=email, account=account)

    def _profiles(self):
        return [dict(phone=p) for p in getattr(self, "_phones", {}).values()]

    def _new_account(self, customer_id, branch_id, account_type):
        account_id = next(self._next_account)
        branch = next(b for b in self.branches if b["branch_id"] == branch_id)
        number = "10" + str(account_id).zfill(14)
        self.accounts[account_id] = dict(account_id=account_id, customer_id=customer_id, account_number=number,
                                         account_type=account_type, balance=Decimal("0"), status="active",
                                         branch_name=branch["branch_name"])
        return dict(account_id=account_id, account_number=number, account_type=account_type)

    def open_account(self, customer_id, branch_id, account_type):
        if sum(1 for a in self.accounts.values() if a["customer_id"] == customer_id) >= 5:
            raise RegistrationError("You can have at most 5 accounts.")
        return self._new_account(customer_id, branch_id, account_type)

    def deposit(self, account_id, amount, channel, city, description):
        acct = self.accounts.get(account_id)
        if not acct:
            raise TransferError("Account not found")
        if acct["status"] != "active":
            raise TransferError("Account is not active")
        acct["balance"] += amount
        return self._insert_txn(None, account_id, "deposit", amount, channel, city, description, NOW())

    def create_admin(self, email, password_hash):
        if email in self.users:
            raise RegistrationError("A user with this email already exists.", "email")
        user_id = next(self._next_user)
        self.users[email] = dict(user_id=user_id, email=email, password_hash=password_hash, role="admin",
                                 customer_id=None, full_name=None)
        return user_id

    # ---- interface ----
    def get_user_for_login(self, email):
        return deepcopy(self.users.get(email))

    def list_accounts(self, customer_id):
        return [deepcopy(a) for a in self.accounts.values() if a["customer_id"] == customer_id]

    def list_transactions(self, customer_id, limit=60):
        mine = {a["account_id"] for a in self.accounts.values() if a["customer_id"] == customer_id}
        out = []
        for t in sorted(self.txns, key=lambda x: (x["created_at"], x["txn_id"]), reverse=True):
            if t["from_account_id"] in mine or t["to_account_id"] in mine:
                out.append(dict(
                    txn_id=t["txn_id"], txn_type=t["txn_type"], amount=t["amount"], channel=t["channel"],
                    location_city=t["location_city"], description=t["description"], status=t["status"],
                    created_at=t["created_at"],
                    from_account=self.accounts[t["from_account_id"]]["account_number"] if t["from_account_id"] else None,
                    to_account=self.accounts[t["to_account_id"]]["account_number"] if t["to_account_id"] else None,
                    outgoing=t["from_account_id"] in mine))
        return out[:limit]

    def account_owned_by(self, account_id, customer_id):
        a = self.accounts.get(account_id)
        return bool(a and a["customer_id"] == customer_id)

    def find_account_by_number(self, number):
        for a in self.accounts.values():
            if a["account_number"] == number:
                return dict(account_id=a["account_id"], account_number=number, status=a["status"],
                            holder=self.customers[a["customer_id"]][0], branch_name=a["branch_name"])
        return None

    def transfer(self, from_id, to_id, amount, channel, city, description):
        src, dst = self.accounts[from_id], self.accounts[to_id]
        if src["status"] != "active":
            raise TransferError("Source account is not active")
        if dst["status"] != "active":
            raise TransferError("Destination account is not active")
        if src["balance"] < amount:
            raise TransferError("Insufficient funds")
        src["balance"] -= amount
        dst["balance"] += amount
        return self._insert_txn(from_id, to_id, "transfer", amount, channel, city, description, NOW())

    def flags_for_txn(self, txn_id):
        return [dict(flag_id=f["flag_id"], risk_score=self.rules[f["rule_id"]][1], rule_name=self.rules[f["rule_id"]][0])
                for f in self.flags if f["txn_id"] == txn_id]

    def list_flags(self, status=None, limit=200):
        rows = []
        for f in self.flags:
            if status and f["status"] != status:
                continue
            t = self.txns[f["txn_id"] - 1]
            acct = self.accounts.get(t["from_account_id"]) if t["from_account_id"] else None
            cust = self.customers[acct["customer_id"]] if acct else (None, None)
            rows.append(dict(
                flag_id=f["flag_id"], status=f["status"], risk_score=self.rules[f["rule_id"]][1],
                flagged_at=f["flagged_at"], reviewed_at=f["reviewed_at"], rule_name=self.rules[f["rule_id"]][0],
                txn_id=t["txn_id"], txn_type=t["txn_type"], amount=t["amount"], channel=t["channel"],
                location_city=t["location_city"], description=t["description"], txn_at=t["created_at"],
                account_id=acct["account_id"] if acct else None,
                account_number=acct["account_number"] if acct else None,
                account_status=acct["status"] if acct else None,
                customer_name=cust[0], home_city=cust[1]))
        rows.sort(key=lambda r: (r["status"] != "open", -r["risk_score"], -r["flagged_at"].timestamp()))
        return rows[:limit]

    def review_flag(self, flag_id, status, reviewer_id, freeze):
        flag = next((f for f in self.flags if f["flag_id"] == flag_id and f["status"] == "open"), None)
        if not flag:
            return None
        flag.update(status=status, reviewed_at=NOW())
        frozen = None
        txn = self.txns[flag["txn_id"] - 1]
        if freeze and status == "confirmed" and txn["from_account_id"]:
            acct = self.accounts[txn["from_account_id"]]
            if acct["status"] == "active":
                acct["status"] = "frozen"
                frozen = acct["account_id"]
        return {"txn_id": txn["txn_id"], "frozen_account_id": frozen}

    def set_account_status(self, account_id, status):
        a = self.accounts.get(account_id)
        if not a or a["status"] == status or a["status"] == "closed":
            return False
        a["status"] = status
        return True

    def stats(self):
        n = NOW()
        day = lambda t: t["created_at"].date()  # noqa: E731
        last24 = [t for t in self.txns if t["created_at"] >= n - timedelta(hours=24)]
        count = lambda s: sum(1 for f in self.flags if f["status"] == s)  # noqa: E731
        by_rule = []
        for rid, (name, _) in self.rules.items():
            fl = [f for f in self.flags if f["rule_id"] == rid]
            by_rule.append(dict(rule_name=name, total=len(fl),
                                confirmed=sum(f["status"] == "confirmed" for f in fl),
                                dismissed=sum(f["status"] == "dismissed" for f in fl),
                                open=sum(f["status"] == "open" for f in fl)))
        by_rule.sort(key=lambda r: -r["total"])
        flagged_txns = {f["txn_id"] for f in self.flags}
        daily = []
        for i in range(13, -1, -1):
            d = (n - timedelta(days=i)).date()
            ts = [t for t in self.txns if day(t) == d]
            daily.append(dict(day=d, txns=len(ts), volume=float(sum(t["amount"] for t in ts)),
                              flagged=sum(1 for t in ts if t["txn_id"] in flagged_txns)))
        return dict(
            totals=dict(total_txns=len(self.txns), txns_24h=len(last24),
                        volume_24h=sum((t["amount"] for t in last24), Decimal(0)),
                        open_flags=count("open"), confirmed_flags=count("confirmed"), dismissed_flags=count("dismissed")),
            by_rule=by_rule, daily=daily)


class FakeStore:
    def __init__(self, seeded=True):
        self.alerts, self.audit = {}, []
        if seeded:
            self._seed()

    def _seed(self):
        n = NOW()
        h = lambda x: n - timedelta(hours=x)  # noqa: E731
        rows = [
            (1, 11, "Large transaction (>= 50,000)", 70, "high", "dismissed", 75000, "online", dict(type="laptop", id="dev-a1f3", ip="103.21.58.14"), ("Mumbai", "India"), [(1, "Customer confirmed by phone: property booking payment.", h(20))], h(24)),
            (2, 16, "Rapid transactions (5 in 10 min)", 80, "high", "open", 4000, "atm", dict(type="atm", id="atm-BLR-221"), ("Bengaluru", "India"), [], h(3)),
            (3, 17, "Transaction outside home city", 50, "medium", "open", 15000, "atm", dict(type="atm", id="atm-DXB-087"), ("Dubai", "UAE"), [], h(5)),
            (4, 18, "Large transaction (>= 50,000)", 70, "high", "confirmed", 90000, "atm", dict(type="atm", id="atm-LON-410"), ("London", "UK"), [(1, "Customer was in Mumbai at the time. Card blocked.", h(1))], h(2)),
            (5, 18, "Transaction outside home city", 50, "medium", "open", 90000, "atm", dict(type="atm", id="atm-LON-410"), ("London", "UK"), [], h(2)),
        ]
        for fid, tid, rule, risk, sev, st, amt, ch, dev, (city, country), notes, at in rows:
            self.alerts[fid] = dict(flag_id=fid, txn_id=tid, rule_name=rule, risk_score=risk, severity=sev, status=st,
                                    context=dict(amount=amt, channel=ch, device=dev, geo=dict(city=city, country=country)),
                                    analyst_notes=[dict(by=b, note=t, at=a) for b, t, a in notes], created_at=at)
        for ev, ent, eid, det, hrs in [
            ("LOGIN", "user", 1, {"method": "password"}, 2),
            ("FLAG_REVIEWED", "fraud_flag", 4, {"from": "open", "to": "confirmed"}, 1.2),
            ("ACCOUNT_FROZEN", "account", 5, {"reason": "Confirmed fraud on txn 18"}, 1.1),
        ]:
            self.audit.append(dict(event_type=ev, actor_user_id=1, role="admin", entity=ent, entity_id=eid,
                                   details=det, ip="10.0.0.5", at=h(hrs)))

    def store_alert(self, alert):
        self.alerts[alert["flag_id"]] = dict(alert, status="open", analyst_notes=[], created_at=NOW())

    def load_alerts(self, flag_ids):
        return {i: dict(context=self.alerts[i]["context"], analyst_notes=self.alerts[i]["analyst_notes"])
                for i in flag_ids if i in self.alerts}

    def review_alert(self, flag_id, status, note, user_id):
        a = self.alerts.get(flag_id)
        if a:
            a["status"] = status
            if note:
                a["analyst_notes"].append(dict(by=user_id, note=note, at=NOW()))

    def alerts_by_city(self, limit=8):
        agg = {}
        for a in self.alerts.values():
            city = (a["context"].get("geo") or {}).get("city")
            if city:
                agg.setdefault(city, []).append(a["risk_score"])
        rows = [dict(city=c, alerts=len(v), avg_risk=round(sum(v) / len(v))) for c, v in agg.items()]
        rows.sort(key=lambda r: (-r["alerts"], r["city"]))
        return rows[:limit]

    def log_audit(self, event_type, actor_user_id, role, entity, entity_id=None, details=None, ip="unknown"):
        self.audit.append(dict(event_type=event_type, actor_user_id=actor_user_id, role=role, entity=entity,
                               entity_id=entity_id, details=details or {}, ip=ip, at=NOW()))

    def recent_audit(self, limit=12):
        return sorted(self.audit, key=lambda e: e["at"], reverse=True)[:limit]
