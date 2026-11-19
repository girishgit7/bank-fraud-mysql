import os
from pathlib import Path


def load_env(path=None):
    """Tiny .env reader so the project needs no extra dependency."""
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_config():
    return {
        "MYSQL_HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "MYSQL_PORT": int(os.environ.get("MYSQL_PORT", "3306")),
        "MYSQL_USER": os.environ.get("MYSQL_USER", "root"),
        "MYSQL_PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
        "MYSQL_DATABASE": os.environ.get("MYSQL_DATABASE", "bank_fraud_db"),
        "MONGODB_URI": os.environ.get("MONGODB_URI", "mongodb://localhost:27017"),
        "MONGODB_DB": os.environ.get("MONGODB_DB", "bank_fraud"),
        "SECRET_KEY": os.environ.get("SECRET_KEY", ""),
        "COOKIE_SECURE": os.environ.get("COOKIE_SECURE", "0") == "1",
    }
