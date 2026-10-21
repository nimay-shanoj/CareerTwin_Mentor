"""Chat REST API: conversations, messages, handshake tokens and network stats.

These endpoints are the HTTP half of the chat system. The WebSocket namespace
(`/chat`) and the raw TCP server both build on the same `ChatService`, so the
rules are identical whichever transport a client uses.
"""

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from chat_service import ChatError, ChatService
from chat_tokens import chat_token_store
from extensions import db
from hub import hub
from mentorship_models import MentorshipRequest, REQ_PENDING

chat_bp = Blueprint("chat_bp", __name__)

chat_service = ChatService()


def error(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@chat_bp.route("/api/conversations", methods=["GET"])
@login_required
def list_conversations():
    return jsonify({"conversations": chat_service.list_conversations(current_user.id)}), 200


@chat_bp.route("/api/conversations/<int:conversation_id>/messages", methods=["GET"])
@login_required
def get_messages(conversation_id):
    before_seq = request.args.get("before_seq")
    after_seq = request.args.get("after_seq")
    limit = request.args.get("limit", 30)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 30

    try:
        messages = chat_service.fetch_messages(
            current_user.id,
            conversation_id,
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
        )
    except ChatError as exc:
        return error(exc.message, exc.code, exc.status)

    return jsonify({"messages": messages}), 200


@chat_bp.route("/api/conversations/<int:conversation_id>/read", methods=["POST"])
@login_required
def mark_read(conversation_id):
    data = request.get_json(silent=True) or {}
    try:
        updated = chat_service.mark_read(
            current_user.id, conversation_id, data.get("up_to_seq", 0)
        )
    except ChatError as exc:
        return error(exc.message, exc.code, exc.status)
    return jsonify({"success": True, "updated": updated}), 200


# ---------------------------------------------------------------------------
# Handshake token for the raw TCP client
# ---------------------------------------------------------------------------
@chat_bp.route("/api/chat/token", methods=["POST"])
@login_required
def issue_chat_token():
    """Mint a single-use token for the raw TCP client."""
    issued = chat_token_store.issue(current_user.id)
    return jsonify(issued), 200


# ---------------------------------------------------------------------------
# Notifications summary (bell badge in the topbar)
# ---------------------------------------------------------------------------
@chat_bp.route("/api/notifications/summary", methods=["GET"])
@login_required
def notifications_summary():
    pending = MentorshipRequest.query.filter_by(
        mentor_id=current_user.id, status=REQ_PENDING
    ).count()
    return (
        jsonify(
            {
                "pending_requests": pending,
                "unread_messages": chat_service.unread_count(current_user.id),
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# Network lab statistics
# ---------------------------------------------------------------------------
@chat_bp.route("/api/network/stats", methods=["GET"])
@login_required
def network_stats():
    """Live server-side view of the chat network.

    Non-admins only see their own connection rows; the aggregate counters are
    global because they carry no personal data.
    """
    stats = hub.stats()
    stats["server_time"] = datetime.utcnow().isoformat()
    stats["your_connections"] = [s.stats() for s in hub.sinks_for(current_user.id)]
    stats["online_users"] = len(hub.online_users())
    return jsonify(stats), 200


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@chat_bp.route("/messages", methods=["GET"])
@chat_bp.route("/messages.html", methods=["GET"])
@login_required
def messages_page():
    return render_template("messages.html")


@chat_bp.route("/messages/<int:conversation_id>", methods=["GET"])
@login_required
def conversation_page(conversation_id):
    try:
        conversation = chat_service.get_conversation(
            current_user.id, conversation_id, require_active=False
        )
    except ChatError as exc:
        return (
            render_template(
                "error.html",
                code=exc.status,
                title="Conversation unavailable",
                message=exc.message,
            ),
            exc.status,
        )

    other = conversation.other_participant(current_user.id)
    return render_template(
        "messages.html",
        conversation=conversation,
        other_user=other,
        read_only=conversation.mentorship.status != "accepted",
    )


@chat_bp.route("/network", methods=["GET"])
@chat_bp.route("/network.html", methods=["GET"])
@login_required
def network_page():
    return render_template("network.html")
