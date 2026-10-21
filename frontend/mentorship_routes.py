"""Mentorship REST API and pages.

Errors are always shaped ``{"error": "<message>", "code": "SNAKE_CASE"}``.

Business rules enforced server-side:
  * You cannot send a mentorship request to yourself.
  * Only one open (pending or accepted) request per mentee–mentor pair.
  * A mentor cannot accept more than ``max_mentees`` active mentees.
  * A mentee may have at most 3 accepted mentors.
  * Accepting a request creates the conversation; ending it makes the
    conversation read-only.
  * Only the two participants may read a conversation or its messages.
"""

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from careers import career_skills
from extensions import db
from mentorship_models import (
    FINAL_STATUSES,
    MentorshipRequest,
    MentorProfile,
    OPEN_STATUSES,
    REQ_ACCEPTED,
    REQ_CANCELLED,
    REQ_DECLINED,
    REQ_ENDED,
    REQ_PENDING,
)

mentorship_bp = Blueprint("mentorship_bp", __name__)

MAX_ACCEPTED_MENTORS = 3
MAX_REQUEST_MESSAGE = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def error(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


def split_tags(value):
    if not value:
        return []
    return [t.strip().lower() for t in str(value).split(",") if t.strip()]


def mentee_skill_set(user):
    """The mentee's skills: profile skills plus the target role's skills."""
    tags = set()
    profile = getattr(user, "profile", None)
    if profile:
        tags.update(split_tags(profile.skills))
        if profile.career_goal:
            tags.update(s.lower() for s in career_skills(profile.career_goal))
    return tags


def mentee_missing_skills(user):
    """Skills the mentee's target role needs but the mentee does not have."""
    profile = getattr(user, "profile", None)
    if not profile or not profile.career_goal:
        return set()
    required = set(s.lower() for s in career_skills(profile.career_goal))
    return required - mentee_skill_set(user)


def compute_match(mentee, mentor_profile):
    """Explainable match score between a mentee and a mentor.

    The score is a weighted blend of three overlap measures:

        jaccard  – 60 %  how similar the two skill sets are
        coverage – 25 %  how much of the mentee's skill set the mentor covers
        gap      – 15 %  how much of the mentee's *missing* skills the mentor
                          can teach (only counts when there is a gap)

    Returns ``(percentage, reasons)`` where ``reasons`` is a list of
    ``{"label", "kind"}`` dictionaries the UI renders as "why matched" chips.
    """
    mentee_tags = mentee_skill_set(mentee)
    mentor_tags = set(mentor_profile.expertise_list())
    missing = mentee_missing_skills(mentee)

    if not mentee_tags and not mentor_tags:
        return 0, []

    union = mentee_tags | mentor_tags
    overlap = mentee_tags & mentor_tags
    covers_gap = mentor_tags & missing

    jaccard = (len(overlap) / len(union)) if union else 0.0
    coverage = (len(overlap) / len(mentee_tags)) if mentee_tags else 0.0
    gap = (len(covers_gap) / len(missing)) if missing else 0.0

    score = 0.60 * jaccard + 0.25 * coverage + 0.15 * gap
    percentage = int(round(score * 100))

    reasons = []
    if overlap:
        reasons.append(
            {
                "kind": "overlap",
                "label": "Shared skills: " + ", ".join(sorted(overlap)[:6]),
                "tags": sorted(overlap)[:6],
            }
        )
    if covers_gap:
        reasons.append(
            {
                "kind": "gap",
                "label": "Covers your gap: " + ", ".join(sorted(covers_gap)[:6]),
                "tags": sorted(covers_gap)[:6],
            }
        )
    if not reasons:
        reasons.append(
            {"kind": "general", "label": "Mentor in your field", "tags": []}
        )
    return percentage, reasons


def mentor_card(mentor_profile, mentee, include_match=True):
    data = mentor_profile.to_dict()
    if include_match:
        percentage, reasons = compute_match(mentee, mentor_profile)
        data["match"] = percentage
        data["match_reasons"] = reasons
    return data


def _request_to_user_id(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mentor directory
# ---------------------------------------------------------------------------
@mentorship_bp.route("/api/mentors", methods=["GET"])
@login_required
def list_mentors():
    """Searchable directory of mentors."""
    q = (request.args.get("q") or "").strip().lower()
    expertise = (request.args.get("expertise") or "").strip().lower()
    only_matches = request.args.get("match") in ("1", "true", "yes")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 12

    query = MentorProfile.query.filter_by(is_active=True)
    rows = query.order_by(MentorProfile.user_id).all()

    results = []
    for profile in rows:
        if profile.user_id == current_user.id:
            continue

        data = mentor_card(profile, current_user)

        if q:
            haystack = " ".join(
                [
                    data["name"],
                    data["headline"],
                    data["company"],
                    data["bio"],
                    " ".join(data["expertise"]),
                ]
            ).lower()
            if q not in haystack:
                continue

        if expertise:
            wanted = set(split_tags(expertise))
            if not wanted & set(data["expertise"]):
                continue

        if only_matches and data.get("match", 0) < 50:
            continue

        results.append(data)

    results.sort(key=lambda d: (d.get("match", 0), d["name"]), reverse=True)

    start = (page - 1) * per_page
    window = results[start : start + per_page]

    return (
        jsonify(
            {
                "mentors": window,
                "total": len(results),
                "page": page,
                "per_page": per_page,
                "pages": max(1, (len(results) + per_page - 1) // per_page),
            }
        ),
        200,
    )


@mentorship_bp.route("/api/mentors/<int:user_id>", methods=["GET"])
@login_required
def get_mentor(user_id):
    profile = MentorProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        return error("Mentor not found.", "NOT_FOUND", 404)
    return jsonify(mentor_card(profile, current_user)), 200


@mentorship_bp.route("/api/mentors/me", methods=["POST"])
@login_required
def upsert_my_mentor_profile():
    """Create or update my mentor profile."""
    data = request.get_json(silent=True) or {}

    headline = (data.get("headline") or "").strip()
    if len(headline) > 120:
        return error("Headline cannot exceed 120 characters.", "HEADLINE_TOO_LONG")

    bio = (data.get("bio") or "").strip()
    expertise = data.get("expertise")
    if isinstance(expertise, list):
        expertise = ", ".join(str(t).strip() for t in expertise if str(t).strip())
    expertise = (expertise or "").strip()

    max_mentees = data.get("max_mentees", 3)
    try:
        max_mentees = int(max_mentees)
    except (TypeError, ValueError):
        return error("max_mentees must be an integer.", "INVALID_MAX_MENTEES")
    if not 1 <= max_mentees <= 20:
        return error("max_mentees must be between 1 and 20.", "INVALID_MAX_MENTEES")

    years = data.get("years_experience", 0)
    try:
        years = max(0, int(years))
    except (TypeError, ValueError):
        return error("years_experience must be an integer.", "INVALID_YEARS")

    profile = MentorProfile.query.filter_by(user_id=current_user.id).first()
    if profile is None:
        profile = MentorProfile(user_id=current_user.id)
        db.session.add(profile)

    profile.headline = headline
    profile.bio = bio
    profile.expertise = expertise
    profile.years_experience = years
    profile.company = (data.get("company") or "").strip()[:100]
    profile.timezone = (data.get("timezone") or "").strip()[:50]
    profile.availability = (data.get("availability") or "").strip()
    profile.max_mentees = max_mentees
    profile.is_active = bool(data.get("is_active", True))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error("Could not save your mentor profile.", "SAVE_FAILED", 500)

    return jsonify({"success": True, "mentor": profile.to_dict()}), 200


@mentorship_bp.route("/api/mentors/me", methods=["DELETE"])
@login_required
def deactivate_my_mentor_profile():
    """Deactivate (never hard-delete) my mentor profile."""
    profile = MentorProfile.query.filter_by(user_id=current_user.id).first()
    if profile is None:
        return error("You do not have a mentor profile.", "NOT_FOUND", 404)
    profile.is_active = False
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error("Could not deactivate your mentor profile.", "SAVE_FAILED", 500)
    return jsonify({"success": True}), 200


# ---------------------------------------------------------------------------
# Mentorship requests
# ---------------------------------------------------------------------------
@mentorship_bp.route("/api/mentorship/requests", methods=["POST"])
@login_required
def create_mentorship_request():
    data = request.get_json(silent=True) or {}
    mentor_id = _request_to_user_id(data.get("mentor_id"))
    message = (data.get("message") or "").strip()[:MAX_REQUEST_MESSAGE]

    if mentor_id is None:
        return error("mentor_id is required.", "MISSING_MENTOR_ID")

    if mentor_id == current_user.id:
        return error("You cannot request mentorship from yourself.", "SELF_REQUEST", 400)

    mentor_profile = MentorProfile.query.filter_by(user_id=mentor_id).first()
    if mentor_profile is None or not mentor_profile.is_active:
        return error("That mentor is not available.", "MENTOR_UNAVAILABLE", 404)

    existing = MentorshipRequest.query.filter(
        MentorshipRequest.mentee_id == current_user.id,
        MentorshipRequest.mentor_id == mentor_id,
        MentorshipRequest.status.in_(OPEN_STATUSES),
    ).first()
    if existing is not None:
        return error(
            "You already have an open request with this mentor.", "DUPLICATE_REQUEST", 409
        )

    new_request = MentorshipRequest(
        mentee_id=current_user.id, mentor_id=mentor_id, message=message, status=REQ_PENDING
    )
    db.session.add(new_request)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error("Could not create the request.", "SAVE_FAILED", 500)

    return jsonify({"success": True, "request": new_request.to_dict()}), 201


@mentorship_bp.route("/api/mentorship/requests", methods=["GET"])
@login_required
def list_mentorship_requests():
    box = (request.args.get("box") or "incoming").lower()
    if box not in ("incoming", "outgoing"):
        return error("box must be 'incoming' or 'outgoing'.", "INVALID_BOX")

    column = MentorshipRequest.mentor_id if box == "incoming" else MentorshipRequest.mentee_id
    rows = (
        MentorshipRequest.query.filter(column == current_user.id)
        .order_by(MentorshipRequest.created_at.desc())
        .all()
    )
    return jsonify({"box": box, "requests": [r.to_dict() for r in rows]}), 200


def _load_request(request_id):
    row = db.session.get(MentorshipRequest, int(request_id))
    if row is None:
        return None, error("Request not found.", "NOT_FOUND", 404)
    if current_user.id not in (row.mentee_id, row.mentor_id):
        return None, error("You are not part of this request.", "FORBIDDEN", 403)
    return row, None


@mentorship_bp.route("/api/mentorship/requests/<int:request_id>/accept", methods=["POST"])
@login_required
def accept_request(request_id):
    row, failure = _load_request(request_id)
    if failure:
        return failure
    if row.mentor_id != current_user.id:
        return error("Only the mentor can accept a request.", "FORBIDDEN", 403)
    if row.status != REQ_PENDING:
        return error(f"This request is already {row.status}.", "INVALID_TRANSITION", 409)

    mentor_profile = MentorProfile.query.filter_by(user_id=current_user.id).first()
    if mentor_profile is None or not mentor_profile.is_active:
        return error("Your mentor profile is not active.", "MENTOR_INACTIVE", 403)

    if mentor_profile.active_mentee_count() >= mentor_profile.max_mentees:
        return error(
            f"You are at your capacity of {mentor_profile.max_mentees} mentees.",
            "MENTOR_AT_CAPACITY",
            409,
        )

    accepted_count = MentorshipRequest.query.filter_by(
        mentee_id=row.mentee_id, status=REQ_ACCEPTED
    ).count()
    if accepted_count >= MAX_ACCEPTED_MENTORS:
        return error(
            f"A mentee may have at most {MAX_ACCEPTED_MENTORS} active mentors.",
            "MENTEE_AT_LIMIT",
            409,
        )

    row.status = REQ_ACCEPTED
    row.responded_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return error("Could not accept the request.", "SAVE_FAILED", 500)

    # Accepting creates the conversation.
    if row.conversation is None:
        from mentorship_models import Conversation

        db.session.add(Conversation(mentorship_id=row.id))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return error("Could not create the conversation.", "SAVE_FAILED", 500)

    return jsonify({"success": True, "request": row.to_dict()}), 200


@mentorship_bp.route("/api/mentorship/requests/<int:request_id>/decline", methods=["POST"])
@login_required
def decline_request(request_id):
    row, failure = _load_request(request_id)
    if failure:
        return failure
    if row.mentor_id != current_user.id:
        return error("Only the mentor can decline a request.", "FORBIDDEN", 403)
    if row.status != REQ_PENDING:
        return error(f"This request is already {row.status}.", "INVALID_TRANSITION", 409)

    row.status = REQ_DECLINED
    row.responded_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True, "request": row.to_dict()}), 200


@mentorship_bp.route("/api/mentorship/requests/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_request(request_id):
    row, failure = _load_request(request_id)
    if failure:
        return failure
    if row.mentee_id != current_user.id:
        return error("Only the mentee can cancel a request.", "FORBIDDEN", 403)
    if row.status != REQ_PENDING:
        return error(f"This request is already {row.status}.", "INVALID_TRANSITION", 409)

    row.status = REQ_CANCELLED
    row.responded_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True, "request": row.to_dict()}), 200


@mentorship_bp.route("/api/mentorship/requests/<int:request_id>/end", methods=["POST"])
@login_required
def end_mentorship(request_id):
    row, failure = _load_request(request_id)
    if failure:
        return failure
    if current_user.id not in (row.mentee_id, row.mentor_id):
        return error("Only a participant can end a mentorship.", "FORBIDDEN", 403)
    if row.status not in (REQ_ACCEPTED, REQ_PENDING):
        return error(f"This request is already {row.status}.", "INVALID_TRANSITION", 409)

    row.status = REQ_ENDED
    row.responded_at = row.responded_at or datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True, "request": row.to_dict()}), 200


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@mentorship_bp.route("/mentors", methods=["GET"])
@mentorship_bp.route("/mentors.html", methods=["GET"])
@login_required
def mentors_page():
    my_profile = MentorProfile.query.filter_by(user_id=current_user.id).first()
    return render_template("mentors.html", my_mentor_profile=my_profile)


@mentorship_bp.route("/mentors/become", methods=["GET"])
@mentorship_bp.route("/mentors/become.html", methods=["GET"])
@login_required
def become_mentor_page():
    my_profile = MentorProfile.query.filter_by(user_id=current_user.id).first()
    return render_template("become_mentor.html", mentor_profile=my_profile)


@mentorship_bp.route("/mentors/<int:user_id>", methods=["GET"])
@login_required
def mentor_detail_page(user_id):
    profile = MentorProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        return render_template("error.html", code=404, title="Mentor not found",
                               message="This mentor does not exist or has been removed."), 404

    percentage, reasons = compute_match(current_user, profile)
    existing = MentorshipRequest.query.filter(
        MentorshipRequest.mentee_id == current_user.id,
        MentorshipRequest.mentor_id == user_id,
        MentorshipRequest.status.in_(OPEN_STATUSES),
    ).first()

    return render_template(
        "mentor_detail.html",
        mentor=profile,
        match=percentage,
        reasons=reasons,
        existing_request=existing,
    )


@mentorship_bp.route("/mentorship", methods=["GET"])
@mentorship_bp.route("/mentorship.html", methods=["GET"])
@login_required
def mentorship_page():
    incoming = (
        MentorshipRequest.query.filter_by(mentor_id=current_user.id)
        .order_by(MentorshipRequest.created_at.desc())
        .all()
    )
    outgoing = (
        MentorshipRequest.query.filter_by(mentee_id=current_user.id)
        .order_by(MentorshipRequest.created_at.desc())
        .all()
    )

    def decorate(rows):
        """Add the other participant and the conversation id to each row."""
        result = []
        for row in rows:
            other = row.mentor if row.mentee_id == current_user.id else row.mentee
            result.append(
                {
                    "request": row,
                    "id": row.id,
                    "status": row.status,
                    "message": row.message or "",
                    "created_at": row.created_at,
                    "responded_at": row.responded_at,
                    "other": other,
                    "other_name": other.name if other else "Unknown",
                    "other_id": other.id if other else None,
                    "conversation_id": row.conversation.id if row.conversation else None,
                }
            )
        return result

    return render_template(
        "mentorship.html",
        incoming=decorate(incoming),
        outgoing=decorate(outgoing),
        my_mentor_profile=MentorProfile.query.filter_by(user_id=current_user.id).first(),
    )
