# Demo guide

Two ways to run the demo: on one machine, and across two laptops on the same
LAN. The second one is the one that shows the networking.

---

## 1. One machine

### 1.1 Prepare

```bash
cd frontend

python -m venv .venv
. .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

Create the configuration:

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```ini
SECRET_KEY=<paste a random string>
FLASK_DEBUG=true
DATABASE_URL=sqlite:///careertwin.db
TCP_CHAT_ENABLED=true
TCP_CHAT_HOST=127.0.0.1
TCP_CHAT_PORT=9000
```

Generate a secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 1.2 Seed the mentor directory

```bash
SEED_MENTOR_PASSWORD=choose-a-password python scripts/seed_mentors.py
```

This creates eight fake mentors (`mentor1@example.test` …
`mentor8@example.test`). The password comes from the environment variable
and is never printed.

### 1.3 Start the server

```bash
python run.py
```

You should see:

```
 * Running on http://127.0.0.1:5000
```

and the TCP server is listening on port 9000.

Check both ports:

```bash
curl http://127.0.0.1:5000/healthz          # {"status":"ok"}
python -c "import socket;s=socket.create_connection(('127.0.0.1',9000),2);print('TCP OK');s.close()"
```

### 1.4 The demo (10 minutes)

1. Open <http://127.0.0.1:5000> — the landing page.
2. Register a student account, then complete the profile (skills and a
   career goal).
3. Go to **Mentors** → open a mentor → **Request mentorship**.
4. Open a second browser (or a private window) and sign in as
   `mentor1@example.test` with the seed password.
5. In the mentor account go to **Mentorship → Incoming** and **Accept**.
6. Both sides now have a conversation. Send a message and watch the ticks
   change from *sending* to *sent* to *delivered* to *read*.
7. Open a terminal and connect the raw TCP client as the student:

   ```bash
   python -m network.cli_client --host 127.0.0.1 --port 9000 \
       --base-url http://127.0.0.1:5000 --email student@example.com
   ```

   Enter the password when prompted, then:

   ```
   /list
   /open 1
   hello from the command line
   ```

   The message appears in the browser instantly, and anything the mentor
   types in the browser appears in the terminal.
8. Open **Network lab** in the browser: live RTT, a sparkline, a frame
   level event log, and the simulation controls. Drag the *added latency*
   slider to 500 ms and the *packet drop* slider to 20 %, then send a
   message — the retry button appears on the failed bubble, and retrying
   does **not** create a duplicate.
9. Turn off your Wi-Fi for 20 seconds and turn it back on. The banner goes
   from *Connected* to *Reconnecting in 3s…* and back. Nothing is lost and
   nothing is duplicated, because the client resumes with `sync:request`.

### 1.5 The load test

With the server still running:

```bash
python -m tools.loadtest --clients 8 --messages 10 \
    --host 127.0.0.1 --port 9000 --base-url http://127.0.0.1:5000
```

It prints the throughput and the p50 / p95 / p99 acknowledgement latency.

---

## 2. Two laptops on the same LAN

This is the demo that shows the networking properly: the raw TCP server is
reached over a real network, not over loopback.

### 2.1 On the server laptop

1. Find the IP address:

   ```bash
   # Windows
   ipconfig
   # macOS / Linux
   ip addr
   ```

   Look for something like `192.168.1.42`.

2. Allow the ports through the firewall. On Windows, run PowerShell as an
   administrator:

   ```powershell
   New-NetFirewallRule -DisplayName "CareerTwin web" -Direction Inbound `
       -Protocol TCP -LocalPort 5000 -Action Allow
   New-NetFirewallRule -DisplayName "CareerTwin chat TCP" -Direction Inbound `
       -Protocol TCP -LocalPort 9000 -Action Allow
   ```

   On Linux with `ufw`:

   ```bash
   sudo ufw allow 5000/tcp
   sudo ufw allow 9000/tcp
   ```

3. Bind to every interface, not just loopback:

   ```bash
   HOST=0.0.0.0 TCP_CHAT_HOST=0.0.0.0 python run.py
   ```

   Both `0.0.0.0` values matter: the first is the web server, the second is
   the raw TCP server.

### 2.2 On the client laptop

Open <http://SERVER_IP:5000> — for example <http://192.168.1.42:5000>.
Everything works, including the login, because the session cookie is only
marked `Secure` on HTTPS connections (see `SESSION_COOKIE_SECURE=auto`).

Connect the command line client to the server laptop:

```bash
python -m network.cli_client --host 192.168.1.42 --port 9000 \
    --base-url http://192.168.1.42:5000 --email student@example.com
```

### 2.3 Finding the server automatically (optional)

The UDP discovery beacon broadcasts `CT_HELLO host:port` every three
seconds:

```bash
# on the server
python -m network.udp_discovery --host 0.0.0.0 --port 9000

# on the client
python -m network.udp_discovery --listen
```

The client prints the servers it can see. UDP broadcast is unreliable by
design — a lost beacon simply means the next one arrives three seconds
later. Allow UDP 9001 in the firewall if you use it.

### 2.4 What to expect

* The browser on the client laptop talks to the server over HTTP and
  WebSocket on port 5000.
* The CLI client talks to the server over raw TCP on port 9000.
* Wireshark on **either** laptop shows the traffic. On the client laptop
  you can capture the raw TCP frames directly; on the server laptop you can
  see both the HTTP/WebSocket traffic and the raw TCP traffic arriving from
  the other machine.

---

## 3. The five minute viva script

A short, ordered walkthrough. Each step names the file to open, because
"it works" is worth much less than "here is the code that does it".

### 3.1 The framing (60 s)

> TCP is a byte stream, not a message transport, so the protocol has to say
> where a message ends. We use a length prefix.

Open `frontend/network/protocol.py` and point at `HEADER =
struct.Struct(">2sBI")` — magic, version, 32-bit big-endian length — and at
`read_frame()`, which uses `readexactly()` so a frame split across segments
is reassembled.

### 3.2 The handshake (45 s)

> The raw TCP port has no cookies, so we use a different credential: a
> signed token that is bound to the user, expires in 60 seconds and can be
> redeemed once.

Open `frontend/chat_tokens.py` (issue/redeem) and
`frontend/network/tcp_server.py` (`_on_hello`). Point at the `TOKEN_USED`
error: the same token cannot be used twice.

### 3.3 The reliability (90 s)

> Every message is committed to the database before it is acknowledged, and
> it gets a per-conversation sequence number, so a retry after a lost ACK
> cannot create a duplicate.

Open `frontend/chat_service.py` → `ChatService.send()`. Point at:

* the idempotency check on `client_msg_id`,
* the atomic `UPDATE conversations SET next_seq = next_seq + 1` inside the
  same transaction as the insert,
* the unique constraints on the `messages` table.

Then open `frontend/chat_service.py` → `mark_read()` and explain the
`sender_id != user_id` filter.

### 3.4 Two transports, one service (60 s)

> The browser and the command line client are two adapters over the same
> service, which is why they can talk to each other.

Open `frontend/hub.py` and point at the `Sink` interface, the
`WebSocketSink` and the `TcpSink`. Then `frontend/network/tcp_server.py` →
`_on_send()`, and `frontend/socket_events.py` → `on_message_send()`: both
call `chat_service.send()`.

### 3.5 The live demo (90 s)

1. Browser and terminal open on the same conversation.
2. Type in the browser → it appears in the terminal.
3. Type in the terminal → it appears in the browser.
4. Open **Network lab** → show the RTT, the frame log and the CSV export.
5. Turn the Wi-Fi off for 20 seconds, back on → the banner recovers, the
   history is complete, and there are no duplicates.
6. Show `python -m tools.loadtest --clients 8 --messages 10` → the
   percentiles.

### 3.6 The close (30 s)

> The raw TCP server runs in one process, so the hub is per process. Scaling
> out means a shared bus — Socket.IO's `message_queue` with Redis — which we
> did not build on purpose.

Open `docs/NETWORK_ARCHITECTURE.md` § 4.9.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `SECRET_KEY is not set` | `.env` is missing or empty | Copy `.env.example` to `.env` and set it |
| The CLI client says `Handshake rejected` | The token expired (60 s) or was already used | The client fetches a new token on every reconnect; if you are testing by hand, ask for a fresh one |
| The CLI client cannot connect | The TCP server is disabled or bound to loopback only | Set `TCP_CHAT_ENABLED=true` and `TCP_CHAT_HOST=0.0.0.0` |
| The browser shows "Reconnecting in 3s…" | The socket dropped | Check the browser console; the Network lab shows the reason |
| The other laptop cannot load the page | The firewall | Allow TCP 5000 and 9000 |
| `Address already in use` | Another process holds the port | `netstat -ano | findstr :9000` and stop it, or change `TCP_CHAT_PORT` |
| The load test says `Login failed` | The demo users do not exist yet | Run `tools/loadtest.py` once (it creates them) or seed the database first |
