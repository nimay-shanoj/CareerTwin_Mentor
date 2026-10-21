"""Flask-SocketIO namespace ``/chat``.

This is the browser half of the chat system. It shares `ChatService` with the
raw TCP server, so validation, authorisation, rate limiting, sequence
allocation and idempotency behave identically on both transports.

Delivery model
--------------
Every message is delivered through the :mod:`hub`, not through Socket.IO
rooms. A user has one socket and many conversations, so the client routes
incoming events by ``conversation_id``. The socket still joins ``user:<id>``
and ``conv:<id>`` rooms (that is part of the protocol description) but the
hub is the single delivery path, which keeps frame accounting accurate and
makes the WebSocket and TCP transports symmetric.
"""

import time
from datetime import datetime, timezone

from flask import request
from flask_login import current_user
from flask_socketio import emit, join_room, leave_room

from chat_routes import chat_service
from extensions import socketio
from hub import WebSocketSink, hub
from mentorship_models import MentorshipRequest, REQ_PENDING

NAMESPACE = "/chat"


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------
@socketio.on("connect", namespace=NAMESPACE)
def on_connect(auth=None):
    """Accept the connection only for an authenticated session."""
    if not current_user.is_authenticated:
        # Returning False refuses the handshake.
        return False

    user_id = current_user.id
    join_room(f"user:{user_id}")

    # Join every conversation this user takes part in.
    for conversation in chat_service.list_conversations(user_id):
        join_room(f"conv:{conversation['id']}")

    sink = WebSocketSink(user_id, socketio, request.sid)
    hub.register(sink)

    _broadcast_presence(user_id, True)
    _push_summary(user_id)
    return True


@socketio.on("disconnect", namespace=NAMESPACE)
def on_disconnect(reason=None):
    """Remove the sink and update presence."""
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return

    for sink in hub.sinks_for(user_id):
        if isinstance(sink, WebSocketSink) and sink.sid == request.sid:
            hub.unregister(sink)
            break

    if not hub.is_online(user_id):
        _broadcast_presence(user_id, False)


def _broadcast_presence(user_id, online):
    """Tell everybody who can see this user that their status changed."""
    try:
        for conversation in chat_service.list_conversations(user_id):
            other_id = conversation.get("other_user_id")
            if other_id:
                hub.deliver(other_id, "presence:update", {"user_id": user_id, "online": online})
    except Exception:
        pass


def _push_summary(user_id):
    """Push the bell-badge counters to a user."""
    try:
        pending = MentorshipRequest.query.filter_by(
            mentor_id=user_id, status=REQ_PENDING
        ).count()
        hub.deliver(
            user_id,
            "notify:summary",
            {
                "pending_requests": pending,
                "unread_messages": chat_service.unread_count(user_id),
            },
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@socketio.on("message:send", namespace=NAMESPACE)
def on_message_send(data):
    """Persist a message, then broadcast it.

    Persist-first means the sender's ack always carries the server-assigned
    ``seq``, and a client that reconnects can ask for everything after the
    last ``seq`` it saw.
    """
    if not current_user.is_authenticated:
        return {"ok": False, "code": "UNAUTHORIZED", "message": "Not signed in."}

    data = data or {}
    conversation_id = data.get("conversation_id")
    client_msg_id = data.get("client_msg_id")
    body = data.get("body")

    try:
        message, created = chat_service.send(
            current_user.id, conversation_id, client_msg_id, body
        )
    except Exception as exc:
        code = getattr(exc, "code", "SEND_FAILED")
        message_text = getattr(exc, "message", str(exc))
        emit("error", {"code": code, "message": message_text})
        return {"ok": False, "code": code, "message": message_text}

    if created:
        payload = {"message": message.to_dict()}
        # Deliver to the recipient…
        conversation = message.conversation
        other_id = conversation.other_participant(current_user.id)
        if other_id:
            hub.deliver(other_id.id, "message:new", payload)
        # …and to the sender's other tabs.
        hub.deliver(current_user.id, "message:new", payload, transports=["websocket"])
        _push_summary(current_user.id)
        if other_id:
            _push_summary(other_id.id)

    return {
        "ok": True,
        "created": created,
        "message": message.to_dict(),
    }


@socketio.on("message:read", namespace=NAMESPACE)
def on_message_read(data):
    """Mark messages as read and tell the other participant."""
    if not current_user.is_authenticated:
        return {"ok": False, "code": "UNAUTHORIZED"}

    data = data or {}
    conversation_id = data.get("conversation_id")
    up_to_seq = data.get("up_to_seq", 0)

    try:
        updated = chat_service.mark_read(current_user.id, conversation_id, up_to_seq)
    except Exception as exc:
        return {
            "ok": False,
            "code": getattr(exc, "code", "READ_FAILED"),
            "message": getattr(exc, "message", str(exc)),
        }

    conversation = chat_service.get_conversation(
        current_user.id, conversation_id, require_active=False
    )
    other = conversation.other_participant(current_user.id)
    if other:
        hub.deliver(
            other.id,
            "message:read",
            {
                "conversation_id": conversation.id,
                "up_to_seq": up_to_seq,
                "by": current_user.id,
            },
        )
    _push_summary(current_user.id)
    return {"ok": True, "updated": updated}


@socketio.on("typing:start", namespace=NAMESPACE)
def on_typing_start(data):
    if not current_user.is_authenticated:
        return {"ok": False, "code": "UNAUTHORIZED"}
    _relay_typing(data, True)


@socketio.on("typing:stop", namespace=NAMESPACE)
def on_typing_stop(data):
    if not current_user.is_authenticated:
        return {"ok": False, "code": "UNAUTHORIZED"}
    _relay_typing(data, False)


def _relay_typing(data, is_typing):
    data = data or {}
    conversation_id = data.get("conversation_id")
    try:
        conversation = chat_service.get_conversation(
            current_user.id, conversation_id, require_active=False
        )
    except Exception:
        return {"ok": False, "code": "FORBIDDEN"}

    other = conversation.other_participant(current_user.id)
    if other:
        hub.deliver(
            other.id,
            "typing",
            {
                "conversation_id": conversation.id,
                "user_id": current_user.id,
                "is_typing": is_typing,
            },
        )
    return {"ok": True}


@socketio.on("sync:request", namespace=NAMESPACE)
def on_sync_request(data):
    """Catch-up after a reconnect: return everything after ``after_seq``."""
    if not current_user.is_authenticated:
        return {"ok": False, "code": "UNAUTHORIZED"}

    data = data or {}
    conversation_id = data.get("conversation_id")
    after_seq = data.get("after_seq", 0)
    try:
        after_seq = int(after_seq)
    except (TypeError, ValueError):
        after_seq = 0

    try:
        messages = chat_service.fetch_messages(
            current_user.id, conversation_id, after_seq=after_seq, limit=100
        )
    except Exception as exc:
        return {
            "ok": False,
            "code": getattr(exc, "code", "SYNC_FAILED"),
            "message": getattr(exc, "message", str(exc)),
        }

    return {"ok": True, "messages": messages}


@socketio.on("rtt:ping", namespace=NAMESPACE)
def on_rtt_ping(data):
    """Round-trip time probe used by the Network Inspector.

    The client sends its own timestamp; the server returns it untouched plus
    its own clock, so the browser can compute RTT and clock offset.
    """
    data = data or {}
    return {
        "ok": True,
        "t": data.get("t"),
        # `datetime.now(timezone.utc)` is used on purpose: `utcnow()` returns a
        # naive datetime, and `.timestamp()` would then interpret it as local
        # time, which is off by the UTC offset.
        "server_time": datetime.now(timezone.utc).timestamp() * 1000.0,
        "received_at": time.time(),
    }


@socketio.on("ping", namespace=NAMESPACE)
def on_plain_ping(data):
    """Socket.IO's own liveness ping, answered with a timestamp."""
    return {"server_time": datetime.now(timezone.utc).timestamp() * 1000.0}
