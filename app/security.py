"""CSRF protection, role checks, login throttling and security headers."""
import hmac
import secrets
import threading
import time
from functools import wraps

from flask import abort, jsonify, make_response, redirect, request, session, url_for


# ---------- CSRF ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(make_response(jsonify(error="Your session expired. Refresh the page and try again."), 403))


# ---------- access control ----------
def current_user():
    return session.get("user")


def api_login_required(role=None):
    """Decorator for JSON endpoints: 401 if signed out, 403 for the wrong role, CSRF on writes."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify(error="Please sign in."), 401
            if role and user["role"] != role:
                return jsonify(error="You do not have access to this."), 403
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                check_csrf()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def page_login_required(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("pages.login_page"))
            if user["role"] != role:
                return redirect(url_for("pages.home"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ---------- login throttling (in memory; fine for a single-process demo) ----------
class LoginLimiter:
    def __init__(self, max_failures=5, window_seconds=600):
        self.max_failures = max_failures
        self.window = window_seconds
        self._fails = {}
        self._lock = threading.Lock()

    def _recent(self, key):
        cutoff = time.time() - self.window
        self._fails[key] = [t for t in self._fails.get(key, []) if t > cutoff]
        return self._fails[key]

    def blocked(self, key):
        with self._lock:
            return len(self._recent(key)) >= self.max_failures

    def fail(self, key):
        with self._lock:
            self._recent(key).append(time.time())

    def reset(self, key):
        with self._lock:
            self._fails.pop(key, None)


# ---------- headers ----------
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.extensions["login_limiter"] = LoginLimiter()
    app.extensions["signup_limiter"] = LoginLimiter(max_failures=15, window_seconds=3600)

    @app.after_request
    def add_headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
