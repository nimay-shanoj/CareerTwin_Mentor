"""Connection hub — one registry, two transports.

The WebSocket path (Socket.IO) and the raw TCP path both deliver messages
through this module, so a message sent from the browser reaches a CLI client
and vice versa without either side knowing about the other.

A `Sink` is anything that can receive an event:

    +----------------+      +-------------------+
    |  WebSocketSink |      |      TcpSink      |
    |  (Socket.IO)   |      | (raw TCP frames)  |
    +--------+-------+      +--------+----------+
             |                       |
             +-----------+-----------+
                         |
                    +----v-----+
                    |   Hub    |
                    +----------+
"""

import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict


class Sink(ABC):
    """A single live connection."""

    transport = "unknown"

    def __init__(self, user_id, connection_id=None):
        self.user_id = user_id
        self.connection_id = connection_id
        self.connected_at = time.time()
        self.frames_out = 0
        self.frames_in = 0
        self.bytes_out = 0
        self.bytes_in = 0

    @abstractmethod
    def send(self, event, payload):
        """Deliver a named event with a JSON-serialisable payload."""

    def close(self, reason=""):
        """Best-effort close of the underlying connection."""

    def stats(self):
        return {
            "connection_id": self.connection_id,
            "user_id": self.user_id,
            "transport": self.transport,
            "connected_at": self.connected_at,
            "frames_in": self.frames_in,
            "frames_out": self.frames_out,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
        }


class WebSocketSink(Sink):
    """A Socket.IO connection.

    `socketio.emit` is safe to call from any thread, which is what lets the
    raw TCP server hand a message to a browser that is connected over
    WebSocket.
    """

    transport = "websocket"

    def __init__(self, user_id, socketio, sid, connection_id=None, namespace="/chat"):
        super().__init__(user_id, connection_id or sid)
        self._socketio = socketio
        self.sid = sid
        self.namespace = namespace

    def send(self, event, payload):
        # The namespace matters: the chat clients live in `/chat`, not in the
        # default namespace, so the emit has to name it explicitly.
        self._socketio.emit(event, payload, to=self.sid, namespace=self.namespace)
        self.frames_out += 1
        self.bytes_out += len(str(payload))

    def close(self, reason=""):
        try:
            self._socketio.server.disconnect(self.sid, namespace=self.namespace)
        except Exception:
            pass


class TcpSink(Sink):
    """A raw TCP connection.

    The writer belongs to the TCP server's asyncio loop. When the hub is
    called from another thread (e.g. the Flask/SocketIO thread) the delivery
    is scheduled with `loop.call_soon_threadsafe`, which is the documented way
    to interact with an asyncio loop from another thread.

    `deliver` is a callable ``(event, payload) -> None`` supplied by the TCP
    server. It owns the per-connection write queue, so backpressure (closing
    slow consumers) is enforced in one place.
    """

    transport = "tcp"

    def __init__(self, user_id, loop, deliver, connection_id=None):
        super().__init__(user_id, connection_id)
        self._loop = loop
        self._deliver = deliver
        self._closed = False

    def send(self, event, payload):
        """Called from any thread."""
        self._loop.call_soon_threadsafe(self._deliver, event, payload)

    def close(self, reason=""):
        self._closed = True


class Hub:
    """Registry of live connections, grouped by user."""

    def __init__(self):
        self._lock = threading.RLock()
        self._by_user = defaultdict(list)
        self._all = {}
        self._started_at = time.time()
        self._counters = {
            "messages_delivered": 0,
            "messages_dropped": 0,
        }

    # --- registry ------------------------------------------------------
    def register(self, sink):
        with self._lock:
            self._by_user[sink.user_id].append(sink)
            self._all[sink.connection_id] = sink
        return sink

    def unregister(self, sink):
        with self._lock:
            sinks = self._by_user.get(sink.user_id, [])
            if sink in sinks:
                sinks.remove(sink)
            if not sinks:
                self._by_user.pop(sink.user_id, None)
            self._all.pop(sink.connection_id, None)

    def sinks_for(self, user_id):
        with self._lock:
            return list(self._by_user.get(user_id, []))

    def all_sinks(self):
        with self._lock:
            return list(self._all.values())

    def is_online(self, user_id):
        return bool(self.sinks_for(user_id))

    def online_users(self):
        with self._lock:
            return list(self._by_user.keys())

    def connection_count(self, user_id=None):
        with self._lock:
            if user_id is None:
                return len(self._all)
            return len(self._by_user.get(user_id, []))

    # --- delivery ------------------------------------------------------
    def deliver(self, user_id, event, payload, transports=None):
        """Push an event to every connection of a user.

        ``transports`` optionally restricts delivery to a subset of transports
        (``["tcp"]``, ``["websocket"]`` …). Returns the number of sinks the
        event was handed to.
        """
        sinks = self.sinks_for(user_id)
        delivered = 0
        for sink in sinks:
            if transports is not None and sink.transport not in transports:
                continue
            try:
                sink.send(event, payload)
                delivered += 1
            except Exception:
                with self._lock:
                    self._counters["messages_dropped"] += 1
        with self._lock:
            self._counters["messages_delivered"] += delivered
        return delivered

    def broadcast_to_room(self, room, event, payload):
        """Deliver to every sink of every user in ``room``."""
        delivered = 0
        for user_id in room:
            delivered += self.deliver(user_id, event, payload)
        return delivered

    # --- stats ---------------------------------------------------------
    def stats(self, user_id=None):
        with self._lock:
            counters = dict(self._counters)
            sinks = list(self._all.values())

        by_transport = defaultdict(int)
        bytes_in = bytes_out = 0
        frames_in = frames_out = 0
        for sink in sinks:
            by_transport[sink.transport] += 1
            bytes_in += sink.bytes_in
            bytes_out += sink.bytes_out
            frames_in += sink.frames_in
            frames_out += sink.frames_out

        payload = {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "connections": len(sinks),
            "connections_by_transport": dict(by_transport),
            "websocket_connections": by_transport.get("websocket", 0),
            "tcp_connections": by_transport.get("tcp", 0),
            "online_users": len(self._by_user),
            "frames_in": frames_in,
            "frames_out": frames_out,
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            **counters,
        }

        if user_id is not None:
            payload["your_connections"] = [
                s.stats() for s in self.sinks_for(user_id)
            ]
        return payload


# The application-wide hub. A single instance is shared by the Socket.IO
# namespace and the TCP server.
hub = Hub()
