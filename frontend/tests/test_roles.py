"""The student / mentor sign-in split.

There is one account system with two roles. A mentor is a student account
that also has an active mentor profile — the role is never stored on the
``users`` table.
"""

import re

import pytest

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


def csrf_of(client, path="/register"):
    response = client.get(path)
    match = CSRF_RE.search(response.get_data(as_text=True))
    return match.group(1) if match else ""


def register(client, email, role="student", name="Someone"):
    return client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": name,
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
            "role": role,
        },
    )


def make_mentor(client, app, email):
    """Give the signed-in user a published mentor profile.

    A user who signed up as a mentor already has a draft profile, so the
    existing row is updated rather than a second one inserted.
    """
    from extensions import db
    from mentorship_models import MentorProfile
    from models import User

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        profile = MentorProfile.query.filter_by(user_id=user.id).first()
        if profile is None:
            profile = MentorProfile(user_id=user.id)
            db.session.add(profile)
        profile.headline = "Test mentor"
        profile.expertise = "python, sql"
        profile.max_mentees = 3
        profile.is_active = True
        profile.is_draft = False
        db.session.commit()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_a_student_lands_on_the_dashboard(client):
    response = register(client, "student@example.test", role="student")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard.html")


def test_a_mentor_lands_on_the_mentor_onboarding(client):
    response = register(client, "mentor@example.test", role="mentor")
    assert response.status_code == 302
    assert "/mentors/become" in response.headers["Location"]


def test_the_role_field_is_optional(client):
    """Existing clients that do not send a role still register as students."""
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_of(client),
            "name": "No Role",
            "email": "norole@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard.html")


def test_an_unknown_role_falls_back_to_student(client):
    response = register(client, "weird@example.test", role="wizard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard.html")


def test_the_sign_up_page_offers_both_roles(client):
    body = client.get("/register").get_data(as_text=True)
    assert 'name="role" value="student"' in body
    assert 'name="role" value="mentor"' in body


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------
def test_a_student_signs_in_to_the_dashboard(client):
    register(client, "student2@example.test", role="student")
    client.get("/logout")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "student2@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard.html")


def test_a_mentor_signs_in_to_the_request_inbox(client, app):
    register(client, "mentor2@example.test", role="mentor")
    make_mentor(client, app, "mentor2@example.test")
    client.get("/logout")

    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "mentor2@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mentorship.html")


def test_the_mentor_sign_in_page_exists(client):
    response = client.get("/mentor/login")
    assert response.status_code == 200
    assert "Mentor sign in" in response.get_data(as_text=True)


def test_the_mentor_sign_in_alias_works(client):
    assert client.get("/mentor/login.html").status_code == 200


def test_a_mentor_signs_in_through_the_mentor_page(client, app):
    register(client, "mentor3@example.test", role="mentor")
    make_mentor(client, app, "mentor3@example.test")
    client.get("/logout")

    response = client.post(
        "/mentor/login",
        data={
            "csrf_token": csrf_of(client, "/mentor/login"),
            "email": "mentor3@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mentorship.html")


def test_the_mentor_page_onboards_an_account_without_a_profile(client):
    register(client, "mentor4@example.test", role="mentor")
    client.get("/logout")

    response = client.post(
        "/mentor/login",
        data={
            "csrf_token": csrf_of(client, "/mentor/login"),
            "email": "mentor4@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert "/mentors/become" in response.headers["Location"]


def test_the_mentor_page_rejects_a_bad_password(client):
    response = client.post(
        "/mentor/login",
        data={
            "csrf_token": csrf_of(client, "/mentor/login"),
            "email": "nobody@example.test",
            "password": "wrong",
        },
    )
    assert response.status_code == 200
    assert "Invalid email or password" in response.get_data(as_text=True)


def test_a_next_parameter_still_wins(client, app):
    register(client, "mentor5@example.test", role="mentor")
    make_mentor(client, app, "mentor5@example.test")
    client.get("/logout")

    response = client.post(
        "/mentor/login?next=/messages",
        data={
            "csrf_token": csrf_of(client, "/mentor/login"),
            "email": "mentor5@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/messages")


# ---------------------------------------------------------------------------
# The role is derived, never stored
# ---------------------------------------------------------------------------
def test_the_users_table_has_no_role_column(app):
    from extensions import db

    with app.app_context():
        columns = {c.name for c in db.metadata.tables["users"].columns}
    assert "role" not in columns
    assert "is_mentor" not in columns


def test_is_mentor_reflects_the_profile(client, app):
    from mentorship_models import is_mentor
    from models import User

    register(client, "derived@example.test", role="mentor")
    with app.app_context():
        user = User.query.filter_by(email="derived@example.test").first()
        # Signed up as a mentor, but the profile is only a draft -> not a
        # mentor in the directory sense.
        assert is_mentor(user) is False
        make_mentor(client, app, "derived@example.test")
        user = User.query.filter_by(email="derived@example.test").first()
        assert is_mentor(user) is True


# ---------------------------------------------------------------------------
# The draft lifecycle
# ---------------------------------------------------------------------------
def test_signing_up_as_a_mentor_creates_a_hidden_draft(client, app):
    from extensions import db
    from mentorship_models import MentorProfile

    register(client, "draft@example.test", role="mentor")
    with app.app_context():
        profile = MentorProfile.query.filter_by(user_id=1).first()
        assert profile is not None
        assert profile.is_draft is True
        assert profile.is_active is False

    # A draft must not appear in the directory.
    other_client, _ = user_factory_solo(app, "watcher@example.test")
    listing = other_client.get("/api/mentors").get_json()
    assert all(m["user_id"] != 1 for m in listing["mentors"])


def user_factory_solo(app, email):
    """A plain signed-in user, for directory checks."""
    from models import User

    test_client = app.test_client()
    token = csrf_of(test_client)
    test_client.post(
        "/register",
        data={
            "csrf_token": token,
            "name": "Watcher",
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    return test_client, {"email": email}


def test_completing_the_onboarding_publishes_the_profile(client, app):
    from extensions import db
    from mentorship_models import MentorProfile

    register(client, "publish@example.test", role="mentor")
    response = client.post(
        "/api/mentors/me",
        json={
            "headline": "Backend engineer",
            "expertise": "python, sql",
            "max_mentees": 2,
        },
    )
    assert response.status_code == 200

    with app.app_context():
        profile = MentorProfile.query.filter_by(user_id=1).first()
        assert profile.is_draft is False
        assert profile.is_active is True


def test_an_empty_profile_is_never_published(client, app):
    from extensions import db
    from mentorship_models import MentorProfile

    register(client, "empty@example.test", role="mentor")
    client.post("/api/mentors/me", json={"headline": "", "expertise": ""})

    with app.app_context():
        profile = MentorProfile.query.filter_by(user_id=1).first()
        assert profile.is_draft is True
        assert profile.is_active is False


def test_a_draft_mentor_lands_on_the_onboarding_after_a_normal_login(client, app):
    register(client, "sticky@example.test", role="mentor")
    client.get("/logout")

    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "sticky@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert "/mentors/become" in response.headers["Location"]


def test_a_deactivated_mentor_lands_on_the_dashboard(client, app):
    register(client, "off@example.test", role="mentor")
    make_mentor(client, app, "off@example.test")
    client.get("/logout")

    # Deactivate the profile.
    client.post(
        "/login",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "off@example.test",
            "password": "password123",
        },
    )
    client.delete("/api/mentors/me")
    client.get("/logout")

    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_of(client, "/login"),
            "email": "off@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard.html")


# ---------------------------------------------------------------------------
# The two sides can meet
# ---------------------------------------------------------------------------
def test_a_student_and_a_mentor_end_up_in_a_conversation(client, app, user_factory):
    """The whole point: two logins, one chat."""
    from extensions import db
    from mentorship_models import MentorshipRequest, REQ_ACCEPTED, Conversation

    # The student.
    student_client, student = user_factory("student5@example.test", "Student Five")
    # The mentor, with a profile.
    mentor_client, mentor = user_factory("mentor6@example.test", "Mentor Six")
    make_mentor(mentor_client, app, "mentor6@example.test")

    # The student asks.
    response = student_client.post(
        "/api/mentorship/requests",
        json={"mentor_id": mentor["id"], "message": "Can you help me?"},
    )
    assert response.status_code == 201
    request_id = response.get_json()["request"]["id"]

    # The mentor accepts, on the mentor side.
    response = mentor_client.post(f"/api/mentorship/requests/{request_id}/accept")
    assert response.status_code == 200

    with app.app_context():
        request = MentorshipRequest.query.get(request_id)
        assert request.status == REQ_ACCEPTED
        assert request.conversation is not None
        conversation_id = request.conversation.id

    # Both sides see the same conversation.
    for side in (student_client, mentor_client):
        conversations = side.get("/api/conversations").get_json()["conversations"]
        assert any(c["id"] == conversation_id for c in conversations)

    # And they can talk.
    response = student_client.post(
        "/api/chat/token", headers={"X-CSRFToken": ""}
    )
    assert response.status_code == 200
