import logging
from datetime import timedelta

from flask import Flask, jsonify, request

from . import security
from .config import get_config, load_env
from .jsonutil import AppJSONProvider


def create_app(test_config=None, repo=None, store=None):
    """repo/store can be injected (the tests do this); by default they use MySQL and MongoDB."""
    load_env()
    cfg = get_config()
    if test_config:
        cfg.update(test_config)

    secret = cfg.get("SECRET_KEY", "")
    if len(secret) < 16:
        raise RuntimeError(
            "SECRET_KEY is missing or too short. Copy .env.example to .env and set SECRET_KEY "
            "to a long random string (at least 16 characters)."
        )

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg.get("COOKIE_SECURE")),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.json = AppJSONProvider(app)
    logging.basicConfig(level=logging.INFO)

    if repo is None:
        from .repo import MySQLRepo

        repo = MySQLRepo(cfg)
    if store is None:
        from .store import MongoStore

        store = MongoStore(cfg["MONGODB_URI"], cfg["MONGODB_DB"])
    app.extensions["repo"] = repo
    app.extensions["store"] = store

    security.init_app(app)

    from .api import bp as api_bp
    from .pages import bp as pages_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(pages_bp)

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found."), 404
        return "Page not found.", 404

    @app.errorhandler(405)
    def bad_method(_):
        return jsonify(error="Method not allowed."), 405

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="Request too large."), 413

    @app.errorhandler(Exception)
    def unexpected(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        app.logger.exception("Unhandled error")
        if request.path.startswith("/api/"):
            return jsonify(error="The service is temporarily unavailable. Please try again."), 500
        return "Something went wrong. Please try again.", 500

    return app
