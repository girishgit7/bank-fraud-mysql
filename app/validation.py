"""Server-side validation for sign-up. The browser checks the same things for convenience,
but this is the version that counts."""
import re
from datetime import date

MIN_AGE = 18
MAX_AGE = 120

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
NAME_RE = re.compile(r"^[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F .'\-]{1,99}$")
CITY_RE = re.compile(r"^[\w .'\-]{1,60}$")


class FieldError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def age_on(born, today):
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def check_password(password, email=""):
    if len(password) < 8:
        raise FieldError("password", "Use at least 8 characters.")
    if len(password) > 128:
        raise FieldError("password", "Use 128 characters or fewer.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise FieldError("password", "Include at least one letter and one number.")
    if email and password.lower() == email.lower():
        raise FieldError("password", "Your password cannot be the same as your email.")


def validate_registration(data, valid_branch_ids, today=None):
    """Returns cleaned values or raises FieldError(field, message)."""
    today = today or date.today()

    full_name = " ".join(str(data.get("fullName", "")).split())
    if not NAME_RE.match(full_name):
        raise FieldError("fullName", "Enter your full name (letters, spaces, hyphens and apostrophes only).")

    email = str(data.get("email", "")).strip().lower()
    if len(email) > 150 or not EMAIL_RE.match(email):
        raise FieldError("email", "Enter a valid email address.")

    phone = re.sub(r"[\s\-()]", "", str(data.get("phone", "")))
    if phone.startswith("+"):
        phone = phone[1:]
    if not re.fullmatch(r"\d{10,15}", phone):
        raise FieldError("phone", "Enter a phone number with 10 to 15 digits.")

    try:
        dob = date.fromisoformat(str(data.get("dob", "")).strip())
    except ValueError:
        raise FieldError("dob", "Enter your date of birth.")
    age = age_on(dob, today)
    if dob > today or age > MAX_AGE:
        raise FieldError("dob", "Enter a valid date of birth.")
    if age < MIN_AGE:
        raise FieldError("dob", f"You must be at least {MIN_AGE} to open an account.")

    home_city = str(data.get("homeCity", "")).strip()
    if not CITY_RE.match(home_city):
        raise FieldError("homeCity", "Enter your home city.")

    password = str(data.get("password", ""))
    check_password(password, email)
    if password != str(data.get("confirmPassword", "")):
        raise FieldError("confirmPassword", "The two passwords do not match.")

    try:
        branch_id = int(data.get("branchId"))
    except (TypeError, ValueError):
        raise FieldError("branchId", "Choose a branch.")
    if branch_id not in valid_branch_ids:
        raise FieldError("branchId", "Choose a branch from the list.")

    return {
        "full_name": full_name, "email": email, "phone": phone, "dob": dob,
        "home_city": home_city, "password": password, "branch_id": branch_id,
    }
