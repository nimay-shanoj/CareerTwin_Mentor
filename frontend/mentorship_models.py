"""Mentorship domain models.

These are **new** tables. Nothing in the existing schema (`users`, `profiles`,
`quiz_results`, `user_progress`, `resumes`) is modified, so `db.create_all()`
keeps working without a migration tool.

Design notes
------------
* A user is a *mentor* if they have a `MentorProfile` row, and a *mentee*
  otherwise. No flag is added to `users`.
* `Conversation.next_seq` is the per-conversation sequence allocator. It is
  incremented atomically inside the same transaction that inserts the message,
  which is what gives messages a gapless, strictly increasing order.
* `Message` is unique on `(conversation_id, client_msg_id)` so a retried send
  can never create a duplicate.
"""

from datetime import datetime

from extensions import db

# --- Status constants --------------------------------------------------------
REQ_PENDING = "pending"
REQ_ACCEPTED = "accepted"
REQ_DECLINED = "declined"
REQ_CANCELLED = "cancelled"
REQ_ENDED = "ended"

OPEN_STATUSES = (REQ_PENDING, REQ_ACCEPTED)
FINAL_STATUSES = (REQ_DECLINED, REQ_CANCELLED, REQ_ENDED)


def mentor_profile_for(user):
    """The user's mentor profile, or ``None``.

    Works for an anonymous user and for a user row that has not been flushed
    yet.
    """
    if user is None or getattr(user, "id", None) is None:
        return None
    try:
        return MentorProfile.query.filter_by(user_id=user.id).first()
    except Exception:
        return None


def is_mentor(user):
    """True when the user has a *published* mentor profile.

    The role is derived, never stored on ``users``: a user becomes a mentor
    by publishing a mentor profile, and everybody else is a mentee. The two
    roles are not exclusive — a mentor can also ask a mentor for help.
    """
    profile = mentor_profile_for(user)
    return profile is not None and bool(profile.is_active) and not profile.is_draft


def is_mentor_draft(user):
    """True when the user started a mentor profile but never published it."""
    profile = mentor_profile_for(user)
    return profile is not None and bool(profile.is_draft)


class MentorProfile(db.Model):
    __tablename__ = "mentor_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    headline = db.Column(db.String(120), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    # Comma-separated list, exactly like `Profile.skills`.
    expertise = db.Column(db.Text, nullable=True)
    years_experience = db.Column(db.Integer, nullable=True, default=0)
    company = db.Column(db.String(100), nullable=True)
    timezone = db.Column(db.String(50), nullable=True)
    availability = db.Column(db.Text, nullable=True)
    max_mentees = db.Column(db.Integer, nullable=False, default=3)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # A draft is a profile that was started (the user signed up as a mentor)
    # but not published yet: it is hidden from the directory and the user is
    # sent back to the onboarding until they complete it.
    is_draft = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("mentor_profile", uselist=False))

    # --- Helpers ---------------------------------------------------------
    def expertise_list(self):
        """Expertise as a normalised list of lowercase tags."""
        if not self.expertise:
            return []
        return [s.strip().lower() for s in self.expertise.split(",") if s.strip()]

    def active_mentee_count(self):
        """How many accepted mentorships this mentor currently has."""
        return (
            MentorshipRequest.query.filter_by(
                mentor_id=self.user_id, status=REQ_ACCEPTED
            ).count()
        )

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "name": self.user.name if self.user else "",
            "email": self.user.email if self.user else "",
            "headline": self.headline or "",
            "bio": self.bio or "",
            "expertise": self.expertise_list(),
            "years_experience": self.years_experience or 0,
            "company": self.company or "",
            "timezone": self.timezone or "",
            "availability": self.availability or "",
            "max_mentees": self.max_mentees,
            "active_mentees": self.active_mentee_count(),
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<MentorProfile user_id={self.user_id}>"


class MentorshipRequest(db.Model):
    __tablename__ = "mentorship_requests"

    id = db.Column(db.Integer, primary_key=True)
    mentee_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mentor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=REQ_PENDING, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime, nullable=True)

    mentee = db.relationship("User", foreign_keys=[mentee_id], backref="sent_requests")
    mentor = db.relationship("User", foreign_keys=[mentor_id], backref="received_requests")

    conversation = db.relationship(
        "Conversation",
        backref=db.backref("mentorship", uselist=False),
        uselist=False,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "mentee_id": self.mentee_id,
            "mentor_id": self.mentor_id,
            "mentee_name": self.mentee.name if self.mentee else "",
            "mentor_name": self.mentor.name if self.mentor else "",
            "message": self.message or "",
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
            "conversation_id": self.conversation.id if self.conversation else None,
        }

    def __repr__(self):
        return f"<MentorshipRequest {self.mentee_id}->{self.mentor_id} {self.status}>"


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    mentorship_id = db.Column(
        db.Integer,
        db.ForeignKey("mentorship_requests.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_message_at = db.Column(db.DateTime, nullable=True)
    # Sequence allocator for messages inside this conversation.
    next_seq = db.Column(db.Integer, nullable=False, default=1)

    messages = db.relationship(
        "Message",
        backref=db.backref("conversation"),
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def participant_ids(self):
        if not self.mentorship:
            return set()
        return {self.mentorship.mentee_id, self.mentorship.mentor_id}

    def other_participant(self, user_id):
        if not self.mentorship:
            return None
        return self.mentorship.mentor if user_id == self.mentorship.mentee_id else self.mentorship.mentee

    def to_dict(self, for_user_id=None, last_message=None, unread=0):
        other = self.other_participant(for_user_id) if for_user_id else None
        return {
            "id": self.id,
            "mentorship_id": self.mentorship_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "other_user_id": other.id if other else None,
            "other_name": other.name if other else "",
            "last_message": last_message,
            "unread": unread,
        }

    def __repr__(self):
        return f"<Conversation {self.id}>"


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body = db.Column(db.Text, nullable=False)
    # Client-generated id used for idempotent retries.
    client_msg_id = db.Column(db.String(36), nullable=False)
    # Strictly increasing, gapless, per conversation.
    seq = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "conversation_id", "client_msg_id", name="uq_message_conversation_client_msg"
        ),
        db.UniqueConstraint("conversation_id", "seq", name="uq_message_conversation_seq"),
    )

    sender = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "body": self.body,
            "client_msg_id": self.client_msg_id,
            "seq": self.seq,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }

    def __repr__(self):
        return f"<Message conv={self.conversation_id} seq={self.seq}>"
