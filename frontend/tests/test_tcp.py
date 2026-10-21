"""End-to-end tests for the raw TCP chat server.

These open a real socket against a real server instance and speak the CTP/1
protocol by hand, so they exercise the framing, the handshake, the state
machine and the cross-transport delivery path.
"""

import json
import os
import socket
import struct
import sys
import uuid

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from network.protocol import (  # noqa: E402
    ACK,
    ERR,
    HELLO,
    HEADER,
    HEADER_SIZE,
    MAGIC,
    MSG,
    PING,
    PONG,
    SEND,
    VERSION,
    WELCOME,
    encode_frame,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def read_exactly(sock, count, timeout=5.0):
    sock.settimeout(timeout)
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock, timeout=5.0):
    header = read_exactly(sock, HEADER_SIZE, timeout)
    magic, version, length = HEADER.unpack(header)
    assert magic == MAGIC
    assert version == VERSION
    body = read_exactly(sock, length, timeout)
    return json.loads(body.decode("utf-8"))


def read_until(sock, wanted, limit=10):
    """Read frames until one of `wanted` arrives."""
    for _ in range(limit):
        frame = read_frame(sock)
        if frame.get("type") in wanted:
            return frame
    raise AssertionError(f"did not receive {wanted}")


class TcpPeer:
    """A tiny hand-written client used by the tests."""

    def __init__(self, port, host="127.0.0.1"):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, payload):
        self.sock.sendall(encode_frame(payload))

    def send_raw(self, data):
        self.sock.sendall(data)

    def frame(self, timeout=5.0):
        return read_frame(self.sock, timeout)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture()
def tcp_server(app):
    """Start a TCP server on an ephemeral port for one test."""
    from network.tcp_server import TcpChatServer

    server = TcpChatServer(
        app,
        host="127.0.0.1",
        port=0,
        hello_timeout=2,
        idle_timeout=5,
        heartbeat=2,
    )
    server.start()
    yield server
    server.stop()


@pytest.fixture()
def token(client, user_factory):
    """A signed-in user and a fresh chat token for them."""
    test_client, user = user_factory("tcp@example.test", "TCP User")
    response = test_client.post("/api/chat/token")
    assert response.status_code == 200
    return test_client, user, response.get_json()["token"]


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------
def test_the_handshake_succeeds(tcp_server, token):
    _, _, chat_token = token
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        frame = peer.frame()
        assert frame["type"] == WELCOME
        assert frame["user"]["id"] is not None
        assert frame["heartbeat_s"] > 0
    finally:
        peer.close()


def test_a_bad_token_is_rejected(tcp_server, token):
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": "not-a-token"})
        frame = peer.frame()
        assert frame["type"] == ERR
        assert frame["code"] in ("TOKEN_INVALID", "TOKEN_EXPIRED", "TOKEN_USED")
    finally:
        peer.close()


def test_a_token_cannot_be_reused(tcp_server, token):
    _, _, chat_token = token

    first = TcpPeer(tcp_server._bound_port)
    try:
        first.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        assert first.frame()["type"] == WELCOME
    finally:
        first.close()

    second = TcpPeer(tcp_server._bound_port)
    try:
        second.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        frame = second.frame()
        assert frame["type"] == ERR
        assert frame["code"] == "TOKEN_USED"
    finally:
        second.close()


def test_hello_must_be_the_first_frame(tcp_server, token):
    _, _, chat_token = token
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": PING, "id": uuid.uuid4().hex, "t": 1})
        frame = peer.frame()
        assert frame["type"] == ERR
    finally:
        peer.close()


def test_a_connection_without_hello_is_dropped(tcp_server, token):
    """The hello timeout closes a connection that never authenticates."""
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.sock.settimeout(10)
        # The server sends an ERR frame and then closes the connection.
        frame = peer.frame(timeout=10)
        assert frame["type"] == ERR
    finally:
        peer.close()


# ---------------------------------------------------------------------------
# Framing errors
# ---------------------------------------------------------------------------
def test_a_bad_magic_gets_an_err_frame_and_a_close(tcp_server, token):
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send_raw(b"XX" + struct.pack(">BI", VERSION, 2) + b"{}")
        frame = peer.frame()
        assert frame["type"] == ERR
        assert frame["code"] == "BAD_MAGIC"
    finally:
        peer.close()


def test_a_bad_version_gets_an_err_frame(tcp_server, token):
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send_raw(MAGIC + struct.pack(">BI", 99, 2) + b"{}")
        frame = peer.frame()
        assert frame["type"] == ERR
        assert frame["code"] == "BAD_VERSION"
    finally:
        peer.close()


def test_an_oversize_payload_gets_an_err_frame(tcp_server, token):
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send_raw(MAGIC + struct.pack(">BI", VERSION, 16 * 1024 + 1))
        frame = peer.frame()
        assert frame["type"] == ERR
        assert frame["code"] == "PAYLOAD_TOO_LARGE"
    finally:
        peer.close()


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
@pytest.fixture()
def conversation(app, user_factory):
    """Two users with an accepted mentorship and a conversation."""
    from extensions import db
    from mentorship_models import Conversation, MentorshipRequest, REQ_ACCEPTED

    mentor_client, mentor = user_factory("tcpmentor@example.test", "TCP Mentor")
    mentee_client, mentee = user_factory("tcpmentee@example.test", "TCP Mentee")

    request = MentorshipRequest(
        mentee_id=mentee["id"],
        mentor_id=mentor["id"],
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
        "mentor": (mentor_client, mentor),
        "mentee": (mentee_client, mentee),
        "conversation_id": conversation_id,
    }


def test_a_message_is_acknowledged_with_a_sequence_number(
    tcp_server, conversation
):
    mentee_client, mentee = conversation["mentee"]
    token_response = mentee_client.post("/api/chat/token")
    chat_token = token_response.get_json()["token"]

    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        assert peer.frame()["type"] == WELCOME

        frame_id = uuid.uuid4().hex
        peer.send(
            {
                "type": SEND,
                "id": frame_id,
                "conversation_id": conversation["conversation_id"],
                "client_msg_id": str(uuid.uuid4()),
                "body": "hello over tcp",
            }
        )
        ack = peer.frame()
        assert ack["type"] == ACK
        assert ack["ref"] == frame_id
        assert ack["seq"] == 1
    finally:
        peer.close()


def test_a_repeated_client_msg_id_is_acknowledged_without_duplicating(
    tcp_server, conversation
):
    mentee_client, mentee = conversation["mentee"]
    chat_token = mentee_client.post("/api/chat/token").get_json()["token"]

    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        peer.frame()

        client_msg_id = str(uuid.uuid4())
        for _ in range(2):
            peer.send(
                {
                    "type": SEND,
                    "id": uuid.uuid4().hex,
                    "conversation_id": conversation["conversation_id"],
                    "client_msg_id": client_msg_id,
                    "body": "only once",
                }
            )
        first = peer.frame()
        second = peer.frame()
        assert first["type"] == ACK
        assert second["type"] == ACK
        assert first["seq"] == second["seq"]
        assert second["created"] is False
    finally:
        peer.close()


def test_ping_is_answered_with_pong(tcp_server, token):
    _, _, chat_token = token
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        peer.frame()
        peer.send({"type": PING, "id": uuid.uuid4().hex, "t": 1234.0})
        frame = read_until(peer.sock, {PONG})
        assert frame["t"] == 1234.0
        assert "server_time" in frame
    finally:
        peer.close()


def test_a_non_participant_cannot_send(tcp_server, conversation, user_factory):
    stranger_client, stranger = user_factory("tcpstranger@example.test", "Stranger")
    chat_token = stranger_client.post("/api/chat/token").get_json()["token"]

    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        peer.frame()
        peer.send(
            {
                "type": SEND,
                "id": uuid.uuid4().hex,
                "conversation_id": conversation["conversation_id"],
                "client_msg_id": str(uuid.uuid4()),
                "body": "let me in",
            }
        )
        frame = peer.frame()
        assert frame["type"] == ERR
        assert frame["code"] == "FORBIDDEN"
    finally:
        peer.close()


# ---------------------------------------------------------------------------
# Cross-transport delivery
# ---------------------------------------------------------------------------
def test_a_tcp_message_reaches_a_websocket_client(tcp_server, conversation, app):
    """The headline demo: a CLI-style TCP client and a browser socket talk."""
    from extensions import socketio

    mentor_client, mentor = conversation["mentor"]
    mentee_client, mentee = conversation["mentee"]

    # The mentor is "the browser": connect a Socket.IO test client with the
    # same Flask session.
    sio_client = socketio.test_client(
        app, flask_test_client=mentor_client, namespace="/chat"
    )
    try:
        assert sio_client.is_connected("/chat")

        # The mentee is "the CLI": a raw TCP client.
        chat_token = mentee_client.post("/api/chat/token").get_json()["token"]
        peer = TcpPeer(tcp_server._bound_port)
        try:
            peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
            assert peer.frame()["type"] == WELCOME

            peer.send(
                {
                    "type": SEND,
                    "id": uuid.uuid4().hex,
                    "conversation_id": conversation["conversation_id"],
                    "client_msg_id": str(uuid.uuid4()),
                    "body": "hello from the command line",
                }
            )
            ack = peer.frame()
            assert ack["type"] == ACK

            received = sio_client.get_received("/chat")
            events = [event for event in received if event["name"] == "message:new"]
            assert events, "the websocket client did not receive the message"
            message = events[-1]["args"][0]["message"]
            assert message["body"] == "hello from the command line"
            assert message["seq"] == ack["seq"]
        finally:
            peer.close()
    finally:
        sio_client.disconnect("/chat")


def test_a_websocket_message_reaches_a_tcp_client(tcp_server, conversation, app):
    """The other direction: browser -> TCP client."""
    from extensions import socketio

    mentor_client, mentor = conversation["mentor"]
    mentee_client, mentee = conversation["mentee"]

    chat_token = mentor_client.post("/api/chat/token").get_json()["token"]
    peer = TcpPeer(tcp_server._bound_port)
    try:
        peer.send({"type": HELLO, "id": uuid.uuid4().hex, "token": chat_token})
        assert peer.frame()["type"] == WELCOME

        sio_client = socketio.test_client(
            app, flask_test_client=mentee_client, namespace="/chat"
        )
        try:
            assert sio_client.is_connected("/chat")
            sio_client.emit(
                "message:send",
                {
                    "conversation_id": conversation["conversation_id"],
                    "client_msg_id": str(uuid.uuid4()),
                    "body": "hello from the browser",
                },
                namespace="/chat",
            )
            # The Socket.IO ack comes back first.
            sio_client.get_received("/chat")

            frame = read_until(peer.sock, {MSG})
            assert frame["message"]["body"] == "hello from the browser"
        finally:
            sio_client.disconnect("/chat")
    finally:
        peer.close()


# ---------------------------------------------------------------------------
# WebSocket authorisation
# ---------------------------------------------------------------------------
def test_an_unauthenticated_socket_is_refused(app):
    from extensions import socketio

    sio_client = socketio.test_client(app, namespace="/chat")
    try:
        assert not sio_client.is_connected("/chat")
    finally:
        if sio_client.is_connected("/chat"):
            sio_client.disconnect("/chat")
