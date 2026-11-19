import logging
import re
import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import security, validation
from .errors import BusinessError, TransferError

bp = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger("ledgerwatch")

CITY_RE = re.compile(r"^[\w .'\-]{1,60}$")
ACCOUNT_RE = re.compile(r"^\d{10,16}$")
MAX_AMOUNT = Decimal("10000000")
MAX_DEPOSIT = Decimal("1000000")
ACCOUNT_TYPES = ("savings", "current")
FLAG_STATUSES = ("open", "confirmed", "dismissed")

_dummy_hash = None


def repo():
    return current_app.extensions["repo"]


def store():
    return current_app.extensions["store"]


def error(message, status, field=None):
    body = {"error": message}
    if field:
        body["field"] = field
    return jsonify(body), status


def severity_for(score):
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def parse_amount(raw, maximum=MAX_AMOUNT):
    """Returns a Decimal with 2 places, or raises ValueError."""
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("invalid amount")
    if not value.is_finite() or value <= 0 or value > maximum:
        raise ValueError("amount out of range")
    cents = value.quantize(Decimal("0.01"))
    if cents != value:
        raise ValueError("too many decimals")
    return cents


def audit(event_type, entity, entity_id=None, details=None):
    """Audit logging must never break the request that caused it."""
    user = security.current_user() or {}
    try:
        store().log_audit(
            event_type,
            user.get("user_id", 0),
            user.get("role", "anonymous"),
            entity,
            entity_id,
            details,
            request.remote_addr or "unknown",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Audit log failed: %s", exc)


def mask_name(full_name):
    parts = full_name.split()
    if len(parts) < 2:
        return parts[0] if parts else ""
    return f"{parts[0]} {parts[-1][0]}."


# =============================================================== auth
def start_session(user_id, role, customer_id, name, email):
    session.clear()  # always a fresh session on sign-in (prevents session fixation)
    session["user"] = {"user_id": user_id, "role": role, "customer_id": customer_id,
                       "name": name or "Administrator", "email": email}
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = True


@bp.post("/login")
def login():
    security.check_csrf()
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    if not email or not password:
        return error("Enter your email and password.", 400)

    limiter = current_app.extensions["login_limiter"]
    key = f"{request.remote_addr}|{email}"
    if limiter.blocked(key):
        return error("Too many failed attempts. Wait a few minutes and try again.", 429)

    global _dummy_hash
    user = repo().get_user_for_login(email)
    if user is None and _dummy_hash is None:
        _dummy_hash = generate_password_hash("not-a-real-password", method="pbkdf2:sha256")
    # Always run one hash check so response time does not reveal which emails exist.
    ok = check_password_hash(user["password_hash"] if user else _dummy_hash, password)
    if not user or not ok:
        limiter.fail(key)
        return error("Incorrect email or password.", 401)

    limiter.reset(key)
    start_session(user["user_id"], user["role"], user["customer_id"], user["full_name"], user["email"])
    audit("LOGIN", "user", user["user_id"], {"method": "password"})
    return jsonify(role=user["role"])


@bp.post("/register")
def register():
    """Customer sign-up: creates the user, the profile and a first savings account, then signs in."""
    security.check_csrf()
    limiter = current_app.extensions["signup_limiter"]
    key = request.remote_addr or "unknown"
    if limiter.blocked(key):
        return error("Too many sign-up attempts from this device. Please try again later.", 429)
    limiter.fail(key)  # every attempt counts, so accounts cannot be mass-created

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Invalid request.", 400)

    branch_ids = {b["branch_id"] for b in repo().list_branches()}
    try:
        cleaned = validation.validate_registration(data, branch_ids)
    except validation.FieldError as exc:
        return error(exc.message, 400, exc.field)

    password_hash = generate_password_hash(cleaned["password"], method="pbkdf2:sha256")
    try:
        created = repo().register_customer(
            cleaned["email"], password_hash, cleaned["full_name"], cleaned["phone"],
            cleaned["dob"], cleaned["home_city"], cleaned["branch_id"],
        )
    except BusinessError as exc:
        return error(str(exc), 409, exc.field)

    start_session(created["user_id"], "customer", created["customer_id"], created["full_name"], created["email"])
    audit("SIGNUP", "user", created["user_id"], {"customer_id": created["customer_id"]})
    return jsonify(role="customer"), 201


@bp.post("/logout")
@security.api_login_required()
def logout():
    session.clear()
    return jsonify(ok=True)


# =============================================================== customer
@bp.get("/accounts")
@security.api_login_required("customer")
def accounts():
    rows = repo().list_accounts(security.current_user()["customer_id"])
    return jsonify(accounts=rows)


@bp.get("/transactions")
@security.api_login_required("customer")
def transactions():
    rows = repo().list_transactions(security.current_user()["customer_id"])
    return jsonify(transactions=rows)


@bp.get("/accounts/lookup")
@security.api_login_required("customer")
def lookup_account():
    number = request.args.get("number", "").strip()
    if not ACCOUNT_RE.match(number):
        return error("Enter a valid account number.", 400)
    found = repo().find_account_by_number(number)
    if not found:
        return error("No account found with that number.", 404)
    return jsonify(holder=mask_name(found["holder"]), branch=found["branch_name"], status=found["status"])


@bp.get("/branches")
@security.api_login_required("customer")
def branches():
    return jsonify(branches=repo().list_branches())


@bp.post("/accounts/open")
@security.api_login_required("customer")
def open_account():
    user = security.current_user()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Invalid request.", 400)
    try:
        branch_id = int(data.get("branchId"))
    except (TypeError, ValueError):
        return error("Choose a branch.", 400, "branchId")
    if branch_id not in {b["branch_id"] for b in repo().list_branches()}:
        return error("Choose a branch from the list.", 400, "branchId")
    account_type = data.get("accountType")
    if account_type not in ACCOUNT_TYPES:
        return error("Choose savings or current.", 400, "accountType")
    try:
        account = repo().open_account(user["customer_id"], branch_id, account_type)
    except BusinessError as exc:
        return error(str(exc), 409)
    audit("ACCOUNT_OPENED", "account", account["account_id"], {"account_type": account_type})
    return jsonify(account=account), 201


@bp.post("/deposit")
@security.api_login_required("customer")
def deposit():
    """Simulated 'add funds' so a brand-new account has money to work with in the demo."""
    user = security.current_user()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Invalid request.", 400)
    try:
        account_id = int(data.get("accountId"))
    except (TypeError, ValueError):
        return error("Choose an account.", 400, "accountId")
    try:
        amount = parse_amount(data.get("amount"), MAX_DEPOSIT)
    except ValueError:
        return error("Enter an amount above 0 (up to 10,00,000) with at most 2 decimals.", 400, "amount")
    if not repo().account_owned_by(account_id, user["customer_id"]):
        return error("That account does not belong to you.", 403)
    try:
        txn_id = repo().deposit(account_id, amount, "online", None, "Added funds (demo)")
    except TransferError as exc:
        return error(str(exc), 400)
    audit("DEPOSIT", "transaction", txn_id, {"amount": float(amount), "account_id": account_id})
    return jsonify(txnId=txn_id), 201


@bp.post("/transfer")
@security.api_login_required("customer")
def transfer():
    user = security.current_user()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Invalid request.", 400)

    # ---- validate input ----
    try:
        from_id = int(data.get("fromAccountId"))
    except (TypeError, ValueError):
        return error("Choose an account to send from.", 400)
    to_number = str(data.get("toAccountNumber", "")).strip()
    if not ACCOUNT_RE.match(to_number):
        return error("Enter a valid destination account number.", 400)
    try:
        amount = parse_amount(data.get("amount"))
    except ValueError:
        return error("Enter an amount above 0 (up to 1,00,00,000) with at most 2 decimals.", 400)
    city = str(data.get("city") or "").strip() or None
    if city and not CITY_RE.match(city):
        return error("Enter a valid city name.", 400)
    description = str(data.get("description") or "").strip()[:200] or None

    # ---- authorisation: the source account must belong to this customer ----
    if not repo().account_owned_by(from_id, user["customer_id"]):
        return error("That account does not belong to you.", 403)
    dest = repo().find_account_by_number(to_number)
    if dest is None:
        return error("Destination account not found.", 404)
    if dest["account_id"] == from_id:
        return error("Choose a different destination account.", 400)

    # ---- the atomic transfer happens inside MySQL (stored procedure) ----
    try:
        txn_id = repo().transfer(from_id, dest["account_id"], amount, "online", city, description)
    except TransferError as exc:
        return error(str(exc), 400)

    # ---- the trigger may have raised flags: copy rich context into MongoDB ----
    flags = repo().flags_for_txn(txn_id)
    for f in flags:
        context = {
            "amount": float(amount),
            "channel": "online",
            "device": {
                "type": "web",
                "user_agent": request.headers.get("User-Agent", "unknown")[:200],
                "ip": request.remote_addr or "unknown",
            },
        }
        if city:
            context["geo"] = {"city": city}
        try:
            store().store_alert(
                {
                    "flag_id": f["flag_id"],
                    "txn_id": txn_id,
                    "rule_name": f["rule_name"],
                    "risk_score": f["risk_score"],
                    "severity": severity_for(f["risk_score"]),
                    "context": context,
                }
            )
        except Exception as exc:  # noqa: BLE001 - money data is already safe in MySQL
            log.warning("Could not store alert details in MongoDB: %s", exc)

    audit(
        "TRANSFER", "transaction", txn_id,
        {"amount": float(amount), "from_account_id": from_id, "to_account_id": dest["account_id"],
         "flagged": bool(flags)},
    )
    return jsonify(txnId=txn_id, flagged=bool(flags)), 201


# =============================================================== admin
@bp.get("/admin/flags")
@security.api_login_required("admin")
def admin_flags():
    requested = request.args.get("status")
    status = requested if requested in FLAG_STATUSES else None
    rows = repo().list_flags(status)

    details = {}
    try:
        details = store().load_alerts([r["flag_id"] for r in rows])
    except Exception as exc:  # noqa: BLE001
        log.warning("MongoDB unavailable, returning flags without details: %s", exc)

    for r in rows:
        extra = details.get(r["flag_id"], {})
        r["context"] = extra.get("context")
        r["analyst_notes"] = extra.get("analyst_notes", [])
    return jsonify(flags=rows)


@bp.patch("/admin/flags/<int:flag_id>")
@security.api_login_required("admin")
def review_flag(flag_id):
    user = security.current_user()
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("confirmed", "dismissed"):
        return error("Choose confirm or dismiss.", 400)
    note = str(data.get("note") or "").strip()[:500]
    freeze = data.get("freezeAccount") is True

    result = repo().review_flag(flag_id, status, user["user_id"], freeze)
    if result is None:
        return error("This flag was not found or was already reviewed.", 409)

    try:
        store().review_alert(flag_id, status, note, user["user_id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not update alert in MongoDB: %s", exc)

    audit("FLAG_REVIEWED", "fraud_flag", flag_id, {"from": "open", "to": status})
    if result["frozen_account_id"]:
        audit("ACCOUNT_FROZEN", "account", result["frozen_account_id"],
              {"reason": f"Confirmed fraud on txn {result['txn_id']}"})
    return jsonify(ok=True, frozenAccountId=result["frozen_account_id"])


@bp.patch("/admin/accounts/<int:account_id>")
@security.api_login_required("admin")
def set_account_status(account_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("active", "frozen"):
        return error("Choose active or frozen.", 400)
    if not repo().set_account_status(account_id, status):
        return error("The account was not found or already has that status.", 409)
    audit("ACCOUNT_FROZEN" if status == "frozen" else "ACCOUNT_UNFROZEN", "account", account_id,
          {"reason": "Changed by an administrator"})
    return jsonify(ok=True)


@bp.get("/admin/stats")
@security.api_login_required("admin")
def admin_stats():
    data = repo().stats()
    by_city = []
    try:
        by_city = store().alerts_by_city()
    except Exception as exc:  # noqa: BLE001
        log.warning("MongoDB aggregation failed: %s", exc)
    return jsonify(totals=data["totals"], byRule=data["by_rule"], daily=data["daily"], byCity=by_city)


@bp.get("/admin/activity")
@security.api_login_required("admin")
def admin_activity():
    events = []
    try:
        events = store().recent_audit(12)
    except Exception as exc:  # noqa: BLE001
        log.warning("MongoDB unavailable for audit trail: %s", exc)
    return jsonify(events=events)
