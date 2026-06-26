from __future__ import annotations

import json
import socket
import struct
from typing import Any


DISCOVERY_PORT = 51333
DEFAULT_TCP_PORT = 51334
DISCOVER_REQUEST = b"HRD_DISCOVER_V1"
ANNOUNCE_TYPE = "hrd_announce_v1"
MAX_HEADER = 1024 * 1024


class ProtocolError(RuntimeError):
    pass


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def send_packet(sock: socket.socket, message: dict[str, Any], payload: bytes = b"") -> None:
    header = dict(message)
    header["payload_len"] = len(payload)
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_HEADER:
        raise ProtocolError("header is too large")
    sock.sendall(struct.pack("!I", len(encoded)))
    sock.sendall(encoded)
    if payload:
        sock.sendall(payload)


def recv_packet(sock: socket.socket) -> tuple[dict[str, Any], bytes]:
    header_len = struct.unpack("!I", recv_exact(sock, 4))[0]
    if header_len <= 0 or header_len > MAX_HEADER:
        raise ProtocolError(f"invalid header length: {header_len}")
    header = json.loads(recv_exact(sock, header_len).decode("utf-8"))
    payload_len = int(header.get("payload_len", 0))
    if payload_len < 0:
        raise ProtocolError("invalid payload length")
    payload = recv_exact(sock, payload_len) if payload_len else b""
    return header, payload


def socket_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()

