"""The CareerTwin computer-network layer.

``protocol``    – the wire format (frame codec), pure and unit testable
``tcp_server``  – the hand-written asyncio TCP chat server
``cli_client``  – a terminal client for the TCP server
``udp_discovery`` – optional LAN discovery beacon
"""

__all__ = ["protocol", "tcp_server", "cli_client"]
