"""Mentorship rules: capacity, duplicates, self-requests and state transitions."""

import pytest

from mentorship_models import (
    MentorshipRequest,
    REQ_ACCEPTED,
    REQ_CANCELLED,
    REQ_DECLINED,
    REQ_ENDED,
    REQ_PENDING,
)


def json_of(response):
    return response.get_json()


# ---------------------------------------------------------------------------
# Mentor profile
# ---------------------------------------------------------------------------
def test_create_a_mentor_profile(user_factory):
    client, user = user_factory("m1@example.test", "Mentor One")
    response = client.post(
        "/api/mentors/me",
        json={
            "headline": "Backend engineer",
            "bio": "I teach web development.",
            "expertise": "python, sql, flask",
            "years_experience": 6,
            "company": "Acme",
            "max_mentees": 2,
        },
    )
    assert response.status_code == 200
    data = json_of(response)
    assert data["success"] is True
    assert data["mentor"]["expertise"] == ["python", "sql", "flask"]
    assert data["mentor"]["max_mentees"] == 2


def test_mentor_profile_rejects_a_bad_capacity(user_factory):
    client, _ = user_factory("m2@example.test", "Mentor Two")
    response = client.post("/api/mentors/me", json={"max_mentees": 999})
    assert response.status_code == 400
    assert json_of(response)["code"] == "INVALID_MAX_MENTEES"


def test_deactivate_a_mentor_profile(mentor, user_factory):
    mentor_client, mentor_user = mentor
    other_client, _ = user_factory("watcher@example.test", "Watcher")

    response = mentor_client.delete("/api/mentors/me")
    assert response.status_code == 200
    assert json_of(response)["success"] is True

    listing = json_of(other_client.get("/api/mentors"))
    assert all(m["user_id"] != mentor_user["id"] for m in listing["mentors"])


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------
def test_the_directory_hides_the_mentor_from_themselves(mentor, user_factory):
    mentor_client, mentor_user = mentor
    other_client, other_user = user_factory("other@example.test", "Other")

    listing = json_of(other_client.get("/api/mentors"))
    ids = [m["user_id"] for m in listing["mentors"]]
    assert mentor_user["id"] in ids
    assert other_user["id"] not in ids


def test_the_directory_can_be_searched(mentor, user_factory):
    mentor_client, mentor_user = mentor
    other_client, _ = user_factory("searcher@example.test", "Searcher")

    listing = json_of(other_client.get("/api/mentors?q=backend"))
    assert listing["total"] >= 1
    assert all("backend" in (m["headline"] or "").lower() for m in listing["mentors"])


def test_the_directory_can_filter_by_expertise(mentor, user_factory):
    mentor_client, _ = mentor
    other_client, _ = user_factory("exp@example.test", "Expertise")

    listing = json_of(other_client.get("/api/mentors?expertise=flask"))
    assert listing["total"] == 1
    assert listing["mentors"][0]["expertise"] == ["python", "sql", "flask"]


def test_the_match_score_is_explained(mentor, mentee):
    mentor_client, _ = mentor
    mentee_client, _ = mentee

    listing = json_of(mentee_client.get("/api/mentors"))
    entry = listing["mentors"][0]
    assert "match" in entry
    assert "match_reasons" in entry
    assert entry["match"] > 0
    assert entry["match_reasons"]
    # The mentee knows python, html and css and aims at Backend Developer,
    # so python/sql/flask from the mentor's expertise are a genuine overlap.
    labels = " ".join(reason["label"] for reason in entry["match_reasons"])
    assert "python" in labels


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def test_you_cannot_request_yourself(mentor):
    client, user = mentor
    response = client.post("/api/mentorship/requests", json={"mentor_id": user["id"]})
    assert response.status_code == 400
    assert json_of(response)["code"] == "SELF_REQUEST"


def test_requesting_a_mentor_creates_a_pending_request(mentor, user_factory):
    mentor_client, mentor_user = mentor
    mentee_client, mentee_user = user_factory("req@example.test", "Requester")

    response = mentee_client.post(
        "/api/mentorship/requests",
        json={"mentor_id": mentor_user["id"], "message": "Please help"},
    )
    assert response.status_code == 201
    data = json_of(response)
    assert data["request"]["status"] == REQ_PENDING
    assert data["request"]["message"] == "Please help"


def test_a_duplicate_open_request_is_rejected(mentor, user_factory):
    mentor_client, mentor_user = mentor
    mentee_client, _ = user_factory("dupe@example.test", "Dupe")

    mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    response = mentee_client.post(
        "/api/mentorship/requests", json={"mentor_id": mentor_user["id"]}
    )
    assert response.status_code == 409
    assert json_of(response)["code"] == "DUPLICATE_REQUEST"


def test_accepting_creates_the_conversation(mentor, user_factory, app):
    from mentorship_models import Conversation

    mentor_client, mentor_user = mentor
    mentee_client, _ = user_factory("acc@example.test", "Accepter")

    mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
    request_id = incoming["requests"][0]["id"]

    response = mentor_client.post(f"/api/mentorship/requests/{request_id}/accept")
    assert response.status_code == 200
    assert json_of(response)["request"]["status"] == REQ_ACCEPTED

    with app.app_context():
        request = MentorshipRequest.query.get(request_id)
        assert request.conversation is not None
        assert Conversation.query.count() == 1


def test_a_mentor_cannot_exceed_their_capacity(mentor, user_factory):
    mentor_client, mentor_user = mentor
    # max_mentees is 3 on the fixture; the mentor fills the three slots.
    for index in range(3):
        mentee_client, _ = user_factory(f"slot{index}@example.test", f"Slot {index}")
        mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
        incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
        pending = [r for r in incoming["requests"] if r["status"] == REQ_PENDING]
        assert pending
        mentor_client.post(f"/api/mentorship/requests/{pending[0]['id']}/accept")

    extra_client, _ = user_factory("overflow@example.test", "Overflow")
    extra_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
    pending = [r for r in incoming["requests"] if r["status"] == REQ_PENDING]
    response = mentor_client.post(f"/api/mentorship/requests/{pending[0]['id']}/accept")
    assert response.status_code == 409
    assert json_of(response)["code"] == "MENTOR_AT_CAPACITY"


def test_a_mentee_may_have_at_most_three_accepted_mentors(mentor, user_factory, app):
    from extensions import db
    from mentorship_models import MentorProfile

    mentee_client, mentee_user = user_factory("limit@example.test", "Limited")

    mentors = []
    for index in range(4):
        client, user = user_factory(f"mentor{index}@example.test", f"Mentor {index}")
        with app.app_context():
            db.session.add(
                MentorProfile(
                    user_id=user["id"],
                    headline="Test mentor",
                    expertise="python",
                    max_mentees=5,
                )
            )
            db.session.commit()
        mentors.append((client, user))

    for client, user in mentors:
        mentee_client.post("/api/mentorship/requests", json={"mentor_id": user["id"]})

    # The mentor accepts the first three; the fourth is refused.
    accepted = 0
    for client, user in mentors:
        incoming = json_of(client.get("/api/mentorship/requests?box=incoming"))
        pending = [r for r in incoming["requests"] if r["status"] == REQ_PENDING]
        if not pending:
            continue
        response = client.post(f"/api/mentorship/requests/{pending[0]['id']}/accept")
        if response.status_code == 200:
            accepted += 1
        else:
            assert json_of(response)["code"] == "MENTEE_AT_LIMIT"

    assert accepted == 3


def test_decline_and_cancel(mentor, user_factory):
    mentor_client, mentor_user = mentor
    mentee_client, _ = user_factory("dec@example.test", "Decliner")

    mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
    request_id = incoming["requests"][0]["id"]

    response = mentor_client.post(f"/api/mentorship/requests/{request_id}/decline")
    assert response.status_code == 200
    assert json_of(response)["request"]["status"] == REQ_DECLINED

    # A declined request can be replaced by a new one.
    response = mentee_client.post(
        "/api/mentorship/requests", json={"mentor_id": mentor_user["id"]}
    )
    assert response.status_code == 201

    outgoing = json_of(mentee_client.get("/api/mentorship/requests?box=outgoing"))
    pending = [r for r in outgoing["requests"] if r["status"] == REQ_PENDING]
    response = mentee_client.post(f"/api/mentorship/requests/{pending[0]['id']}/cancel")
    assert response.status_code == 200
    assert json_of(response)["request"]["status"] == REQ_CANCELLED


def test_only_the_mentor_can_accept(mentor, user_factory):
    mentor_client, mentor_user = mentor
    mentee_client, _ = user_factory("only@example.test", "Only")

    mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
    request_id = incoming["requests"][0]["id"]

    response = mentee_client.post(f"/api/mentorship/requests/{request_id}/accept")
    assert response.status_code == 403
    assert json_of(response)["code"] == "FORBIDDEN"


def test_ending_a_mentorship_makes_the_conversation_read_only(
    mentor, user_factory, app
):
    from chat_service import ChatError, ChatService

    mentor_client, mentor_user = mentor
    mentee_client, _ = user_factory("end@example.test", "Ender")

    mentee_client.post("/api/mentorship/requests", json={"mentor_id": mentor_user["id"]})
    incoming = json_of(mentor_client.get("/api/mentorship/requests?box=incoming"))
    request_id = incoming["requests"][0]["id"]
    mentor_client.post(f"/api/mentorship/requests/{request_id}/accept")

    outgoing = json_of(mentee_client.get("/api/mentorship/requests?box=outgoing"))
    conversation_id = outgoing["requests"][0]["conversation_id"]
    assert conversation_id

    response = mentee_client.post(f"/api/mentorship/requests/{request_id}/end")
    assert response.status_code == 200
    assert json_of(response)["request"]["status"] == REQ_ENDED

    with app.app_context():
        service = ChatService()
        with pytest.raises(ChatError) as exc:
            service.get_conversation(mentor_user["id"], conversation_id)
        assert exc.value.code == "CONVERSATION_READ_ONLY"
