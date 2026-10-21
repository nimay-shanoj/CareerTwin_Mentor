"""Shared chat business logic.

Both transports (WebSocket via Socket.IO and the hand-written raw TCP server)
call into this module, so validation, authorisation, rate limiting, sequence
allocation, persistence and idempotency behave identically no matter how a
message arrived.
"""

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from extensions import db
from mentorship_models import (
    Conversation,
    Message,
    MentorshipRequest,
    REQ_ACCEPTED,
)


class ChatError(Exception):
    """An error that can be returned to a client verbatim."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self):
        return {"error": self.message, "code": self.code}


class RateLimiter:
    """Simple sliding-window rate limiter, keyed by user id.

    In-memory by design: it protects a single process. Behind multiple
    workers it would need to live in Redis (see docs/NETWORK_ARCHITECTURE.md).
    """

    def __init__(self, limit=20, window=10):
        self.limit = limit
        self.window = window
        self._hits = defaultdict(deque)

    def check(self, key):
        """Raise ChatError when the caller exceeded the limit."""
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            raise ChatError(
                "RATE_LIMITED",
                f"Too many messages. You can send {self.limit} messages per {self.window} seconds.",
                status=429,
            )
        bucket.append(now)
        return len(bucket)

    def reset(self, key=None):
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


class ChatService:
    """Stateless façade over the conversation/message tables."""

    def __init__(self, limit=20, window=10, max_chars=2000):
        self.rate_limiter = RateLimiter(limit=limit, window=window)
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    def get_conversation(self, user_id, conversation_id, require_active=True):
        """Return a conversation the user is allowed to see.

        Raises ChatError(404) when it does not exist and ChatError(403) when
        the user is not one of the two participants.
        """
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError):
            raise ChatError("INVALID_ID", "Invalid conversation id.", status=400)

        conversation = db.session.get(Conversation, conversation_id)
        if conversation is None:
            raise ChatError("NOT_FOUND", "Conversation not found.", status=404)

        if user_id not in conversation.participant_ids():
            raise ChatError("FORBIDDEN", "You are not a participant in this conversation.", status=403)

        if require_active:
            mentorship = conversation.mentorship
            if mentorship is None or mentorship.status != REQ_ACCEPTED:
                raise ChatError(
                    "CONVERSATION_READ_ONLY",
                    "This mentorship is no longer active, so the conversation is read-only.",
                    status=403,
                )
        return conversation

    def is_participant(self, user_id, conversation_id):
        try:
            conversation = db.session.get(Conversation, int(conversation_id))
        except (TypeError, ValueError):
            return False
        return conversation is not None and user_id in conversation.participant_ids()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_body(self, body):
        if body is None or not isinstance(body, str):
            raise ChatError("INVALID_BODY", "Message body must be a string.")
        cleaned = body.strip()
        if not cleaned:
            raise ChatError("EMPTY_BODY", "Message body cannot be empty.")
        if len(cleaned) > self.max_chars:
            raise ChatError(
                "BODY_TOO_LONG", f"Message body cannot exceed {self.max_chars} characters."
            )
        return cleaned

    @staticmethod
    def validate_client_msg_id(client_msg_id):
        if not client_msg_id or not isinstance(client_msg_id, str):
            raise ChatError("MISSING_CLIENT_MSG_ID", "client_msg_id is required.")
        try:
            uuid.UUID(client_msg_id)
        except (ValueError, AttributeError, TypeError):
            raise ChatError("INVALID_CLIENT_MSG_ID", "client_msg_id must be a UUID.")
        return client_msg_id

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def send(self, user_id, conversation_id, client_msg_id, body, check_rate=True):
        """Persist a message and return ``(message, created)``.

        ``created`` is False when an identical ``client_msg_id`` was already
        stored for that conversation – the original message is returned, which
        is what makes retries idempotent.
        """
        client_msg_id = self.validate_client_msg_id(client_msg_id)
        cleaned = self.validate_body(body)
        conversation = self.get_conversation(user_id, conversation_id)

        if check_rate:
            self.rate_limiter.check(user_id)

        # Idempotency: an already-known client_msg_id means this is a retry.
        existing = Message.query.filter_by(
            conversation_id=conversation.id, client_msg_id=client_msg_id
        ).first()
        if existing is not None:
            return existing, False

        # Allocate the sequence number atomically: a single UPDATE that
        # increments the counter in the database, inside the same transaction
        # that inserts the message. The unique (conversation_id, seq)
        # constraint is the final backstop against a duplicate.
        db.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(next_seq=Conversation.next_seq + 1, last_message_at=datetime.utcnow())
        )
        db.session.flush()
        db.session.refresh(conversation)
        seq = conversation.next_seq - 1

        message = Message(
            conversation_id=conversation.id,
            sender_id=user_id,
            body=cleaned,
            client_msg_id=client_msg_id,
            seq=seq,
        )
        db.session.add(message)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Lost a race against a concurrent send with the same id: the
            # other writer won, so return their message.
            existing = Message.query.filter_by(
                conversation_id=conversation.id, client_msg_id=client_msg_id
            ).first()
            if existing is None:
                raise ChatError("SEND_FAILED", "Could not store the message.", status=500)
            return existing, False

        return message, True

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def mark_read(self, user_id, conversation_id, up_to_seq):
        """Mark every message up to ``up_to_seq`` as read by ``user_id``."""
        conversation = self.get_conversation(user_id, conversation_id, require_active=False)
        try:
            up_to_seq = int(up_to_seq)
        except (TypeError, ValueError):
            raise ChatError("INVALID_SEQ", "up_to_seq must be an integer.")

        now = datetime.utcnow()
        updated = (
            Message.query.filter(
                Message.conversation_id == conversation.id,
                Message.seq <= up_to_seq,
                Message.read_at.is_(None),
                Message.sender_id != user_id,
            )
            .update({"read_at": now}, synchronize_session=False)
        )
        if updated:
            db.session.commit()
        return updated

    def fetch_messages(self, user_id, conversation_id, after_seq=None, before_seq=None, limit=30):
        """Return messages for a conversation.

        ``after_seq``  – catch-up after a reconnect (strictly newer messages).
        ``before_seq`` – paging backwards through history.
        """
        conversation = self.get_conversation(user_id, conversation_id, require_active=False)
        limit = max(1, min(int(limit or 30), 100))

        query = Message.query.filter_by(conversation_id=conversation.id)
        if after_seq is not None:
            query = query.filter(Message.seq > int(after_seq))
            query = query.order_by(Message.seq.asc())
        elif before_seq is not None:
            query = query.filter(Message.seq < int(before_seq))
            query = query.order_by(Message.seq.desc())
        else:
            query = query.order_by(Message.seq.desc())

        messages = query.limit(limit).all()
        if before_seq is not None or (after_seq is None and before_seq is None):
            messages = list(reversed(messages))
        return [m.to_dict() for m in messages]

    def list_conversations(self, user_id):
        """All conversations the user takes part in, newest activity first."""
        rows = (
            db.session.query(Conversation)
            .join(MentorshipRequest, Conversation.mentorship_id == MentorshipRequest.id)
            .filter(
                (MentorshipRequest.mentee_id == user_id)
                | (MentorshipRequest.mentor_id == user_id)
            )
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .all()
        )

        result = []
        for conversation in rows:
            last = (
                Message.query.filter_by(conversation_id=conversation.id)
                .order_by(Message.seq.desc())
                .first()
            )
            unread = (
                Message.query.filter_by(conversation_id=conversation.id, read_at=None)
                .filter(Message.sender_id != user_id)
                .count()
            )
            result.append(
                conversation.to_dict(
                    for_user_id=user_id,
                    last_message=last.to_dict() if last else None,
                    unread=unread,
                )
            )
        return result

    def unread_count(self, user_id):
        """Total number of unread messages across every conversation."""
        conversations = self.list_conversations(user_id)
        return sum(c["unread"] for c in conversations)
