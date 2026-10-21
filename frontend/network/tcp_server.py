"""The raw TCP chat server.

This is a genuine, hand-written application-layer protocol running directly on
top of TCP — no chat library is involved. See ``docs/PROTOCOL.md`` for the
wire format and ``docs/NETWORK_ARCHITECTURE.md`` for the layer mapping.

The server runs in its own thread with its own asyncio event loop, started
from the Flask app factory when ``TCP_CHAT_ENABLED=1``.

Single-process limitation
-------------------------
The message hub lives in the process that owns the sockets, so a message sent
over WebSocket is only visible to raw TCP clients connected to *the same*
process. Scaling out needs a shared bus (Redis pub/sub via Socket.IO's
``message_queue``); that path is deliberately not built here.
"""

import asyncio
import json
import logging
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime

from network.protocol import (
    ACK,
    BYE,
    ERR,
    HELLO,
    MSG,
    PING,
    PONG,
    PRESENCE,
    READ,
    SEND,
    TYPING,
    WELCOME,
    MAX_PAYLOAD,
    ProtocolError,
    encode_frame,
    error_frame,
    read_frame,
)
from hub import TcpSink, hub

log = logging.getLogger("careertwin.tcp")

# Socket.io event name -> protocol frame type.
EVENT_TO_TYPE = {
    "message:new": MSG,
    "message:read": READ,
    "typing": TYPING,
    "presence:update": PRESENCE,
}

STATE_CONNECTED = "CONNECTED"
STATE_AUTHENTICATED = "AUTHENTICATED"
STATE_CLOSING = "CLOSING"


class TcpConnection:
    """One client connection and its state machine.

    ``CONNECTED`` -> ``AUTHENTICATED`` -> ``CLOSING``

    A connection that does not send a valid ``HELLO`` within the hello
    timeout is dropped, and a connection that stops sending frames for longer
    than the idle timeout is dropped as well.
    """

    def __init__(self, server, reader, writer, peer):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.peer = peer
        self.id = uuid.uuid4().hex[:12]
        self.state = STATE_CONNECTED
        self.user_id = None
        self.user_name = ""
        self.sink = None
        self.created_at = time.time()
        self.last_frame_at = time.time()
        self.frames_in = 0
        self.frames_out = 0
        self.bytes_in = 0
        self.bytes_out = 0
        self._queue = None
        self._writer_task = None
        self._closing = False

    # -- lifecycle ----------------------------------------------------
    def start(self):
        self._queue = asyncio.Queue(maxsize=self.server.write_queue_limit)
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def _writer_loop(self):
        """Drain the write queue.

        A bounded queue is the backpressure mechanism: when a client stops
        reading, the queue fills up and the connection is closed instead of
        letting the server buffer without limit.
        """
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    break
                self.writer.write(frame)
                await self.writer.drain()
                self.frames_out += 1
                self.bytes_out += len(frame)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("writer loop ended: %s", exc)

    def enqueue(self, frame):
        """Queue a frame for writing. Called from the connection's own loop."""
        if self._closing:
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning(
                "slow consumer dropped conn=%s user=%s (write queue full)",
                self.id, self.user_id,
            )
            self.server.schedule_close(self, "backpressure")

    def deliver(self, event, payload):
        """Deliver a hub event to this client.

        This is called from *another* thread through ``TcpSink``, but the
        ``call_soon_threadsafe`` in the sink guarantees it runs inside this
        connection's event loop.
        """
        frame_type = EVENT_TO_TYPE.get(event)
        if frame_type is None:
            return
        body = {"type": frame_type, "id": uuid.uuid4().hex}
        if isinstance(payload, dict):
            body.update(payload)
        else:
            body["payload"] = payload
        try:
            self.enqueue(encode_frame(body))
        except ProtocolError as exc:
            log.warning("cannot encode %s: %s", event, exc)

    async def send(self, payload):
        """Queue a frame built by the server itself."""
        try:
            self.enqueue(encode_frame(payload))
        except ProtocolError as exc:
            log.warning("cannot encode frame: %s", exc)

    async def send_error(self, code, message, ref=None):
        await self.send(error_frame(code, message, ref))
        await asyncio.sleep(0)  # let the writer drain a little
        await self.close(f"error:{code}")

    async def close(self, reason=""):
        if self.state == STATE_CLOSING:
            return
        self.state = STATE_CLOSING
        log.info(
            "close conn=%s user=%s reason=%s frames_in=%s frames_out=%s bytes_in=%s bytes_out=%s",
            self.id, self.user_id, reason, self.frames_in, self.frames_out,
            self.bytes_in, self.bytes_out,
        )
        if self.sink is not None:
            self.server.hub.unregister(self.sink)
            self.sink = None
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if self._writer_task is not None:
            self._writer_task.cancel()
        try:
            self.writer.close()
        except Exception:
            pass


class TcpChatServer:
    """The asyncio TCP chat server."""

    def __init__(
        self,
        app,
        host="127.0.0.1",
        port=9000,
        max_connections=100,
        max_payload=MAX_PAYLOAD,
        heartbeat=5,
        idle_timeout=30,
        hello_timeout=5,
        write_queue_limit=64,
    ):
        self.app = app
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.max_payload = max_payload
        self.heartbeat = heartbeat
        self.idle_timeout = idle_timeout
        self.hello_timeout = hello_timeout
        self.write_queue_limit = write_queue_limit

        self.hub = hub
        self._loop = None
        self._thread = None
        self._server = None
        self._connections = set()
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._bound_port = None

    # -- lifecycle ----------------------------------------------------
    def start(self):
        """Start the server in a daemon thread with its own event loop."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._thread_main, name="ct-tcp-server", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=10)
        log.info("TCP chat server listening on %s:%s", self.host, self._bound_port)

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:  # pragma: no cover - defensive
            log.error("TCP server stopped: %s", exc)
        finally:
            self._ready.set()

    async def _serve(self):
        self._loop = asyncio.get_running_loop()

        # Graceful shutdown on SIGINT/SIGTERM.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._loop.add_signal_handler(sig, self._request_stop)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                pass

        self._server = await asyncio.start_server(
            self._handle,
            host=self.host,
            port=self.port,
            reuse_address=True,
            backlog=64,
        )
        self._bound_port = self._server.sockets[0].getsockname()[1]
        self._ready.set()

        async with self._server:
            await self._server.serve_forever()

    def _request_stop(self):
        self._stopping.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._schedule_server_stop)

    def _schedule_server_stop(self):
        if self._server is not None:
            self._server.close()

    def stop(self):
        """Stop the server and close every connection."""
        self._stopping.set()
        if self._loop is not None and self._server is not None:
            try:
                self._loop.call_soon_threadsafe(self._schedule_server_stop)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def schedule_close(self, connection, reason):
        """Close a connection from inside its own event loop."""
        if connection.state == STATE_CLOSING:
            return
        asyncio.create_task(connection.close(reason))

    # -- connection handling ------------------------------------------
    async def _handle(self, reader, writer):
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                # Nagle would batch small writes and add latency to chat
                # messages; keepalive detects a peer that vanished without a
                # FIN.
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass

        peer = writer.get_extra_info("peername")
        if len(self._connections) >= self.max_connections:
            log.warning("refusing connection from %s: limit reached", peer)
            try:
                writer.write(encode_frame(error_frame("SERVER_FULL", "Too many connections.")))
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return

        connection = TcpConnection(self, reader, writer, peer)
        self._connections.add(connection)
        connection.start()

        log.info(
            "connect conn=%s peer=%s total=%s", connection.id, peer, len(self._connections)
        )

        try:
            await self._session(connection)
        except asyncio.CancelledError:
            await connection.close("cancelled")
        except (ConnectionResetError, BrokenPipeError):
            await connection.close("reset")
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("connection error: %s", exc)
            await connection.close("error")
        finally:
            self._connections.discard(connection)
            await connection.close("done")

    async def _session(self, connection):
        """The read loop of one connection."""
        while not self._stopping.is_set():
            # -- idle / hello timeout ---------------------------------
            timeout = (
                self.hello_timeout
                if connection.state == STATE_CONNECTED
                else self.idle_timeout
            )
            try:
                frame = await asyncio.wait_for(
                    read_frame(connection.reader, self.max_payload), timeout=timeout
                )
            except asyncio.TimeoutError:
                if connection.state == STATE_CONNECTED:
                    await connection.send_error(
                        "HELLO_TIMEOUT", "No HELLO frame received in time."
                    )
                else:
                    await connection.send_error(
                        "IDLE_TIMEOUT", "No frame received within the idle timeout."
                    )
                return
            except asyncio.IncompleteReadError:
                return  # peer closed the connection
            except ProtocolError as exc:
                await connection.send_error(exc.code, exc.message)
                return

            connection.last_frame_at = time.time()
            connection.frames_in += 1

            await self._dispatch(connection, frame)

            if connection.state == STATE_CLOSING:
                return

    async def _dispatch(self, connection, frame):
        frame_type = (frame.get("type") or "").upper()

        if connection.state == STATE_CONNECTED and frame_type != HELLO:
            await connection.send_error(
                "HELLO_REQUIRED", "The first frame must be HELLO."
            )
            return

        if frame_type == HELLO:
            await self._on_hello(connection, frame)
        elif frame_type == SEND:
            await self._on_send(connection, frame)
        elif frame_type == READ:
            await self._on_read(connection, frame)
        elif frame_type == TYPING:
            await self._on_typing(connection, frame)
        elif frame_type == PING:
            await connection.send(
                {"type": PONG, "id": uuid.uuid4().hex, "t": frame.get("t"),
                 "server_time": datetime.utcnow().timestamp() * 1000.0}
            )
        elif frame_type == BYE:
            await connection.send({"type": BYE, "id": uuid.uuid4().hex})
            await connection.close("client bye")
        else:
            await connection.send_error(
                "UNKNOWN_TYPE", f"Unknown frame type {frame_type!r}."
            )

    # -- frame handlers -----------------------------------------------
    async def _on_hello(self, connection, frame):
        if connection.state == STATE_AUTHENTICATED:
            await connection.send_error(
                "ALREADY_AUTHENTICATED", "This connection is already authenticated."
            )
            return

        token = frame.get("token")
        try:
            from chat_tokens import chat_token_store

            with self.app.app_context():
                user_id = chat_token_store.redeem(token)
                user = self._load_user(user_id)
        except Exception as exc:
            code = getattr(exc, "code", "TOKEN_INVALID")
            message = getattr(exc, "message", str(exc))
            await connection.send_error(code, message)
            return

        if user is None:
            await connection.send_error("TOKEN_INVALID", "Unknown user.")
            return

        connection.user_id = user.id
        connection.user_name = user.name
        connection.state = STATE_AUTHENTICATED

        connection.sink = TcpSink(
            user.id, self._loop, connection.deliver, connection_id=connection.id
        )
        self.hub.register(connection.sink)

        await connection.send(
            {
                "type": WELCOME,
                "id": uuid.uuid4().hex,
                "user": {"id": user.id, "name": user.name},
                "server_time": datetime.utcnow().timestamp() * 1000.0,
                "heartbeat_s": self.heartbeat,
                "connection_id": connection.id,
            }
        )

        log.info(
            "auth conn=%s user=%s(%s) transport=tcp", connection.id, user.name, user.id
        )

        # Tell the other participants that this user is online.
        self._broadcast_presence(user.id, True)

    async def _on_send(self, connection, frame):
        conversation_id = frame.get("conversation_id")
        client_msg_id = frame.get("client_msg_id")
        body = frame.get("body")
        ref = frame.get("id")

        try:
            from chat_routes import chat_service

            with self.app.app_context():
                message, created = chat_service.send(
                    connection.user_id, conversation_id, client_msg_id, body
                )
                message_dict = message.to_dict()
                conversation = message.conversation
                other = conversation.other_participant(connection.user_id)
                other_id = other.id if other else None
        except Exception as exc:
            code = getattr(exc, "code", "SEND_FAILED")
            message_text = getattr(exc, "message", str(exc))
            await connection.send(error_frame(code, message_text, ref))
            return

        await connection.send(
            {"type": ACK, "id": uuid.uuid4().hex, "ref": ref, "seq": message_dict["seq"],
             "message_id": message_dict["id"], "created": created}
        )

        if created and other_id is not None:
            self.hub.deliver(other_id, "message:new", {"message": message_dict})

    async def _on_read(self, connection, frame):
        conversation_id = frame.get("conversation_id")
        up_to_seq = frame.get("up_to_seq", 0)
        try:
            from chat_routes import chat_service

            with self.app.app_context():
                chat_service.mark_read(connection.user_id, conversation_id, up_to_seq)
                conversation = chat_service.get_conversation(
                    connection.user_id, conversation_id, require_active=False
                )
                other = conversation.other_participant(connection.user_id)
                other_id = other.id if other else None
        except Exception as exc:
            code = getattr(exc, "code", "READ_FAILED")
            message_text = getattr(exc, "message", str(exc))
            await connection.send(error_frame(code, message_text, frame.get("id")))
            return

        if other_id is not None:
            self.hub.deliver(
                other_id,
                "message:read",
                {
                    "conversation_id": conversation_id,
                    "up_to_seq": up_to_seq,
                    "by": connection.user_id,
                },
            )

    async def _on_typing(self, connection, frame):
        conversation_id = frame.get("conversation_id")
        is_typing = bool(frame.get("is_typing", True))
        try:
            from chat_routes import chat_service

            with self.app.app_context():
                conversation = chat_service.get_conversation(
                    connection.user_id, conversation_id, require_active=False
                )
                other = conversation.other_participant(connection.user_id)
                other_id = other.id if other else None
        except Exception:
            return

        if other_id is not None:
            self.hub.deliver(
                other_id,
                "typing",
                {
                    "conversation_id": conversation_id,
                    "user_id": connection.user_id,
                    "is_typing": is_typing,
                },
            )

    # -- helpers -------------------------------------------------------
    def _load_user(self, user_id):
        from models import User

        return self.app.extensions["sqlalchemy"].session.get(User, user_id)

    def _broadcast_presence(self, user_id, online):
        try:
            from chat_routes import chat_service

            with self.app.app_context():
                for conversation in chat_service.list_conversations(user_id):
                    other_id = conversation.get("other_user_id")
                    if other_id:
                        self.hub.deliver(
                            other_id, "presence:update", {"user_id": user_id, "online": online}
                        )
        except Exception:
            pass


def start_from_app(app):
    """Start the TCP server from the Flask app factory.

    Returns the server instance, or ``None`` when ``TCP_CHAT_ENABLED`` is off.
    """
    if not app.config.get("TCP_CHAT_ENABLED"):
        log.info("TCP chat server disabled (TCP_CHAT_ENABLED=0)")
        return None

    server = TcpChatServer(
        app,
        host=app.config.get("TCP_CHAT_HOST", "127.0.0.1"),
        port=int(app.config.get("TCP_CHAT_PORT", 9000)),
        max_connections=int(app.config.get("TCP_CHAT_MAX_CONNECTIONS", 100)),
        max_payload=int(app.config.get("TCP_CHAT_MAX_PAYLOAD", MAX_PAYLOAD)),
        heartbeat=int(app.config.get("TCP_CHAT_HEARTBEAT", 5)),
        idle_timeout=int(app.config.get("TCP_CHAT_IDLE_TIMEOUT", 30)),
        hello_timeout=int(app.config.get("TCP_CHAT_HELLO_TIMEOUT", 5)),
        write_queue_limit=int(app.config.get("TCP_CHAT_WRITE_QUEUE_LIMIT", 64)),
    )
    server.start()
    app.extensions["tcp_chat_server"] = server
    return server
