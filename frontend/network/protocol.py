"""The CareerTwin TCP wire protocol (CTP/1).

Frame layout — all integers big-endian::

    +---------+---------+-------------+----------------------+
    | MAGIC   | VERSION | LENGTH      | PAYLOAD              |
    | 2 bytes | 1 byte  | 4 bytes u32 | LENGTH bytes, UTF-8  |
    | 0x43 54 | 0x01    |             | JSON object          |
    +---------+---------+-------------+----------------------+

Why a length prefix?  TCP is a *stream*, not a message transport. It makes no
promise about how the bytes of one message are split across segments, so the
receiver cannot find a message boundary on its own. Prefixing every frame with
its length is the standard solution: the receiver reads exactly the number of
bytes the header announces, no matter how the network chopped them up.

The payload is a UTF-8 JSON object with a ``type`` discriminator::

    {"type": "SEND", "id": "<uuid>", "conversation_id": 3, ...}

This module contains no I/O and no application logic, which makes it easy to
unit test – see ``tests/test_protocol.py``.
"""

import json
import struct

# --- Frame constants --------------------------------------------------------
MAGIC = b"\x43\x54"  # b"CT"
VERSION = 1
MAX_PAYLOAD = 16 * 1024  # 16 KiB

# magic (2) + version (1) + length (4)
HEADER = struct.Struct(">2sBI")
HEADER_SIZE = HEADER.size  # 7 bytes

# --- Frame types ------------------------------------------------------------
HELLO = "HELLO"
WELCOME = "WELCOME"
SEND = "SEND"
ACK = "ACK"
MSG = "MSG"
TYPING = "TYPING"
READ = "READ"
PING = "PING"
PONG = "PONG"
PRESENCE = "PRESENCE"
ERR = "ERR"
BYE = "BYE"

KNOWN_TYPES = {
    HELLO, WELCOME, SEND, ACK, MSG, TYPING, READ, PING, PONG, PRESENCE, ERR, BYE,
}

# --- Error codes ------------------------------------------------------------
ERR_BAD_MAGIC = "BAD_MAGIC"
ERR_BAD_VERSION = "BAD_VERSION"
ERR_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
ERR_BAD_JSON = "BAD_JSON"
ERR_BAD_FRAME = "BAD_FRAME"
ERR_NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
ERR_ALREADY_AUTHENTICATED = "ALREADY_AUTHENTICATED"
ERR_UNKNOWN_TYPE = "UNKNOWN_TYPE"
ERR_INVALID_PAYLOAD = "INVALID_PAYLOAD"
ERR_TOKEN_INVALID = "TOKEN_INVALID"
ERR_TOKEN_EXPIRED = "TOKEN_EXPIRED"
ERR_TOKEN_USED = "TOKEN_USED"
ERR_FORBIDDEN = "FORBIDDEN"
ERR_CONVERSATION_READ_ONLY = "CONVERSATION_READ_ONLY"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_INTERNAL = "INTERNAL_ERROR"


class ProtocolError(Exception):
    """A framing error. The connection must be answered with ERR and closed."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def encode_frame(payload):
    """Serialise a JSON object into a single frame.

    ``payload`` must be a dict that is JSON-serialisable.
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_PAYLOAD:
        raise ProtocolError(
            ERR_PAYLOAD_TOO_LARGE,
            f"Payload of {len(body)} bytes exceeds the {MAX_PAYLOAD} byte limit.",
        )
    return HEADER.pack(MAGIC, VERSION, len(body)) + body


def decode_header(header):
    """Validate a 7 byte header.

    Returns ``(length,)`` or raises :class:`ProtocolError`.
    """
    if len(header) != HEADER_SIZE:
        raise ProtocolError(ERR_BAD_FRAME, "Incomplete frame header.")
    magic, version, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise ProtocolError(
            ERR_BAD_MAGIC, f"Bad magic {magic!r}: expected {MAGIC!r}."
        )
    if version != VERSION:
        raise ProtocolError(
            ERR_BAD_VERSION, f"Unsupported protocol version {version}."
        )
    if length > MAX_PAYLOAD:
        raise ProtocolError(
            ERR_PAYLOAD_TOO_LARGE,
            f"Payload of {length} bytes exceeds the {MAX_PAYLOAD} byte limit.",
        )
    return length


def decode_payload(body):
    """Decode a payload into a JSON object."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(ERR_BAD_JSON, f"Payload is not valid UTF-8: {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ERR_BAD_JSON, f"Payload is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise ProtocolError(ERR_BAD_JSON, "Payload must be a JSON object.")
    return data


class FrameDecoder:
    """Incremental frame decoder.

    Feed it whatever bytes arrive; it returns the frames that are complete and
    keeps the remainder buffered. This is what makes a frame split across
    several TCP segments (or several frames arriving in one segment) work.
    """

    def __init__(self, max_payload=MAX_PAYLOAD):
        self.max_payload = max_payload
        self._buffer = bytearray()
        self._need = None      # payload length of the frame being collected
        self._header = bytearray()

    def feed(self, data):
        """Add bytes to the buffer and return the list of decoded frames."""
        self._buffer.extend(data)
        frames = []

        while True:
            if self._need is None:
                # Collecting the header.
                if len(self._buffer) < HEADER_SIZE:
                    break
                header = bytes(self._buffer[:HEADER_SIZE])
                del self._buffer[:HEADER_SIZE]
                magic, version, length = HEADER.unpack(header)
                if magic != MAGIC:
                    raise ProtocolError(
                        ERR_BAD_MAGIC, f"Bad magic {magic!r}: expected {MAGIC!r}."
                    )
                if version != VERSION:
                    raise ProtocolError(
                        ERR_BAD_VERSION, f"Unsupported protocol version {version}."
                    )
                if length > self.max_payload:
                    raise ProtocolError(
                        ERR_PAYLOAD_TOO_LARGE,
                        f"Payload of {length} bytes exceeds the "
                        f"{self.max_payload} byte limit.",
                    )
                self._need = length
                self._header = bytearray(header)
                continue

            # Collecting the payload.
            if len(self._buffer) < self._need:
                break
            body = bytes(self._buffer[: self._need])
            del self._buffer[: self._need]
            self._need = None
            frames.append(decode_payload(body))

        return frames

    @property
    def pending(self):
        """Bytes buffered for a frame that is not complete yet."""
        return len(self._buffer)

    @property
    def needs_payload(self):
        """Payload bytes still expected for the frame being collected."""
        return self._need or 0

    def reset(self):
        self._buffer.clear()
        self._header.clear()
        self._need = None


async def read_frame(reader, max_payload=MAX_PAYLOAD):
    """Read exactly one frame from an :class:`asyncio.StreamReader`.

    ``readexactly`` is what makes a frame split across several TCP segments
    work: it keeps reading until it has the promised number of bytes (or the
    peer disconnects, which raises ``IncompleteReadError``).
    """
    header = await reader.readexactly(HEADER_SIZE)
    magic, version, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise ProtocolError(ERR_BAD_MAGIC, f"Bad magic {magic!r}: expected {MAGIC!r}.")
    if version != VERSION:
        raise ProtocolError(ERR_BAD_VERSION, f"Unsupported protocol version {version}.")
    if length > max_payload:
        raise ProtocolError(
            ERR_PAYLOAD_TOO_LARGE,
            f"Payload of {length} bytes exceeds the {max_payload} byte limit.",
        )
    body = await reader.readexactly(length)
    return decode_payload(body)


def error_frame(code, message, ref=None):
    """Build an ERR frame payload."""
    payload = {"type": ERR, "code": code, "message": message}
    if ref:
        payload["ref"] = ref
    return payload


def describe():
    """Human readable summary of the protocol (used by the docs generator)."""
    return {
        "magic": MAGIC.hex(),
        "version": VERSION,
        "header_size": HEADER_SIZE,
        "max_payload": MAX_PAYLOAD,
        "types": sorted(KNOWN_TYPES),
    }
