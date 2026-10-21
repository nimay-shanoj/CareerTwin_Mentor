"""Authentication blueprint: register, login, logout.

Form field names (``name``, ``email``, ``password``, ``confirm_password``) are
part of the public contract and must not change.
"""

from urllib.parse import urljoin, urlparse

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from extensions import db
from models import User

auth_bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8


def is_safe_redirect_url(target, host_url):
    """Return True when ``target`` points back at this host.

    Guards against open redirects: only same-host *relative* paths (or an
    explicit match on the current host) are allowed.
    """
    if not target:
        return False
    ref_url = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def _safe_next(default_endpoint="dashboard_page"):
    """Resolve the ``?next=`` parameter to a safe local target."""
    target = request.args.get("next") or request.form.get("next")
    if target and is_safe_redirect_url(target, request.host_url):
        return target
    return url_for(default_endpoint)


@auth_bp.route("/register", methods=["GET", "POST"])
@auth_bp.route("/register.html", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard_page"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        # Simple validations
        if not name or not email or not password or not confirm_password:
            return render_template("register.html", error="All fields are required.")

        if len(password) < MIN_PASSWORD_LENGTH:
            return render_template(
                "register.html",
                error=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
            )

        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.")

        existing_user = User.query.filter(func.lower(User.email) == email).first()
        if existing_user:
            return render_template("register.html", error="Email address already registered.")

        # Create and save user
        new_user = User(name=name, email=email)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        # Log in the user and redirect to dashboard
        login_user(new_user)
        return redirect(_safe_next())

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@auth_bp.route("/login.html", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard_page"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            return render_template("login.html", error="Please enter email and password.")

        user = User.query.filter(func.lower(User.email) == email).first()
        if not user or not user.check_password(password):
            return render_template("login.html", error="Invalid email or password.")

        login_user(user)
        return redirect(_safe_next())

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
