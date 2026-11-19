"""Creates a fraud-analyst (admin) account. Analysts cannot sign up on the website, by design.

    python scripts/create_admin.py
    python scripts/create_admin.py --email analyst@yourbank.com     (you are still asked for the password)
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

from app.config import get_config, load_env  # noqa: E402
from app.errors import BusinessError  # noqa: E402
from app.validation import EMAIL_RE, FieldError, check_password  # noqa: E402

load_env()
cfg = get_config()

email = ""
if "--email" in sys.argv:
    i = sys.argv.index("--email")
    email = sys.argv[i + 1].strip().lower() if i + 1 < len(sys.argv) else ""
while not email:
    email = input("Analyst email: ").strip().lower()
if len(email) > 150 or not EMAIL_RE.match(email):
    sys.exit("That does not look like a valid email address.")

password = getpass.getpass("Password (typing is hidden): ")
if password != getpass.getpass("Repeat password: "):
    sys.exit("The two passwords do not match. Nothing was created.")
try:
    check_password(password, email)
except FieldError as exc:
    sys.exit(f"{exc.message} Nothing was created.")

try:
    from app.repo import MySQLRepo

    user_id = MySQLRepo(cfg).create_admin(email, generate_password_hash(password, method="pbkdf2:sha256"))
except BusinessError as exc:
    sys.exit(str(exc))
except ImportError:
    sys.exit("PyMySQL is not installed. Run:  pip install -r requirements.txt")
except Exception as exc:  # noqa: BLE001
    sys.exit(f"Could not reach MySQL: {exc}\nCheck the MYSQL_* values in .env and that schema.sql has been loaded.")

print(f"Analyst account created (user id {user_id}). Sign in at http://localhost:5001 with {email}.")
