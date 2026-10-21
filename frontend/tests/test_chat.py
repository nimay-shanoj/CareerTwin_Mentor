"""Chat behaviour: authorisation, idempotency, sequencing, receipts and limits."""

import uuid

import pytest

from chat_service import ChatError, ChatService


@pytest.fixture()
def chat_pair(mentor, user_factory, app):
    """A mentor and a mentee with an accepted request (so a conversation exists)."""
    from extensions import db
    from mentorship_models import MentorshipRequest, MentorProfile, REQ_ACCEPTED, Conversation

    mentor_client, mentor_user = mentor
    mentee_client, mentee_user = user_factory("chat@example.test", "Chatter")

    request = MentorshipRequest(
        mentee_id=mentee_user["id"],
        mentor_id=mentor_user["id"],
        message="hi",
        status=REQ_ACCEPTED,
    )
    with app.app_context():
        db.session.add(request)
        db.session.commit()
        conversation = Conversation(mentorship_id=request.id)
        db.session.add(conversation)
        db.session.commit()
        conversation_id = conversation.id

    return {
        "mentor": (mentor_client, mentor_user),
        "mentee": (mentee_client, mentee_user),
        "conversation_id": conversation_id,
    }


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def test_a_non_participant_cannot_read_the_messages(chat_pair, user_factory):
    stranger_client, _ = user_factory("stranger@example.test", "Stranger")
    response = stranger_client.get(
        f"/api/conversations/{chat_pair['conversation_id']}/messages"
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "FORBIDDEN"


def test_a_non_participant_cannot_send(chat_pair, user_factory, app):
    stranger_client, stranger = user_factory("sender@example.test", "Sender")
    with app.app_context():
        service = ChatService()
        with pytest.raises(ChatError) as exc:
            service.send(
                stranger["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                "let me in",
            )
        assert exc.value.code == "FORBIDDEN"


def test_the_conversation_list_only_shows_your_own(chat_pair, user_factory):
    stranger_client, _ = user_factory("watcher2@example.test", "Watcher")
    conversations = stranger_client.get("/api/conversations").get_json()["conversations"]
    assert all(
        c["id"] != chat_pair["conversation_id"] for c in conversations
    )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def test_a_message_is_persisted_with_a_sequence_number(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        message, created = service.send(
            mentee_user["id"],
            chat_pair["conversation_id"],
            str(uuid.uuid4()),
            "hello there",
        )
        assert created is True
        assert message.seq == 1
        assert message.body == "hello there"
        assert message.conversation_id == chat_pair["conversation_id"]


def test_sequence_numbers_are_strictly_increasing(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        seqs = []
        for index in range(5):
            message, _ = service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                f"message {index}",
            )
            seqs.append(message.seq)
        assert seqs == [1, 2, 3, 4, 5]


def test_a_repeated_client_msg_id_does_not_duplicate(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    client_msg_id = str(uuid.uuid4())

    with app.app_context():
        service = ChatService()
        first, created_first = service.send(
            mentee_user["id"], chat_pair["conversation_id"], client_msg_id, "only once"
        )
        second, created_second = service.send(
            mentee_user["id"], chat_pair["conversation_id"], client_msg_id, "only once"
        )

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert first.seq == second.seq

        messages = service.fetch_messages(
            mentee_user["id"], chat_pair["conversation_id"]
        )
        assert len(messages) == 1


def test_an_empty_body_is_rejected(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        with pytest.raises(ChatError) as exc:
            service.send(
                mentee_user["id"], chat_pair["conversation_id"], str(uuid.uuid4()), "   "
            )
        assert exc.value.code == "EMPTY_BODY"


def test_an_overlong_body_is_rejected(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        with pytest.raises(ChatError) as exc:
            service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                "x" * 2001,
            )
        assert exc.value.code == "BODY_TOO_LONG"


def test_a_malformed_client_msg_id_is_rejected(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        with pytest.raises(ChatError) as exc:
            service.send(
                mentee_user["id"], chat_pair["conversation_id"], "not-a-uuid", "hi"
            )
        assert exc.value.code == "INVALID_CLIENT_MSG_ID"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_the_rate_limit_trips(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService(limit=5, window=10)
        for index in range(5):
            service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                f"message {index}",
            )
        with pytest.raises(ChatError) as exc:
            service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                "one too many",
            )
        assert exc.value.code == "RATE_LIMITED"
        assert exc.value.status == 429


# ---------------------------------------------------------------------------
# Read receipts
# ---------------------------------------------------------------------------
def test_marking_messages_as_read(chat_pair, app):
    mentor_client, mentor_user = chat_pair["mentor"]
    mentee_client, mentee_user = chat_pair["mentee"]

    with app.app_context():
        service = ChatService()
        service.send(
            mentee_user["id"],
            chat_pair["conversation_id"],
            str(uuid.uuid4()),
            "read me",
        )
        updated = service.mark_read(
            mentor_user["id"], chat_pair["conversation_id"], 1
        )
        assert updated == 1

        messages = service.fetch_messages(
            mentor_user["id"], chat_pair["conversation_id"]
        )
        assert messages[0]["read_at"] is not None


def test_the_unread_count_tracks_new_messages(chat_pair, app):
    mentor_client, mentor_user = chat_pair["mentor"]
    mentee_client, mentee_user = chat_pair["mentee"]

    with app.app_context():
        service = ChatService()
        assert service.unread_count(mentor_user["id"]) == 0
        service.send(
            mentee_user["id"],
            chat_pair["conversation_id"],
            str(uuid.uuid4()),
            "unread",
        )
        assert service.unread_count(mentor_user["id"]) == 1
        service.mark_read(mentor_user["id"], chat_pair["conversation_id"], 1)
        assert service.unread_count(mentor_user["id"]) == 0


# ---------------------------------------------------------------------------
# Paging and catch-up
# ---------------------------------------------------------------------------
def test_messages_can_be_paged_backwards(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        for index in range(10):
            service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                f"message {index}",
            )
        page = service.fetch_messages(
            mentee_user["id"], chat_pair["conversation_id"], before_seq=6, limit=5
        )
        assert [m["seq"] for m in page] == [1, 2, 3, 4, 5]


def test_sync_after_a_reconnect_returns_only_newer_messages(chat_pair, app):
    mentee_client, mentee_user = chat_pair["mentee"]
    with app.app_context():
        service = ChatService()
        for index in range(5):
            service.send(
                mentee_user["id"],
                chat_pair["conversation_id"],
                str(uuid.uuid4()),
                f"message {index}",
            )
        tail = service.fetch_messages(
            mentee_user["id"], chat_pair["conversation_id"], after_seq=3
        )
        assert [m["seq"] for m in tail] == [4, 5]


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
def test_the_conversations_endpoint(chat_pair):
    mentee_client, _ = chat_pair["mentee"]
    response = mentee_client.get("/api/conversations")
    assert response.status_code == 200
    conversations = response.get_json()["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["id"] == chat_pair["conversation_id"]
    assert conversations[0]["other_name"]


def test_the_notifications_summary(chat_pair, app):
    mentor_client, mentor_user = chat_pair["mentor"]
    mentee_client, mentee_user = chat_pair["mentee"]

    with app.app_context():
        service = ChatService()
        service.send(
            mentee_user["id"],
            chat_pair["conversation_id"],
            str(uuid.uuid4()),
            "hello",
        )

    summary = mentor_client.get("/api/notifications/summary").get_json()
    assert summary["unread_messages"] == 1
    assert summary["pending_requests"] == 0
