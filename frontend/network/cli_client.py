"""Command line client for the CareerTwin raw TCP chat server.

The client is deliberately written with nothing but the standard library's
``socket`` module: it builds and parses the frames by hand, which makes it a
readable demonstration of the protocol.

Flow
----
1. Log in over HTTP (the browser session is not used on the TCP channel).
2. Ask the server for a single-use chat token.
3. Open a TCP connection and redeem the token with a ``HELLO`` frame.

Usage::

    python -m network.cli_client \\
        --host 127.0.0.1 --port 9000 \\
        --base-url http://127.0.0.1:5000 \\
        --email you@example.com

Commands
--------
    /list                 list your conversations
    /open <conversation>  open a conversation and show its history
    /whoami               show the authenticated user
    /help                 show this help
    /quit                 disconnect and exit

Anything else is sent as a message to the currently open conversation.
"""

import argparse
import json
import os
import re
import socket
import struct
import sys
import threading
import time
import uuid

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
    HEADER,
    HEADER_SIZE,
    MAGIC,
    VERSION,
    ProtocolError,
    encode_frame,
)

# Reconnect backoff: 1s -> 30s, with jitter.
BACKOFF = [1, 2, 4, 8, 15, 30]

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------
def supports_colour():
    return sys.stdout.isatty()


def colour(text, code):
    if not supports_colour():
        return text
    return f"\033[{code}m{text}\033[0m"


def info(message):
    print(colour(message, "36"))


def warn(message):
    print(colour(message, "33"))


def error(message):
    print(colour(message, "31"))


def success(message):
    print(colour(message, "32"))


# ---------------------------------------------------------------------------
# HTTP side: log in and fetch a chat token
# ---------------------------------------------------------------------------
def http_login(base_url, email, password):
    """Log in over HTTP and return ``(session_cookie, csrf_token)``."""
    import requests

    session = requests.Session()

    login_page = session.get(f"{base_url}/login", timeout=10)
    match = CSRF_RE.search(login_page.text)
    if not match:
        raise RuntimeError("Could not find a CSRF token on the login page.")
    csrf_token = match.group(1)

    response = session.post(
        f"{base_url}/login",
        data={
            "csrf_token": csrf_token,
            "email": email,
            "password": password,
        },
        timeout=10,
        allow_redirects=False,
    )
    if response.status_code not in (302, 303):
        raise RuntimeError("Login failed – check the email and password.")

    return session, csrf_token


def fetch_chat_token(session, base_url, csrf_token):
    """Ask the server for a single-use TCP handshake token."""
    response = session.post(
        f"{base_url}/api/chat/token",
        headers={"X-CSRFToken": csrf_token},
        timeout=10,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not get a chat token: {response.text[:200]}")
    return response.json()["token"]


def fetch_conversations(session, base_url, csrf_token):
    response = session.get(
        f"{base_url}/api/conversations", headers={"X-CSRFToken": csrf_token}, timeout=10
    )
    if response.status_code != 200:
        return []
    return response.json().get("conversations", [])


def fetch_messages(session, base_url, csrf_token, conversation_id, limit=30):
    response = session.get(
        f"{base_url}/api/conversations/{conversation_id}/messages?limit={limit}",
        headers={"X-CSRFToken": csrf_token},
        timeout=10,
    )
    if response.status_code != 200:
        return []
    return response.json().get("messages", [])


# ---------------------------------------------------------------------------
# The TCP client
# ---------------------------------------------------------------------------
class TcpClient:
    """A minimal, hand-written client for the CTP/1 protocol."""

    def __init__(self, host, port, token, on_event=None):
        self.host = host
        self.port = port
        self.token = token
        self.on_event = on_event or (lambda kind, payload: None)
        self.sock = None
        self.connected = False
        self.user = None
        self.heartbeat = 5
        self._stop = threading.Event()
        self._reader = None
        self._last_rtt = None

    # -- connection ---------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(1.0)
        self.connected = True

        self.send_frame({"type": HELLO, "id": uuid.uuid4().hex, "token": self.token})

        frame = self.read_frame(timeout=10)
        if frame is None or frame.get("type") != WELCOME:
            raise RuntimeError(f"Handshake rejected: {frame}")

        self.user = frame.get("user")
        self.heartbeat = frame.get("heartbeat_s", 5) or 5
        return frame

    def close(self):
        self.connected = False
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.sendall(encode_frame({"type": BYE, "id": uuid.uuid4().hex}))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- framing ------------------------------------------------------
    def send_frame(self, payload):
        if self.sock is None:
            raise RuntimeError("Not connected.")
        data = encode_frame(payload)
        self.sock.sendall(data)
        return len(data)

    def read_frame(self, timeout=None):
        """Read exactly one frame, or return None on timeout/close."""
        if self.sock is None:
            return None
        deadline = time.monotonic() + (timeout if timeout is not None else 1.0)
        try:
            header = self._read_exactly(HEADER_SIZE, deadline)
        except (ConnectionError, OSError, TimeoutError):
            return None
        if header is None:
            return None

        magic, version, length = HEADER.unpack(header)
        if magic != MAGIC:
            raise ProtocolError("BAD_MAGIC", f"Unexpected magic {magic!r}")
        if version != VERSION:
            raise ProtocolError("BAD_VERSION", f"Unexpected version {version}")

        body = self._read_exactly(length, deadline)
        if body is None:
            return None
        return json.loads(body.decode("utf-8"))

    def _read_exactly(self, count, deadline):
        """Read exactly ``count`` bytes or return None.

        This is the client-side counterpart of ``readexactly``: TCP may
        deliver the bytes in any number of chunks, so we keep reading until we
        have them all.
        """
        chunks = []
        remaining = count
        while remaining > 0:
            if time.monotonic() > deadline:
                return None
            try:
                chunk = self.sock.recv(remaining)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- background loops ---------------------------------------------
    def start_reader(self):
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    def _reader_loop(self):
        while not self._stop.is_set() and self.connected:
            try:
                frame = self.read_frame(timeout=1.0)
            except ProtocolError as exc:
                error(f"Protocol error: {exc}")
                break
            except (OSError, ValueError):
                break
            if frame is None:
                continue
            self._handle(frame)

    def _handle(self, frame):
        kind = frame.get("type")
        if kind == MSG:
            message = frame.get("message", {})
            self.on_event("message", message)
        elif kind == ACK:
            self.on_event("ack", frame)
        elif kind == READ:
            self.on_event("read", frame)
        elif kind == TYPING:
            self.on_event("typing", frame)
        elif kind == PRESENCE:
            self.on_event("presence", frame)
        elif kind == PONG:
            rtt = self._measure_rtt(frame)
            self.on_event("pong", {"rtt": rtt, "frame": frame})
        elif kind == ERR:
            self.on_event("error", frame)
        elif kind == WELCOME:
            self.on_event("welcome", frame)
        else:
            self.on_event("unknown", frame)

    def _measure_rtt(self, pong_frame):
        now = time.time() * 1000.0
        sent = pong_frame.get("t")
        if sent:
            self._last_rtt = now - sent
        return self._last_rtt

    def ping(self):
        try:
            self.send_frame({"type": PING, "id": uuid.uuid4().hex, "t": time.time() * 1000.0})
        except (OSError, RuntimeError):
            pass


# ---------------------------------------------------------------------------
# The interactive shell
# ---------------------------------------------------------------------------
def format_time(value):
    if not value:
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(time.mktime(
            time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        )))
    except (ValueError, TypeError):
        return ""


def run(args):
    password = args.password or os.environ.get("CHAT_PASSWORD")
    if not password:
        import getpass

        password = getpass.getpass("Password: ")

    base_url = args.base_url.rstrip("/")
    info(f"Logging in to {base_url} as {args.email} …")
    session, csrf_token = http_login(base_url, args.email, password)
    token = fetch_chat_token(session, base_url, csrf_token)
    success("Got a chat token.")

    client = TcpClient(args.host, args.port, token, on_event=on_event)
    state = {"conversation": None}

    attempt = 0
    while True:
        try:
            client.connect()
        except (OSError, RuntimeError) as exc:
            error(f"Could not connect: {exc}")
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)] + (0.25 * attempt)
            attempt += 1
            warn(f"Retrying in {wait:.1f}s …")
            time.sleep(wait)
            # The handshake token is single use, so a new one is needed.
            try:
                token = fetch_chat_token(session, base_url, csrf_token)
                client.token = token
            except Exception:
                pass
            continue

        attempt = 0
        success(
            f"Connected to {args.host}:{args.port} as {client.user.get('name')} "
            f"(id {client.user.get('id')})."
        )
        info(f"Type /help for commands. Heartbeat every {client.heartbeat}s.")

        client.start_reader()
        stop_ping = threading.Event()

        def ping_loop():
            while not stop_ping.is_set():
                time.sleep(max(1, client.heartbeat // 2 or 1))
                client.ping()

        pinger = threading.Thread(target=ping_loop, daemon=True)
        pinger.start()

        reason = shell(client, session, base_url, csrf_token, state)

        stop_ping.set()
        client.close()
        if reason == "quit":
            return 0
        warn("Disconnected. Reconnecting …")
        try:
            token = fetch_chat_token(session, base_url, csrf_token)
            client.token = token
        except Exception:
            pass


def on_event(kind, payload):
    if kind == "message":
        who = "you" if payload.get("sender_id") == payload.get("_self") else f"user {payload.get('sender_id')}"
        info(f"  ← [{payload.get('seq')}] {who}: {payload.get('body')}")
    elif kind == "ack":
        success(f"  ✓ stored (seq {payload.get('seq')})")
    elif kind == "read":
        info(f"  ✓ read up to {payload.get('up_to_seq')}")
    elif kind == "typing":
        state = "is typing" if payload.get("is_typing") else "stopped typing"
        info(f"  … user {payload.get('user_id')} {state}")
    elif kind == "presence":
        online = "online" if payload.get("online") else "offline"
        info(f"  ● user {payload.get('user_id')} is {online}")
    elif kind == "pong":
        rtt = payload.get("rtt")
        if rtt is not None:
            info(f"  · RTT {rtt:.1f} ms")
    elif kind == "error":
        error(f"  ! {payload.get('code')}: {payload.get('message')}")


def shell(client, session, base_url, csrf_token, state):
    """The interactive loop. Returns 'quit' or 'reconnect'."""
    while True:
        try:
            line = input(colour("> ", "32"))
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"

        line = line.strip()
        if not line:
            continue

        if line == "/quit" or line == "/exit":
            return "quit"

        if line == "/help":
            print(
                "\n".join(
                    [
                        "  /list                  list your conversations",
                        "  /open <conversation>   open a conversation and show history",
                        "  /whoami                show the authenticated user",
                        "  /help                  this help",
                        "  /quit                  disconnect and exit",
                        "",
                        "  Anything else is sent as a message to the open conversation.",
                    ]
                )
            )
            continue

        if line == "/whoami":
            print(json.dumps(client.user, indent=2))
            continue

        if line == "/list":
            conversations = fetch_conversations(session, base_url, csrf_token)
            if not conversations:
                info("  No conversations yet.")
            for conversation in conversations:
                unread = f" [{conversation['unread']} unread]" if conversation.get("unread") else ""
                info(
                    f"  #{conversation['id']:<4} {conversation.get('other_name', '?')}"
                    f"{unread}"
                )
            continue

        if line.startswith("/open"):
            parts = line.split()
            if len(parts) < 2:
                error("  Usage: /open <conversation_id>")
                continue
            try:
                conversation_id = int(parts[1])
            except ValueError:
                error("  The conversation id must be a number.")
                continue
            state["conversation"] = conversation_id
            success(f"  Conversation {conversation_id} is now open.")
            for message in fetch_messages(
                session, base_url, csrf_token, conversation_id
            ):
                info(
                    f"    [{message.get('seq')}] {message.get('sender_id')}: "
                    f"{message.get('body')}"
                )
            continue

        if state.get("conversation") is None:
            error("  Open a conversation first: /open <conversation_id>")
            continue

        try:
            client.send_frame(
                {
                    "type": SEND,
                    "id": uuid.uuid4().hex,
                    "conversation_id": state["conversation"],
                    "client_msg_id": str(uuid.uuid4()),
                    "body": line,
                }
            )
            info("  -> sent")
        except (OSError, RuntimeError) as exc:
            error(f"  ! could not send: {exc}")
            return "reconnect"


def main(argv=None):
    # The Windows console defaults to cp1252, which cannot encode the
    # characters used in the output. UTF-8 with a replacement fallback keeps
    # the client usable in every terminal.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    parser = argparse.ArgumentParser(
        description="CareerTwin raw TCP chat client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="TCP server host")
    parser.add_argument("--port", type=int, default=9000, help="TCP server port")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:5000", help="Base URL of the web app"
    )
    parser.add_argument("--email", required=True, help="Account email address")
    parser.add_argument(
        "--password", default=None, help="Password (prompted for when omitted)"
    )
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
