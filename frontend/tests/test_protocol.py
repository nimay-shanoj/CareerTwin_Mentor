"""Tests for the CTP/1 frame codec.

These cover the interesting cases of a length-prefixed protocol on top of a
byte stream: a frame split across segments, several frames in one segment, a
bad magic number, an oversize payload and a truncated payload.
"""

import os
import struct
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from network.protocol import (  # noqa: E402
    ERR,
    HEADER,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    VERSION,
    FrameDecoder,
    ProtocolError,
    decode_header,
    encode_frame,
    read_frame,
)


def test_encode_frame_layout():
    frame = encode_frame({"type": "PING", "id": "abc"})
    magic, version, length = HEADER.unpack(frame[:HEADER_SIZE])
    assert magic == MAGIC == b"\x43\x54"
    assert version == VERSION == 1
    assert length == len(frame) - HEADER_SIZE
    assert frame[HEADER_SIZE:] == b'{"type":"PING","id":"abc"}'


def test_header_size_is_seven_bytes():
    assert HEADER_SIZE == 7


def test_decode_header_accepts_a_good_header():
    frame = encode_frame({"type": "PING"})
    assert decode_header(frame[:HEADER_SIZE]) == len(frame) - HEADER_SIZE


def test_decode_header_rejects_bad_magic():
    header = struct.pack(">2sBI", b"XX", VERSION, 2)
    with pytest.raises(ProtocolError) as exc:
        decode_header(header)
    assert exc.value.code == "BAD_MAGIC"


def test_decode_header_rejects_bad_version():
    header = struct.pack(">2sBI", MAGIC, 99, 2)
    with pytest.raises(ProtocolError) as exc:
        decode_header(header)
    assert exc.value.code == "BAD_VERSION"


def test_decode_header_rejects_oversize_payload():
    header = struct.pack(">2sBI", MAGIC, VERSION, MAX_PAYLOAD + 1)
    with pytest.raises(ProtocolError) as exc:
        decode_header(header)
    assert exc.value.code == "PAYLOAD_TOO_LARGE"


def test_encode_frame_rejects_oversize_payload():
    with pytest.raises(ProtocolError) as exc:
        encode_frame({"type": "SEND", "body": "x" * (MAX_PAYLOAD + 10)})
    assert exc.value.code == "PAYLOAD_TOO_LARGE"


def test_decoder_handles_a_frame_split_across_chunks():
    decoder = FrameDecoder()
    frame = encode_frame({"type": "SEND", "body": "hello"})

    # One byte at a time – the worst case a network can produce.
    frames = []
    for index in range(len(frame)):
        frames.extend(decoder.feed(frame[index : index + 1]))

    assert frames == [{"type": "SEND", "body": "hello"}]


def test_decoder_handles_several_frames_in_one_chunk():
    decoder = FrameDecoder()
    blob = encode_frame({"type": "PING", "n": 1}) + encode_frame({"type": "PING", "n": 2})
    frames = decoder.feed(blob)
    assert frames == [{"type": "PING", "n": 1}, {"type": "PING", "n": 2}]


def test_decoder_handles_a_mixed_chunk():
    decoder = FrameDecoder()
    first = encode_frame({"type": "A"})
    second = encode_frame({"type": "B", "pad": "x" * 100})

    # Half of the first frame, then the rest of it plus the whole second one.
    frames = decoder.feed(first[:3])
    assert frames == []
    frames = decoder.feed(first[3:] + second)
    assert frames == [{"type": "A"}, {"type": "B", "pad": "x" * 100}]


def test_decoder_rejects_bad_magic():
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(b"XX" + struct.pack(">BI", VERSION, 2) + b"{}")
    assert exc.value.code == "BAD_MAGIC"


def test_decoder_rejects_bad_version():
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(MAGIC + struct.pack(">BI", 42, 2) + b"{}")
    assert exc.value.code == "BAD_VERSION"


def test_decoder_rejects_oversize_payload():
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(MAGIC + struct.pack(">BI", VERSION, MAX_PAYLOAD + 1))
    assert exc.value.code == "PAYLOAD_TOO_LARGE"


def test_decoder_rejects_invalid_json():
    decoder = FrameDecoder()
    header = struct.pack(">2sBI", MAGIC, VERSION, 3)
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(header + b"{{{")
    assert exc.value.code == "BAD_JSON"


def test_decoder_rejects_non_object_payload():
    decoder = FrameDecoder()
    header = struct.pack(">2sBI", MAGIC, VERSION, 2)
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(header + b"[]")
    assert exc.value.code == "BAD_JSON"


def test_decoder_rejects_invalid_utf8():
    decoder = FrameDecoder()
    header = struct.pack(">2sBI", MAGIC, VERSION, 2)
    with pytest.raises(ProtocolError) as exc:
        decoder.feed(header + b"\xff\xfe")
    assert exc.value.code == "BAD_JSON"


# --- the async reader ------------------------------------------------------
class FakeReader:
    """Minimal stand-in for asyncio.StreamReader."""

    def __init__(self, data):
        self._data = data
        self._pos = 0

    async def readexactly(self, count):
        chunk = self._data[self._pos : self._pos + count]
        if len(chunk) < count:
            raise EOFError("not enough data")
        self._pos += count
        return chunk


def test_read_frame_works_without_an_event_loop_plugin():
    """`read_frame` is a plain coroutine, so it can be driven with asyncio.run."""
    import asyncio

    reader = FakeReader(encode_frame({"type": "PONG", "t": 2}))
    frame = asyncio.run(read_frame(reader))
    assert frame == {"type": "PONG", "t": 2}

def test_pending_reports_incomplete_bytes():
    decoder = FrameDecoder()
    frame = encode_frame({"type": "PING"})
    decoder.feed(frame[:-2])
    # The header was consumed; the payload is still short by two bytes.
    assert decoder.pending == len(frame) - HEADER_SIZE - 2
    assert decoder.needs_payload > 0
