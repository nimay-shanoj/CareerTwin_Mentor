"""Shared pytest fixtures.

The application is a flat module directory (absolute imports such as
``from config import Config``), so the ``frontend`` directory has to be on
``sys.path`` before anything is imported.
"""

import os
import re
import sys

import pytest

FRONTEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")
# The TCP server is started explicitly in the integration test, not by the
# app factory.
os.environ.setdefault("TCP_CHAT_ENABLED", "0")

from config import Config  # noqa: E402
from extensions import db  # noqa: E402

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


class TestConfig(Config):
    TESTING = True
    DEBUG = False
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    TCP_CHAT_ENABLED = False
    SERVER_NAME = "localhost"


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    """A Flask app bound to a temporary SQLite file.

    A *file* database (rather than ``sqlite://``) is used on purpose: the raw
    TCP server runs in its own thread and talks to the same database, and a
    file gives every thread its own connection with proper locking.

    The application context is deliberately **not** held open across the
    yield. An open context would keep ``flask.g`` alive between requests, so
    Flask-Login's cached user would leak from one test into the next.
    """
    from app import create_app

    db_path = tmp_path_factory.mktemp("careertwin-db") / "test.db"

    class SessionConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(db_path)

    application = create_app(SessionConfig)

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


def csrf_from(response):
    """Extract the CSRF token from a rendered page."""
    match = CSRF_RE.search(response.get_data(as_text=True))
    return match.group(1) if match else ""


def csrf_of(client, path="/register"):
    """Fetch a page and return its CSRF token."""
    return csrf_from(client.get(path))


def wipe_database(app):
    """Remove every row from every table."""
    with app.app_context():
        db.session.remove()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture(autouse=True)
def clean_database(app):
    """Give every test an empty database."""
    wipe_database(app)
    yield
    wipe_database(app)


@pytest.fixture()
def client(app):
    """A test client."""
    return app.test_client()


@pytest.fixture()
def user_factory(app):
    """Create signed-in users, each with their own test client.

    ``user_factory("a@example.test", "Alice")`` returns
    ``(test_client, {"id": .., "email": .., "name": ..})``.
    """

    def _make(email="user@example.test", name="Test User", password="password123"):
        from models import User

        test_client = app.test_client()
        response = test_client.get("/register")
        token = csrf_from(response)
        test_client.post(
            "/register",
            data={
                "csrf_token": token,
                "name": name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )
        with app.app_context():
            user = User.query.filter_by(email=email).first()
            user_id = user.id if user else None
        return test_client, {"id": user_id, "email": email, "name": name}

    return _make


@pytest.fixture()
def mentor(user_factory, app):
    """A user with an active mentor profile."""
    from mentorship_models import MentorProfile

    test_client, user = user_factory("mentor@example.test", "Mentor One")
    with app.app_context():
        db.session.add(
            MentorProfile(
                user_id=user["id"],
                headline="Backend engineer",
                bio="I teach web development.",
                expertise="python, sql, flask",
                years_experience=6,
                company="Acme",
                max_mentees=3,
            )
        )
        db.session.commit()
    return test_client, user


@pytest.fixture()
def mentee(user_factory, app):
    """A user with a profile and a career goal."""
    from models import Profile

    test_client, user = user_factory("mentee@example.test", "Mentee One")
    with app.app_context():
        db.session.add(
            Profile(
                user_id=user["id"],
                phone="+91 90000 00000",
                college_name="Example University",
                degree="B.Sc. Computer Science",
                skills="python, html, css",
                career_goal="Backend Developer",
            )
        )
        db.session.commit()
    return test_client, user
