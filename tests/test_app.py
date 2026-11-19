"""Run with:  python -m unittest discover -s tests -v
These tests exercise the Flask app (auth, CSRF, roles, validation, JSON, fraud-review flow) against
in-memory fakes. They do not need MySQL or MongoDB."""
import re
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from tests.fakes import FakeRepo, FakeStore


def new_client(seeded=True):
    repo, store = FakeRepo(seeded=seeded), FakeStore(seeded=seeded)
    app = create_app({"SECRET_KEY": "test-secret-key-0123456789"}, repo=repo, store=store)
    return app, app.test_client(), repo, store


def csrf_from(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client, email, password="Password@123"):
    token = csrf_from(client)
    res = client.post("/api/login", json={"email": email, "password": password}, headers={"X-CSRF-Token": token})
    if res.status_code != 200:
        return res, None
    page = "/admin" if res.get_json()["role"] == "admin" else "/dashboard"
    return res, csrf_from(client, page)


def as_customer(email="aarav@mail.com"):
    app, client, repo, store = new_client()
    res, token = login(client, email)
    assert res.status_code == 200, res.get_json()
    return client, repo, store, {"X-CSRF-Token": token}


def as_admin():
    return as_customer("admin@bank.com")


class AuthTests(unittest.TestCase):
    def test_login_page_and_redirects(self):
        app, client, *_ = new_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get("/dashboard").status_code, 302)
        self.assertEqual(client.get("/admin").status_code, 302)
        self.assertEqual(client.get("/login").status_code, 200)

    def test_login_success_roles_and_redirect(self):
        app, client, *_ = new_client()
        res, _ = login(client, "aarav@mail.com")
        self.assertEqual(res.get_json(), {"role": "customer"})
        self.assertEqual(client.get("/dashboard").status_code, 200)
        self.assertEqual(client.get("/admin").status_code, 302)  # customers cannot open the admin page

    def test_wrong_password_unknown_user_and_missing_fields(self):
        app, client, *_ = new_client()
        self.assertEqual(login(client, "aarav@mail.com", "nope")[0].status_code, 401)
        self.assertEqual(login(client, "ghost@mail.com", "x")[0].status_code, 401)
        token = csrf_from(client)
        res = client.post("/api/login", json={"email": "", "password": ""}, headers={"X-CSRF-Token": token})
        self.assertEqual(res.status_code, 400)

    def test_login_requires_csrf(self):
        app, client, *_ = new_client()
        res = client.post("/api/login", json={"email": "aarav@mail.com", "password": "Password@123"})
        self.assertEqual(res.status_code, 403)

    def test_login_is_throttled(self):
        app, client, *_ = new_client()
        codes = [login(client, "priya@mail.com", "wrong")[0].status_code for _ in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        self.assertEqual(login(client, "priya@mail.com")[0].status_code, 429)  # even the right password

    def test_logout_ends_session(self):
        client, repo, store, h = as_customer()
        self.assertEqual(client.post("/api/logout", headers=h).status_code, 200)
        self.assertEqual(client.get("/api/accounts").status_code, 401)

    def test_api_requires_login_and_role(self):
        app, client, *_ = new_client()
        self.assertEqual(client.get("/api/accounts").status_code, 401)
        self.assertEqual(client.get("/api/admin/flags").status_code, 401)
        c, *_ = as_customer()
        self.assertEqual(c.get("/api/admin/flags").status_code, 403)
        self.assertEqual(c.get("/api/admin/stats").status_code, 403)
        a, *_ = as_admin()
        self.assertEqual(a.get("/api/accounts").status_code, 403)

    def test_writes_need_csrf_header(self):
        client, *_ = as_customer()
        res = client.post("/api/transfer", json={"fromAccountId": 1, "toAccountNumber": "1000000000000002", "amount": 10})
        self.assertEqual(res.status_code, 403)

    def test_security_headers(self):
        app, client, *_ = new_client()
        res = client.get("/login")
        self.assertIn("default-src 'self'", res.headers["Content-Security-Policy"])
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.client, self.repo, self.store, self.h = as_customer()

    def transfer(self, **over):
        body = {"fromAccountId": 1, "toAccountNumber": "1000000000000002", "amount": 2000, "city": "Mumbai"}
        body.update(over)
        return self.client.post("/api/transfer", json=body, headers=self.h)

    def test_accounts_only_own_and_money_is_number(self):
        data = self.client.get("/api/accounts").get_json()["accounts"]
        self.assertEqual([a["account_id"] for a in data], [1, 6])
        self.assertIsInstance(data[0]["balance"], float)

    def test_transactions_only_own_with_direction(self):
        txns = self.client.get("/api/transactions").get_json()["transactions"]
        self.assertTrue(txns)
        self.assertTrue(all("1000000000000001" in (t["from_account"], t["to_account"]) or
                            "1000000000000006" in (t["from_account"], t["to_account"]) for t in txns))
        self.assertTrue(txns[0]["created_at"].endswith("Z"))

    def test_transfer_success_updates_balances_no_flag(self):
        before = float(self.repo.accounts[1]["balance"])
        res = self.transfer()
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.get_json()["flagged"])
        self.assertEqual(float(self.repo.accounts[1]["balance"]), before - 2000)
        self.assertTrue(any(e["event_type"] == "TRANSFER" for e in self.store.audit))

    def test_transfer_large_from_new_city_is_flagged_and_stored_in_mongo(self):
        res = self.transfer(amount=60000, city="Delhi")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.get_json()["flagged"])
        txn_id = res.get_json()["txnId"]
        new_alerts = [a for a in self.store.alerts.values() if a["txn_id"] == txn_id]
        self.assertEqual({a["rule_name"] for a in new_alerts},
                         {"Large transaction (>= 50,000)", "Transaction outside home city"})
        self.assertEqual(new_alerts[0]["context"]["geo"]["city"], "Delhi")

    def test_transfer_validation_and_authorisation(self):
        cases = [
            (dict(amount=0), 400), (dict(amount=-5), 400), (dict(amount="abc"), 400), (dict(amount=1.234), 400),
            (dict(amount=10**9), 400), (dict(toAccountNumber="12"), 400), (dict(fromAccountId="x"), 400),
            (dict(city="<script>"), 400),
            (dict(fromAccountId=2), 403),                          # someone else's account
            (dict(toAccountNumber="1000000000009999"), 404),       # unknown destination
            (dict(toAccountNumber="1000000000000001"), 400),       # same account
            (dict(amount=10**6), 400),                             # insufficient funds
            (dict(toAccountNumber="1000000000000005"), 400),       # destination frozen
        ]
        for over, code in cases:
            with self.subTest(over=over):
                self.assertEqual(self.transfer(**over).status_code, code)
        self.assertEqual(float(self.repo.accounts[1]["balance"]), 250000.0)  # nothing moved

    def test_lookup_masks_name(self):
        res = self.client.get("/api/accounts/lookup?number=1000000000000002")
        self.assertEqual(res.get_json()["holder"], "Priya V.")
        self.assertEqual(self.client.get("/api/accounts/lookup?number=1000000000009999").status_code, 404)
        self.assertEqual(self.client.get("/api/accounts/lookup?number=abc").status_code, 400)


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.client, self.repo, self.store, self.h = as_admin()

    def test_flags_list_sorted_with_mongo_details(self):
        flags = self.client.get("/api/admin/flags").get_json()["flags"]
        self.assertEqual([f["flag_id"] for f in flags[:3]], [2, 5, 3])   # open first, highest risk first, ties newest first
        self.assertEqual({f["status"] for f in flags}, {"open", "confirmed", "dismissed"})
        self.assertEqual(flags[0]["context"]["device"]["id"], "atm-BLR-221")
        only_open = self.client.get("/api/admin/flags?status=open").get_json()["flags"]
        self.assertEqual({f["status"] for f in only_open}, {"open"})
        self.assertEqual(len(self.client.get("/api/admin/flags?status=bogus").get_json()["flags"]), 5)

    def test_flags_still_work_if_mongo_is_down(self):
        def boom(*a, **k):
            raise RuntimeError("mongo down")
        self.store.load_alerts = boom
        res = self.client.get("/api/admin/flags")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.get_json()["flags"][0]["context"])

    def test_review_confirm_with_note_and_freeze(self):
        res = self.client.patch("/api/admin/flags/3", json={"status": "confirmed", "note": "Card cloned", "freezeAccount": True}, headers=self.h)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["frozenAccountId"], 4)
        self.assertEqual(self.repo.accounts[4]["status"], "frozen")
        self.assertEqual(self.store.alerts[3]["status"], "confirmed")
        self.assertEqual(self.store.alerts[3]["analyst_notes"][-1]["note"], "Card cloned")
        kinds = {e["event_type"] for e in self.store.audit}
        self.assertTrue({"FLAG_REVIEWED", "ACCOUNT_FROZEN"} <= kinds)

    def test_review_twice_conflicts_and_bad_status(self):
        self.assertEqual(self.client.patch("/api/admin/flags/2", json={"status": "dismissed"}, headers=self.h).status_code, 200)
        self.assertEqual(self.client.patch("/api/admin/flags/2", json={"status": "dismissed"}, headers=self.h).status_code, 409)
        self.assertEqual(self.client.patch("/api/admin/flags/3", json={"status": "open"}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.patch("/api/admin/flags/3", json={"status": "confirmed"}).status_code, 403)  # no CSRF

    def test_unfreeze_and_freeze_account(self):
        self.assertEqual(self.client.patch("/api/admin/accounts/5", json={"status": "active"}, headers=self.h).status_code, 200)
        self.assertEqual(self.repo.accounts[5]["status"], "active")
        self.assertEqual(self.client.patch("/api/admin/accounts/5", json={"status": "active"}, headers=self.h).status_code, 409)
        self.assertEqual(self.client.patch("/api/admin/accounts/5", json={"status": "closed"}, headers=self.h).status_code, 400)

    def test_stats_shape(self):
        d = self.client.get("/api/admin/stats").get_json()
        self.assertEqual(d["totals"]["open_flags"], 3)
        self.assertEqual(d["totals"]["confirmed_flags"], 1)
        self.assertEqual(len(d["daily"]), 14)
        self.assertEqual(d["byRule"][0]["total"], 2)
        self.assertEqual(d["byCity"][0], {"city": "London", "alerts": 2, "avg_risk": 60})

    def test_activity_feed(self):
        events = self.client.get("/api/admin/activity").get_json()["events"]
        self.assertTrue(events and events[0]["at"].endswith("Z"))


# ====================================================================== sign-up and fresh start
def payload(**over):
    base = dict(fullName="Neha Kapoor", email="neha@mail.com", phone="9876501234", dob="1996-03-03",
                homeCity="Mumbai", password="Secret123", confirmPassword="Secret123", branchId=1)
    base.update(over)
    return base


def post_signup(client, **over):
    token = csrf_from(client, "/signup")
    return client.post("/api/register", json=payload(**over), headers={"X-CSRF-Token": token})


def signed_up(app, **over):
    """A new client that has just signed up; returns (client, csrf headers)."""
    client = app.test_client()
    res = post_signup(client, **over)
    assert res.status_code == 201, res.get_json()
    return client, {"X-CSRF-Token": csrf_from(client, "/dashboard")}


class SignupTests(unittest.TestCase):
    def test_pages_have_no_preset_credentials_and_link_to_each_other(self):
        app, client, *_ = new_client(seeded=False)
        login_html = client.get("/login").get_data(as_text=True)
        self.assertNotIn("Password@123", login_html)
        self.assertNotIn("admin@bank.com", login_html)
        self.assertIn('href="/signup"', login_html)
        signup_html = client.get("/signup").get_data(as_text=True)
        self.assertIn("Mumbai Main", signup_html)
        self.assertIn('href="/login"', signup_html)

    def test_successful_signup_signs_in_and_creates_a_first_account(self):
        app, client, repo, store = new_client(seeded=False)
        res = post_signup(client)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json(), {"role": "customer"})
        self.assertEqual(client.get("/dashboard").status_code, 200)
        accounts = client.get("/api/accounts").get_json()["accounts"]
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["account_type"], "savings")
        self.assertEqual(accounts[0]["balance"], 0)
        self.assertRegex(accounts[0]["account_number"], r"^\d{16}$")
        self.assertTrue(any(e["event_type"] == "SIGNUP" for e in store.audit))

    def test_new_user_can_sign_in_again_later(self):
        app, client, *_ = new_client(seeded=False)
        post_signup(client)
        other = app.test_client()
        res, _ = login(other, "neha@mail.com", "Secret123")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(login(app.test_client(), "neha@mail.com", "WrongPass1")[0].status_code, 401)

    def test_email_is_normalised_and_signed_in_user_is_redirected_from_signup(self):
        app, client, *_ = new_client(seeded=False)
        self.assertEqual(post_signup(client, email="  Neha@Mail.COM ").status_code, 201)
        self.assertEqual(client.get("/signup").status_code, 302)
        self.assertEqual(login(app.test_client(), "neha@mail.com", "Secret123")[0].status_code, 200)

    def test_each_invalid_field_is_rejected_with_its_name(self):
        today = date.today()
        under18 = today.replace(year=today.year - 17).isoformat()
        future = today.replace(year=today.year + 1).isoformat()
        cases = [
            (dict(fullName=""), "fullName"), (dict(fullName="1234"), "fullName"),
            (dict(email="nope"), "email"), (dict(email="a@b"), "email"),
            (dict(phone="123"), "phone"), (dict(phone="abcdefghij"), "phone"),
            (dict(dob=""), "dob"), (dict(dob="banana"), "dob"), (dict(dob=future), "dob"),
            (dict(dob=under18), "dob"), (dict(dob="1800-01-01"), "dob"),
            (dict(homeCity=""), "homeCity"), (dict(homeCity="<b>x</b>"), "homeCity"),
            (dict(password="short1", confirmPassword="short1"), "password"),
            (dict(password="allletters", confirmPassword="allletters"), "password"),
            (dict(password="12345678", confirmPassword="12345678"), "password"),
            (dict(confirmPassword="Different1"), "confirmPassword"),
            (dict(branchId=99), "branchId"), (dict(branchId="x"), "branchId"),
        ]
        for over, field in cases:
            with self.subTest(over=over):
                app, client, repo, _ = new_client(seeded=False)  # fresh app each time: own throttle
                res = post_signup(client, **over)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.get_json()["field"], field)
                self.assertEqual(repo.users, {})  # nothing was created

    def test_exactly_18_today_is_allowed(self):
        today = date.today()
        app, client, *_ = new_client(seeded=False)
        self.assertEqual(post_signup(client, dob=today.replace(year=today.year - 18).isoformat()).status_code, 201)

    def test_duplicate_email_and_phone_are_refused(self):
        app, client, *_ = new_client(seeded=False)
        self.assertEqual(post_signup(client).status_code, 201)
        dup_email = post_signup(app.test_client(), phone="9000000001")
        self.assertEqual((dup_email.status_code, dup_email.get_json()["field"]), (409, "email"))
        dup_upper = post_signup(app.test_client(), email="NEHA@mail.com", phone="9000000002")
        self.assertEqual(dup_upper.status_code, 409)
        dup_phone = post_signup(app.test_client(), email="other@mail.com")
        self.assertEqual((dup_phone.status_code, dup_phone.get_json()["field"]), (409, "phone"))

    def test_signup_needs_csrf_and_is_throttled(self):
        app, client, *_ = new_client(seeded=False)
        self.assertEqual(client.post("/api/register", json=payload()).status_code, 403)
        codes = [post_signup(app.test_client(), fullName="").status_code for _ in range(16)]
        self.assertEqual(codes[:15], [400] * 15)
        self.assertEqual(codes[15], 429)

    def test_signed_up_user_cannot_reach_admin(self):
        app, client, *_ = new_client(seeded=False)
        post_signup(client)
        self.assertEqual(client.get("/admin").status_code, 302)
        self.assertEqual(client.get("/api/admin/flags").status_code, 403)


class AccountsAndFundsTests(unittest.TestCase):
    def setUp(self):
        self.app, _, self.repo, self.store = new_client(seeded=False)
        self.client, self.h = signed_up(self.app)
        self.acct = self.client.get("/api/accounts").get_json()["accounts"][0]

    def deposit(self, amount, account_id=None):
        return self.client.post("/api/deposit", headers=self.h,
                                json={"accountId": account_id or self.acct["account_id"], "amount": amount})

    def test_add_funds_increases_balance_and_is_audited(self):
        self.assertEqual(self.deposit(2500.50).status_code, 201)
        self.assertEqual(self.client.get("/api/accounts").get_json()["accounts"][0]["balance"], 2500.5)
        txns = self.client.get("/api/transactions").get_json()["transactions"]
        self.assertEqual((txns[0]["txn_type"], txns[0]["outgoing"]), ("deposit", False))
        self.assertTrue(any(e["event_type"] == "DEPOSIT" for e in self.store.audit))

    def test_large_deposits_are_not_scored_as_fraud(self):
        self.assertEqual(self.deposit(900000).status_code, 201)
        self.assertEqual(self.repo.flags, [])

    def test_deposit_validation_and_ownership(self):
        for amount in (0, -5, "abc", 1.234, 1000001):
            with self.subTest(amount=amount):
                self.assertEqual(self.deposit(amount).status_code, 400)
        self.assertEqual(self.deposit(100, account_id=99999).status_code, 403)
        self.repo.accounts[self.acct["account_id"]]["status"] = "frozen"
        self.assertEqual(self.deposit(100).status_code, 400)

    def test_open_account_branches_and_limit(self):
        branches = self.client.get("/api/branches").get_json()["branches"]
        self.assertEqual(len(branches), 4)
        for i in range(4):
            res = self.client.post("/api/accounts/open", headers=self.h, json={"branchId": 2, "accountType": "current"})
            self.assertEqual(res.status_code, 201, res.get_json())
        self.assertEqual(len(self.client.get("/api/accounts").get_json()["accounts"]), 5)
        over = self.client.post("/api/accounts/open", headers=self.h, json={"branchId": 2, "accountType": "savings"})
        self.assertEqual(over.status_code, 409)
        self.assertIn("at most 5", over.get_json()["error"])

    def test_open_account_validation(self):
        bad = [dict(branchId=99, accountType="savings"), dict(branchId="x", accountType="savings"),
               dict(branchId=1, accountType="platinum"), dict(branchId=1)]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/accounts/open", headers=self.h, json=body).status_code, 400)

    def test_transfer_between_own_accounts(self):
        self.client.post("/api/accounts/open", headers=self.h, json={"branchId": 1, "accountType": "current"})
        a, b = self.client.get("/api/accounts").get_json()["accounts"]
        self.deposit(1000)
        res = self.client.post("/api/transfer", headers=self.h, json={
            "fromAccountId": a["account_id"], "toAccountNumber": b["account_number"], "amount": 400})
        self.assertEqual(res.status_code, 201)
        balances = [x["balance"] for x in self.client.get("/api/accounts").get_json()["accounts"]]
        self.assertEqual(balances, [600, 400])


class FreshSystemEndToEnd(unittest.TestCase):
    def test_empty_system_to_flagged_transfer_and_review(self):
        app, anon, repo, store = new_client(seeded=False)

        # nobody exists yet: even the old demo logins are gone
        self.assertEqual(login(anon, "admin@bank.com")[0].status_code, 401)
        self.assertEqual(login(app.test_client(), "aarav@mail.com")[0].status_code, 401)

        # the analyst is created out-of-band (scripts/create_admin.py) and sees an empty system
        repo.create_admin("analyst@bank.com", generate_password_hash("Analyst99", method="pbkdf2:sha256:1000"))
        admin = app.test_client()
        res, token = login(admin, "analyst@bank.com", "Analyst99")
        self.assertEqual(res.status_code, 200)
        ah = {"X-CSRF-Token": token}
        self.assertEqual(admin.get("/api/admin/flags").get_json()["flags"], [])
        stats = admin.get("/api/admin/stats").get_json()
        self.assertEqual(stats["totals"]["open_flags"], 0)
        self.assertEqual(stats["byCity"], [])

        # two customers sign up
        alice, alice_h = signed_up(app, fullName="Alice Rao", email="alice@mail.com", phone="9000000010", homeCity="Mumbai")
        bob, _ = signed_up(app, fullName="Bob Das", email="bob@mail.com", phone="9000000011", homeCity="Delhi")
        a_acct = alice.get("/api/accounts").get_json()["accounts"][0]
        b_acct = bob.get("/api/accounts").get_json()["accounts"][0]

        # Alice adds funds, then sends a large amount while "in" a different city
        self.assertEqual(alice.post("/api/deposit", headers=alice_h, json={"accountId": a_acct["account_id"], "amount": 100000}).status_code, 201)
        res = alice.post("/api/transfer", headers=alice_h, json={
            "fromAccountId": a_acct["account_id"], "toAccountNumber": b_acct["account_number"],
            "amount": 60000, "city": "Dubai"})
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.get_json()["flagged"])

        # Bob received the money
        self.assertEqual(bob.get("/api/accounts").get_json()["accounts"][0]["balance"], 60000)

        # the analyst sees two open flags with context, and reviews one with a freeze
        flags = admin.get("/api/admin/flags?status=open").get_json()["flags"]
        self.assertEqual(len(flags), 2)
        self.assertEqual({f["customer_name"] for f in flags}, {"Alice Rao"})
        self.assertEqual(flags[0]["context"]["geo"]["city"], "Dubai")
        res = admin.patch(f"/api/admin/flags/{flags[0]['flag_id']}", headers=ah,
                          json={"status": "confirmed", "note": "Not the customer", "freezeAccount": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["frozenAccountId"], a_acct["account_id"])

        # Alice's account is now frozen: she cannot send or add funds
        res = alice.post("/api/transfer", headers=alice_h, json={
            "fromAccountId": a_acct["account_id"], "toAccountNumber": b_acct["account_number"], "amount": 10})
        self.assertEqual(res.status_code, 400)
        self.assertIn("not active", res.get_json()["error"])
        self.assertEqual(admin.get("/api/admin/stats").get_json()["totals"]["confirmed_flags"], 1)
        kinds = {e["event_type"] for e in store.audit}
        self.assertTrue({"SIGNUP", "DEPOSIT", "TRANSFER", "FLAG_REVIEWED", "ACCOUNT_FROZEN", "LOGIN"} <= kinds)



class ErrorTests(unittest.TestCase):
    def test_unknown_api_route_is_json(self):
        app, client, *_ = new_client()
        res = client.get("/api/nope")
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.get_json())

    def test_missing_secret_key_fails_clearly(self):
        with self.assertRaises(RuntimeError):
            create_app({"SECRET_KEY": ""}, repo=FakeRepo(), store=FakeStore())


if __name__ == "__main__":
    unittest.main()
