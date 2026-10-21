"""Load test for the raw TCP chat server.

Spawns N TCP clients that each send M messages, then reports throughput and
the p50 / p95 / p99 acknowledgement latency.

Only fake users are used: they are created on the fly with a generated
address under ``example.test`` and a password taken from the environment (or
a throwaway one).

Usage::

    python -m tools.loadtest --clients 10 --messages 20 \\
        --base-url http://127.0.0.1:5000 --host 127.0.0.1 --port 9000
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
import uuid

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------
def ensure_users(count, password):
    """Create (or reuse) fake users and pair them up into conversations."""
    from app import create_app
    from extensions import db
    from mentorship_models import Conversation, MentorshipRequest, MentorProfile, REQ_ACCEPTED
    from models import User

    app = create_app()
    created = []

    with app.app_context():
        for index in range(count):
            email = f"loadtest{index}@example.test"
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=f"Load Test {index}", email=email)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
            created.append(user.id)

        # Give every user a mentor profile so they can accept requests.
        for user_id in created:
            if MentorProfile.query.filter_by(user_id=user_id).first() is None:
                db.session.add(
                    MentorProfile(
                        user_id=user_id,
                        headline="Load test mentor",
                        expertise="python,sql",
                        max_mentees=50,
                    )
                )
        db.session.commit()

        # Pair them up and create the conversations.
        conversations = []
        for i in range(0, len(created) - 1, 2):
            a, b = created[i], created[i + 1]
            request = MentorshipRequest.query.filter(
                MentorshipRequest.mentee_id == a,
                MentorshipRequest.mentor_id == b,
                MentorshipRequest.status == REQ_ACCEPTED,
            ).first()
            if request is None:
                request = MentorshipRequest(
                    mentee_id=a, mentor_id=b, message="load test", status=REQ_ACCEPTED
                )
                db.session.add(request)
                db.session.commit()
            if request.conversation is None:
                db.session.add(Conversation(mentorship_id=request.id))
                db.session.commit()
            conversations.append(
                {
                    "conversation_id": request.conversation.id,
                    "sender": a,
                    "recipient": b,
                }
            )

    return created, conversations


def login(base_url, email, password):
    """Log in over HTTP and return ``(cookie_header, csrf_token, token)``."""
    import requests

    session = requests.Session()
    page = session.get(f"{base_url}/login", timeout=10)
    match = CSRF_RE.search(page.text)
    if not match:
        raise RuntimeError("No CSRF token on the login page.")
    csrf_token = match.group(1)

    response = session.post(
        f"{base_url}/login",
        data={"csrf_token": csrf_token, "email": email, "password": password},
        timeout=10,
        allow_redirects=False,
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"Login failed for {email}")

    token_response = session.post(
        f"{base_url}/api/chat/token",
        headers={"X-CSRFToken": csrf_token},
        timeout=10,
    )
    token_response.raise_for_status()
    return session.cookies.get_dict(), csrf_token, token_response.json()["token"]


# ---------------------------------------------------------------------------
# The TCP workload
# ---------------------------------------------------------------------------
async def run_client(host, port, token, conversation_id, messages, results, label):
    """Connect, send `messages` frames and record the ACK latency."""
    from network.protocol import (
        ACK,
        HELLO,
        WELCOME,
        HEADER,
        HEADER_SIZE,
        MAGIC,
        VERSION,
        ProtocolError,
        encode_frame,
    )

    latencies = []
    errors = 0
    reader = None
    writer = None

    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as exc:
        results["errors"] += 1
        results["messages"].append({"client": label, "error": str(exc)})
        return

    try:
        sock = writer.get_extra_info("socket")
        if sock is not None:
            import socket as socket_module

            sock.setsockopt(socket_module.IPPROTO_TCP, socket_module.TCP_NODELAY, 1)

        writer.write(
            encode_frame({"type": HELLO, "id": uuid.uuid4().hex, "token": token})
        )
        await writer.drain()

        # Wait for WELCOME.
        header = await reader.readexactly(HEADER_SIZE)
        magic, version, length = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION:
            raise ProtocolError("BAD_FRAME", "Unexpected header.")
        await reader.readexactly(length)

        for index in range(messages):
            frame_id = uuid.uuid4().hex
            started = time.perf_counter()
            writer.write(
                encode_frame(
                    {
                        "type": "SEND",
                        "id": frame_id,
                        "conversation_id": conversation_id,
                        "client_msg_id": str(uuid.uuid4()),
                        "body": f"load test message {index}",
                    }
                )
            )
            await writer.drain()

            # Read frames until the ACK for this frame arrives.
            while True:
                header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=15)
                magic, version, length = HEADER.unpack(header)
                body = await reader.readexactly(length)
                frame = json.loads(body.decode("utf-8"))
                if frame.get("type") == ACK and frame.get("ref") == frame_id:
                    latencies.append((time.perf_counter() - started) * 1000.0)
                    break
                if frame.get("type") == "ERR":
                    errors += 1
                    break

        results["latencies"].extend(latencies)
        results["errors"] += errors
        results["sent"] += len(latencies)
    except (asyncio.TimeoutError, OSError, ProtocolError, asyncio.IncompleteReadError) as exc:
        results["errors"] += 1
        results["messages"].append({"client": label, "error": str(exc)})
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def main_async(args):
    password = args.password or os.environ.get("LOADTEST_PASSWORD") or "loadtest-password"
    users, conversations = ensure_users(args.clients, password)
    print(f"Prepared {len(users)} users and {len(conversations)} conversations.")

    tokens = []
    for index, user_id in enumerate(users):
        email = f"loadtest{index}@example.test"
        _, _, token = login(args.base_url.rstrip("/"), email, password)
        tokens.append(token)

    results = {"latencies": [], "errors": 0, "sent": 0, "messages": []}

    started = time.perf_counter()
    tasks = []
    for index, token in enumerate(tokens):
        conversation_id = conversations[index // 2]["conversation_id"]
        tasks.append(
            run_client(
                args.host,
                args.port,
                token,
                conversation_id,
                args.messages,
                results,
                f"client-{index}",
            )
        )
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started

    latencies = results["latencies"]
    print()
    print("=" * 56)
    print("  Raw TCP chat server – load test")
    print("=" * 56)
    print(f"  clients            : {args.clients}")
    print(f"  messages / client  : {args.messages}")
    print(f"  elapsed            : {elapsed:.2f} s")
    print(f"  acknowledged       : {results['sent']}")
    print(f"  errors             : {results['errors']}")
    if latencies:
        throughput = results["sent"] / elapsed if elapsed else 0
        print(f"  throughput         : {throughput:.1f} msg/s")
        print(f"  latency p50        : {percentile(latencies, 0.50):.1f} ms")
        print(f"  latency p95        : {percentile(latencies, 0.95):.1f} ms")
        print(f"  latency p99        : {percentile(latencies, 0.99):.1f} ms")
        print(f"  latency mean       : {statistics.mean(latencies):.1f} ms")
        print(f"  latency min/max    : {min(latencies):.1f} / {max(latencies):.1f} ms")
    for failure in results["messages"][:10]:
        print(f"  ! {failure['client']}: {failure['error']}")
    print("=" * 56)


def main(argv=None):
    parser = argparse.ArgumentParser(description="CareerTwin TCP load test")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--messages", type=int, default=20)
    parser.add_argument("--password", default=None)
    args = parser.parse_args(argv)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
