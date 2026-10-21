"""Authentication, authorisation and CSRF tests."""

import re

import pytest

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


def csrf_of(client, path="/register"):
    response = client.get(path)
    match = CSRF_RE.search(response.get_data(as_text=True))
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_creates_a_user_and_signs_in(client, app):
    from models import User

    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "Ada Lovelace",
            "email": "ada@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    with app.app_context():
        user = User.query.filter_by(email="ada@example.test").first()
        assert user is not None
        assert user.check_password("password123")


def test_register_rejects_a_short_password(client):
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "Short",
            "email": "short@example.test",
            "password": "abc",
            "confirm_password": "abc",
        },
    )
    assert response.status_code == 200
    assert "at least 8 characters" in response.get_data(as_text=True)


def test_register_rejects_mismatched_passwords(client):
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "Mismatch",
            "email": "mismatch@example.test",
            "password": "password123",
            "confirm_password": "password124",
        },
    )
    assert response.status_code == 200
    assert "do not match" in response.get_data(as_text=True)


def test_register_rejects_a_duplicate_email(client, app):
    payload = {
        "csrf_token": csrf_of(client),
        "name": "First",
        "email": "dupe@example.test",
        "password": "password123",
        "confirm_password": "password123",
    }
    client.post("/register", data=payload)

    # A second browser (a fresh client) tries the same address.
    second = app.test_client()
    payload["csrf_token"] = csrf_of(second)
    payload["name"] = "Second"
    response = second.post("/register", data=payload)
    assert response.status_code == 200
    assert "already registered" in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
def test_login_with_the_wrong_password_fails(client):
    client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "Login Test",
            "email": "login@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    client.get("/logout")
    response = client.post(
        "/login",
        data={"csrf_token": csrf_of(client, "/login"), "email": "login@example.test", "password": "wrong"},
    )
    assert response.status_code == 200
    assert "Invalid email or password" in response.get_data(as_text=True)


def test_logout_clears_the_session(client):
    client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "Logout Test",
            "email": "logout@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert client.get("/dashboard").status_code == 200
    client.get("/logout")
    assert client.get("/dashboard").status_code == 302


# ---------------------------------------------------------------------------
# Protected pages
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/overview",
        "/profile",
        "/resume",
        "/roadmap",
        "/skillgap",
        "/projects",
        "/quiz",
        "/progress",
        "/mentors",
        "/mentorship",
        "/messages",
        "/network",
    ],
)
def test_protected_pages_redirect_anonymous_users(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "login" in response.headers["Location"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/overview",
        "/api/progress",
        "/api/mentors",
        "/api/conversations",
        "/api/notifications/summary",
        "/api/network/stats",
        "/api/quiz/history",
    ],
)
def test_protected_apis_return_401_json(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.get_json()["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def test_post_without_a_csrf_token_is_rejected(app):
    """A dedicated app with CSRF enabled."""
    from config import Config
    from app import create_app

    class CsrfConfig(Config):
        TESTING = True
        SECRET_KEY = "csrf-test"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        WTF_CSRF_ENABLED = True
        TCP_CHAT_ENABLED = False

    csrf_app = create_app(CsrfConfig)
    csrf_client = csrf_app.test_client()

    response = csrf_client.post(
        "/api/progress/toggle", json={"task_id": 1, "completed": True}
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "CSRF_ERROR"


def test_form_post_without_a_csrf_token_is_rejected(app):
    from config import Config
    from app import create_app

    class CsrfConfig(Config):
        TESTING = True
        SECRET_KEY = "csrf-test"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        WTF_CSRF_ENABLED = True
        TCP_CHAT_ENABLED = False

    csrf_app = create_app(CsrfConfig)
    csrf_client = csrf_app.test_client()

    response = csrf_client.post(
        "/login", data={"email": "x@example.test", "password": "password123"}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Open redirect
# ---------------------------------------------------------------------------
def test_next_parameter_rejects_an_external_host(client):
    response = client.post(
        "/login?next=https://evil.example.com/steal",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "someone@example.test",
            "password": "password123",
        },
    )
    # The login itself fails (no such user) so we stay on the login page and
    # are certainly not redirected to the external host.
    assert "evil.example.com" not in response.headers.get("Location", "")


def test_next_parameter_accepts_a_local_path(user_factory):
    test_client, _ = user_factory("next@example.test", "Next User")
    # Log out and log back in with ?next=.
    test_client.get("/logout")
    token = csrf_of(test_client, "/login")
    response = test_client.post(
        "/login?next=/progress",
        data={"csrf_token": token, "email": "next@example.test", "password": "password123"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/progress")
