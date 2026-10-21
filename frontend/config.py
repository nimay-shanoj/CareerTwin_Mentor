"""Application configuration.

Every environment-specific value is read from the environment (optionally via a
``.env`` file).  No secret is ever stored in this file.

Database resolution order:
    1. ``DATABASE_URL``            – full SQLAlchemy URL, wins outright.
    2. ``MYSQL_*`` env vars        – builds a MySQL URL (still the default engine).
    3. ``sqlite:///<db>.db``       – local fallback so a fresh clone runs.

The Vercel branch always uses ``/tmp`` for both the database and uploads.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load `.env` if present. Real environment variables always win.
load_dotenv(BASE_DIR / ".env")


def _as_bool(value, default=False):
    """Coerce an environment string to a bool."""
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _is_vercel():
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


_VERCEL = _is_vercel()

# --- Database URL -----------------------------------------------------------
_DB_URL = os.environ.get("DATABASE_URL")
if not _DB_URL:
    if _VERCEL:
        _DB_URL = "sqlite:////tmp/careertwin.db"
    elif os.environ.get("MYSQL_USER"):
        _mysql_user = os.environ["MYSQL_USER"]
        _mysql_pass = os.environ.get("MYSQL_PASSWORD", "")
        _mysql_host = os.environ.get("MYSQL_HOST", "localhost")
        _mysql_port = os.environ.get("MYSQL_PORT", "3306")
        _mysql_db = os.environ.get("MYSQL_DB", "careertwin")
        from urllib.parse import quote_plus

        _DB_URL = (
            f"mysql+pymysql://{quote_plus(_mysql_user)}:{quote_plus(_mysql_pass)}"
            f"@{_mysql_host}:{_mysql_port}/{_mysql_db}"
        )
    else:
        # Local convenience fallback – MySQL stays the default engine for anyone
        # who configures it, but a fresh clone must still start.
        _DB_URL = "sqlite:///" + str(BASE_DIR / "careertwin.db")


class Config:
    """Base configuration shared by every environment."""

    # --- Core -------------------------------------------------------------
    DEBUG = _as_bool(os.environ.get("FLASK_DEBUG"), False)
    TESTING = False

    SECRET_KEY = os.environ.get("SECRET_KEY")

    # --- Database ---------------------------------------------------------
    DATABASE_URL = _DB_URL
    SQLALCHEMY_DATABASE_URI = _DB_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # --- Uploads ----------------------------------------------------------
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or str(
        Path("/tmp/uploads") if _VERCEL else BASE_DIR / "uploads"
    )
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    # --- Sessions & cookies ----------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # The Secure flag has to follow the scheme of the *current* request:
    # a cookie marked Secure is silently dropped by the browser over plain
    # HTTP, which would break the two-laptop LAN demo. With
    # SESSION_COOKIE_SECURE=auto the flag is added by an after_request hook
    # for HTTPS requests only; set it to true/false to force the behaviour.
    _secure_mode = os.environ.get("SESSION_COOKIE_SECURE", "auto").strip().lower()
    if _secure_mode in ("true", "1", "yes", "on"):
        SESSION_COOKIE_SECURE_MODE = "always"
    elif _secure_mode in ("false", "0", "no", "off"):
        SESSION_COOKIE_SECURE_MODE = "never"
    else:
        SESSION_COOKIE_SECURE_MODE = "auto"
    # Flask needs a real boolean here.
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE_MODE == "always"

    # --- CSRF -------------------------------------------------------------
    # ``None`` makes tokens session-scoped: they live as long as the session
    # cookie instead of expiring on a fixed clock.
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_HEADERS = ["X-CSRFToken", "X-CSRF-Token"]

    # --- Chat / networking (Phase 4 & 5) ---------------------------------
    SOCKETIO_ASYNC_MODE = os.environ.get("SOCKETIO_ASYNC_MODE", "threading")
    SOCKETIO_CORS_ALLOWED_ORIGINS = [
        o.strip()
        for o in (os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS") or "").split(",")
        if o.strip()
    ]

    CHAT_TOKEN_TTL = _as_int(os.environ.get("CHAT_TOKEN_TTL"), 60)
    CHAT_RATE_LIMIT_MESSAGES = _as_int(os.environ.get("CHAT_RATE_LIMIT_MESSAGES"), 20)
    CHAT_RATE_LIMIT_WINDOW = _as_int(os.environ.get("CHAT_RATE_LIMIT_WINDOW"), 10)
    CHAT_MAX_MESSAGE_CHARS = _as_int(os.environ.get("CHAT_MAX_MESSAGE_CHARS"), 2000)

    TCP_CHAT_ENABLED = _as_bool(os.environ.get("TCP_CHAT_ENABLED"), True)
    TCP_CHAT_HOST = os.environ.get("TCP_CHAT_HOST", "127.0.0.1")
    TCP_CHAT_PORT = _as_int(os.environ.get("TCP_CHAT_PORT"), 9000)
    TCP_CHAT_MAX_CONNECTIONS = _as_int(os.environ.get("TCP_CHAT_MAX_CONNECTIONS"), 100)
    TCP_CHAT_MAX_PAYLOAD = _as_int(os.environ.get("TCP_CHAT_MAX_PAYLOAD"), 16 * 1024)
    TCP_CHAT_HEARTBEAT = _as_int(os.environ.get("TCP_CHAT_HEARTBEAT"), 5)
    TCP_CHAT_IDLE_TIMEOUT = _as_int(os.environ.get("TCP_CHAT_IDLE_TIMEOUT"), 30)
    TCP_CHAT_HELLO_TIMEOUT = _as_int(os.environ.get("TCP_CHAT_HELLO_TIMEOUT"), 5)
    TCP_CHAT_WRITE_QUEUE_LIMIT = _as_int(os.environ.get("TCP_CHAT_WRITE_QUEUE_LIMIT"), 64)

    @classmethod
    def validate(cls):
        """Fail fast with an actionable message.

        Called from the app factory. A missing ``SECRET_KEY`` is only fatal
        outside debug mode, so local development still works without one.
        """
        if not cls.SECRET_KEY:
            if not cls.DEBUG:
                raise RuntimeError(
                    "SECRET_KEY is not set. Copy .env.example to .env and generate a "
                    "value with:\n"
                    "    python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            cls.SECRET_KEY = "insecure-debug-key-do-not-use-in-production"
            cls.SESSION_COOKIE_SECURE = False
        return cls
