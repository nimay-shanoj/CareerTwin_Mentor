"""Authentication blueprint: register, login, mentor login, logout.

There is **one** account system with **two roles**:

* a **student** (mentee) — the default; they browse mentors, send requests
  and chat with their mentor;
* a **mentor** — a student account that also has an active mentor profile;
  they receive requests and can accept them.

The role is derived from the mentor profile, never stored on ``users``, so
no existing table changes. The sign-up form simply asks which role you want
and sends a new mentor to the mentor onboarding instead of the dashboard.

Form field names (``name``, ``email``, ``password``, ``confirm_password``)
are part of the public contract and must not change. ``role`` is additive.
"""

from urllib.parse import urljoin, urlparse

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from extensions import db
from mentorship_models import is_mentor, is_mentor_draft, mentor_profile_for
from models import User

auth_bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8

ROLE_STUDENT = "student"
ROLE_MENTOR = "mentor"


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


def _safe_next(default=None):
    """Resolve the ``?next=`` parameter to a safe local target.

    ``default`` is used when there is no (valid) ``next`` parameter.
    """
    target = request.args.get("next") or request.form.get("next")
    if target and is_safe_redirect_url(target, request.host_url):
        return target
    return default or url_for("dashboard_page")


def _landing_for(user):
    """Where a signed-in user belongs.

    * a published mentor lands in the request inbox,
    * a mentor who has not finished the onboarding lands back there,
    * everybody else lands on the dashboard.
    """
    if is_mentor(user):
        return url_for("mentorship_bp.mentorship_page")
    if is_mentor_draft(user):
        return url_for("mentorship_bp.become_mentor_page", welcome="1")
    return url_for("dashboard_page")


@auth_bp.route("/register", methods=["GET", "POST"])
@auth_bp.route("/register.html", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_landing_for(current_user))

    role = (request.form.get("role") or ROLE_STUDENT).strip().lower()
    if role not in (ROLE_STUDENT, ROLE_MENTOR):
        role = ROLE_STUDENT

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        # Simple validations
        if not name or not email or not password or not confirm_password:
            return render_template(
                "register.html", error="All fields are required.", role=role
            )

        if len(password) < MIN_PASSWORD_LENGTH:
            return render_template(
                "register.html",
                error=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
                role=role,
            )

        if password != confirm_password:
            return render_template(
                "register.html", error="Passwords do not match.", role=role
            )

        existing_user = User.query.filter(func.lower(User.email) == email).first()
        if existing_user:
            return render_template(
                "register.html",
                error="Email address already registered.",
                role=role,
            )

        # Create and save user
        new_user = User(name=name, email=email)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        # Log in the user. A new mentor goes straight to the mentor
        # onboarding; a student goes to the dashboard.
        login_user(new_user)
        if role == ROLE_MENTOR:
            # A draft profile records the intent to mentor. It stays hidden
            # from the directory until the onboarding is completed.
            if mentor_profile_for(new_user) is None:
                from mentorship_models import MentorProfile

                db.session.add(
                    MentorProfile(
                        user_id=new_user.id,
                        is_active=False,
                        is_draft=True,
                    )
                )
                db.session.commit()
            return redirect(
                _safe_next(url_for("mentorship_bp.become_mentor_page", welcome="1"))
            )
        return redirect(_safe_next(url_for("dashboard_page")))

    return render_template("register.html", role=role)


@auth_bp.route("/login", methods=["GET", "POST"])
@auth_bp.route("/login.html", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_landing_for(current_user))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            return render_template(
                "login.html", error="Please enter email and password."
            )

        user = User.query.filter(func.lower(User.email) == email).first()
        if not user or not user.check_password(password):
            return render_template(
                "login.html", error="Invalid email or password."
            )

        login_user(user)
        return redirect(_safe_next(_landing_for(user)))

    return render_template("login.html")


@auth_bp.route("/mentor/login", methods=["GET", "POST"])
@auth_bp.route("/mentor/login.html", methods=["GET", "POST"])
def mentor_login():
    """Sign in to the mentor side of CareerTwin.

    It is the same account system — a mentor is simply a student account
    that also has a mentor profile. The difference is where you land: a
    mentor goes to the request inbox, and an account without a mentor
    profile is sent to the mentor onboarding.
    """
    if current_user.is_authenticated:
        return redirect(_landing_for(current_user))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            return render_template(
                "mentor_login.html", error="Please enter email and password."
            )

        user = User.query.filter(func.lower(User.email) == email).first()
        if not user or not user.check_password(password):
            return render_template(
                "mentor_login.html", error="Invalid email or password."
            )

        login_user(user)
        if is_mentor(user):
            return redirect(
                _safe_next(url_for("mentorship_bp.mentorship_page"))
            )
        # Signed in, but no published mentor profile yet — send them to the
        # onboarding so they can finish it.
        return redirect(url_for("mentorship_bp.become_mentor_page", welcome="1"))

    return render_template("mentor_login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
