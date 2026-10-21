"""CareerTwin application factory.

The route layout defined here is part of the public contract: endpoint names
(``dashboard_page``, ``resume_page``, ``auth.login`` …) and URLs (including the
``.html`` aliases) must keep working, because templates and ``url_for`` calls
depend on them.
"""

import logging
import os
from datetime import datetime

from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError
from itsdangerous import URLSafeTimedSerializer

from chat_tokens import chat_token_store

from config import Config
from extensions import csrf, db, login_manager, socketio


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

from auth import auth_bp
from careers import CAREER_NAMES, CAREERS, careers_bp
from chat_routes import chat_bp
from mentorship_routes import mentorship_bp
from overview_routes import compute_overview, overview_bp
from profile_routes import profile_bp
from progress_routes import progress_bp
from quiz_routes import quiz_bp
from resume_routes import resume_bp

# Human readable title for the app-shell topbar, keyed by endpoint.
PAGE_TITLES = {
    "dashboard_page": "Dashboard",
    "profile_bp.profile": "Profile",
    "resume_page": "Resume analyser",
    "skillgap_page": "Skill gap",
    "roadmap_page": "Roadmap",
    "projects_page": "Projects",
    "quiz_page": "Career quiz",
    "progress_page": "Progress",
    "overview_page": "Insights",
    "mentorship_bp.mentors_page": "Mentor directory",
    "mentorship_bp.mentor_detail_page": "Mentor profile",
    "mentorship_bp.become_mentor_page": "Become a mentor",
    "mentorship_bp.mentorship_page": "Mentorship",
    "auth.mentor_login": "Mentor sign in",
    "chat_bp.messages_page": "Messages",
    "chat_bp.conversation_page": "Messages",
    "chat_bp.network_page": "Network lab",
}


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    config_class.validate()

    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"  # Redirect target for @login_required

    csrf.init_app(app)
    socketio.init_app(
        app,
        async_mode=app.config.get("SOCKETIO_ASYNC_MODE", "threading"),
        cors_allowed_origins=app.config.get("SOCKETIO_CORS_ALLOWED_ORIGINS") or [],
        logger=False,
        engineio_logger=False,
    )

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    app.register_blueprint(auth_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(quiz_bp)
    app.register_blueprint(progress_bp)
    app.register_blueprint(resume_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(careers_bp)
    app.register_blueprint(mentorship_bp)
    app.register_blueprint(chat_bp)

    # The chat handshake tokens are signed with the app secret.
    chat_token_store._serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt="careertwin-chat-token"
    )
    chat_token_store._ttl = app.config.get("CHAT_TOKEN_TTL", 60)

    # `now()` is used by the footer for the copyright year.
    app.jinja_env.globals["now"] = datetime.now

    # Behind a reverse proxy the scheme and the client IP arrive in headers.
    if _as_bool(os.environ.get("TRUST_PROXY"), False):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.context_processor
    def inject_shell_context():
        """Values every template needs: the page title and the badge counts."""
        return {
            "page_title": PAGE_TITLES.get(request.endpoint, "CareerTwin"),
            "nav_notifications": getattr(g, "nav_notifications", None),
            "career_names": CAREER_NAMES,
            "careers": CAREERS,
        }

    # ------------------------------------------------------------------
    # Request hooks
    # ------------------------------------------------------------------
    @app.before_request
    def _load_nav_notifications():
        """Badge counts for the sidebar and the topbar bell.

        Computed once per request and only for logged-in users. Any failure is
        swallowed: a badge is never worth breaking a page for.
        """
        if not current_user.is_authenticated:
            g.nav_notifications = {"pending_requests": 0, "unread_messages": 0}
            return None
        try:
            from chat_routes import chat_service
            from mentorship_models import MentorshipRequest, REQ_PENDING

            g.nav_notifications = {
                "pending_requests": MentorshipRequest.query.filter_by(
                    mentor_id=current_user.id, status=REQ_PENDING
                ).count(),
                "unread_messages": chat_service.unread_count(current_user.id),
            }
        except Exception:
            g.nav_notifications = {"pending_requests": 0, "unread_messages": 0}
        return None

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.unauthorized_handler
    def _unauthorized():
        """Return JSON for API callers, redirect for browsers."""
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
        # `next` is validated server-side in auth.py before it is used.
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.after_request
    def _adapt_session_cookie(response):
        """Mark the session cookie Secure only on encrypted connections.

        Flask sets the flag statically, but the right value depends on how
        the client reached the server: a cookie marked Secure is silently
        dropped by the browser over plain HTTP, which would break the
        two-laptop LAN demo. With ``SESSION_COOKIE_SECURE=auto`` the flag
        follows the scheme of the current request; set it to ``true`` or
        ``false`` to force the behaviour.
        """
        mode = app.config.get("SESSION_COOKIE_SECURE_MODE", "auto")
        header = response.headers.get("Set-Cookie")
        if not header:
            return response

        if mode == "always":
            if "; Secure" not in header:
                response.headers["Set-Cookie"] = header + "; Secure"
            return response

        if mode == "never":
            response.headers["Set-Cookie"] = header.replace("; Secure", "")
            return response

        # auto
        if request.is_secure:
            if "; Secure" not in header:
                response.headers["Set-Cookie"] = header + "; Secure"
        elif "; Secure" in header:
            response.headers["Set-Cookie"] = header.replace("; Secure", "")
        return response

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------
    @app.errorhandler(CSRFError)
    def _csrf_error(exc):
        if request.path.startswith("/api/"):
            return (
                jsonify({"error": "CSRF token missing or invalid", "code": "CSRF_ERROR"}),
                400,
            )
        return render_template(
            "error.html",
            code=400,
            title="Security check failed",
            message="Your session expired or the form was tampered with. Please try again.",
        ), 400

    @app.errorhandler(413)
    def file_too_large(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "File is too large (max 16MB).", "code": "FILE_TOO_LARGE"}), 413
        return "File is too large (max 16MB).", 413

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found", "code": "NOT_FOUND"}), 404
        return render_template(
            "error.html",
            code=404,
            title="Page not found",
            message="The page you are looking for does not exist or has moved.",
        ), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled server error")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error", "code": "SERVER_ERROR"}), 500
        return render_template(
            "error.html",
            code=500,
            title="Something went wrong",
            message="An unexpected error occurred. The team has been notified.",
        ), 500

    # ------------------------------------------------------------------
    # Base routes to render existing frontend pages
    # ------------------------------------------------------------------
    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.route("/")
    @app.route("/landing.html")
    def index():
        return render_template("landing.html")

    @app.route("/dashboard")
    @app.route("/dashboard.html")
    @login_required
    def dashboard_page():
        from mentorship_models import is_mentor

        return render_template(
            "dashboard.html",
            overview=compute_overview(current_user),
            is_mentor=is_mentor(current_user),
        )

    @app.route("/resume")
    @app.route("/resume.html")
    @login_required
    def resume_page():
        return render_template("resume.html")

    @app.route("/roadmap")
    @app.route("/roadmap.html")
    @login_required
    def roadmap_page():
        return render_template("roadmap.html")

    @app.route("/skillgap")
    @app.route("/skillgap.html")
    @login_required
    def skillgap_page():
        return render_template("skillgap.html")

    @app.route("/projects")
    @app.route("/projects.html")
    @login_required
    def projects_page():
        return render_template("projects.html")

    @app.route("/quiz")
    @app.route("/quiz.html")
    @login_required
    def quiz_page():
        return render_template("quiz.html")

    @app.route("/progress")
    @app.route("/progress.html")
    @login_required
    def progress_page():
        return render_template("progress.html")

    @app.route("/overview")
    @app.route("/overview.html")
    @login_required
    def overview_page():
        return render_template("overview.html", overview=compute_overview(current_user))

    # ------------------------------------------------------------------
    # Database initialization
    # ------------------------------------------------------------------
    with app.app_context():
        try:
            # Every model module must be imported before this call so that
            # `db.create_all()` sees the complete metadata.
            import mentorship_models  # noqa: F401  (registers the new tables)

            db.create_all()
            app.logger.info("Database tables verified/created successfully.")
        except Exception as exc:  # pragma: no cover - depends on the environment
            app.logger.error("Error during database initialization: %s", exc)
            app.logger.error(
                "Check that your database is reachable. Set DATABASE_URL in .env "
                "(see .env.example) – the default local fallback is SQLite."
            )

        # Say which database is in use – it is the first thing anybody asks
        # when the app behaves unexpectedly.
        for key, engine in db.engines.items():
            print(f"Database engine {key} -> {engine.url}")

    return app


# Imported at the bottom so `models` is registered on `db` before `create_app()`
# runs `db.create_all()`.
from models import User  # noqa: E402

# Registers the /chat Socket.IO handlers on the shared `socketio` object.
import socket_events  # noqa: E402,F401

app = create_app()


def start_background_services(target_app):
    """Start the raw TCP chat server.

    It runs in its own thread with its own event loop so that the CLI client
    can talk to the same process — and therefore the same database — as the
    web app. Set ``TCP_CHAT_ENABLED=0`` to switch it off.
    """
    if target_app.config.get("TESTING"):
        return None
    if not target_app.config.get("TCP_CHAT_ENABLED"):
        return None
    try:
        from network.tcp_server import start_from_app

        return start_from_app(target_app)
    except Exception as exc:  # pragma: no cover - the web app must still run
        target_app.logger.error("Could not start the TCP chat server: %s", exc)
        return None


start_background_services(app)


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    socketio.run(app, host=host, port=port, debug=app.config["DEBUG"])
