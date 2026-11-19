"""Runs the full web app on in-memory fakes (no MySQL/MongoDB needed): python -m tests.serve_fake

    python -m tests.serve_fake            demo data (customers, flags) to explore the screens
    FRESH=1 python -m tests.serve_fake    a brand-new empty system with one analyst: admin@bank.com
"""
import os

from werkzeug.security import generate_password_hash

from app import create_app
from tests.fakes import FakeRepo, FakeStore

fresh = os.environ.get("FRESH") == "1"
repo = FakeRepo(seeded=not fresh)
if fresh:
    repo.create_admin("admin@bank.com", generate_password_hash("Password@123", method="pbkdf2:sha256:1000"))
app = create_app({"SECRET_KEY": "demo-secret-key-for-fakes-only-0123456789"}, repo=repo, store=FakeStore(seeded=not fresh))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5057")))
