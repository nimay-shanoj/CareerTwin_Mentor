# Wireshark guide

Everything in this project can be observed on the wire. This page lists the
filters, and what each capture is supposed to show.

---

## 1. Capturing on your own machine

Wireshark needs elevated privileges to open a network interface, and it
cannot capture the loopback interface on Windows without the
[Npcap loopback adapter](https://npcap.com/) (installed with the "Npcap
Loopback Adapter" option). The easiest capture on a laptop is therefore
the **raw TCP server**, which is a real TCP connection even on `127.0.0.1`.

### 1.1 Capture the raw TCP chat (port 9000)

1. Start the app with the TCP server enabled:

   ```bash
   cd frontend
   TCP_CHAT_ENABLED=1 TCP_CHAT_PORT=9000 python run.py
   ```

2. Start Wireshark and pick the interface. On Windows, use the Npcap
   Loopback Adapter (or your Wi-Fi/Ethernet adapter if you are testing
   across two machines).

3. Set the display filter to `tcp.port == 9000`.

4. Connect the CLI client:

   ```bash
   python -m network.cli_client --host 127.0.0.1 --port 9000 \
       --base-url http://127.0.0.1:5000 --email you@example.com
   ```

5. Type a message. It appears in the capture as raw payload bytes.

### 1.2 Capture the browser (port 5000)

The web app speaks HTTP and WebSocket on port 5000. Useful filters:

```
http                                   # every HTTP exchange
http.request.uri contains "api"        # the REST calls
websocket                             # the WebSocket frames
http.request.full_uri == "http://127.0.0.1:5000/socket.io/?EIO=4&transport=websocket"
```

The Socket.IO handshake is a normal HTTP `GET` with an `Upgrade` header. After
the upgrade, the frames are shown as `WebSocket` in the dissection tree.

### 1.3 Capture across two machines

Both machines must be on the same network. On the server:

```bash
HOST=0.0.0.0 TCP_CHAT_HOST=0.0.0.0 python run.py
```

Then capture on the interface that carries the LAN traffic, with the filter
`tcp.port == 9000 or tcp.port == 5000`.

---

## 2. What to point at

This is the part that matters in a viva: each of these is something the
implementation deliberately does, and the capture is the proof.

### 2.1 The three-way handshake

*Filter* `tcp.flags.syn == 1`

```
No.     Time        Source        Destination   Flags
1       0.000000    127.0.0.1     127.0.0.1     SYN
2       0.000245    127.0.0.1     127.0.0.1     SYN, ACK
3       0.000510    127.0.0.1     127.0.0.1     ACK
```

This is the L4 connection setup that `asyncio.start_server` performs. It is
also what makes the raw TCP server a genuine network application rather
than an in-process function call.

### 2.2 Our own frames

*Filter* `tcp.port == 9000`

Right-click a packet → **Follow → TCP Stream**. The reassembled stream shows
the length prefix and the JSON payload:

```
CT....>{"type":"HELLO","id":"3f2a9c1d7e4b08a5","token":"..."}
CT.....{"type":"SEND","id":"b81f4d0c9a2e3756","conversation_id":7,...
```

The first two bytes are `43 54` — the ASCII characters `CT`. That is the
magic number from the frame format, and it is what lets the receiver
resynchronise (or refuse to).

To see it as bytes, select the payload and open
**View → Show Packet in Bytes**.

### 2.3 A frame split across segments

*Filter* `tcp.port == 9000 && tcp.len > 0`

Send a long message (> 1400 bytes, so it exceeds the MSS on a typical
Ethernet link) and look for the payload arriving in several segments. The
receiver still gets one message, because it reads exactly `LENGTH` bytes
before it tries to parse the JSON. `FrameDecoder.feed()` is the same logic
in the pure-Python decoder, and `tests/test_protocol.py` asserts it.

To make this obvious, type a long message into the CLI client and look for
several TCP segments carrying parts of one frame.

### 2.4 The HTTP 101 Switching Protocols (the WebSocket upgrade)

*Filter* `http.response.code == 101`

```
GET /socket.io/?EIO=4&transport=websocket HTTP/1.1
...
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: ...
```

This is the moment the browser's connection stops being HTTP and becomes a
WebSocket. The status code `101` is the one to look for.

### 2.5 WebSocket frames

*Filter* `websocket`

A `message:send` event shows up as a WebSocket **text** frame whose payload
is the Socket.IO packet format:

```
42["message:send",{"conversation_id":7,"client_msg_id":"...","body":"Hello"}]
```

`42` is the Socket.IO packet type for an event. The `message:new` event
arrives the same way in the other direction.

### 2.6 Keepalives and heartbeats

*Filter* `tcp.port == 9000 && tcp.len > 0`

The CLI client sends a `PING` frame every 10 seconds. On a quiet
connection you will see a small frame roughly every ten seconds, which is
the application-level heartbeat keeping the connection alive and detecting
a dead peer.

For TCP-level keepalives, look for the keepalive probes (usually empty
payloads with an unusual sequence number) — these come from `SO_KEEPALIVE`,
which the server sets on every accepted socket.

### 2.7 FIN and ACK on close

*Filter* `tcp.port == 9000 && tcp.flags.fin == 1`

When the CLI client quits, the client sends `FIN`, the server answers with
`FIN, ACK`, and the last `ACK` completes the four-way close. The server
also sends a `BYE` frame first, so you can see the graceful application-level
close *before* the transport-level one.

### 2.8 The REST call that issues the handshake token

*Filter* `http.request.uri == "/api/chat/token"` or
`http.request.method == "POST" && http.request.uri contains "chat/token"`

The response is JSON:

```json
{"expires_in": 60, "token": "eyJ1aWQiOjUsIm5pZCI6ImE5YjMifQ.XXXX.YYYY"}
```

Point out that the token is a **different credential** from the session
cookie, that it expires in 60 seconds and that it can be redeemed only
once — and that the password itself never travels over the raw socket.

### 2.9 A 403 for a conversation you are not part of

*Filter* `http.response.code == 403`

Request a conversation that belongs to somebody else and the API answers
with a JSON body:

```json
{"code": "FORBIDDEN", "error": "You are not a participant in this conversation."}
```

That is the authorisation rule from `ChatService.get_conversation()`, and it
is enforced for both transports.

---

## 3. A ready-made filter list

```
# the raw TCP chat
tcp.port == 9000

# our frames only (skip the pure ACK chatter)
tcp.port == 9000 && tcp.len > 0

# the web app
tcp.port == 5000

# the WebSocket upgrade
http.response.code == 101

# everything the browser sends
http.request || websocket

# a full conversation, both directions, reassembled
tcp.port == 9000 && tcp.len > 0
```

---

## 4. A five minute capture script

1. Open Wireshark, capture on loopback, filter `tcp.port == 9000`.
2. Start the app: `TCP_CHAT_ENABLED=1 python run.py`.
3. Start the CLI client and log in.
4. In Wireshark, find the three-way handshake and point at it.
5. Find the `HELLO` frame, follow the TCP stream, point at the `CT` magic.
6. Send a message; find the `SEND` frame and the `ACK` with the sequence
   number.
7. Send a long message; point at the segments it was split into.
8. Open the browser at `/network`, show the live RTT and the event log, and
   point at the `rtt:ping` / `rtt:pong` frames in the capture.
9. Kill the network for a few seconds (turn off Wi-Fi, or stop the server),
   bring it back, and show the client reconnecting with backoff and
   resuming with `sync:request` — no message lost, no message duplicated.
