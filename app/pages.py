import logging

from flask import Blueprint, current_app, redirect, render_template, url_for

from . import security

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    user = security.current_user()
    if not user:
        return redirect(url_for("pages.login_page"))
    return redirect(url_for("pages.admin_page" if user["role"] == "admin" else "pages.dashboard_page"))


@bp.get("/login")
def login_page():
    user = security.current_user()
    if user:
        return redirect(url_for("pages.home"))
    return render_template("login.html")


@bp.get("/signup")
def signup_page():
    if security.current_user():
        return redirect(url_for("pages.home"))
    branches, problem = [], False
    try:
        branches = current_app.extensions["repo"].list_branches()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("ledgerwatch").warning("Could not load branches for sign-up: %s", exc)
        problem = True
    return render_template("signup.html", branches=branches, problem=problem)


@bp.get("/dashboard")
@security.page_login_required("customer")
def dashboard_page():
    return render_template("customer.html", user=security.current_user())


@bp.get("/admin")
@security.page_login_required("admin")
def admin_page():
    return render_template("admin.html", user=security.current_user())
