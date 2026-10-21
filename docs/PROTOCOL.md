# CTP/1 — the CareerTwin TCP protocol

CTP/1 is the application-layer protocol spoken by the raw TCP chat server
(`network/tcp_server.py`). It is deliberately small enough to read in one
sitting and to implement from scratch — the reference client
(`network/cli_client.py`) does exactly that, using nothing but the standard
library's `socket` module.

---

## 1. Frame format

Every frame has the same seven byte header followed by a JSON payload.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          MAGIC (0x43 54)     |    VERSION    |     LENGTH    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                     PAYLOAD  (LENGTH bytes, UTF-8 JSON)        |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field    | Size | Value                                     |
|----------|------|-------------------------------------------|
| MAGIC    | 2 B  | `0x43 0x54` — the ASCII bytes `CT`        |
| VERSION  | 1 B  | `0x01`                                    |
| LENGTH   | 4 B  | payload length, **unsigned, big-endian**  |
| PAYLOAD  | L B  | UTF-8 encoded JSON **object**             |

The maximum payload is **16 KiB** (16384 bytes). A larger `LENGTH` is
rejected before a single payload byte is read.

### Why a length prefix?

TCP is a **byte stream**, not a message transport. It guarantees ordered and
reliable delivery of bytes, and nothing else. A single `send()` on one side
can arrive as several segments on the other side, and several `send()` calls
can arrive as one segment. The receiver therefore has no way to find a
message boundary by itself.

The length prefix removes the ambiguity: the receiver reads exactly `LENGTH`
bytes after the header, no matter how the network chopped them up. The
reference implementation does this with `readexactly()` (asyncio) and
`_read_exactly()` (the CLI client), both of which keep reading until the
promised number of bytes has arrived.

The alternative — a delimiter such as `\n` — breaks as soon as a message
body contains the delimiter, which is why JSON over a stream normally uses
framing rather than a separator.

---

## 2. Connection state machine

```
          TCP connect
               |
               v
        +-------------+   HELLO with a valid, unspent token   +-------------+
        |  CONNECTED | -------------------------------------> | AUTHENTICATED |
        +-------------+                                        +-------------+
               |                                                |     |     |
               | no HELLO within 5 s                           |     |     |
               | -> ERR HELLO_TIMEOUT, close                   |     |     |
               |                                                |     |     |
               | any other frame before HELLO                  |     |     |
               | -> ERR HELLO_REQUIRED, close                  |     |     |
               |                                                |     |     |
               | ERR (bad magic/version/payload)               |     |     |
               +-> close                                       |     |     |
                                                                |     |     |
                                                                |     |     +--> BYE
                                                                |     |          -> close
                                                                |     |
                                                                |     +--> idle for 30 s
                                                                |          -> ERR IDLE_TIMEOUT, close
                                                                |
                                                                +--> ERR (framing/validation)
                                                                     -> close
```

* `CONNECTED` → `AUTHENTICATED` happens exactly once, on a valid `HELLO`.
* A `HELLO` on an already authenticated connection is rejected with
  `ERR ALREADY_AUTHENTICATED`.
* Every error path answers with an `ERR` frame and then closes the
  connection. The stream cannot be resynchronised after a framing error, so
  closing is the only safe action.
* Clients send a `PING` every 10 seconds. The server advertises its
  preferred heartbeat interval in the `WELCOME` frame.

---

## 3. Message types

### Client → server

| Type    | Fields                                             | Meaning |
|---------|----------------------------------------------------|---------|
| `HELLO` | `token` (string), `id` (uuid)                     | Authenticate. Must be the first frame. |
| `SEND`  | `conversation_id` (int), `client_msg_id` (uuid), `body` (string), `id` (uuid) | Send a message. |
| `READ`  | `conversation_id` (int), `up_to_seq` (int)         | Read receipt. |
| `TYPING`| `conversation_id` (int), `is_typing` (bool)        | Typing indicator. |
| `PING`  | `t` (float, client timestamp in ms)                | Keepalive probe. |
| `BYE`   | `id` (uuid)                                        | Graceful close. |

### Server → client

| Type      | Fields                                                        | Meaning |
|-----------|---------------------------------------------------------------|---------|
| `WELCOME` | `user` (`{id, name}`), `server_time` (float ms), `heartbeat_s` (int), `connection_id` (string) | Greeting after a successful `HELLO`. |
| `ACK`     | `ref` (the `id` of the frame being acknowledged), `seq` (int), `message_id` (int), `created` (bool) | The message was stored. `seq` is the per-conversation sequence number. |
| `MSG`     | `message` (the message object)                                | An incoming message. |
| `READ`    | `conversation_id` (int), `up_to_seq` (int), `by` (int)        | The other participant read up to `seq`. |
| `TYPING`  | `conversation_id` (int), `user_id` (int), `is_typing` (bool)   | The other participant is typing. |
| `PONG`    | `t` (float, the client's timestamp), `server_time` (float ms) | Answer to `PING`. |
| `PRESENCE`| `user_id` (int), `online` (bool)                               | A participant came online or went offline. |
| `ERR`     | `code` (string), `message` (string), `ref` (string, optional) | Something went wrong. |
| `BYE`     | `id` (uuid)                                                  | The server is closing the connection. |

The `message` object carried by `MSG` has the same shape as the JSON
returned by `GET /api/conversations/<id>/messages`:

```json
{
  "id": 42,
  "conversation_id": 7,
  "sender_id": 3,
  "body": "Hello!",
  "client_msg_id": "9c2f5a10-4d3b-4c8e-9f21-7a6b5c4d3e2f",
  "seq": 21,
  "created_at": "2026-10-21T12:00:00.123456",
  "delivered_at": null,
  "read_at": null
}
```

---

## 4. Error codes

| Code                   | Meaning |
|------------------------|---------|
| `BAD_MAGIC`            | The first two bytes were not `0x43 0x54`. The stream is out of sync. |
| `BAD_VERSION`          | The protocol version byte was not `0x01`. |
| `PAYLOAD_TOO_LARGE`    | The announced payload exceeded 16 KiB. |
| `BAD_JSON`             | The payload was not a UTF-8 JSON object. |
| `BAD_FRAME`            | The header was incomplete or malformed. |
| `HELLO_REQUIRED`       | A frame other than `HELLO` arrived before authentication. |
| `HELLO_TIMEOUT`        | No `HELLO` within 5 seconds of connecting. |
| `ALREADY_AUTHENTICATED` | A second `HELLO` on an authenticated connection. |
| `TOKEN_INVALID`        | The handshake token was not valid. |
| `TOKEN_EXPIRED`        | The handshake token was older than its TTL. |
| `TOKEN_USED`           | The handshake token had already been redeemed. |
| `UNKNOWN_TYPE`         | The frame type is not part of CTP/1. |
| `INVALID_PAYLOAD`      | A required field was missing or of the wrong type. |
| `FORBIDDEN`            | The sender is not a participant of that conversation. |
| `CONVERSATION_READ_ONLY` | The mentorship has ended, so the conversation is closed. |
| `RATE_LIMITED`         | More than 20 messages in 10 seconds. |
| `IDLE_TIMEOUT`         | No frame received for 30 seconds. |
| `SERVER_FULL`          | The server already has its maximum number of connections. |
| `INTERNAL_ERROR`       | An unexpected server-side failure. |

---

## 5. Worked examples

### 5.1 A `HELLO` frame

The payload is
`{"type":"HELLO","id":"3f2a9c1d7e4b08a5","token":"PLACEHOLDER"}` — 62 bytes,
so the length field is `0x0000003e`.

```
0000  43 54 01 00 00 00 3e 7b 22 74 79 70 65 22 3a 22  |CT....>{"type":|
0010  48 45 4c 4c 4f 22 2c 22 69 64 22 3a 22 33 66 32  |HELLO","id":"3f2|
0020  61 39 63 31 64 37 65 34 62 30 38 61 35 22 2c 22  |a9c1d7e4b08a5",|
0030  74 6f 6b 65 6e 22 3a 22 50 4c 41 43 45 48 4f 4c  |token":"PLACEHOL|
0040  44 45 52 22 7d                                   |DER"}|
```

Reading it byte by byte:

| Bytes      | Meaning                                            |
|------------|----------------------------------------------------|
| `43 54`    | magic `CT`                                         |
| `01`       | protocol version 1                                 |
| `00 00 00 3e` | payload length 62 (big-endian)                 |
| `7b … 7d`  | the JSON object, 62 bytes of UTF-8                 |

### 5.2 A `SEND` frame

The payload is
`{"type":"SEND","id":"b81f4d0c9a2e3756","conversation_id":7,"client_msg_id":"9c2f5a10-4d3b-4c8e-9f21-7a6b5c4d3e2f","body":"Hello!"}`
— 130 bytes, so the length field is `0x00000082`.

```
0000  43 54 01 00 00 00 82 7b 22 74 79 70 65 22 3a 22  |CT.....{"type":|
0010  53 45 4e 44 22 2c 22 69 64 22 3a 22 62 38 31 66  |SEND","id":"b81f|
0020  34 64 30 63 39 61 32 65 33 37 35 36 22 2c 22 63  |4d0c9a2e3756","c|
0030  6f 6e 76 65 72 73 61 74 69 6f 6e 5f 69 64 22 3a  |onversation_id":|
0040  37 2c 22 63 6c 69 65 6e 74 5f 6d 73 67 5f 69 64  |7,"client_msg_id|
0050  22 3a 22 39 63 32 66 35 61 31 30 2d 34 64 33 62  |":"9c2f5a10-4d3b|
0060  2d 34 63 38 65 2d 39 66 32 31 2d 37 61 36 62 35  |-4c8e-9f21-7a6b|
0070  63 34 64 33 65 32 66 22 2c 22 62 6f 64 79 22 3a  |c4d3e2f","body":|
0080  22 48 65 6c 6c 6f 21 22 7d                       |"Hello!"}|
```

The `client_msg_id` is what makes a retry safe: if the client sends the
same frame again after a timeout, the server recognises the identifier,
returns the original message and **does not insert a second row**. The
`ACK` for the second attempt carries `"created": false`.

---

## 6. Authentication

The raw TCP channel never carries a password and never uses the browser's
session cookie. Instead:

1. The client logs in over **HTTP** (`POST /login`).
2. It asks the server for a handshake token (`POST /api/chat/token`).
3. The server returns a signed token (`itsdangerous`, HMAC-SHA256) that is
   bound to the user id, expires after 60 seconds and can be redeemed
   **once**.
4. The client sends the token in its `HELLO` frame. The server redeems it.

```
  client                         server
    |  POST /api/chat/token        |
    |------------------------------>|   issues a signed, single-use token
    |  {"token": "…", "expires_in": 60}
    |<------------------------------|
    |  TCP connect                 |
    |------------------------------>|
    |  HELLO {"token": "…"}         |   validates the signature, checks the
    |------------------------------>|   expiry, marks the nonce as spent
    |  WELCOME {"user": …}          |
    |<------------------------------|
```

A stolen token is therefore useless after 60 seconds *and* after a single
use.

---

## 7. Reliability rules

| Behaviour | How it is implemented |
|-----------|----------------------|
| **Ordering** | Every message gets a per-conversation `seq`, allocated atomically inside the transaction that inserts it. The unique constraint on `(conversation_id, seq)` is the final guard. |
| **Idempotency** | `(conversation_id, client_msg_id)` is unique. A repeat returns the original message with `created: false`. |
| **Heartbeat** | The client sends `PING` every 10 s; the server advertises `heartbeat_s` in `WELCOME`. |
| **Dead peer detection** | No frame for 30 s → `ERR IDLE_TIMEOUT` and close. `SO_KEEPALIVE` catches a peer that vanished without a FIN. |
| **Reconnect** | Exponential backoff with jitter: 1 s → 2 s → 4 s → 8 s → 15 s → 30 s. |
| **Resume** | After a reconnect the client asks for everything after the last `seq` it saw (`after_seq`), so nothing is reloaded and nothing is lost. |
| **Flow control** | At most 10 unacknowledged sends per connection; the server additionally keeps a bounded write queue and closes a consumer that cannot keep up. |
| **Backpressure** | The write queue is bounded (`TCP_CHAT_WRITE_QUEUE_LIMIT`, 64 frames). A full queue means the peer is not reading: the connection is closed instead of buffering without limit. |

---

## 8. Transport comparison

CTP/1 sits next to the WebSocket transport used by the browser:

| | WebSocket (Socket.IO) | Raw TCP (CTP/1) |
|---|---|---|
| Framing | the WebSocket protocol (RFC 6455) frames the data | our own length-prefixed frames |
| Encoding | Socket.IO packet format, JSON payloads | raw JSON payloads |
| Authentication | the Flask-Login session cookie | a single-use signed token |
| Transport | HTTP upgrade, one TCP connection | a plain TCP connection |
| Visible in Wireshark | as WebSocket frames | as raw TCP payload bytes |
| Purpose | the browser client | the command line client, LAN demo, load test |

Both transports share the same `ChatService`, so validation, authorisation,
rate limiting, sequencing and idempotency behave identically.
