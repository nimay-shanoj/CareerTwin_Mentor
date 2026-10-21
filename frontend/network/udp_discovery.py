"""Optional: LAN discovery for the raw TCP chat server.

Broadcasts a small beacon every three seconds so that a client on the same
network can find the server without being told its IP address::

    CT_HELLO host:port

This is a *stretch goal* of the project plan. It uses UDP broadcast, which
is unreliable by design: a beacon that is lost simply means the next one
arrives three seconds later. Nothing in the application depends on it.

Usage::

    # on the server
    python -m network.udp_discovery --host 0.0.0.0 --port 9000

    # on the client
    python -m network.udp_discovery --listen
"""

import argparse
import socket
import struct
import sys
import time

MAGIC = b"CT_HELLO"
DEFAULT_PORT = 9001
BROADCAST_ADDRESS = "255.255.255.255"


def build_beacon(host, port):
    return f"{MAGIC.decode()} {host}:{port}".encode("utf-8")


def broadcast(host, port, discovery_port, interval):
    """Send the beacon forever."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    address = host if host else "0.0.0.0"
    payload = build_beacon(address, port)
    print(f"Broadcasting {payload.decode()} on UDP {discovery_port} every {interval}s …")

    try:
        while True:
            try:
                sock.sendto(payload, (BROADCAST_ADDRESS, discovery_port))
            except OSError as exc:
                print(f"  broadcast failed: {exc}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


def listen(discovery_port):
    """Print every beacon that arrives."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", discovery_port))
    except OSError as exc:
        print(f"Could not bind UDP {discovery_port}: {exc}", file=sys.stderr)
        return 1

    print(f"Listening for CareerTwin servers on UDP {discovery_port} …")
    while True:
        try:
            data, address = sock.recvfrom(1024)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith(MAGIC.decode()):
            print(f"  {address[0]}  ->  {text}")
    sock.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="CareerTwin UDP LAN discovery")
    parser.add_argument("--host", default="", help="Address to advertise")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to advertise")
    parser.add_argument(
        "--discovery-port", type=int, default=DEFAULT_PORT, help="UDP port to use"
    )
    parser.add_argument("--interval", type=float, default=3.0, help="Beacon interval")
    parser.add_argument("--listen", action="store_true", help="Listen instead of sending")
    args = parser.parse_args(argv)

    if args.listen:
        return listen(args.discovery_port)
    broadcast(args.host, args.port, args.discovery_port, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
